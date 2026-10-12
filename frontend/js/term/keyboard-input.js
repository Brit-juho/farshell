import { queueNativePasteFallback } from './clipboard.js';
// 하나의 route를 사용한다. xterm은 레거시/IME, custom은 협상된 확장 키만 담당.
import { getSession } from '../core/store.js';
import { decideKey, registerTerminalTarget } from '../core/keyboard-router.js';
import { isComposingKey } from '../core/keyboard-event.js';
import { get as setting } from '../core/settings.js';
import { isMacKeyboard } from '../core/platform.js';
import { wireKittyKeyboard, encodeKey } from './kitty-keys.js';
import { applyStickyMod } from './keybar.js';
const terminals = new Map();
export function disposeKeyboardInput(id) { terminals.delete(id); }
export function sendVirtualKey(id, opts) {
  const entry = terminals.get(id);
  if (!entry) return false;
  const keyNames = { esc: 'Escape', tab: 'Tab', up: 'ArrowUp', down: 'ArrowDown', left: 'ArrowLeft', right: 'ArrowRight' };
  const fixed = { ctrlc: '\x03', prefix: '\x02', backtab: '\x1b[Z' };
  const mods = opts.mods || {};
  const key = keyNames[opts.key] || opts.seq || '';
  const e = { type: 'keydown', key, ctrlKey: !!mods.ctrl, altKey: !!mods.alt,
    shiftKey: !!mods.shift, metaKey: false, isComposing: false };
  let bytes = fixed[opts.key] || encodeKey(e, entry.kitty.flags);
  if (!bytes) {
    bytes = globalThis.VTKeySeq.keySeq(opts);
    if (!mods.ctrl && !mods.alt && !mods.shift && entry.term.modes?.applicationCursorKeysMode && /^Arrow/.test(key)) {
      bytes = bytes.replace('\x1b[', '\x1bO');
    }
  }
  if (!bytes) return false;
  entry.term.input(bytes, true);
  return true;
}
export function wireKeyboardInput(id, term, wrapper) {
  const kitty = wireKittyKeyboard(term);
  const entry = { term, kitty };
  terminals.set(id, entry);
  registerTerminalTarget(wrapper, () => {
    const session = getSession(id), handle = session?.wsHandle;
    return { sessionId: id, canSend: () => terminals.get(id) === entry && getSession(id) === session && getSession(id)?.wsHandle === handle };
  });
  term.onDispose?.(() => { if (terminals.get(id) === entry) terminals.delete(id); });
  let composing = false, physicalPending = false, compositionCommit = false;
  let beforeInputTarget = null, beforeInputValue = null;
  wrapper.addEventListener('beforeinput', (e) => {
    beforeInputTarget = e.target;
    beforeInputValue = typeof e.target.value === 'string' ? e.target.value : null;
  }, true);
  wrapper.addEventListener('compositionstart', () => { composing = true; }, true);
  wrapper.addEventListener('compositionend', () => { composing = false; compositionCommit = true; }, true);
  wrapper.addEventListener('keyup', () => { physicalPending = false; }, true);
  // onData에는 텍스트/키/응답이 섞인다. modifier는 인코딩 전 soft input에만 적용.
  wrapper.addEventListener('input', (e) => {
    const nativeTarget = beforeInputTarget, nativeValue = beforeInputValue;
    beforeInputTarget = null; beforeInputValue = null;
    const physical = physicalPending;
    physicalPending = false;
    const commit = compositionCommit; compositionCommit = false;
    if (physical || composing || commit || e.isComposing || e.inputType !== 'insertText') return;
    if (!e.data || e.data.length !== 1 || e.data.charCodeAt(0) >= 128) return;
    // Android keyCode=229에서 xterm은 textarea 차이를 타이머로 다시 읽는다.
    // 처리한 입력의 native 변경을 원복해야 input 전파 차단만으로 남는 원문을 막는다.
    if (nativeTarget !== e.target || nativeValue == null) return;
    const data = applyStickyMod(e.data);
    if (data === e.data) return;
    e.target.value = nativeValue;
    e.preventDefault(); e.stopImmediatePropagation();
    term.input(data, true);
  }, true);
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== 'keydown') return true;
    physicalPending = !['Unidentified', 'Process'].includes(e.key) && e.keyCode !== 229;
    const d = decideKey(e);
    if (d.terminal === 'block') return false;
    const nativePaste = isMacKeyboard
      ? ((e.metaKey && !e.ctrlKey && !e.altKey) || (e.ctrlKey && e.shiftKey && !e.metaKey && !e.altKey))
      : e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey;
    if (nativePaste && e.key?.toLowerCase() === 'v') { queueNativePasteFallback(id, e); return true; }
    if (composing || isComposingKey(e) || d.stroke.altGraph) return true;
    // native Cmd+C/V는 브라우저 몫. kitty가 이 키를 재인코딩하지 않는다.
    if (isMacKeyboard && e.metaKey && ['c', 'v'].includes(e.key?.toLowerCase())) return true;
    // unicode Option 입력은 xterm의 조합/문자 경로를 유지한다.
    if (isMacKeyboard && e.altKey && setting('terminal.optionMode') !== 'meta') return true;
    const seq = encodeKey(e, kitty.flags);
    if (!seq) return true;
    e.preventDefault(); term.input(seq, true); return false;
  });
  return kitty;
}
