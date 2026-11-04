// 음성 입력 「확인 후 전송」 — 서버가 인식만 하고 pane에 입력하지 않았을 때(pending_confirm)
// 패널의 #voice-confirm 영역에 편집 가능한 텍스트 상자와 「입력」「취소」를 보인다.
//
// voice.js 번들 안의 모듈이다(recording.js가 가져간다): core/store.js·core/dom.js를 import하지 말 것.
// 입력은 POST /voice/inject — 서버가 토큰 주체·대상 pane·길이·중복을 다시 검증한다.
// 대상이 사라졌거나 토큰이 만료되면 다른 곳으로 보내지 않고 「인식 텍스트 복사」만 남긴다.
import { apiFetch } from '../core/api.js';
import { t } from '../core/i18n.js';

const API = `${location.protocol}//${location.host}`;
const MAX_CHARS = 4000;  // 서버 voice_confirm.MAX_TEXT_CHARS와 같다

let current = null;  // { token, voiceId, name, timer, busy, locked }

const $ = (id) => document.getElementById(id);

function stage(phase, target) {
  window.dispatchEvent(new CustomEvent('vt:voice-stage', { detail: { phase, ...(target !== undefined ? { target } : {}) } }));
}

function status(msg) {
  const el = $('mic-status');
  if (el) el.textContent = msg;
}

function reasonText(reason) {
  switch (reason) {
    case 'dangerous': return t('위험할 수 있는 명령이 들어 있어 확인합니다');
    case 'always': return t('설정에 따라 입력 전에 확인합니다');
    case 'unknown': return t('입력 대상이 무엇인지 알 수 없어 확인합니다');
    default: return t('일반 셸에 입력하기 전에 확인합니다');
  }
}

function setMessage(msg) {
  const el = $('voice-confirm-msg');
  if (el) { el.textContent = msg || ''; el.hidden = !msg; }
}

function setBusy(busy) {
  if (!current) return;
  current.busy = busy;
  const send = $('voice-confirm-send');
  const cancel = $('voice-confirm-cancel');
  if (send) send.disabled = busy || current.locked;
  if (cancel) cancel.disabled = busy;
}

/** 더 보낼 수 없는 상태(대상 없음·만료). 텍스트는 남겨 복사할 수 있게 한다. */
function lock(message) {
  if (!current) return;
  current.locked = true;
  clearTimeout(current.timer);
  setMessage(message);
  const copy = $('voice-confirm-copy');
  if (copy) copy.hidden = false;
  setBusy(false);
}

export function confirmActive() { return !!current; }

/** 확인 영역을 닫고 대기 중이던 토큰을 버린다(서버 토큰은 TTL로 사라진다). */
export function clearConfirm() {
  if (!current) return;
  clearTimeout(current.timer);
  current = null;
  const box = $('voice-confirm');
  if (box) box.hidden = true;
  const ta = $('voice-confirm-text');
  if (ta) ta.value = '';
  setMessage('');
  const copy = $('voice-confirm-copy');
  if (copy) copy.hidden = true;
}

async function sendNow() {
  if (!current || current.busy || current.locked) return;
  const ta = $('voice-confirm-text');
  const text = ta.value.trim();
  if (!text) { setMessage(t('입력할 텍스트가 비었습니다')); return; }
  if (text.length > MAX_CHARS) { setMessage(t('텍스트가 너무 깁니다')); return; }
  const mine = current;
  setBusy(true);
  setMessage('');
  let res = null;
  let data = null;
  try {
    res = await apiFetch(`${API}/voice/inject`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: mine.token, text, voice_id: mine.voiceId }),
    });
    try { data = await res.json(); } catch { data = null; }
  } catch (e) {
    if (current === mine) { setBusy(false); setMessage(t('입력하지 못했습니다. 다시 시도해 주세요')); }
    return;
  }
  if (current !== mine) return;  // 그 사이 취소·새 녹음으로 바뀜
  if (res.ok && data?.injected) {
    clearConfirm();
    status(data.duplicate ? t('이미 입력했습니다') : t('입력 완료 — {name}: "{text}"', { name: mine.name, text: data.text || text }));
    stage('done');
    return;
  }
  const code = data?.code || '';
  const msg = data?.error || t('전송 실패 ({status})', { status: res.status });
  if (code === 'target_gone' || code === 'expired' || code === 'voice_id') {
    lock(msg);
    stage('error');
    return;
  }
  setBusy(false);
  setMessage(msg);
}

function wire() {
  const box = $('voice-confirm');
  if (!box || box.dataset.wired) return !!box;
  box.dataset.wired = 'true';
  $('voice-confirm-send').addEventListener('click', () => sendNow());
  $('voice-confirm-cancel').addEventListener('click', () => {
    clearConfirm();
    status(t('입력을 취소했습니다'));
    stage('ready');
  });
  $('voice-confirm-copy').addEventListener('click', async () => {
    const ta = $('voice-confirm-text');
    try { await navigator.clipboard.writeText(ta.value); setMessage(t('복사했습니다')); }
    catch { ta.focus(); ta.select(); setMessage(t('직접 복사해 주세요')); }
  });
  $('voice-confirm-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendNow(); }
  });
  return true;
}

/** 서버 응답(pending_confirm)을 확인 영역에 올린다. 영역이 없는 화면이면 false — 호출자가 복사 안내로 대신한다. */
export function showConfirm({ text, token, voiceId, name, reason, ttlSec }) {
  if (!wire() || !token) return false;
  clearConfirm();
  const mine = { token, voiceId: voiceId || '', name: name || '', timer: null, busy: false, locked: false };
  current = mine;
  $('voice-confirm-reason').textContent = `${name ? name + ' · ' : ''}${reasonText(reason)}`;
  const ta = $('voice-confirm-text');
  ta.value = text || '';
  ta.maxLength = MAX_CHARS;
  $('voice-confirm-copy').hidden = true;
  setMessage('');
  $('voice-confirm').hidden = false;
  setBusy(false);
  // 서버 TTL보다 조금 일찍 막아, 눌렀는데 만료됐다는 응답을 받는 일을 줄인다.
  const ms = Math.max(5, (Number(ttlSec) || 300) - 5) * 1000;
  mine.timer = setTimeout(() => { if (current === mine) { lock(t('확인 시간이 지났습니다. 인식 텍스트를 복사해 새로 녹음해 주세요')); stage('error'); } }, ms);
  return true;
}
