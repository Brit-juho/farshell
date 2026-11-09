// 집중 모드의 **하나뿐인 스위치**(리디자인 7단계 데스크톱 + 8단계 폰). 시안: desktop.html D05 · D05-B · D05-C,
// mobile.html M14, spec.html 「집중 모드 규칙」.
//
// 켜짐 값은 설정 스토어의 `ui.focus.active` 하나다(persist:false — 이 탭의 메모리에만, core/settings.js). 단축키
// (`focusMode`) · 제목 막대 버튼 · FarShell 메뉴 · 팔레트(: 명령 · > 설정) · 설정 화면 · 폰 상단바 버튼이 모두 이 값을
// 바꾸고(`focus.toggle` 액션은 여기서 **한 번만** 등록한다), 여기서 구독해 `<html class="vt-focus">`를 켜고 끈 뒤
// `vt:focus-change`(document에서, 위로 전파 — window에서도 들린다)를 한 번 쏜다. 폭에 따라 맡는 쪽이 갈린다:
//
//   - **넓은 화면(≥720, 데스크톱)** — styles/screens/98-focus.css가 활동 막대 · 사이드바 · 제목 막대 · 상태 막대의
//     자리(--vt-activity-w · --vt-side-w · --titlebar-h · --statusbar-h)를 0으로 만들어 body padding이 **한 번**
//     바뀐다 — 칸마다 ResizeObserver → refit 게이트 → fit 한 번, WS resize 한 번. 막대는 fixed라 흐름에 없고 숨길 때는
//     transform·opacity만 움직인다. 숨은 막대에는 `inert`를 건다(보이지 않는 버튼에 Tab이 닿지 않게). 가장자리 노출 ·
//     들어올 때 안내 · 터치용 끝내기 버튼은 지연 청크(shell/Focus.tsx) — 처음 켤 때 main.js가 넘긴 로더로 부른다.
//   - **폰(<720)** — layout/mobile-focus.js: 상단바 · 아래 탭을 숨기고(98-mobile.css) 위 가장자리 끌어내리기로 나간다.
//   - **개입 알약**(#vt-focus-pill)은 두 폭이 함께 쓰는 한 요소다(mobile-focus.js가 그린다, 모양은 폭별 CSS).
//
// 켠 채 창 폭이 720을 넘나들면 집중 모드를 끝낸다 — 숨길 막대의 종류가 바뀌므로(데스크톱 막대 넷 ↔ 폰 상단바 · 아래 탭)
// 다른 쪽 화면을 막대 없이 시작하지 않게 한다.
import { registerAction } from '../core/dom.js';
import { register as registerKey } from '../core/keymap.js';
import { get as setting, set as setSetting, subscribe as onSettings } from '../core/settings.js';
import { getSession, activeSessionId, allSessions, sessionDisplayName } from '../core/store.js';
import { allStatuses, agentDetails } from '../agent/state.js';

export const FOCUS_ACTIVE_KEY = 'ui.focus.active';
export const FOCUS_CLASS = 'vt-focus';
/** 데스크톱에서 숨는 크롬. 작업대는 지연 마운트라 정적 자리(#vt-workbench-slot, display:contents)에 건다. */
export const FOCUS_CHROME_IDS = ['vt-workbench-slot', 'vt-titlebar', 'vt-hud'];
/** 켜고 끌 때 document에서 쏘는 이벤트(detail.on · detail.source 'desktop'|'mobile'). bubbles라 window에서도 들린다. */
export const FOCUS_EVENT = 'vt:focus-change';
export const COMPACT_QUERY = '(max-width:719px)';

let _loader = null;
let _uiRequested = false;

function _mq() {
  try { return window.matchMedia?.(COMPACT_QUERY) || null; } catch (_) { return null; }
}
export function isCompactWidth() { return Boolean(_mq()?.matches); }

export function isFocusActive() {
  return document.documentElement.classList.contains(FOCUS_CLASS);
}

function _setInert(on) {
  for (const id of FOCUS_CHROME_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.inert = on;
    if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert');
  }
}

function _ensureUi() {
  if (_uiRequested || typeof _loader !== 'function') return;
  _uiRequested = true;
  Promise.resolve()
    .then(() => _loader())
    .catch((e) => { _uiRequested = false; console.error('[FarShell focus]', e); });
}

