// 마이크 녹음 + STT 전송 — F4에서 voice.js에서 분리.
//
// 🔴 F4 완료 직후 발견해 여기서 고친 사고: 이 파일은 voice.js 전용 lib entry로
// 별도 빌드된다(vite.config.js 참고, app.js와 완전히 분리된 두 번의 Rollup 실행).
// 그 말은 `import ... from '../core/store.js'`를 쓰면 voice 번들이 core/store.js
// 소스를 "app.js와 공유하는 하나의 모듈"이 아니라 **자기 것만의 새 사본**으로
// 통째로 인라인한다는 뜻이다 — sessions/activeId 같은 상태를 가진 모듈을 그렇게
// import하면, 그 사본이 자기 자신의 빈 `sessions = {}`/`activeId = null`로
// `window.sessions`/`window.activeId`/`window.getSession`을 실제 앱 상태 위에
// **덮어써 버린다**(실측: 세션이 실제로 있는데도 voice.js 로드 후
// `window.sessens`가 빈 객체가 됨 — picker.js/search.js/quickopen.js처럼 아직
// classic script라 bare `sessions`/`getSession`을 읽는 파일 전부가 깨진다).
// apiFetch(core/api.js)는 내부에서 `window.VT_TOKEN`/`window.API_BASE`를 그때그때
// bare로 읽을 뿐 자체 상태가 없어 안전하지만, core/store.js·core/dom.js처럼
// 모듈 최상위에 mutable state(`sessions`/`registry`)를 갖고 `window.*`에 무조건
// 덮어쓰는 모듈은 여기서 import하면 안 된다 — 대신 그 값이 필요하면 실제
// 공유 인스턴스인 `window.activeId`/`window.registerAction`을 bare로 읽는다
// (F4 이전 voice.js가 classic script로 하던 방식 그대로).
import { apiFetch } from '../core/api.js';
import { pauseVoice } from './tts.js';
// 순수 함수 모듈(상태·DOM 없음) — 번들에 인라인돼도 안전하다.
import {
  pickRecorderMime, contentTypeOf, buildAudioConstraints, buildRecorderOptions,
  micErrorMessage, retryWithBackoff, newVoiceId,
} from './capture.js';
import { t } from '../core/i18n.js';
// PCM 청크 경로(AudioWorklet → 16kHz PCM16 → POST /voice/chunk). 지원하지 않거나 시작에 실패하면 아래 MediaRecorder 경로를 쓴다.
import { primeAudioContext } from './pcm-capture.js';
import { startPcmSession } from './pcm-session.js';

const API = `${location.protocol}//${location.host}`;

const micBtn = document.getElementById('mic-btn-wrap');
export const micStatus = document.getElementById('mic-status');

let mediaRecorder = null;
let pcmSession = null;  // PCM 청크 경로의 진행 중 녹음(없으면 MediaRecorder 경로)
let audioChunks = [];
let target = null;
let busy = false;
let recordTimer = null;
let _failedUpload = null;  // 전송 실패한 마지막 녹음 { blob, contentType, ... attempts, failedAt } — 최대 1건, 새 녹음 시작 시 버린다
let _retryTimer = null;
const RETRY_DELAYS = [1000, 3000];
// 「다시 보내기」는 일시 장애(터널 끊김 등)를 넘기려는 것이다. 누르지 않으면 이 시간 뒤 버튼과 보관분을 버린다.
const RETRY_TTL_MS = 15000;
const MAX_FAILED_ATTEMPTS = 2;  // 처음 전송 + 다시 보내기 1회. 그래도 실패하면 더 붙잡지 않는다.
// 서버가 "잠시 뒤 다시"라는 뜻으로 돌려주는 상태. 이 밖의 5xx는 같은 녹음을 다시 보내도 같은 결과다.
const TRANSIENT_STATUS = new Set([429, 502, 503, 504]);
const retryBtn = document.getElementById('mic-retry-btn');
if (retryBtn) retryBtn.addEventListener('click', () => resendFailed());
export let isRecording = false;

// --- 마이크 녹음 ---

// STT 모델은 서버에서 처음 사용할 때 로딩에 몇 초 걸린다. 모델은 자동으로 받지 않는다 —
// 설정 「음성 및 알림」에서 명시적으로 받아야 한다. 받아 둔 모델이라도 안 올라가 있으면 첫 탭은
// "준비"(올리기)로 소비하고 그 사실을 알린다. 안 받았거나 못 쓰는 환경이면 서버에 아무것도 시키지
// 않고 안내만 보인다(실수로 눌러도 부작용이 없다).
let _sttPreparing = false;

