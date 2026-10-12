const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
async function load() {
  const env = createDomEnv('<div id="term-a"><div class="xterm"><textarea></textarea></div></div><input id="editor">');
  doms.push(env.dom);
  const w = env.window;
  Object.defineProperty(w.navigator, 'platform', { value: 'MacIntel' });
  w.matchMedia = () => ({ matches: false });
  w.fetch = async () => ({ ok: true, json: async () => ({}) });
  w.showToast = () => {};
  const cache = new Map();
  const get = (p) => importFresh(path.join(__dirname, '../js', p), env.context, cache);
  return { w, get, K: await get('core/keymap.js'), scopes: await get('core/keyboard-scopes.js'),
    settings: await get('core/settings.js'), input: await get('term/keyboard-input.js') };
}
function key(w, target, opts) {
  const e = new w.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...opts });
  target.dispatchEvent(e); return e;
}
test('Option 생성문자와 한글 배열에서도 code 기반 앱 액션이 한 번만 실행된다', async () => {
  const { w, K } = await load(); let calls = 0;
  K.register('paneEven', () => calls++);
  const t = w.document.querySelector('textarea');
  const e = key(w, t, { key: '∂', code: 'KeyD', metaKey: true, altKey: true });
  K.handleKeydown(e);
  assert.equal(calls, 1); assert.equal(e.defaultPrevented, true);
  key(w, t, { key: 'ㅇ', code: 'KeyD', metaKey: true, altKey: true });
  assert.equal(calls, 2);
});
test('IME·AltGraph·repeat는 분할을 실행하지 않는다. 편집창도 보존한다', async () => {
  const { w, K } = await load(); let calls = 0;
  K.register('splitRight', () => calls++);
  const t = w.document.querySelector('textarea');
  key(w, t, { key: 'd', code: 'KeyD', metaKey: true, isComposing: true });
  key(w, t, { key: 'd', code: 'KeyD', metaKey: true, repeat: true });
  key(w, w.document.querySelector('input'), { key: 'd', code: 'KeyD', metaKey: true });
  assert.equal(K.match({ key: 'd', code: 'KeyD', metaKey: true, altGraph: true }), null);
  assert.equal(calls, 0);
});
test('최상위 모달만 Esc를 처리하고 녹화는 기존 shortcut보다 우선한다', async () => {
  const { w, K, scopes } = await load(); const calls = [];
  K.register('splitRight', () => calls.push('split'));
  const a = scopes.pushScope({ id: 'parent', modal: true, onKey: () => { calls.push('parent'); return true; } });
  const b = scopes.pushScope({ id: 'child', modal: true, onKey: () => { calls.push('child'); return true; } });
  key(w, w.document.body, { key: 'Escape' }); assert.deepEqual(calls, ['child']);
  const c = scopes.pushScope({ id: 'record', kind: 'recorder', onKey: () => { calls.push('record'); return true; } });
  key(w, w.document.body, { key: 'd', code: 'KeyD', metaKey: true });
  assert.deepEqual(calls, ['child', 'record']); c(); b(); a();
});
test('앱+터미널 전달은 확장 인코딩을 거쳐 한 번만 보내며 terminal-only는 앱을 실행하지 않는다', async () => {
  const { w, K, input } = await load(); let calls = 0; const bytes = [];
  K.register('splitRight', () => calls++);
  const wrapper = w.document.querySelector('#term-a'), t = wrapper.querySelector('textarea');
  const term = { input: (s) => bytes.push(s), attachCustomKeyEventHandler(fn) { this.fn = fn; } };
  const kitty = input.wireKeyboardInput('a', term, wrapper); kitty.push(1);
  t.addEventListener('keydown', (e) => term.fn(e));
  await K.setDelivery('splitRight', 'app-and-terminal');
  key(w, t, { key: 'd', code: 'KeyD', metaKey: true });
  assert.equal(calls, 1); assert.deepEqual(bytes, ['\x1b[100;9u']);
  await K.setDelivery('splitRight', 'terminal');
  key(w, t, { key: 'd', code: 'KeyD', metaKey: true });
  assert.equal(calls, 1); assert.equal(bytes.length, 2);
});
test('V2 기기 설정은 global을 변경하지 않고 reset 후 옛 override를 되살리지 않는다', async () => {
  const { K, settings } = await load();
  await settings.set('keymap.overrides', JSON.stringify({ splitRight: { binding: 'Mod+G' } }));
  assert.equal(K.getBinding('splitRight').combo, 'Mod+G');
  await K.setBinding('splitRight', 'Mod+J');
  assert.equal(settings.scopeOf('keymap.bindingsV2'), 'device');
  assert.match(settings.get('keymap.overrides'), /Mod\+G/);
  await K.reset('splitRight'); assert.equal(K.getBinding('splitRight').combo, 'Mod+D');
});
test('Alt/Ctrl 기본 문자·프로토콜 응답에는 sticky 키를 재적용하지 않는다', async () => {
  const { w, input } = await load(); const bytes = [];
  const term = { input: (s) => bytes.push(s), attachCustomKeyEventHandler(fn) { this.fn = fn; } };
  input.wireKeyboardInput('a', term, w.document.querySelector('#term-a'));
  input.sendVirtualKey('a', { key: 'ctrlc', mods: { alt: true } });
  assert.deepEqual(bytes, ['\x03']);
  input.disposeKeyboardInput('a'); assert.equal(input.sendVirtualKey('a', { key: 'ctrlc' }), false);
});

test('Android keyCode=229 지연 textarea 검사에서도 sticky 입력은 원문을 추가 전송하지 않는다', async () => {
  for (const [modifier, expected] of [['ctrl', '\x03'], ['alt', '\x1bc'], ['shift', 'C']]) {
    const { w, input, get } = await load(); const bytes = [];
    const keybar = await get('term/keybar.js');
    const wrapper = w.document.querySelector('#term-a'), textarea = wrapper.querySelector('textarea');
    const term = { input: (s) => bytes.push(s), attachCustomKeyEventHandler(fn) { this.fn = fn; } };
    input.wireKeyboardInput('a', term, wrapper);
    keybar._mods[modifier].latch = 'once';
    textarea.value = 'previous';
    // vendor xterm의 Android fallback은 keydown 당시 값과 지연된 값을 비교한다.
    textarea.addEventListener('keydown', (e) => {
      term.fn(e);
      const old = textarea.value;
      w.setTimeout(() => { if (textarea.value !== old) bytes.push(textarea.value.replace(old, '')); }, 0);
    });
    key(w, textarea, { key: 'Unidentified', keyCode: 229 });
    textarea.dispatchEvent(new w.InputEvent('beforeinput', { data: 'c', inputType: 'insertText', bubbles: true }));
    textarea.value += 'c';
    textarea.dispatchEvent(new w.InputEvent('input', { data: 'c', inputType: 'insertText', bubbles: true }));
    await new Promise((r) => w.setTimeout(r, 10));
    assert.deepEqual(bytes, [expected], `${modifier}: 변환된 입력만 한 번 전달`);
    assert.equal(textarea.value, 'previous');
    assert.equal(keybar._mods[modifier].latch, 'off');
  }
});
