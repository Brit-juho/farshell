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

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// xterm이 실제로 하는 것과 같은 모양의 최소 스텁 — selection.js가 부르는
// 것만 들고 있으면 된다.
function fakeTerm() {
  return {
    getSelection: () => '',
    attachCustomKeyEventHandler() {},
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
  return { window, S, Sel };
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
