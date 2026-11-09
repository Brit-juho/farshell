// 리디자인 7단계 — 데스크톱 집중 모드(시안 desktop.html D05 · D05-B · D05-C, spec.html 「집중 모드 규칙」).
//
// 이 파일은 core 쪽의 **작은 스위치**다. 하는 일은 셋뿐이다:
//   1. `<html class="vt-focus">`를 켜고 끈다. 그러면 styles/screens/98-focus.css가 활동 막대 · 사이드바 · 제목 막대 ·
//      상태 막대의 자리(--vt-activity-w · --vt-side-w · --titlebar-h · --statusbar-h)를 0으로 만들어 body padding이
//      **한 번** 바뀐다 — 칸마다 ResizeObserver → refit 게이트 → fit 한 번, WS resize 한 번. 막대 자체는 fixed라
//      흐름에 없고, 숨길 때는 transform·opacity만 움직인다(폭·높이·padding에 transition 없음).
//   2. 숨은 막대에 `inert`를 건다 — 보이지 않는 버튼에 Tab이 닿거나 스크린 리더가 읽지 않게.
//   3. 가장자리 노출 · 개입 알약 · 들어올 때 안내 · 터치용 끝내기 버튼은 지연 청크(shell/Focus.tsx)가 그린다.
//      처음 켤 때 main.js가 넘긴 로더로 한 번만 불러온다(초기 페이로드에 넣지 않는다).
//
// 켜짐 값은 설정 스토어의 `ui.focus.active`(persist:false — 이 탭의 메모리에만, core/settings.js)다. 설정 화면의
// 스위치 · 팔레트 `>` 줄 · 단축키(`focusMode`) · 제목 막대 버튼 · FarShell 메뉴가 모두 이 한 값을 바꾸고, 여기서
// 구독해 화면에 반영한다. compact(<720)에서는 CSS가 아무것도 숨기지 않는다(모바일 집중 모드는 별도 단계).
import { registerAction } from '../core/dom.js';
import { register as registerKey } from '../core/keymap.js';
import { get as setting, set as setSetting, subscribe as onSettings } from '../core/settings.js';
import { getSession, activeSessionId } from '../core/store.js';

export const FOCUS_ACTIVE_KEY = 'ui.focus.active';
export const FOCUS_CLASS = 'vt-focus';
/** 숨는 크롬. 작업대는 지연 마운트라 정적 자리(#vt-workbench-slot, display:contents)에 건다. */
export const FOCUS_CHROME_IDS = ['vt-workbench-slot', 'vt-titlebar', 'vt-hud'];
/** 켜고 끌 때 window에 쏘는 이벤트(detail.on). 지연 청크(제목 막대 버튼 · Focus.tsx)가 듣는다. */
export const FOCUS_EVENT = 'vt:focus-change';

let _loader = null;
let _uiRequested = false;

export function isFocusActive() {
  return document.documentElement.classList.contains(FOCUS_CLASS);
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
  const active = document.activeElement;
  root.classList.toggle(FOCUS_CLASS, on);
  if (!on) root.classList.remove('vt-reveal-left', 'vt-reveal-top', 'vt-reveal-bottom');
  let focusWasInChrome = false;
  for (const id of FOCUS_CHROME_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (on && active && el.contains(active)) focusWasInChrome = true;
    el.inert = on;
    if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert');
  }
  // 숨은 막대 안에 키보드 초점이 남으면 어디에도 보이지 않는 곳을 친다 — 활성 터미널로 옮긴다.
  if (focusWasInChrome) {
    try { active.blur(); } catch (_) { /* 이미 떨어졌으면 그대로 */ }
    try { getSession(activeSessionId())?.term?.focus(); } catch (_) { /* 세션이 없으면 본문에 둔다 */ }
  }
  if (on) _ensureUi();
  window.dispatchEvent(new CustomEvent(FOCUS_EVENT, { detail: { on } }));
}

/** 켜기/끄기. force가 없으면 뒤집는다. 값은 설정 스토어에 쓰고, 화면은 구독(아래)이 바꾼다. */
export function toggleFocus(force) {
  const next = typeof force === 'boolean' ? force : !setting(FOCUS_ACTIVE_KEY);
  setSetting(FOCUS_ACTIVE_KEY, next);
}

/**
 * 지연 UI(shell/Focus.tsx) 로더를 받는다. main.js가 부팅 뒤에 부른다 — 그 전에 이미 켜져 있었다면 바로 불러온다.
 * @param {() => Promise<unknown>} fn
 */
export function setFocusUiLoader(fn) {
  _loader = fn;
  if (isFocusActive()) _ensureUi();
}

onSettings((changed) => {
  if (!changed || typeof changed !== 'object' || !(FOCUS_ACTIVE_KEY in changed)) return;
  _apply(!!setting(FOCUS_ACTIVE_KEY));
});

registerAction('focus.toggle', () => toggleFocus());
registerAction('focus.exit', () => toggleFocus(false));
registerKey('focusMode', () => toggleFocus());

// 다른 모듈이 이 파일보다 먼저 값을 바꿨을 수는 없지만(메모리 값, 기본 꺼짐), 모듈 재평가(테스트)에 대비해 맞춘다.
if (setting(FOCUS_ACTIVE_KEY)) _apply(true);