function stage(phase, targetName) {
  window.dispatchEvent(new CustomEvent('vt:voice-stage', { detail: { phase, ...(targetName !== undefined ? { target: targetName } : {}) } }));
}

const NOT_READY_MSG = t('음성 모델이 준비되지 않았습니다 — 설정 「음성 및 알림」에서 준비하세요');

// 'ready' 녹음해도 됨 · 'consumed' 이번 탭은 모델 올리기로 썼음 · 'blocked' 안내만 보이고 끝.
// 서버 상태를 매번 GET으로 확인한다(캐시하지 않음 — 서버 재시작으로 모델이 내려가도 바로 반영).
async function prepareStt() {
  let st;
  try {
    st = await apiFetch(`${API}/voice/stt/status`).then((r) => r.json());
  } catch (e) {
    micStatus.textContent = t('음성 상태를 확인하지 못했습니다 (서버 응답 없음)');
    stage('error');
    return 'blocked';
  }
  if (st.loaded && st.loaded_model === st.model) return 'ready';
  if (!st.available || !st.selected_valid || !st.selected_ready) {
    micStatus.textContent = st.last_error || NOT_READY_MSG;
    stage('error');
    return 'blocked';
  }
  if (_sttPreparing) return 'blocked';
  _sttPreparing = true;
  stage('preparing', '');
  micBtn.querySelector('.label').textContent = t('준비 중...');
  micStatus.textContent = t('선택한 음성 모델 준비 중…');
  try {
    const r = await apiFetch(`${API}/voice/stt/preload`, { method: 'POST' });
    const d = await r.json();
    if (r.ok && d.loaded) {
      micStatus.textContent = t('준비 완료 — 마이크를 눌러 녹음');
      stage('ready');
      return 'consumed';
    }
    micStatus.textContent = d.error || t('음성 준비 실패 — STT가 설치돼 있는지 확인하세요');
    stage('error');
  } catch (e) {
    micStatus.textContent = t('음성 준비 실패 (서버 응답 없음)');
    stage('error');
  } finally {
    _sttPreparing = false;
    micBtn.querySelector('.label').textContent = t('음성 입력');
  }
  return 'blocked';
}

export async function toggleRecording() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

export async function startRecording() {
  if (busy) return;
  if (window.vtRelayHost?.() && window.vtRelayHost() !== 'local') {
    micStatus.textContent = t('원격 호스트에 직접 접속해 음성을 사용하세요'); return;
  }
  busy = true;
  micStatus.textContent = t('입력 대상과 마이크 확인 중…');
  stage('starting', '');
  let stream = null;
  let paused = false;
  let pcm = null;
  // iOS Safari는 AudioContext를 사용자 제스처 안에서 만들고 resume해야 한다 — 아래 await들보다 먼저 만든다.
  let pcmCtx = primeAudioContext();
  const dropCtx = () => { if (pcmCtx) { try { pcmCtx.close().catch(() => {}); } catch {} pcmCtx = null; } };
  try {
    const prep = await prepareStt();
    if (prep !== 'ready') { dropCtx(); busy = false; return; }
    // 마이크부터 연다 — 없거나 거부되면 서버에 입력 대상 토큰 같은 상태를 만들기 전에 끝난다.
    stream = await navigator.mediaDevices.getUserMedia({ audio: buildAudioConstraints() });
    const sid = window.activeId || '';
    const tr = await apiFetch(`${API}/voice/target`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid }),
    });
    const captured = await tr.json();
    if (!tr.ok) throw new Error(captured.error || t('입력 대상 준비 실패'));
    // PCM 청크 경로를 먼저 시도한다. 실패하면(워클릿 미지원·컨텍스트 거부 등) 같은 스트림으로 MediaRecorder 경로를 쓴다.
    const voiceId = newVoiceId();
    if (pcmCtx) {
      try {
        pcm = await startPcmSession({
          ctx: pcmCtx, stream, apiFetch, base: API, voiceId, token: captured.token, sessionId: sid,
          onLimit: () => stopRecording(),
        });
        console.debug('[voice] 캡처 경로: pcm-chunk (입력 %dHz → 16kHz)', pcm.sampleRate);
      } catch (e) {
        console.debug('[voice] PCM 청크 시작 실패 — MediaRecorder로 폴백:', e && e.message);
      }
      pcmCtx = null;  // 성공하면 세션이, 실패하면 startPcmCapture가 닫는다
    }
    const mime = pcm ? '' : pickRecorderMime((t) => MediaRecorder.isTypeSupported(t));
    const recorder = pcm ? null : new MediaRecorder(stream, buildRecorderOptions(mime));
    if (!pcm) console.debug('[voice] 캡처 경로: MediaRecorder (%s)', mime || '기본');
    // 여기까지 오면 녹음이 시작된다 — 이전 실패분·버튼·TTS 일시정지는 이때서야 정리한다.
    target = { ...captured, sessionId: sid };
    stage('starting', target.name);
    pauseVoice(true); paused = true;
    dropFailedUpload();
    if (pcm) { pcmSession = pcm; pcmVoiceId = voiceId; stream = null; }  // 트랙은 세션이 닫는다
    mediaRecorder = recorder;
    audioChunks = [];
    const liveStream = stream;

    if (recorder) recorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    if (recorder) recorder.onstop = async () => {
      liveStream.getTracks().forEach(t => t.stop());
      // 실제 녹음 형식은 recorder가 알려주는 값이 가장 정확하다(mime ''이면 브라우저 기본).
      const contentType = contentTypeOf(recorder.mimeType || mime);
      const blob = new Blob(audioChunks, { type: contentType });
      _stopAt = performance.now();
      try { await sendAudio(blob, contentType, target); }
      finally { busy = false; pauseVoice(false); }
    };

    if (recorder) recorder.start();
    stream = null;  // 이제 onstop이 닫는다
    busy = false;
    recordTimer = setTimeout(() => stopRecording(), 60000);
    isRecording = true;
    micBtn.classList.add('recording');
    micBtn.querySelector('.label').textContent = t('녹음 중...');
    micStatus.textContent = t('{name}에 녹음 중 — 탭하여 중지 (최대 60초)', { name: target.name });
    stage('recording', target.name);
  } catch (err) {
    console.error('마이크 접근 실패:', err);
    dropCtx();
    if (pcm) { pcmSession = null; pcm.cancel(); }
    if (stream) stream.getTracks().forEach(t => t.stop());
    micStatus.textContent = err.name === 'Error' ? err.message : micErrorMessage(err);
    busy = false;
    if (paused) pauseVoice(false);
    stage('error');
  }
}