function _apply(on) {
  const root = document.documentElement;
  if (root.classList.contains(FOCUS_CLASS) === on) return;
  const compact = isCompactWidth();
  const active = document.activeElement;
  root.classList.toggle(FOCUS_CLASS, on);
  if (!on) root.classList.remove('vt-reveal-left', 'vt-reveal-top', 'vt-reveal-bottom');
  // 막대 넷은 데스크톱에만 있다(폰에서는 이미 display:none). 끌 때는 폭과 상관없이 늘 푼다.
  const hideChrome = on && !compact;
  const focusWasInChrome = hideChrome && !!active
    && FOCUS_CHROME_IDS.some((id) => document.getElementById(id)?.contains(active));
  _setInert(hideChrome);
  // 숨은 막대 안에 키보드 초점이 남으면 보이지 않는 곳을 친다 — 활성 터미널로 옮긴다.
  if (focusWasInChrome) {
    try { active.blur(); } catch (_) { /* 이미 떨어졌으면 그대로 */ }
    try { getSession(activeSessionId())?.term?.focus(); } catch (_) { /* 세션이 없으면 본문에 둔다 */ }
  }
  if (hideChrome) _ensureUi();
  document.dispatchEvent(new CustomEvent(FOCUS_EVENT, { bubbles: true, detail: { on, source: compact ? 'mobile' : 'desktop' } }));
}

/** 켜기/끄기. 값은 설정 스토어에 쓰고, 화면은 구독(아래)이 바꾼다. */
export function setFocus(on) {
  setSetting(FOCUS_ACTIVE_KEY, Boolean(on));
}
/** 뒤집기 — force가 있으면 그 값. */
export function toggleFocus(force) {
  setFocus(typeof force === 'boolean' ? force : !setting(FOCUS_ACTIVE_KEY));
}

/**
 * 지연 UI(shell/Focus.tsx) 로더를 받는다. main.js가 부팅 뒤에 부른다 — 그 전에 이미 켜져 있었다면 바로 불러온다.
 * @param {() => Promise<unknown>} fn
 */
export function setFocusUiLoader(fn) {
  _loader = fn;
  if (isFocusActive() && !isCompactWidth()) _ensureUi();
}

// ── 개입 — 가장 오래 기다린 세션 ─────────────────────────────────────────────
// 상태 막대 칩(agents.jump)과 개입 알약(이름 · 종류를 보이고 누르면 같은 곳으로 간다)이 같은 세션을 가리키도록
// 한 곳에서 고른다. since를 모르는 항목은 맨 뒤로 보낸다.
export function oldestWaiting() {
  const details = agentDetails();
  const sinceOf = (name) => {
    const v = details[name] && details[name].since;
    const n = typeof v === 'number' ? v : Date.parse(v);
    return Number.isFinite(n) ? n : Infinity;
  };
  const waiting = [...allStatuses()].filter(([, st]) => st === 'waiting').map(([name]) => name);
  if (!waiting.length) return null;
  waiting.sort((a, b) => sinceOf(a) - sinceOf(b));
  const tmuxName = waiting[0];
  const sessions = allSessions();
  const sessionId = Object.keys(sessions).find((id) => {
    const s = sessions[id];
    return s && (s.tmuxName || s.tmux_name) === tmuxName && !s.remote;
  }) || null;
  return {
    tmuxName, sessionId, count: waiting.length,
    name: (sessionId && sessionDisplayName(sessionId)) || tmuxName,
    kind: (details[tmuxName] && details[tmuxName].kind) || null,
  };
}

/** 가장 오래 기다린 세션으로 간다(레일 · 세션 탭의 행 열기와 같은 window 브리지). */
export function jumpToWaiting() {
  const w = oldestWaiting();
  if (!w) return;
  const open = window.vtOpenSessionFromRail;
  if (typeof open === 'function') void open(w.sessionId ? { sessionId: w.sessionId, tmuxName: w.tmuxName } : { tmuxName: w.tmuxName });
}

onSettings((changed) => {
  if (!changed || typeof changed !== 'object' || !(FOCUS_ACTIVE_KEY in changed)) return;
  _apply(!!setting(FOCUS_ACTIVE_KEY));
});

// 폭이 720을 넘나들면 끝낸다(파일 머리말).
try {
  _mq()?.addEventListener?.('change', () => { if (isFocusActive() || setting(FOCUS_ACTIVE_KEY)) setFocus(false); });
} catch (_) { /* matchMedia 없는 환경 */ }

registerAction('focus.toggle', () => toggleFocus());
registerAction('focus.exit', () => setFocus(false));
registerKey('focusMode', () => toggleFocus());

// 모듈 재평가(테스트)에 대비해 맞춘다 — 메모리 값이라 보통은 꺼진 채 시작한다.
if (setting(FOCUS_ACTIVE_KEY)) _apply(true);
