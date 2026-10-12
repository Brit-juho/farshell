import { snapshotKey, isComposingKey } from './keyboard-event.js';
import { routeScope } from './keyboard-scopes.js';
const decisions = new WeakMap();
const terminalTargets = new WeakMap();
export function registerTerminalTarget(wrapper, capture) { terminalTargets.set(wrapper, capture); }
function captureTarget(target) {
  for (let node = target; node; node = node.parentElement) {
    const capture = terminalTargets.get(node);
    if (capture) return capture();
  }
  return null;
}
let handler = null, wired = false;
export function getKeyDecision(e) { return decisions.get(e); }
export function decideKey(e) {
  if (decisions.has(e)) return decisions.get(e);
  const stroke = snapshotKey(e);
  const d = { stroke, targetContext: captureTarget(e.target), owner: 'terminal', terminal: 'default', consumed: false };
  decisions.set(e, d); // 재진입해도 액션을 다시 실행하지 않는다.
  if (isComposingKey(e)) { d.owner = 'native'; return d; }
  const scope = routeScope(e);
  if (scope) {
    Object.assign(d, scope);
    if (e.target?.closest?.('.xterm')) d.consumed = true;
  }
  if (!scope || (!scope.consumed && scope.allowActions.length)) handler?.(e, d, scope?.allowActions);
  if (d.consumed) { e.preventDefault(); e.stopImmediatePropagation(); }
  return d;
}
export function wireKeyboardRouter(fn) {
  if (fn) handler = fn;
  if (wired || typeof document === 'undefined') return;
  wired = true;
  document.addEventListener('keydown', decideKey, true);
}
// 앱+터미널: 원래 타깃에만 명시적으로 전달. 바뀐 활성 세션을 재조회하지 않는다.
export function forwardTerminalKey(target, original, stroke, context) {
  if (!target?.isConnected || !target.closest?.('.xterm') || (context && !context.canSend())) return;
  const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true,
    key: stroke.key, code: stroke.code, location: stroke.location, ctrlKey: stroke.ctrlKey,
    metaKey: stroke.metaKey, altKey: stroke.altKey, shiftKey: stroke.shiftKey, repeat: stroke.repeat });
  decisions.set(e, { stroke, owner: 'terminal', terminal: 'encode', consumed: false, forwarded: true });
  target.dispatchEvent(e);
}
