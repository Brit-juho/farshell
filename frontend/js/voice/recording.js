// 마이크 녹음 + STT 전송 — F4에서 voice.js에서 분리.
import { apiFetch } from '../core/api.js';
import { activeSessionId } from '../core/store.js';
import { registerAction } from '../core/dom.js';
import { _stopCurrentTTS } from './tts.js';

const API = `${location.protocol}//${location.host}`;

export const micBtn = document.getElementById('mic-btn-wrap');
export const micStatus = document.getElementById('mic-status');

export let mediaRecorder = null;
let audioChunks = [];
export let isRecording = false;

// --- 마이크 녹음 ---

// STT 모델은 서버에서 첫 사용 시 ~400MB를 잡고 (최초 1회는 다운로드) 로딩에 몇 초 걸린다.
// 터미널만 쓰는 사람은 음성을 안 켜면 이 비용이 0이다. 그래서 첫 녹음 전에 명시적으로
// "준비"시키고 그 사실을 사용자에게 알린다. 준비되면 다음 탭부터 바로 녹음.
let _sttReady = false;
let _sttPreparing = false;

async function ensureSttReady() {
  if (_sttReady) return true;
  if (_sttPreparing) return false;
  _sttPreparing = true;
  micBtn.querySelector('.label').textContent = '준비 중...';
  micStatus.textContent = '음성 모델 준비 중… 최초 1회는 다운로드로 시간이 걸립니다 (메모리 ~400MB)';
  try {
    const r = await apiFetch(`${API}/voice/stt/preload`, { method: 'POST' });
    const d = await r.json();
    if (r.ok && d.loaded) {
      _sttReady = true;
      micBtn.querySelector('.label').textContent = '음성 입력';
      micStatus.textContent = '✅ 준비 완료 — 마이크를 눌러 녹음';
      return false;  // 이번 탭은 '준비'로 소비. 사용자가 다시 눌러 녹음.
    }
    micStatus.textContent = '음성 준비 실패 — STT가 설치돼 있는지 확인하세요';
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

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
    audioChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(audioChunks, { type: 'audio/webm' });
      await sendAudio(blob);
    };

    mediaRecorder.start();
    isRecording = true;
    micBtn.classList.add('recording');
    micBtn.querySelector('.label').textContent = '녹음 중...';
    micStatus.textContent = '녹음 중 — 탭하여 중지';
  } catch (err) {
    console.error('마이크 접근 실패:', err);
    micStatus.textContent = '마이크 권한 필요';
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

async function sendAudio(blob) {
  try {
    // [H2] 현재 활성 세션 ID를 쿼리 파라미터로 전달. core/store.js가 항상 먼저
    // 로드되므로(F3(b)) activeSessionId()는 세션이 없으면 null을 준다 — 옛
    // `typeof activeId !== 'undefined'` 방어 체크는 store.js 도입 전 잔재라 필요 없다.
    const sid = activeSessionId() || '';
    const res = await apiFetch(`${API}/voice/input?session_id=${sid}`, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/webm' },
      body: blob,
    });
    // C5: 4xx/5xx면 body가 JSON이 아닐 수 있어 res.ok 확인 후 파싱.
    if (!res.ok) { micStatus.textContent = `전송 실패 (${res.status})`; return; }
    const data = await res.json();
    micStatus.textContent = data.text ? `"${data.text}"` : '인식 실패';
    // W1-5: 핸즈프리 자동 재시작 분기 제거. 매 녹음은 명시적 클릭으로만.
    setTimeout(() => { micStatus.textContent = ''; }, 3000);
  } catch (err) {
    console.error('음성 전송 실패:', err);
    micStatus.textContent = '전송 실패';
  }
}

// --- 활성 세션 동기화 (하위 호환) ---
function notifyActiveSession(sessionId) {
  // voice/input에서 직접 session_id를 보내므로 서버 전역 상태 불필요
  // 하지만 local_mic에서 쓸 수 있으므로 유지
}

// 이벤트 바인딩은 data-action="voice.record"(index.html)로 처리.
registerAction('voice.record', () => toggleRecording());
