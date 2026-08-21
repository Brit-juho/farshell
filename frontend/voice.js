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
// W1-5: handsFreeModeOn 제거됨 (VAD 미구현 상태에서의 가짜 핸즈프리 모드 폐기)
// W2-3: 이어폰 미디어 키 트리거 토글.
// ⚠ setupMediaSession()의 무음 오디오 loop=true 재생은 주석("AudioContext.suspend()로
// 배터리 절약")과 달리 실제로는 suspend 로직이 없어 브라우저가 탭을 "미디어 재생 중"으로
// 계속 인식 — 오디오 파이프라인이 절대 안 쉬고 장시간(하루 종일) 세션에서 CPU/메모리가
// 누적되는 원인으로 확인됨(사용자 실측: 이 기능 ON 직후 CPU/RAM 폭증 재현). 그래서 더 이상
// localStorage로 기억하지 않는다 — 매 페이지 로드마다 항상 OFF로 시작, 필요할 때만 명시적으로 켠다.
let mediaKeyTriggerOn = false;

const micBtn = document.getElementById('mic-btn-wrap');
const micStatus = document.getElementById('mic-status');

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
    const r = await fetch(`${API}/voice/stt/preload`, { method: 'POST' });
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

async function toggleRecording() {
  if (isRecording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  // 준비 안 됐으면 이번 탭은 모델 준비만 하고 리턴 (녹음은 준비 완료 후 다음 탭)
  if (!_sttReady) { await ensureSttReady(); return; }
  try {
    // [D8 barge-in] 재생 중인 TTS 중단 — 사용자가 말하기 시작하면 즉시 정지
    try {
      fetch(`${API}/voice/cancel`, { method: 'POST' }).catch(() => {});
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

// --- TTS 재생 ---

async function speakText(text) {
  try {
    const res = await fetch(`${API}/voice/output`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    // C5: 빈 텍스트 400/서버 500이면 오디오가 아닌 에러 JSON이 온다 — 재생하지 않음.
    if (!res.ok) { console.warn('[TTS] 서버 오류:', res.status); return; }
    const audioBlob = await res.blob();
    if (audioBlob.size === 0) return;
    playAudioBlob(audioBlob);
  } catch (err) {
    console.error('TTS 실패:', err);
  }
}

// [A1] autoplay 정책 대응 — play() 실패 시 UI로 수동 재생 유도
let _pendingAudioUrl = null;
// 현재 재생 중인 TTS의 object URL. audio 엘리먼트 자체(window._currentTTSAudio)와
// 항상 쌍으로 관리 — pause()만으로 중단하면 ended/error가 안 불려 URL.revokeObjectURL이
// 영영 안 불리고, Audio 엘리먼트의 디코더/오디오 파이프라인도 안 풀린다. task_complete
// 알림이 응답마다 오는 활성 세션에서는 이게 매번 하나씩 새서 GB 단위로 쌓인다.
let _currentTTSUrl = null;

// [D8 barge-in] 재생 중이던 TTS를 완전히 정지 — pause + revoke + src 해제(디코더 파이프라인
// 즉시 반환). barge-in이 일어나는 모든 지점(startRecording, playAudioBlob)이 이걸 공유한다.
function _stopCurrentTTS() {
  const audio = window._currentTTSAudio;
  if (audio) {
    try { audio.pause(); } catch {}
    try { audio.removeAttribute('src'); audio.load(); } catch {}
    window._currentTTSAudio = null;
  }
  if (_currentTTSUrl) {
    URL.revokeObjectURL(_currentTTSUrl);
    _currentTTSUrl = null;
  }
}

function playAudioBlob(blob) {
  console.log('[TTS] playAudioBlob called,', blob.size, 'bytes');
  // [D8 barge-in] 기존 재생 중이던 오디오가 있으면 먼저 완전히 정리
  _stopCurrentTTS();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  window._currentTTSAudio = audio;
  _currentTTSUrl = url;
  audio.onended = () => {
    console.log('[TTS] 재생 완료');
    if (_currentTTSUrl === url) { URL.revokeObjectURL(url); _currentTTSUrl = null; }
    if (window._currentTTSAudio === audio) window._currentTTSAudio = null;
  };
  audio.onerror = (e) => {
    console.error('[TTS] 재생 에러:', e);
    if (_currentTTSUrl === url) { URL.revokeObjectURL(url); _currentTTSUrl = null; }
    if (window._currentTTSAudio === audio) window._currentTTSAudio = null;
  };

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
    btn.textContent = '터치하여 재생';
    btn.className = 'vt-btn-primary';
    btn.style.cssText = 'display:block;margin:6px auto 0;font-size:13px;';
    btn.onclick = () => {
      if (_pendingAudioUrl) {
        const url = _pendingAudioUrl;
        const a = new Audio(url);
        window._currentTTSAudio = a;
        _currentTTSUrl = url;
        a.play();
        const cleanup = () => {
          if (_currentTTSUrl === url) { URL.revokeObjectURL(url); _currentTTSUrl = null; }
          if (window._currentTTSAudio === a) window._currentTTSAudio = null;
        };
        a.onended = cleanup;
        a.onerror = cleanup;
        _pendingAudioUrl = null;
      }
      btn.remove();
    };
    document.getElementById('mic-status').appendChild(btn);
  }
}

// W2-3: 이어폰 미디어 키 트리거 ON/OFF 토글. 이번 탭에서만 유효 — 새로고침/재접속 시 항상 OFF로 시작.
function toggleMediaKeyTrigger() {
  mediaKeyTriggerOn = !mediaKeyTriggerOn;
  const btn = document.getElementById('mediakey-btn');
  if (btn) btn.classList.toggle('active', mediaKeyTriggerOn);

  if (mediaKeyTriggerOn) {
    // ON: Media Session 핸들러 등록 + 무음 오디오 재생
    setupMediaSession();
    micStatus.textContent = '이어폰 Play/Pause = 녹음 토글';
  } else {
    // OFF: 핸들러 해제 + 무음 완전 정지 → OS가 기본 미디어 컨트롤 가짐
    if ('mediaSession' in navigator) {
      try {
        navigator.mediaSession.setActionHandler('play', null);
        navigator.mediaSession.setActionHandler('pause', null);
        navigator.mediaSession.setActionHandler('nexttrack', null);
        navigator.mediaSession.setActionHandler('previoustrack', null);
        navigator.mediaSession.metadata = null;
        navigator.mediaSession.playbackState = 'none';
      } catch (e) {}
    }
    if (silentAudio) {
      try {
        silentAudio.pause();
        silentAudio.removeAttribute('src');
        silentAudio.load();
      } catch (e) {}
      silentAudio = null;  // 다음 ON 토글 시 재생성
    }
    micStatus.textContent = '이어폰 미디어 키 → OS 기본 동작';
  }
  setTimeout(() => { micStatus.textContent = ''; }, 2500);
}

// --- 음성 전용 모드 ---

function toggleVoiceOnly() {
  document.body.classList.toggle('voice-only-mode');
  const btn = document.getElementById('voiceonly-btn');
  const isOn = document.body.classList.contains('voice-only-mode');
  if (btn) {
    btn.classList.toggle('active', isOn);
  }
  micStatus.textContent = isOn ? '음성 전용 모드 — 터미널 숨김' : '';
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
let _notifyStableTimer = null;

function connectNotify() {
  // C2: 상한 도달 후 영구 포기하지 않는다(모바일 장시간 세션의 flap에도 알림 유지).
  // [회귀 fb827a6] 단, onopen에서 _notifyRetries를 '즉시' 0으로 리셋하면 서버가 accept
  // 직후 닫는 half-open flap에서 지수 백오프가 자라지 못해 2초마다 영구 재연결한다
  // (터미널 WS와 동일한 메모리 스톰). 3초 이상 안정적으로 열린 뒤에만 리셋한다.
  notifyWs = new WebSocket(WS_NOTIFY);

  notifyWs.onopen = () => {
    clearTimeout(_notifyStableTimer);
    _notifyStableTimer = setTimeout(() => { _notifyRetries = 0; }, 3000);
  };

  notifyWs.onmessage = (e) => {
    // 메시지 처리 중 어떤 예외(잘못된 JSON, Notification 생성자 throw 등)가 나도
    // notify 채널 전체가 죽지 않도록 방어한다.
    try {
      if (typeof e.data === 'string') {
        const data = JSON.parse(e.data);
        console.log('[NOTIFY] received:', data.type, data.summary?.slice(0, 60));
        if (data.type === 'task_complete') {
          pendingMeta = data;
          showNotification(data.summary, data.session_id);
          // 탭 파비콘 '완료' 뱃지 — 탭 재포커스 시 favicon.js가 자동 해제
          if (window.VTFavicon) VTFavicon.set('done');
        } else if (data.type === 'clipboard_push' && data.text) {
          // 맥북(서버) 쪽 clipboard_daemon.py가 감지한 시스템 클립보드 변경 —
          // copyToClipboard는 terminal.js가 전역(classic script)으로 정의.
          // "⋯ → 설정 → 드래그 시 자동 복사"를 꺼도 이 경로(fsh clip)만 안 막히면
          // 여전히 자동으로 클립보드가 덮어써지므로 같은 설정을 공유한다.
          const autoSyncOff = (localStorage.getItem('vt_autocopy_on_select') ?? 'on') === 'off';
          if (!autoSyncOff && typeof copyToClipboard === 'function') {
            copyToClipboard(data.text).then((ok) => {
              if (ok && typeof showToast === 'function') showToast('클립보드 동기화됨');
            });
          }
        }
      } else if (e.data instanceof Blob) {
        console.log('[NOTIFY] audio blob:', e.data.size, 'bytes');
        if (e.data.size > 0) {
          playAudioBlob(e.data);
        }
        pendingMeta = null;
      }
    } catch (err) {
      console.warn('[NOTIFY] 메시지 처리 오류(무시):', err);
    }
  };

  notifyWs.onclose = (ev) => {
    clearTimeout(_notifyStableTimer);
    // 인증 실패(4001)는 재시도해도 동일 → 재연결 중단.
    if (ev && ev.code === 4001) { console.warn('[NOTIFY] 인증 실패 — 재연결 중단'); return; }
    // [M2] 지수 백오프 재연결 (무한 — 지수는 5로 clamp해 오버플로 방지)
    _notifyRetries++;
    const delay = Math.min(1000 * Math.pow(2, Math.min(_notifyRetries, 5)), 30000);
    setTimeout(connectNotify, delay);
  };

  notifyWs.onerror = () => {
    notifyWs.close();
  };
}

function showNotification(summary, sessionId) {
  // 화면 상단에 토스트 알림
  const toast = document.createElement('div');
  toast.className = 'vt-toast ok';
  // 요약 텍스트 (최대 100자). summary가 없을 수도 있으므로 문자열로 정규화.
  const text = summary == null ? '' : String(summary);
  const short = text.length > 100 ? text.slice(0, 100) + '...' : text;
  toast.textContent = `✅ [${sessionId}] ${short}`;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.5s';
    setTimeout(() => toast.remove(), 500);
  }, 5000);

  // 백그라운드 브라우저 알림 (화면 밖에서도 보이게)
  showBrowserNotification('FarShell — 작업 완료', short);
}

// ⚠ Android Chrome 등 모바일 브라우저는 `new Notification()` 생성자를 금지하고
// (TypeError: Failed to construct 'Notification': Illegal constructor)
// ServiceWorkerRegistration.showNotification()만 허용한다. 예전엔 권한 수락 직후
// 첫 task_complete에서 이 생성자가 던지면 notify 처리 전체가 깨졌다(=수락하면 멈춤).
// SW 경로를 우선 쓰고, 모든 경로를 try/catch로 감싸 어떤 플랫폼에서도 예외가 새지 않게 한다.
function showBrowserNotification(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready
        .then((reg) => reg.showNotification(title, { body }))
        .catch(() => { try { new Notification(title, { body }); } catch (_) {} });
      return;
    }
    new Notification(title, { body });
  } catch (_) { /* 알림 생성 실패는 무시 — 화면 토스트로 이미 알렸다 */ }
}

// 이벤트 바인딩 (mic-btn-wrap의 onclick="toggleRecording()"으로 처리)

// 알림 WebSocket 연결
connectNotify();

// 알림 권한은 여기서 미리 요청하지 않는다 — 브라우저는 권한을 한 번만 물어보므로,
// 사용자가 "푸시 알림"을 켜기도 전에 아무 탭에서나 먼저 소진시키면 나중에 진짜로
// 켜려 할 때 다시 물어볼 방법이 없다. ⋯ 메뉴 "푸시 알림" 토글(pushui.js → togglePush()
// → VTPush.subscribe())에서만 요청한다. showBrowserNotification()은 권한 미승인 시
// 조용히 스킵하므로 여기서 미리 받아둘 필요가 없다.

// --- PWA Service Worker 등록 ---
// (P5) 등록은 js/swreg.js 로 옮겼다. 이 파일은 음성 미설치 환경에서 아예 로드되지
// 않으므로(grid.js가 capabilities를 보고 결정), 여기 두면 SW가 등록조차 안 됐다.
// → PWA 오프라인 캐시도, Web Push도 음성 설치 여부에 인질로 잡혀 있었다.

// --- 무선 이어폰 터치 컨트롤 (Media Session API) ---
// play/pause 미디어 키를 녹음 토글로 가로챈다.
// 브라우저가 미디어 세션을 인식하려면 무음 오디오가 재생 중이어야 한다.

let silentAudio = null;

function setupMediaSession() {
  if (!('mediaSession' in navigator)) return;
  // 이중 호출 방어 — 이미 setup됐으면 silentAudio가 존재
  if (silentAudio) return;

  // [M3] 무음 오디오 — OS가 이 탭을 미디어 키 대상으로 잡으려면 오디오가 최소 한 번은
  // "재생"된 적이 있어야 한다(브라우저 오디오 포커스 요건). 예전엔 이걸 무한 loop=true로
  // 영원히 재생시켜서 오디오 파이프라인이 하루 종일 절대 안 쉬었다(CPU/메모리 누적의 실측
  // 확인된 원인). 일시정지된 음악 앱도 재생 버튼을 계속 받는 것처럼, 미디어 세션은 '재생 중'을
  // 유지할 필요 없이 '한 번 재생됨 + 일시정지' 상태로도 OS 미디어 키를 계속 받는다.
  // → 아주 짧게 1회만 재생하고 즉시 pause한다. 오디오 파이프라인이 대부분의 시간 동안
  // 실제로 쉬므로 CPU/메모리 비용이 없다.
  silentAudio = new Audio();
  silentAudio.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';
  silentAudio.loop = false;
  silentAudio.volume = 0.01;
  const _playP = silentAudio.play();
  if (_playP && _playP.then) {
    _playP.then(() => {
      try { silentAudio.pause(); } catch (_) {}
      navigator.mediaSession.playbackState = 'paused';
    }).catch(() => {});
  }

  navigator.mediaSession.metadata = new MediaMetadata({
    title: 'FarShell',
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

// 페이지 로드 직후 토글 버튼 active 상태 동기화 (사용자 인터랙션 전에도)
(function syncMediaKeyButton() {
  const apply = () => {
    const btn = document.getElementById('mediakey-btn');
    if (btn) btn.classList.toggle('active', mediaKeyTriggerOn);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply, { once: true });
  } else {
    apply();
  }
})();

// 첫 사용자 인터랙션 후 Media Session 활성화 (W2-3 토글 ON일 때만)
document.addEventListener('click', function initMedia() {
  if (mediaKeyTriggerOn) {
    setupMediaSession();
  }
  document.removeEventListener('click', initMedia);
}, { once: true });
