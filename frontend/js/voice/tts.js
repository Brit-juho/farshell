// TTS 재생 — F4에서 voice.js에서 분리. barge-in(재생 중 새 녹음 시작 시 즉시
// 정지)이 recording.js와 이 파일 양쪽에서 일어나므로 `_stopCurrentTTS`를 export한다.
import { apiFetch } from '../core/api.js';

const API = `${location.protocol}//${location.host}`;

export async function speakText(text) {
  try {
    const res = await apiFetch(`${API}/voice/output`, {
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
// 즉시 반환). barge-in이 일어나는 모든 지점(recording.js의 startRecording, playAudioBlob)이
// 이걸 공유한다.
export function _stopCurrentTTS() {
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

export function playAudioBlob(blob) {
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
