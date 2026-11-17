// 음성 입력 대상 고정 — 「현재 보는 세션 따라가기」(기본) 또는 「특정 세션 고정」.
//
// 이 파일은 voice.js 번들(recording.js)과 app.js 번들(status-panel.js·설정)이 **각자 사본**으로 가져간다.
// 그래서 모듈 상태를 두지 않는다 — 값은 localStorage에 있고(저장이 막히면 window 쪽 메모리 값),
// 세션 목록은 core/store.js가 노출한 window.sessions를 bare로 읽는다(recording.js 상단 주석: store.js를
// 여기서 import하면 voice 번들이 빈 사본으로 window.sessions를 덮어쓴다). store.js·dom.js를 import하지 말 것.
//
// 고정 값은 웹 PTY id가 아니라 **tmux 세션 이름**이다. PTY id는 화면을 새로 열 때마다 달라질 수 있어
// 새로고침 뒤에 고정이 풀리기 때문이다. 녹음 시점에 그 이름을 가진 열린 세션 id를 찾아 서버에 보낸다.
// 찾지 못하면 활성 세션으로 대신하지 않고 막는다(missing).
import { t } from '../core/i18n.js';
import { vtFetch } from '../core/api.js';

export const PIN_KEY = 'vt_voice_pin';
export const PIN_EVENT = 'vt:voice-pin';
const MEMORY = '__vtVoicePinMemory';

export function getPin() {
  try {
    const v = window.localStorage.getItem(PIN_KEY);
    if (typeof v === 'string') return v;
  } catch { /* 저장소 접근 불가 — 메모리 값으로 */ }
  return typeof window[MEMORY] === 'string' ? window[MEMORY] : '';
}

/** name이 빈 문자열이면 고정을 푼다(따라가기). */
export function setPin(name) {
  const v = typeof name === 'string' ? name.slice(0, 200) : '';
  window[MEMORY] = v;
  try {
    if (v) window.localStorage.setItem(PIN_KEY, v); else window.localStorage.removeItem(PIN_KEY);
  } catch { /* 저장 실패해도 이 화면에서는 동작한다 */ }
  window.dispatchEvent(new CustomEvent(PIN_EVENT, { detail: { name: v } }));
}

const tmuxNameOf = (s) => (s && (s.tmuxName || s.tmux_name)) || '';

/** 지금 이 화면에 열려 있는 tmux 세션 [{ id, name }] — 이름 중복은 한 번만, 원격 호스트 세션은 뺀다. */
export function pinCandidates() {
  const all = window.sessions || {};
  const seen = new Set();
  const out = [];
  for (const id of Object.keys(all)) {
    const s = all[id];
    const name = tmuxNameOf(s);
    if (!name || s.remote || seen.has(name)) continue;
    seen.add(name);
    out.push({ id, name });
  }
  return out;
}

/** 녹음에 쓸 웹 세션 id를 정한다. → { sid, name, pinned, missing }.
 *  고정이 없으면 활성 세션. 고정이 있는데 열린 세션이 없으면 missing(활성 세션으로 대체하지 않는다). */
export function resolveInputTarget() {
  const pin = getPin();
  if (!pin) return { sid: window.activeId || '', name: '', pinned: false, missing: false };
  const all = window.sessions || {};
  const active = window.activeId;
  // 같은 tmux 세션이 여러 화면으로 열려 있으면 활성 화면을 우선한다.
  if (active && tmuxNameOf(all[active]) === pin && !all[active].remote) return { sid: active, name: pin, pinned: true, missing: false };
  const hit = pinCandidates().find((c) => c.name === pin);
  if (hit) return { sid: hit.id, name: pin, pinned: true, missing: false };
  return { sid: '', name: pin, pinned: true, missing: true };
}

export function pinMissingMessage() { return t('고정한 입력 대상이 없습니다'); }

/** 변경 알림(같은 화면의 setPin + 다른 탭의 storage). 해제 함수를 돌려준다. */
export function onPinChange(fn) {
  const local = () => fn();
  const cross = (e) => { if (e.key === PIN_KEY || e.key === null) fn(); };
  window.addEventListener(PIN_EVENT, local);
  window.addEventListener('storage', cross);
  return () => { window.removeEventListener(PIN_EVENT, local); window.removeEventListener('storage', cross); };
}

