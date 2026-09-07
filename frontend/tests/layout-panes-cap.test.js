// L8 — pane 상한에 걸린 분할 버튼이 실제로 disabled가 되고 이유가 붙는지.
// 지금까지는 canSplit()이 클릭을 조용히 무시하기만 해서 "눌렀는데 아무 일도
// 안 일어난다"로 보였다(L8 착수 전 확인 사항).
//
// 하네스는 pane-picker.test.js와 같다 — layout/panes.js는 term/session.js·
// agent/preview.js(UMD 전역 VTAnsiLex를 최상단에서 읽는다)까지 끌고 오므로
// 진짜 index.html 마크업 + 최소 vendor 스텁이 필요하다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const KEYSEQ_JS = path.join(__dirname, '../js/lib/keyseq.js');
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');
const ANSILEX_JS = path.join(__dirname, '../js/lib/ansilex.js');
const SESSION_JS = path.join(__dirname, '../js/term/session.js');
const PANES_JS = path.join(__dirname, '../js/layout/panes.js');
const STORE_JS = path.join(__dirname, '../js/layout/store.js');

class FakeTerminal {
  constructor(opts) { this.options = opts; this.cols = 80; this.rows = 24; }
  loadAddon() {} open(el) { this.element = el; } focus() {} reset() {} write() {}
  onData() {} getSelection() { return ''; } attachCustomKeyEventHandler() {} dispose() {}
}
class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = 0; }
  addEventListener() {} send() {} close() { this.readyState = 3; }
}
FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSING = 2; FakeWebSocket.CLOSED = 3;

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function buildWindow(innerWidth) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  Object.defineProperty(window, 'innerWidth', { value: innerWidth, configurable: true });
  // compact 렌더 모드로 새지 않게 포인터는 fine으로 고정 — 이 테스트가 보려는
  // 건 폭 구간별 상한이지 compact 렌더가 아니다.
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  await importFresh(TOAST_JS, env.context, cache);
  await importFresh(ANSILEX_JS, env.context, cache);
  const sessionNs = await importFresh(SESSION_JS, env.context, cache);
  await importFresh(PANES_JS, env.context, cache);
  const storeNs = await importFresh(STORE_JS, env.context, cache);
  return { window, ...sessionNs, store: storeNs };
}

function splitButtons(document) {
  return Array.from(document.querySelectorAll('.vt-pane-split-row, .vt-pane-split-col'));
}

test('상한 미만이면 분할 버튼이 활성이고 title은 그대로', async () => {
  const { window, addSession, store } = await buildWindow(1280); // wide 상한 6
  addSession('a');
  store.splitActivePane('row'); // leaf 2개 — 여유 있음

  const btns = splitButtons(window.document);
  assert.ok(btns.length >= 2);
  assert.ok(btns.every((b) => b.disabled === false), '전부 활성이어야 한다');
  assert.strictEqual(window.document.querySelector('.vt-pane-split-row').title, '오른쪽 분할');
});

test('상한에 도달하면 모든 분할 버튼이 disabled + 이유가 title/aria-label에 붙는다', async () => {
  const { window, addSession, store } = await buildWindow(390); // compact 폭 상한 2
  addSession('a');
  store.splitActivePane('row'); // leaf 2개 = 상한 도달

  const btns = splitButtons(window.document);
  assert.ok(btns.length >= 2);
  assert.ok(btns.every((b) => b.disabled === true), '전부 비활성이어야 한다');
  assert.match(btns[0].title, /최대 2개/);
  assert.match(btns[0].getAttribute('aria-label'), /분할 한도/);
});

test('닫아서 상한 아래로 내려오면 다시 활성으로 돌아온다', async () => {
  const { window, addSession, store } = await buildWindow(390);
  addSession('a');
  const second = store.splitActivePane('row');
  assert.ok(splitButtons(window.document).every((b) => b.disabled));

  store.closePane(second);
  assert.ok(splitButtons(window.document).every((b) => b.disabled === false), '상한이 풀리면 되살아나야 한다');
});
