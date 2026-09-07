// S4 — 「앱에 마우스 이벤트 전달」(`mouse.forwardToApp`) 구현.
// off면 앱(vim/tmux/Claude Code)이 마우스 리포팅을 켜도 무시하고 **항상 로컬
// 선택**이 되게 한다 — iTerm2 기본 동작.
//
// 근거는 `S1` spike(50-settings-keymap.md 참고). 실측으로 확인한 xterm 동작:
//
//   1) 앱이 리포팅을 켜면 xterm은 **SelectionService 자체를 disable**한다.
//        areMouseEventsActive ? _selectionService.disable() : enable()
//      → 이벤트 전달만 막으면 "앱에도 안 가고 선택도 안 되는" 상태가 된다.
//   2) 전달 여부는 `shouldForceSelection(e)`가 가른다.
//        isMac ? (e.altKey && options.macOptionClickForcesSelection) : e.shiftKey
//      → macOS에서 shiftKey는 강제 선택이 아니라 **마우스 리포트의 modifier
//        비트**로 인코딩돼 그대로 앱에 전달된다(실측: `\e[<4;6;3M`).
//
// 그래서 세 조각이 모두 필요하다: enable() 되돌리기 + 플랫폼별 강제선택 옵션 +
// 캡처 단계에서 수식키를 붙인 합성 이벤트 재발행.
//
// ⚠ `term._core`는 xterm의 **내부 API**다. 이 파일이 그 접근을 독점하는 이유가
// 그것이다 — xterm이 공개 API를 주거나 내부 구조가 바뀌면 이 파일만 고치면 된다.
// 모든 접근을 try/catch로 감싸, 깨져도 "예전 동작(앱 전달)으로 돌아갈 뿐"이
// 되도록 한다(터미널이 먹통이 되는 것보다 낫다).
import { get as setting, subscribe as onSettings } from '../core/settings.js';
import { allSessions } from '../core/store.js';

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '');

// 세션별 정리 함수 — 설정을 다시 켜면 되돌린다.
const _wired = new WeakMap();

function _selectionService(term) {
  try {
    return term._core && term._core._selectionService;
  } catch (_) {
    return null;
  }
}

// xterm은 마우스 모드가 바뀔 때마다 disable/enable을 다시 호출한다. 그래서
// 한 번 enable()하고 끝낼 수 없고, 모드 전환 시점마다 되돌려야 한다.
function _keepSelectionEnabled(term) {
  const ss = _selectionService(term);
  if (!ss || typeof ss.enable !== 'function') return () => {};
  ss.enable();
  let disposable = null;
  try {
    const cms = term._core.coreMouseService;
    if (cms && typeof cms.onProtocolChange === 'function') {
      disposable = cms.onProtocolChange(() => { try { ss.enable(); } catch (_) {} });
    }
  } catch (_) { /* 내부 API 없음 — 아래 폴백만으로 동작 */ }

  // onProtocolChange가 없거나 바뀌면 폴백: 저빈도 폴링. 마우스 모드는 사용자가
  // 앱을 띄우는 순간에만 바뀌므로 1초면 충분하고, 비용은 불리언 비교 하나다.
  const timer = setInterval(() => {
    try { if (!ss._enabled) ss.enable(); } catch (_) {}
  }, 1000);

  return () => {
    clearInterval(timer);
    try { disposable && disposable.dispose && disposable.dispose(); } catch (_) {}
  };
}

// 원본 mousedown을 멈추고 강제선택 수식키를 붙인 합성 이벤트를 재발행한다.
function _wireForceSelection(term) {
  let screen = null;
  try {
    screen = term._core && term._core.screenElement;
  } catch (_) { /* 무시 */ }
  if (!screen) return () => {};

  const onDown = (e) => {
    if (e.__vtForced) return;   // 우리가 만든 합성 이벤트는 그대로 통과
    e.stopImmediatePropagation();
    e.preventDefault();
    const synth = new MouseEvent('mousedown', {
      bubbles: true, cancelable: true,
      clientX: e.clientX, clientY: e.clientY,
      button: e.button, buttons: e.buttons, detail: e.detail,
      altKey: IS_MAC ? true : e.altKey,
      shiftKey: IS_MAC ? e.shiftKey : true,
      ctrlKey: e.ctrlKey, metaKey: e.metaKey,
    });
    synth.__vtForced = true;
    e.target.dispatchEvent(synth);
  };
  screen.addEventListener('mousedown', onDown, true);
  return () => screen.removeEventListener('mousedown', onDown, true);
}

// 세션 하나에 현재 설정을 반영한다.
export function applyMouseMode(session) {
  if (!session || !session.term) return;
  const forward = setting('mouse.forwardToApp');
  const already = _wired.get(session.term);

  if (forward) {
    if (already) { already(); _wired.delete(session.term); }
    return;
  }
  if (already) return;   // 이미 로컬 선택 모드

  try {
    session.term.options.macOptionClickForcesSelection = true;
  } catch (_) { /* 옵션이 없는 버전 — 아래 두 조각만으로도 비-mac에서는 동작한다 */ }
  const undoEnable = _keepSelectionEnabled(session.term);
  const undoForce = _wireForceSelection(session.term);
  _wired.set(session.term, () => { undoEnable(); undoForce(); });
}

export function applyMouseModeToAll() {
  for (const s of Object.values(allSessions())) applyMouseMode(s);
}

onSettings((changed) => {
  if (!changed || 'mouse.forwardToApp' in changed || Object.keys(changed).length > 3) {
    applyMouseModeToAll();
  }
});
