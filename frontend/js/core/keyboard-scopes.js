// 리스너 등록 순서 대신 스택으로 키 소유자를 결정한다. 모달/녹화 공용.
import { wireKeyboardRouter } from './keyboard-router.js';
const scopes = [];
export function pushScope(spec) {
  wireKeyboardRouter();
  const previous = typeof document === 'undefined' ? null : document.activeElement;
  const entry = { ...spec, previous };
  scopes.push(entry);
  if (spec.modal && spec.element) queueMicrotask(() => {
    const el = elementOf(entry);
    if (scopes.at(-1) !== entry || !el?.isConnected || el.contains(document.activeElement)) return;
    el.querySelector('input:not([disabled]), button:not([disabled]), [tabindex="0"]')?.focus();
  });
  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    const idx = scopes.indexOf(entry);
    const wasTop = idx === scopes.length - 1;
    if (idx >= 0) scopes.splice(idx, 1);
    if (wasTop && spec.restoreFocus !== false && previous?.isConnected) previous.focus?.();
  };
}
function elementOf(s) { return typeof s.element === 'function' ? s.element() : s.element; }
export function routeScope(e) {
  // 녹화 버튼이 재렌더/폐쇄로 사라진 경우 자동 해제한다.
  for (let i = scopes.length - 1; i >= 0; i--) {
    const s = scopes[i], el = elementOf(s);
    if (s.element && !el?.isConnected) { scopes.splice(i, 1); s.onDispose?.(); }
  }
  const recorder = [...scopes].reverse().find((s) => s.kind === 'recorder');
  const scope = recorder || [...scopes].reverse().find((s) => !s.enabled || s.enabled());
  if (!scope) return null;
  const el = elementOf(scope);
  const handled = scope.onKey?.(e) === true || e.defaultPrevented;
  if (!handled && scope.modal && e.key === 'Tab' && el) {
    const items = [...el.querySelectorAll('button, input, textarea, select, a[href], [tabindex]')]
      .filter((n) => !n.disabled && n.tabIndex >= 0 && !n.hidden && !n.closest('[hidden]'));
    const first = items[0], last = items[items.length - 1];
    if (!first) e.preventDefault();
    else if (!el.contains(document.activeElement) || (!e.shiftKey && document.activeElement === last)) {
      e.preventDefault(); first.focus();
    } else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  }
  return { owner: recorder ? 'recorder' : 'scope', terminal: 'block',
    consumed: handled || e.defaultPrevented || !!recorder, allowActions: scope.allowActions || [] };
}

export function hasBlockingScope() { return scopes.some((s) => (s.modal || s.kind === 'recorder') && (!s.enabled || s.enabled())); }
