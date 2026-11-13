// 지정한 화면만 수신한다. 큐 순서는 서버 ACK로 관리한다.
import { vtFetch } from '../core/api.js';
import { screenId } from '../core/screen-id.js';
import { t } from '../core/i18n.js';
import { retryDelay } from '../core/ws-retry.js';
let socket = null;
let generation = '';
let current = null;
let reconnect = null;
let retries = 0;
// 녹음 중(pauseVoice(true) ~ pauseVoice(false))에는 이 화면에서 TTS를 재생하지 않는다. 서버 큐 일시정지는 웹소켓
// 메시지라, 녹음 시작 때 연결이 끊겨 있었거나 녹음 중에 재연결되면(서버는 끊긴 화면의 일시정지를 지운다) 빠진다.
// 그 틈을 여기서 막는다: 재연결 시 일시정지를 다시 보내고, 그 사이 도착한 음성은 재생하지 않고 취소로 알린다.
// 해제가 빠져도 영영 막히지 않게 서버 일시정지와 같은 120초가 지나면 저절로 풀린다(녹음은 최대 60초).
const HOLD_MAX_MS = 120000;
let holdSince = 0;
const holding = () => holdSince > 0 && Date.now() - holdSince < HOLD_MAX_MS;
const keys = new Map();
const policies = new Map();
const lookingUp = new Set();

function send(msg) {
  if (socket?.readyState === 1) socket.send(JSON.stringify(msg));
}
function ack(id, status) { send({ type: 'voice_ack', id, status }); }

export function _stopCurrentTTS() {
  if (!current) return;
  const old = current;
  current = null;
  old.audio.pause();
  old.audio.removeAttribute('src');
  old.audio.load();
  URL.revokeObjectURL(old.url);
  document.getElementById('play-pending-btn')?.remove();
  window._currentTTSAudio = null;
  if (old.id) ack(old.id, 'cancelled');
}

function play(blob, id = '') {
  _stopCurrentTTS();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  const item = current = { audio, url, id };
  window._currentTTSAudio = audio;
  const finish = (status) => {
    if (current !== item) return;
    current = null;
    URL.revokeObjectURL(url);
    audio.removeAttribute('src'); audio.load();
    document.getElementById('play-pending-btn')?.remove();
    window._currentTTSAudio = null;
    if (id) ack(id, status);
  };
  audio.onended = () => finish('played');
  audio.onerror = () => finish('failed');
  const start = async () => {
    if (current !== item) return;
    try {
      await audio.play();
      document.getElementById('play-pending-btn')?.remove();
    } catch (e) {
      if (current !== item) return;
      if (id) ack(id, 'blocked');
      let btn = document.getElementById('play-pending-btn');
      if (!btn) {
        btn = document.createElement('button');
        btn.id = 'play-pending-btn'; btn.className = 'vt-btn solid';
        btn.textContent = t('터치하여 재생');
        btn.onclick = () => start();
        document.getElementById('mic-status')?.appendChild(btn);
      }
    }
  };
  start();
}

// 구 모듈 호출 호환. 알림 방송의 바이너리 음성은 notify.js에서 더 이상 재생하지 않는다.
export function playAudioBlob(blob) { play(blob); }

export function pauseVoice(pause) {
  holdSince = pause ? Date.now() : 0;
  if (pause) _stopCurrentTTS();
  send({ type: 'voice_barge', pause });
}

function visibility() {
  const sess = window.getSession?.(window.activeId);
  const name = sess?.tmuxName || sess?.tmux_name;
  const key = keys.get(name);
  // 설정 화면을 열지 않아도 현재 화면의 가시성과 저장된 정책을 확인한다.
  if (generation && name && !key && !sess.remote && !lookingUp.has(name)) {
    lookingUp.add(name);
    vtFetch(`/voice/session?session_id=${encodeURIComponent(window.activeId)}&screen=${screenId()}`)
      .then((data) => {
        if (data?.target && data?.policy) window.voiceRememberSession(data.target.name, data.target.key, data.policy, data.owns);
      }).catch(() => {}).finally(() => lookingUp.delete(name));
  }
  send({ type: 'voice_visible', keys: !document.hidden && key ? [key] : [] });
}

function connect() {
  if (window.vtRelayHost?.() !== undefined && window.vtRelayHost() !== 'local') return;
  if (typeof WebSocket !== 'function') return;
  const params = new URLSearchParams({ screen: screenId() });
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws-voice?${params}`);
  socket.onmessage = async (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'voice_connected') {
        generation = msg.generation; retries = 0; keys.clear(); policies.clear();
        if (holding()) send({ type: 'voice_barge', pause: true });
        window.dispatchEvent(new CustomEvent('voice-connected'));
        visibility();
      } else if (msg.type === 'voice_policy') {
        policies.set(msg.key, msg); visibility();
        window.dispatchEvent(new CustomEvent('voice-policy', { detail: msg }));
      } else if (msg.type === 'voice_cancel') {
        if (current?.id === msg.id) _stopCurrentTTS();
      } else if (msg.type === 'voice_audio' && msg.generation === generation && holding()) {
        send({ type: 'voice_barge', pause: true });
        ack(msg.id, 'cancelled');
      } else if (msg.type === 'voice_audio' && msg.generation === generation) {
        const raw = atob(msg.audio);
        const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
        const status = document.getElementById('mic-status');
        if (status) status.textContent = t('{name} 응답 읽기', { name: msg.name });
        play(new Blob([bytes], { type: msg.media_type }), msg.id);
      }
    } catch (e) { console.warn('[voice]', e); }
  };
  socket.onclose = (ev) => {
    generation = ''; _stopCurrentTTS();
    if (ev.code === 4001 || ev.code === 4000) return;
    clearTimeout(reconnect);
    reconnect = setTimeout(connect, retryDelay(retries++, 1000));   // 지터만(core/ws-retry.js). 4000은 '새 연결로 교체됨'이라 워치독·복귀 재연결 대상이 아니다
  };
  socket.onerror = () => socket.close();
}
window.voiceRememberSession = (name, key, policy, owns) => { keys.set(name, key); if (policy) policies.set(key, { policy, owns }); visibility(); };
document.addEventListener('visibilitychange', visibility);
window.storeSubscribe?.(visibility);
connect();
