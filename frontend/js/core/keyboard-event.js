// 앱 단축키의 물리 위치와 실제 입력 문자는 절대로 합치지 않는다.
export function snapshotKey(e) {
  return Object.freeze({ key: e.key || '', code: e.code || '', location: e.location || 0,
    ctrlKey: !!e.ctrlKey, metaKey: !!e.metaKey, altKey: !!e.altKey, shiftKey: !!e.shiftKey,
    altGraph: !!e.getModifierState?.('AltGraph'), isComposing: !!e.isComposing || e.keyCode === 229,
    repeat: !!e.repeat, type: e.type, source: 'physical' });
}
export function physicalKey(e) {
  if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(e.code)) return e.code.slice(5);
  if (e.code === 'Space') return 'Space';
  // 기호는 배열의 논리 문자를 쓴다. JIS/US를 같은 물리 위치에 고정하지 않는다.
  return e.key === ' ' ? 'Space' : e.key?.length === 1 ? e.key.toLowerCase() : e.key;
}
export function isComposingKey(e) {
  return e.isComposing || e.keyCode === 229 || ['Dead', 'Unidentified', 'Process', 'AltGraph'].includes(e.key);
}
