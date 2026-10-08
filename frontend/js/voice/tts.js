// 지정한 화면만 수신한다. 큐 순서는 서버 ACK로 관리한다.
import { vtFetch } from '../core/api.js';
import { screenId } from '../core/screen-id.js';
let socket = null;
let generation = '';
let current = null;
let reconnect = null;
let retries = 0;
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
        btn.textContent = '터치하여 재생';
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
  const btn = document.getElementById('voice-session-settings');
  const cfg = policies.get(key);
  if (btn) btn.textContent = cfg?.policy.mode === 'off' || !cfg ? '응답 읽기 설정' : cfg.owns ? '응답 읽기 켜짐' : '세션 음성 설정';
  send({ type: 'voice_visible', keys: !document.hidden && key ? [key] : [] });
}

function connect() {
  if (window.vtRelayHost?.() !== undefined && window.vtRelayHost() !== 'local') return;
  if (typeof WebSocket !== 'function') return;
  const query = new URLSearchParams(location.search).get('token') || window.VT_TOKEN || '';
  const params = new URLSearchParams({ screen: screenId() });
  if (query) params.set('token', query);
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws-voice?${params}`);
  socket.onmessage = async (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'voice_connected') {
        generation = msg.generation; retries = 0; keys.clear(); policies.clear();
        window.dispatchEvent(new CustomEvent('voice-connected'));
        visibility();
      } else if (msg.type === 'voice_policy') {
        policies.set(msg.key, msg); visibility();
        window.dispatchEvent(new CustomEvent('voice-policy', { detail: msg }));
      } else if (msg.type === 'voice_cancel') {
        if (current?.id === msg.id) _stopCurrentTTS();
      } else if (msg.type === 'voice_audio' && msg.generation === generation) {
        const raw = atob(msg.audio);
        const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
        const status = document.getElementById('mic-status');
        if (status) status.textContent = `${msg.name} 응답 읽기`;
        play(new Blob([bytes], { type: msg.media_type }), msg.id);
      }
    } catch (e) { console.warn('[voice]', e); }
  };
  socket.onclose = (ev) => {
    generation = ''; _stopCurrentTTS();
    if (ev.code === 4001 || ev.code === 4000) return;
    clearTimeout(reconnect);
    reconnect = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(++retries, 5)));
  };
  socket.onerror = () => socket.close();
}
window.voiceRememberSession = (name, key, policy, owns) => { keys.set(name, key); if (policy) policies.set(key, { policy, owns }); visibility(); };
document.addEventListener('visibilitychange', visibility);
window.storeSubscribe?.(visibility);
connect();