export function stopRecording() {
  if (!isRecording) return;
  clearTimeout(recordTimer); busy = true;
  if (pcmSession) {
    const s = pcmSession; pcmSession = null;
    finishPcm(s);
  } else if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
  }
  isRecording = false;
  micBtn.classList.remove('recording');
  micBtn.querySelector('.label').textContent = t('음성 입력');
  micStatus.textContent = t('처리 중...');
  stage('processing');
}

let _stopAt = 0;
let pcmVoiceId = '';

// PCM 청크 경로의 녹음 종료: 남은 조각을 final로 보내고, 결과는 MediaRecorder 경로와 같은 표시 코드가 받는다.
async function finishPcm(session) {
  _stopAt = performance.now();
  try {
    await session.stop();
    _failedUpload = { session, voiceId: pcmVoiceId, captured: target, attempts: 0, failedAt: 0 };
    await uploadWithRetry();
  } finally { busy = false; pauseVoice(false); }
}

function hideRetry() {
  clearTimeout(_retryTimer); _retryTimer = null;
  if (retryBtn) { retryBtn.hidden = true; retryBtn.remove(); }
}

// 보관한 녹음과 버튼을 함께 버린다 — 둘이 따로 남으면 눌러도 안 되는 버튼이 생긴다.
function dropFailedUpload() {
  if (_failedUpload && _failedUpload.session) _failedUpload.session.cancel();  // 서버에 쌓인 조각도 버린다
  _failedUpload = null; hideRetry();
}

// 세션을 옮기면 이전 세션에 보낼 재전송 버튼은 더 붙잡지 않는다.
document.addEventListener('vt:session-switch', () => { if (!busy) dropFailedUpload(); });

// 상태줄에 「전송 실패 — 다시 보내기」 버튼을 붙인다(textContent 갱신이 자식을 지우므로 매번 다시 붙임).
function showRetry(msg) {
  micStatus.textContent = msg;
  if (!retryBtn) return;
  retryBtn.hidden = false;
  micStatus.appendChild(retryBtn);
  clearTimeout(_retryTimer);
  _retryTimer = setTimeout(() => {
    if (busy) return;
    dropFailedUpload();
    micStatus.textContent = t('전송하지 못했습니다. 새로 녹음해 주세요');
  }, RETRY_TTL_MS);
}

