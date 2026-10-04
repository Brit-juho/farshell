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
import { _stopCurrentTTS } from './tts.js';
// 순수 함수 모듈(상태·DOM 없음) — 번들에 인라인돼도 안전하다.
import {
  pickRecorderMime, contentTypeOf, buildAudioConstraints, buildRecorderOptions,
  micErrorMessage, retryWithBackoff, newVoiceId,
} from './capture.js';

const API = `${location.protocol}//${location.host}`;

const micBtn = document.getElementById('mic-btn-wrap');
export const micStatus = document.getElementById('mic-status');

let mediaRecorder = null;
let audioChunks = [];
let _failedUpload = null;  // 전송 실패한 마지막 녹음 { blob, contentType } — 최대 1건, 새 녹음 시작 시 버린다
const RETRY_DELAYS = [1000, 3000];
const retryBtn = document.getElementById('mic-retry-btn');
if (retryBtn) retryBtn.addEventListener('click', () => resendFailed());
export let isRecording = false;

// --- 마이크 녹음 ---

// STT 모델은 서버에서 첫 사용 시 ~400MB를 잡고 로딩에 몇 초 걸린다. 모델은 자동으로 받지 않는다 —
// 설정 「음성 및 알림」에서 명시적으로 받아야 한다(안 받았으면 준비가 실패하고 그 이유를 보여준다).
// 터미널만 쓰는 사람은 음성을 안 켜면 이 비용이 0이다. 그래서 첫 녹음 전에 명시적으로
// "준비"시키고 그 사실을 사용자에게 알린다. 준비되면 다음 탭부터 바로 녹음.
let _sttReady = false;
let _sttPreparing = false;

async function ensureSttReady() {
  if (_sttReady) return true;
  if (_sttPreparing) return false;
  _sttPreparing = true;
  micBtn.querySelector('.label').textContent = '준비 중...';
  micStatus.textContent = '음성 모델 준비 중… (메모리 ~400MB)';
  try {
    const r = await apiFetch(`${API}/voice/stt/preload`, { method: 'POST' });
    const d = await r.json();
    if (r.ok && d.loaded) {
      _sttReady = true;
      micBtn.querySelector('.label').textContent = '음성 입력';
      micStatus.textContent = '준비 완료 — 마이크를 눌러 녹음';
      return false;  // 이번 탭은 '준비'로 소비. 사용자가 다시 눌러 녹음.
    }
    micBtn.querySelector('.label').textContent = '음성 입력';
    micStatus.textContent = d.error || '음성 준비 실패 — STT가 설치돼 있는지 확인하세요';
  } catch (e) {
    micStatus.textContent = '음성 준비 실패 (서버 응답 없음)';
  } finally {
    _sttPreparing = false;
  }
  return false;
}

export async function toggleRecording() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

export async function startRecording() {
  // 준비 안 됐으면 이번 탭은 모델 준비만 하고 리턴 (녹음은 준비 완료 후 다음 탭)
  if (!_sttReady) { await ensureSttReady(); return; }
  try {
    // [D8 barge-in] 재생 중인 TTS 중단 — 사용자가 말하기 시작하면 즉시 정지
    try {
      apiFetch(`${API}/voice/cancel`, { method: 'POST' }).catch(() => {});
      _stopCurrentTTS();
    } catch {}

    _failedUpload = null;
    hideRetry();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: buildAudioConstraints() });
    const mime = pickRecorderMime((t) => MediaRecorder.isTypeSupported(t));
    try {
      mediaRecorder = new MediaRecorder(stream, buildRecorderOptions(mime));
    } catch (e) {
      stream.getTracks().forEach(t => t.stop());
      throw e;
    }
    audioChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      // 실제 녹음 형식은 recorder가 알려주는 값이 가장 정확하다(mime ''이면 브라우저 기본).
      const contentType = contentTypeOf(mediaRecorder.mimeType || mime);
      const blob = new Blob(audioChunks, { type: contentType });
      _stopAt = performance.now();
      await sendAudio(blob, contentType);
    };

    mediaRecorder.start();
    isRecording = true;
    micBtn.classList.add('recording');
    micBtn.querySelector('.label').textContent = '녹음 중...';
    micStatus.textContent = '녹음 중 — 탭하여 중지';
  } catch (err) {
    console.error('마이크 접근 실패:', err);
    micStatus.textContent = micErrorMessage(err);
  }
}

