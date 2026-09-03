// 이어폰 미디어 키 트리거 + 음성 전용 모드 — F4에서 voice.js에서 분리.
// W2-3: 이어폰 미디어 키(재생/일시정지)를 녹음 토글로 가로챈다. 브라우저가
// 미디어 세션을 인식하려면 무음 오디오가 재생 중이어야 한다.
import { registerAction } from '../core/dom.js';
import { isRecording, startRecording, stopRecording, toggleRecording, micStatus } from './recording.js';

// W1-5: handsFreeModeOn 제거됨 (VAD 미구현 상태에서의 가짜 핸즈프리 모드 폐기)
// ⚠ setupMediaSession()의 무음 오디오 loop=true 재생은 주석("AudioContext.suspend()로
// 배터리 절약")과 달리 실제로는 suspend 로직이 없어 브라우저가 탭을 "미디어 재생 중"으로
// 계속 인식 — 오디오 파이프라인이 절대 안 쉬고 장시간(하루 종일) 세션에서 CPU/메모리가
// 누적되는 원인으로 확인됨(사용자 실측: 이 기능 ON 직후 CPU/RAM 폭증 재현). 그래서 더 이상
// localStorage로 기억하지 않는다 — 매 페이지 로드마다 항상 OFF로 시작, 필요할 때만 명시적으로 켠다.
let mediaKeyTriggerOn = false;

// W2-3: 이어폰 미디어 키 트리거 ON/OFF 토글. 이번 탭에서만 유효 — 새로고침/재접속 시 항상 OFF로 시작.
export function toggleMediaKeyTrigger() {
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

export function toggleVoiceOnly() {
  document.body.classList.toggle('voice-only-mode');
  const btn = document.getElementById('voiceonly-btn');
  const isOn = document.body.classList.contains('voice-only-mode');
  if (btn) {
    btn.classList.toggle('active', isOn);
  }
  micStatus.textContent = isOn ? '음성 전용 모드 — 터미널 숨김' : '';
}

// --- 무선 이어폰 터치 컨트롤 (Media Session API) ---
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

// F3(c): data-action 위임용 등록. voice.js는 capability에 따라 조건부로만
// 로드되므로, 등록도 로드된 경우에만 이뤄진다(미로드 시 core/dom.js가 조용히
// no-op — .needs-voice로 애초에 버튼도 숨어 있다).
registerAction('voice.only-toggle', () => toggleVoiceOnly());
registerAction('voice.mediakey-toggle', () => toggleMediaKeyTrigger());