// 목록 글자 — 별칭(레일 행과 같은 이름). 값·고정은 계속 세션 ID(tmux 이름)다.
// 별칭은 core/store.js의 공용 지도(window.sessionAlias)에서 읽는다 — 이 파일은 voice.js 번들에도 사본이 들어가므로
// store.js를 import하지 않는다(위 머리말). 지도가 없는 환경(store 없이 뜬 화면)만 window에 둔 자체 지도를 쓴다.
const TITLES = '__vtPinTitles';
const TITLES_TTL_MS = 15000;
const cleanTitle = (v) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 128) : '');

/** 세션 ID → 보이는 이름(별칭). 모르면 ID. */
export function pinLabel(name) {
  if (!name) return '';
  if (typeof window.sessionAlias === 'function') return window.sessionAlias(name) || name;
  const c = window[TITLES];
  return (c && c.map && c.map[name]) || name;
}

/** 서버의 세션 목록으로 별칭을 받아 둔다(공용 지도가 있으면 거기에). 15초 안이면 건너뛰고, 보이는 이름이 바뀌었을 때만 true. */
async function refreshPinTitles() {
  const c = window[TITLES] || (window[TITLES] = { map: {}, at: 0, busy: false });
  // 다른 맥을 보는 중이면 받지 않는다 — 그 맥의 목록이 아니고, 음성 패널도 직접 접속이 필요하다고 막아 둔 상태다.
  const relay = window.vtRelayHost?.();
  if (relay && relay !== 'local') return false;
  if (c.busy || Date.now() - c.at < TITLES_TTL_MS) return false;
  c.busy = true;
  try {
    const list = await vtFetch('/api/tmux/sessions');
    const rows = (Array.isArray(list) ? list : []).filter((x) => x && typeof x.name === 'string' && x.name);
    const before = JSON.stringify(rows.map((x) => pinLabel(x.name)));
    if (typeof window.setSessionAliases === 'function') window.setSessionAliases(rows);
    else {
      const map = {};
      for (const x of rows) {
        const title = cleanTitle(typeof x.alias === 'string' ? x.alias : x.catalog && x.catalog.title) || cleanTitle(c.map[x.name]);
        if (title) map[x.name] = title;
      }
      c.map = map;
    }
    return JSON.stringify(rows.map((x) => pinLabel(x.name))) !== before;
  } catch { return false; } finally { c.at = Date.now(); c.busy = false; }
}

/** <select>를 현재 목록·고정 값으로 채운다. 고정한 세션이 목록에 없으면 「(없음)」으로 남겨 보여 준다. */
export function fillPinSelect(select) {
  const pin = getPin();
  const names = pinCandidates().map((c) => c.name);
  const keep = document.activeElement === select;
  const sig = JSON.stringify([pin, names, names.map(pinLabel)]);
  if (select.dataset.sig === sig) return;
  select.dataset.sig = sig;
  select.replaceChildren();
  const follow = document.createElement('option');
  follow.value = ''; follow.textContent = t('현재 보는 세션 따라가기');
  select.appendChild(follow);
  // 별칭은 한 맥 안에서 겹치지 않으므로 ID를 덧붙여 구분하지 않는다.
  for (const name of names) {
    const o = document.createElement('option');
    o.value = name; o.textContent = pinLabel(name);
    select.appendChild(o);
  }
  if (pin && !names.includes(pin)) {
    const o = document.createElement('option');
    o.value = pin; o.textContent = t('{name} (없음)', { name: pinLabel(pin) });
    select.appendChild(o);
  }
  select.value = pin;
  if (keep) select.focus();
  // 제목은 비동기로 받는다 — 받은 뒤 바뀐 게 있으면 한 번 더 채운다(받는 동안 목록이 닫혔으면 건너뛴다).
  void refreshPinTitles().then((changed) => { if (changed && select.isConnected) fillPinSelect(select); });
}

/** select 변경을 저장하고 목록 변화를 따라간다. 해제 함수를 돌려준다. */
export function bindPinSelect(select, { onChange } = {}) {
  fillPinSelect(select);
  const change = () => { setPin(select.value); onChange?.(); };
  select.addEventListener('change', change);
  const off = onPinChange(() => { fillPinSelect(select); onChange?.(); });
  return () => { select.removeEventListener('change', change); off(); };
}