export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
  }
  isRecording = false;
  micBtn.classList.remove('recording');
  micBtn.querySelector('.label').textContent = '음성 입력';
  micStatus.textContent = '처리 중...';
}

let _stopAt = 0;

function hideRetry() { if (retryBtn) { retryBtn.hidden = true; retryBtn.remove(); } }

// 상태줄에 「전송 실패 — 다시 보내기」 버튼을 붙인다(textContent 갱신이 자식을 지우므로 매번 다시 붙임).
function showRetry(msg) {
  micStatus.textContent = msg;
  if (!retryBtn) return;
  retryBtn.hidden = false;
  micStatus.appendChild(retryBtn);
}

// 한 번의 POST. 네트워크 실패·5xx는 재시도 대상으로 던지고, 4xx·성공 응답은 결과로 돌려준다.
async function postOnce(blob, contentType, voiceId) {
  // [H2] 현재 활성 세션 ID를 쿼리 파라미터로 전달. window.activeId는 core/store.js
  // (app.js 번들)가 실 세션 전환마다 갱신해주는 진짜 공유 값이다 — 이 파일에서
  // core/store.js를 직접 import하면 안 되는 이유는 파일 상단 주석 참고.
  const sid = window.activeId || '';
  const res = await apiFetch(`${API}/voice/input?session_id=${encodeURIComponent(sid)}`, {
    method: 'POST',
    // 같은 녹음의 재전송이 터미널에 두 번 입력되지 않게 서버가 이 id로 중복을 건너뛴다.
    headers: { 'Content-Type': contentType, 'X-Voice-Id': voiceId },
    body: blob,
  });
  // C5: 4xx/5xx면 body가 JSON이 아닐 수 있어 파싱 실패를 허용한다.
  let data = null;
  try { data = await res.json(); } catch {}
  if (res.status >= 500) {
    const e = new Error(`server ${res.status}`);
    e.status = res.status; e.data = data;
    throw e;
  }
  return { res, data };
}

async function sendAudio(blob, contentType) {
  _failedUpload = { blob, contentType, voiceId: newVoiceId() };
  await uploadWithRetry();
}

async function resendFailed() {
  if (!_failedUpload) return;
  hideRetry();
  micStatus.textContent = '다시 보내는 중...';
  _stopAt = performance.now();
  await uploadWithRetry();
}

async function uploadWithRetry() {
  const { blob, contentType, voiceId } = _failedUpload;
  try {
    const { res, data } = await retryWithBackoff(() => postOnce(blob, contentType, voiceId), RETRY_DELAYS);
    _failedUpload = null;
    console.debug('[voice] 왕복 %dms, 서버 timing:', Math.round(performance.now() - _stopAt), data && data.timing);
    if (!res.ok) {
      // 4xx(인증·형식 거부)는 재시도하지 않는다 — 서버가 준 메시지를 그대로 보여준다.
      micStatus.textContent = (data && data.error) || `전송 실패 (${res.status})`;
    } else if (data && data.text) {
      micStatus.textContent = `"${data.text}"`;
    } else {
      micStatus.textContent = (data && data.error) || '인식 실패';
    }
    // W1-5: 핸즈프리 자동 재시작 분기 제거. 매 녹음은 명시적 클릭으로만.
    setTimeout(() => { micStatus.textContent = ''; }, 3000);
  } catch (err) {
    console.error('음성 전송 실패:', err);
    // 보관(_failedUpload)은 유지 — 버튼으로 같은 blob을 재전송한다.
    showRetry('전송 실패 — ');
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
