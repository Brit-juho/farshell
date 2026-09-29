// 2026-09-18 — 붙여넣기가 두 번 들어가던 회귀의 고정판.
//
// term/selection.js는 wrapper의 **capture** 단계에서 paste를 가로채 N24
// 경로(sendPaste)로 보낸다. 예전엔 preventDefault만 했는데, xterm은 paste
// 리스너를 textarea와 element에 따로 걸어두고 그 핸들러(handlePasteEvent)가
// `defaultPrevented`를 보지 않는다 — 그래서 같은 텍스트가 우리 경로로 한 번,
// xterm의 onData로 또 한 번 PTY에 들어갔다. capture에서 전파를 끊는지가
// 여기서 지키는 불변식이다(preventDefault는 다른 리스너를 못 막는다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const STORE_JS = path.join(__dirname, '../js/core/store.js');
const SELECTION_JS = path.join(__dirname, '../js/term/selection.js');
const KEYMAP_JS = path.join(__dirname, '../js/core/keymap.js');
const CLIPBOARD_JS = path.join(__dirname, '../js/term/clipboard.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// xterm이 실제로 하는 것과 같은 모양의 최소 스텁 — selection.js가 부르는
// 것만 들고 있으면 된다.
function fakeTerm() {
  return {
    getSelection: () => '',
    attachCustomKeyEventHandler(handler) { this.keyHandler = handler; },
    input() {},
    parser: { registerCsiHandler() {}, registerOscHandler() {} },
  };
}

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="w"><div class="xterm"><textarea></textarea></div></div></body></html>');
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = 'http://localhost:7777';
  window._tokenQuery = '';
  window.showToast = () => {};
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  const cache = new Map();
  const S = await importFresh(STORE_JS, env.context, cache);
  const Sel = await importFresh(SELECTION_JS, env.context, cache);
  const K = await importFresh(KEYMAP_JS, env.context, cache);
  const Clip = await importFresh(CLIPBOARD_JS, env.context, cache);
  return { window, S, Sel, K, Clip };
}

// jsdom에는 ClipboardEvent 생성자가 없다 — paste 이벤트에 clipboardData만
// 붙여 흉내낸다(리스너 쪽에서 쓰는 건 items / getData 둘뿐이다).
function pasteEvent(window, text) {
  const e = new window.Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(e, 'clipboardData', {
    value: { items: [], getData: (t) => (t === 'text/plain' ? text : '') },
  });
  return e;
}

class FakeOpenSocket {
  constructor() { this.readyState = 1; this.sent = []; }
  send(data) { this.sent.push(data); }
}

test('붙여넣기는 한 번만 나간다 — xterm의 paste 리스너까지 전파를 끊는다', async () => {
  const { window, S, Sel } = await load();
  const sock = new FakeOpenSocket();
  S.registerSession('a', { ws: sock });

  const wrapper = window.document.getElementById('w');
  Sel.wireClipboard('a', fakeTerm(), wrapper);

  // xterm이 거는 자리 그대로 — textarea(target)와 .xterm(element)에 bubble 리스너.
  let xtermPastes = 0;
  const xtermHandler = () => { xtermPastes += 1; };
  window.document.querySelector('textarea').addEventListener('paste', xtermHandler);
  window.document.querySelector('.xterm').addEventListener('paste', xtermHandler);

  window.document.querySelector('textarea').dispatchEvent(pasteEvent(window, 'echo hi'));

  assert.strictEqual(xtermPastes, 0, 'xterm 쪽 리스너가 돌면 같은 텍스트가 PTY에 두 번 들어간다');
  assert.strictEqual(sock.sent.length, 1);
  assert.strictEqual(JSON.parse(sock.sent[0]).text, 'echo hi');
});

for (const passthrough of [false, true]) {
  test(`Ctrl+Shift+V — 터미널에도 전달 ${passthrough ? '켜짐' : '꺼짐'}에서 네이티브 paste 한 번`, async () => {
    const { window, S, Sel, K, Clip } = await load();
    const sock = new FakeOpenSocket();
    S.registerSession('a', { ws: sock });
    await K.setPassthrough('paste', passthrough);
    let directReads = 0;
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { readText: async () => { directReads += 1; return 'echo hi'; } }, configurable: true,
    });
    K.register('paste', () => Clip.queueNativePasteFallback('a'));
    const wrapper = window.document.getElementById('w');
    const term = fakeTerm();
    Sel.wireClipboard('a', term, wrapper);
    const textarea = wrapper.querySelector('textarea');
    const key = new window.KeyboardEvent('keydown', {
      key: 'V', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true,
    });
    textarea.dispatchEvent(key);
    assert.strictEqual(key.defaultPrevented, false);
    assert.strictEqual(term.keyHandler(key), true);
    assert.strictEqual(directReads, 0, '네이티브 paste 전에 Clipboard API를 읽지 않아야 한다');

    textarea.dispatchEvent(pasteEvent(window, 'echo hi'));
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.strictEqual(directReads, 0, 'paste 이벤트가 왔으면 폴백을 취소해야 한다');
    assert.strictEqual(sock.sent.length, 1);
    assert.strictEqual(JSON.parse(sock.sent[0]).text, 'echo hi');
  });
}

