/**
 * Voice module — 마이크 녹음 + TTS 재생 + 작업 완료 알림 수신
 */

const API = `${location.protocol}//${location.host}`;
const _vToken = new URLSearchParams(location.search).get('token') || '';
const _vTokenQ = _vToken ? `?token=${_vToken}` : '';
const WS_NOTIFY = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws-notify${_vTokenQ}`;

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let handsFreeModeOn = false;

const micBtn = document.getElementById('mic-btn-wrap');
const micStatus = document.getElementById('mic-status');

// --- 마이크 녹음 ---

async function toggleRecording() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  try {
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
    micStatus.textContent = '🔴 녹음 중 — 탭하여 중지';
  } catch (err) {
    console.error('마이크 접근 실패:', err);
    micStatus.textContent = '마이크 권한 필요';
  }
}

function stopRecording() {
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
    // [H2] 현재 활성 세션 ID를 쿼리 파라미터로 전달
    const sid = typeof activeId !== 'undefined' ? activeId : '';
    const res = await fetch(`${API}/voice/input?session_id=${sid}`, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/webm' },
      body: blob,
    });
    const data = await res.json();
    micStatus.textContent = data.text ? `"${data.text}"` : '인식 실패';
    // 핸즈프리: STT 처리 후 자동으로 다음 녹음 시작
    if (handsFreeModeOn) {
      setTimeout(() => startRecording(), 500);
    } else {
      setTimeout(() => { micStatus.textContent = ''; }, 3000);
    }
  } catch (err) {
    console.error('음성 전송 실패:', err);
    micStatus.textContent = '전송 실패';
  }
}

// --- TTS 재생 ---

async function speakText(text) {
  try {
    const res = await fetch(`${API}/voice/output`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const audioBlob = await res.blob();
    playAudioBlob(audioBlob);
  } catch (err) {
    console.error('TTS 실패:', err);
  }
}

// [A1] autoplay 정책 대응 — play() 실패 시 UI로 수동 재생 유도
let _pendingAudioUrl = null;

function playAudioBlob(blob) {
  console.log('[TTS] playAudioBlob called,', blob.size, 'bytes');
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  audio.onended = () => { console.log('[TTS] 재생 완료'); URL.revokeObjectURL(url); };
  audio.onerror = (e) => { console.error('[TTS] 재생 에러:', e); URL.revokeObjectURL(url); };

  const playPromise = audio.play();
  if (playPromise) {
    playPromise.then(() => {
      console.log('[TTS] 재생 시작 성공');
    }).catch((err) => {
      console.warn('[TTS] autoplay 차단:', err.message);
      _pendingAudioUrl = url;
      showPlayButton();
    });
  }
}

function showPlayButton() {
  let btn = document.getElementById('play-pending-btn');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'play-pending-btn';
    btn.textContent = '🔊 터치하여 재생';
    btn.style.cssText = `
      padding: 8px 16px; border-radius: 8px; border: none;
      background: #a6e3a1; color: #1e1e2e; font-size: 13px;
      cursor: pointer; display: block; margin: 0 auto;
    `;
    btn.onclick = () => {
      if (_pendingAudioUrl) {
        const a = new Audio(_pendingAudioUrl);
        a.play();
        a.onended = () => { URL.revokeObjectURL(_pendingAudioUrl); _pendingAudioUrl = null; };
      }
      btn.remove();
    };
    document.getElementById('mic-status').appendChild(btn);
  }
}

// --- 핸즈프리 모드 ---

function toggleHandsFree() {
  handsFreeModeOn = !handsFreeModeOn;
  const btn = document.getElementById('handsfree-btn');
  if (btn) {
    btn.classList.toggle('active', handsFreeModeOn);
  }
  if (handsFreeModeOn) {
    micStatus.textContent = '🔄 핸즈프리 모드 — 자동으로 계속 녹음합니다';
    if (!isRecording) startRecording();
  } else {
    micStatus.textContent = '';
    if (isRecording) stopRecording();
  }
}

// --- 음성 전용 모드 ---

function toggleVoiceOnly() {
  document.body.classList.toggle('voice-only-mode');
  const btn = document.getElementById('voiceonly-btn');
  const isOn = document.body.classList.contains('voice-only-mode');
  if (btn) {
    btn.classList.toggle('active', isOn);
  }
  micStatus.textContent = isOn ? '🎧 음성 전용 모드 — 터미널 숨김' : '';
}

// --- 활성 세션 동기화 (하위 호환) ---

function notifyActiveSession(sessionId) {
  // voice/input에서 직접 session_id를 보내므로 서버 전역 상태 불필요
  // 하지만 local_mic에서 쓸 수 있으므로 유지
}

// --- 작업 완료 알림 WebSocket ---

let notifyWs = null;
let pendingMeta = null;
let _notifyRetries = 0;
const _notifyMaxRetries = 20;

function connectNotify() {
  if (_notifyRetries >= _notifyMaxRetries) return;

  notifyWs = new WebSocket(WS_NOTIFY);

  notifyWs.onopen = () => { _notifyRetries = 0; };

  notifyWs.onmessage = (e) => {
    if (typeof e.data === 'string') {
      const data = JSON.parse(e.data);
      console.log('[NOTIFY] received:', data.type, data.summary?.slice(0, 60));
      if (data.type === 'task_complete') {
        pendingMeta = data;
        showNotification(data.summary, data.session_id);
      }
    } else if (e.data instanceof Blob) {
      console.log('[NOTIFY] audio blob:', e.data.size, 'bytes');
      if (e.data.size > 0) {
        playAudioBlob(e.data);
      }
      pendingMeta = null;
    }
  };

  notifyWs.onclose = () => {
    // [M2] 지수 백오프 재연결
    _notifyRetries++;
    const delay = Math.min(1000 * Math.pow(2, _notifyRetries), 30000);
    setTimeout(connectNotify, delay);
  };

  notifyWs.onerror = () => {
    notifyWs.close();
  };
}

function showNotification(summary, sessionId) {
  // 화면 상단에 토스트 알림
  const toast = document.createElement('div');
  toast.style.cssText = `
    position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
    background: #313244; color: #cdd6f4; padding: 10px 20px;
    border-radius: 10px; font-size: 13px; z-index: 200;
    max-width: 90vw; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    animation: fadeIn 0.3s ease;
  `;
  // 요약 텍스트 (최대 100자)
  const short = summary.length > 100 ? summary.slice(0, 100) + '...' : summary;
  toast.textContent = `✅ [${sessionId}] ${short}`;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.5s';
    setTimeout(() => toast.remove(), 500);
  }, 5000);

  // 브라우저 Notification API (백그라운드용)
  if (Notification.permission === 'granted') {
    new Notification('랄프톤 — 작업 완료', { body: short });
  }
}

// 이벤트 바인딩 (mic-btn-wrap의 onclick="toggleRecording()"으로 처리)

// 알림 WebSocket 연결
connectNotify();

// 알림 권한 — 첫 사용자 인터랙션 시 한 번만 요청
if ('Notification' in window && Notification.permission === 'default') {
  document.addEventListener('click', function _reqNotify() {
    Notification.requestPermission();
    document.removeEventListener('click', _reqNotify);
  }, { once: true });
}

// --- PWA Service Worker 등록 ---
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
}

// --- 무선 이어폰 터치 컨트롤 (Media Session API) ---
// play/pause 미디어 키를 녹음 토글로 가로챈다.
// 브라우저가 미디어 세션을 인식하려면 무음 오디오가 재생 중이어야 한다.

let silentAudio = null;

function setupMediaSession() {
  if (!('mediaSession' in navigator)) return;

  // [M3] 무음 오디오 — AudioContext.suspend()로 배터리 절약
  silentAudio = new Audio();
  silentAudio.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';
  silentAudio.loop = true;
  silentAudio.volume = 0.01;
  silentAudio.play().catch(() => {});

  navigator.mediaSession.metadata = new MediaMetadata({
    title: '랄프톤 Voice Terminal',
    artist: '음성 입력 대기 중',
    album: 'Voice Control',
  });

  // play = 녹음 시작, pause = 녹음 중지
  navigator.mediaSession.setActionHandler('play', () => {
    if (!isRecording) {
      startRecording();
      navigator.mediaSession.metadata.artist = '녹음 중...';
      navigator.mediaSession.playbackState = 'playing';
    }
  });

  navigator.mediaSession.setActionHandler('pause', () => {
    if (isRecording) {
      stopRecording();
      navigator.mediaSession.metadata.artist = '음성 입력 대기 중';
      navigator.mediaSession.playbackState = 'paused';
    }
  });

  // 더블탭 (다음 트랙) = 녹음 토글
  navigator.mediaSession.setActionHandler('nexttrack', () => {
    toggleRecording();
  });

  // 이전 트랙 = TTS로 마지막 출력 읽어주기 (향후 확장)
  navigator.mediaSession.setActionHandler('previoustrack', () => {
    // 추후: 마지막 터미널 출력을 TTS로 읽기
  });

  navigator.mediaSession.playbackState = 'paused';
}

// 첫 사용자 인터랙션 후 Media Session 활성화
document.addEventListener('click', function initMedia() {
  setupMediaSession();
  document.removeEventListener('click', initMedia);
}, { once: true });