// 한 번의 POST. 네트워크 실패·5xx는 재시도 대상으로 던지고, 4xx·성공 응답은 결과로 돌려준다.
async function postOnce(blob, contentType, voiceId, captured) {
  // [H2] 현재 활성 세션 ID를 쿼리 파라미터로 전달. window.activeId는 core/store.js
  // (app.js 번들)가 실 세션 전환마다 갱신해주는 진짜 공유 값이다 — 이 파일에서
  // core/store.js를 직접 import하면 안 되는 이유는 파일 상단 주석 참고.
  const sid = captured.sessionId;
  const res = await apiFetch(`${API}/voice/input?session_id=${encodeURIComponent(sid)}`, {
    method: 'POST',
    // 같은 녹음의 재전송이 터미널에 두 번 입력되지 않게 서버가 이 id로 중복을 건너뛴다.
    headers: { 'Content-Type': contentType, 'X-Voice-Id': voiceId, 'X-Voice-Target': captured.token },
    body: blob,
  });
  // C5: 4xx/5xx면 body가 JSON이 아닐 수 있어 파싱 실패를 허용한다.
  let data = null;
  try { data = await res.json(); } catch {}
  if (TRANSIENT_STATUS.has(res.status)) {
    const e = new Error(`server ${res.status}`);
    e.status = res.status; e.data = data;
    throw e;
  }
  return { res, data };
}

async function sendAudio(blob, contentType, captured) {
  _failedUpload = { blob, contentType, voiceId: newVoiceId(), captured, attempts: 0, failedAt: 0 };
  await uploadWithRetry();
}

async function resendFailed() {
  if (!_failedUpload || busy) return;
  if (Date.now() - _failedUpload.failedAt > RETRY_TTL_MS) {
    dropFailedUpload(); micStatus.textContent = t('재전송 시간이 만료됐습니다. 새로 녹음해 주세요'); return;
  }
  busy = true;
  hideRetry();
  micStatus.textContent = t('다시 보내는 중...');
  stage('processing');
  _stopAt = performance.now();
  try { await uploadWithRetry(); } finally { busy = false; }
}

async function uploadWithRetry() {
  const { blob, contentType, voiceId, captured, session } = _failedUpload;
  try {
    // PCM 청크 경로는 업로더가 재전송·ack를 이미 맡고 있다(실패 후 다시 부르면 남은 조각으로 재시도).
    const { res, data } = session ? await session.result() : await retryWithBackoff(() => postOnce(blob, contentType, voiceId, captured), RETRY_DELAYS);
    _failedUpload = null;
    console.debug('[voice] 왕복 %dms, 서버 timing:', Math.round(performance.now() - _stopAt), data && data.timing);
    if (!res.ok) {
      // 4xx(인증·형식 거부)는 재시도하지 않는다 — 서버가 준 메시지를 그대로 보여준다.
      micStatus.textContent = (data && data.error) || t('전송 실패 ({status})', { status: res.status });
    } else if (data && data.text) {
      micStatus.textContent = data.injected ? t('입력 완료 — {name}: "{text}"', { name: captured.name, text: data.text }) : t('입력되지 않음 — {text}', { text: data.text });
      if (!data.injected && navigator.clipboard) {
        const copy = document.createElement('button'); copy.className = 'vt-btn sm'; copy.textContent = t('인식 텍스트 복사');
        copy.onclick = () => navigator.clipboard.writeText(data.text); micStatus.appendChild(copy);
      }
    } else {
      micStatus.textContent = (data && data.error) || t('인식 실패');
    }
    // W1-5: 핸즈프리 자동 재시작 분기 제거. 매 녹음은 명시적 클릭으로만.
    stage(res.ok && data?.injected ? 'done' : 'error');
  } catch (err) {
    console.error('음성 전송 실패:', err);
    const f = _failedUpload;
    if (f && ++f.attempts < MAX_FAILED_ATTEMPTS) {
      // 보관(_failedUpload)은 유지 — 버튼으로 같은 blob을 재전송한다.
      f.failedAt = Date.now();
      showRetry(t('전송 실패 — '));
    } else {
      dropFailedUpload();
      micStatus.textContent = t('전송하지 못했습니다. 새로 녹음해 주세요');
    }
    stage('error');
  }
}

// --- 활성 세션 동기화 (하위 호환) ---
function notifyActiveSession(sessionId) {
  // voice/input에서 직접 session_id를 보내므로 서버 전역 상태 불필요
  // 하지만 local_mic에서 쓸 수 있으므로 유지
}

// 이벤트 바인딩은 data-action="voice.record"(index.html)로 처리. window.registerAction
// 은 app.js 번들의 진짜 인스턴스다 — core/dom.js를 여기서 import하면 안 되는 이유는
// 파일 상단 주석 참고(자체 registry 사본을 만들어 문서 클릭 리스너가 중복된다).
window.registerAction('voice.record', () => toggleRecording());