test('Ctrl+Shift+V — paste 이벤트가 없는 브라우저에서는 Clipboard API로 한 번 붙인다', async () => {
  const { window, S, Sel, K, Clip } = await load();
  const sock = new FakeOpenSocket();
  S.registerSession('a', { ws: sock });
  Object.defineProperty(window.navigator, 'clipboard', {
    value: { readText: async () => 'echo hi' }, configurable: true,
  });
  K.register('paste', () => Clip.queueNativePasteFallback('a'));
  const wrapper = window.document.getElementById('w');
  Sel.wireClipboard('a', fakeTerm(), wrapper);
  wrapper.querySelector('textarea').dispatchEvent(new window.KeyboardEvent('keydown', {
    key: 'V', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true,
  }));
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.strictEqual(sock.sent.length, 1);
  assert.strictEqual(JSON.parse(sock.sent[0]).text, 'echo hi');
});

test('터미널에도 전달 — 코드 뷰어와 재지정한 붙여넣기 액션은 각각 한 번만 실행한다', async () => {
  const { window, Sel, K } = await load();
  const wrapper = window.document.getElementById('w');
  const term = fakeTerm();
  Sel.wireClipboard('a', term, wrapper);
  const textarea = wrapper.querySelector('textarea');
  for (const { id, key, ctrlKey, altKey } of [
    { id: 'viewer', key: 'E', ctrlKey: true },
    { id: 'paste', key: 'V', altKey: true },
  ]) {
    if (id === 'paste') await K.setBinding('paste', 'Alt+Shift+V');
    await K.setPassthrough(id, true);
    let runs = 0;
    K.register(id, () => { runs += 1; });
    const event = new window.KeyboardEvent('keydown', {
      key, ctrlKey: !!ctrlKey, altKey: !!altKey, shiftKey: true,
      bubbles: true, cancelable: true,
    });
    textarea.dispatchEvent(event);
    assert.strictEqual(term.keyHandler(event), true);
    assert.strictEqual(runs, 1, `${id} 액션이 문서와 xterm에서 중복 실행되면 안 된다`);
  }
});

test('붙일 것이 없으면 가로채지 않는다 — xterm 기본 동작을 막지 않는다', async () => {
  const { window, S, Sel } = await load();
  const sock = new FakeOpenSocket();
  S.registerSession('a', { ws: sock });

  const wrapper = window.document.getElementById('w');
  Sel.wireClipboard('a', fakeTerm(), wrapper);

  let xtermPastes = 0;
  window.document.querySelector('textarea').addEventListener('paste', () => { xtermPastes += 1; });
  window.document.querySelector('textarea').dispatchEvent(pasteEvent(window, ''));

  assert.strictEqual(sock.sent.length, 0);
  assert.strictEqual(xtermPastes, 1);
});

// 2026-09-23 — 예전엔 "선택 모드일 때만" contextmenu(Android 롱프레스)를
// 브라우저 네이티브 선택에 양보했다. 이제 롱프레스는 전부 term/touch-select.js가
// xterm 자신의 select() 모델로 처리하므로(그 파일 머리말 — 접근성 레이어
// 재렌더가 네이티브 Range를 끊는다) 네이티브 선택에 양보할 이유 자체가
// 없어졌다 — 터치 기기에서는 선택 모드 여부와 무관하게 이 이벤트를 항상
// 막아 OS의 콜아웃/컨텍스트 메뉴가 우리 핸들 위에 겹쳐 뜨는 것만 막는다
// (PuTTY 스타일 복사/붙여넣기도 같이 죽는다 — 그건 이제 keybar 버튼이 한다).
test('터치 기기 — contextmenu는 PuTTY 복사/붙여넣기로 안 새고 그냥 막힌다', async () => {
  const { window, Sel } = await load();
  window.matchMedia = (q) => ({ matches: q.includes('coarse') });
  const wrapper = window.document.getElementById('w');
  Sel.wireClipboard('a', fakeTerm(), wrapper);

  const event = new window.Event('contextmenu', { bubbles: true, cancelable: true });
  const allowed = wrapper.dispatchEvent(event);

  assert.strictEqual(allowed, false, '터치의 네이티브 콜아웃/컨텍스트 메뉴는 항상 막아야 자체 핸들과 안 겹친다');
  assert.strictEqual(event.defaultPrevented, true);
});

test('데스크톱(포인터 정밀) — contextmenu는 그대로 PuTTY 스타일을 유지한다(막되, 붙여넣기 시도)', async () => {
  const { window, Sel } = await load();
  window.matchMedia = (q) => ({ matches: false }); // 정밀 포인터
  window.navigator.clipboard = undefined; // jsdom 기본값과 동일 — 읽기는 실패하지만 시도는 한다
  const wrapper = window.document.getElementById('w');
  Sel.wireClipboard('a', fakeTerm(), wrapper); // fakeTerm().getSelection()은 항상 ''

  const event = new window.Event('contextmenu', { bubbles: true, cancelable: true });
  const allowed = wrapper.dispatchEvent(event);

  assert.strictEqual(allowed, false, '데스크톱은 이전과 같이 항상 막고 복사 또는 붙여넣기를 시도한다');
  assert.strictEqual(event.defaultPrevented, true);
});
