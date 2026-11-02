// 뷰 탭 우클릭 메뉴(layout/tabbar.js) — 이름 변경 · 뷰 닫기 둘뿐, 묶음 해제는 일부러 없다.
// 진짜 index.html + 실제 모듈 그래프. 닫기는 탭 ×와 같은 closeView라 그 결과(뷰가 사라짐)까지 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

class FakeTerminal {
  constructor(opts) { this.options = opts; this.cols = 80; this.rows = 24; }
  loadAddon() {} open(el) { this.element = el; } focus() {} reset() {} write() {}
  onData() {} getSelection() { return ''; } attachCustomKeyEventHandler() {} dispose() {}
}
class FakeWebSocket { constructor(url) { this.url = url; this.readyState = 0; } addEventListener() {} send() {} close() {} }
const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = () => new Promise((r) => setTimeout(r, 30));

async function setup() {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.innerWidth = 1280;
  window.WebSocket = FakeWebSocket;
  window.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };   // tabbar.js 가 넘침 마스크용으로 쓴다
  window.Element.prototype.scrollIntoView = function () {};   // jsdom 에는 없다(활성 탭을 보이는 자리로 끌어올 때 쓴다)
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  window.fetch = () => ok([]);
  const cache = new Map();
  await importFresh(J('lib/keyseq.js'), env.context, cache);
  await importFresh(J('ui/toast.js'), env.context, cache);
  await importFresh(J('lib/ansilex.js'), env.context, cache);
  const sess = await importFresh(J('term/session.js'), env.context, cache);
  await importFresh(J('layout/panes.js'), env.context, cache);
  await importFresh(J('core/dom.js'), env.context, cache);
  const S = await importFresh(J('layout/store.js'), env.context, cache);
  await importFresh(J('term/session-actions.js'), env.context, cache);
  await importFresh(J('layout/tabbar.js'), env.context, cache);
  const doc = window.document;
  sess.addSession('a', 'A', { tmuxName: 'ta' });
  const t1 = S.getActiveTabId();
  sess.addSession('b', 'B', { tmuxName: 'tb' });
  await flush();
  return { window, doc, S, t1 };
}
const tabEl = (ctx, id) => ctx.doc.querySelector(`.vt-wtab[data-tab-id="${id}"]`);
// 메뉴 코드는 처음 쓸 때 지연 import되므로(layout/tab-menu.js) 뜰 때까지 기다린다.
async function rightClick(ctx, el) {
  const before = ctx.doc.querySelectorAll('.vt-wtab-menu').length;
  el.dispatchEvent(new ctx.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 20 }));
  for (let i = 0; i < 100; i++) {
    const menus = ctx.doc.querySelectorAll('.vt-wtab-menu');
    if (menus.length && (before === 0 || menus[0].getAttribute('aria-label') !== ctx._lastLabel)) { ctx._lastLabel = menus[0].getAttribute('aria-label'); return; }
    await new Promise((r) => setTimeout(r, 10));
  }
}

test('탭 우클릭 — 이름 변경과 뷰 닫기 두 항목, 묶음 해제는 없다', async () => {
  const ctx = await setup();
  await rightClick(ctx, tabEl(ctx, ctx.t1));
  const items = [...ctx.doc.querySelectorAll('.vt-wtab-menu [role="menuitem"]')].map((b) => b.textContent);
  assert.deepStrictEqual(items, ['이름 변경', '뷰 닫기']);
  assert.strictEqual(ctx.doc.activeElement.textContent, '이름 변경', '첫 항목에 포커스');
});

test('뷰 닫기 항목 — 탭 ×와 같이 그 뷰가 사라지고 메뉴도 닫힌다', async () => {
  const ctx = await setup();
  const before = ctx.S.getTabs().length;
  await rightClick(ctx, tabEl(ctx, ctx.t1));
  [...ctx.doc.querySelectorAll('.vt-wtab-menu button')].find((b) => b.textContent === '뷰 닫기').click();
  await flush();
  assert.strictEqual(ctx.S.getTabs().length, before - 1);
  assert.strictEqual(ctx.doc.querySelector('.vt-wtab-menu'), null);
});

test('Escape·바깥 클릭은 메뉴만 닫고 뷰는 그대로다', async () => {
  const ctx = await setup();
  const before = ctx.S.getTabs().length;
  await rightClick(ctx, tabEl(ctx, ctx.t1));
  ctx.doc.dispatchEvent(new ctx.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.strictEqual(ctx.doc.querySelector('.vt-wtab-menu'), null);
  await rightClick(ctx, tabEl(ctx, ctx.t1));
  assert.ok(ctx.doc.querySelector('.vt-wtab-menu'));
  ctx.doc.body.dispatchEvent(new ctx.window.MouseEvent('pointerdown', { bubbles: true }));
  assert.strictEqual(ctx.doc.querySelector('.vt-wtab-menu'), null);
  assert.strictEqual(ctx.S.getTabs().length, before);
});

test('메뉴는 한 번에 하나 — 다른 탭에서 다시 열면 앞의 것이 닫힌다', async () => {
  const ctx = await setup();
  const [first, second] = ctx.S.getTabs();
  await rightClick(ctx, tabEl(ctx, first.id));
  await rightClick(ctx, tabEl(ctx, second.id));
  assert.strictEqual(ctx.doc.querySelectorAll('.vt-wtab-menu').length, 1);
  assert.strictEqual(ctx.doc.querySelector('.vt-wtab-menu').getAttribute('aria-label'), 'tb', '두 번째 탭의 이름');
});

test('이름 변경 항목 — 입력 대화상자를 연다', async () => {
  const ctx = await setup();
  await rightClick(ctx, tabEl(ctx, ctx.t1));
  [...ctx.doc.querySelectorAll('.vt-wtab-menu button')].find((b) => b.textContent === '이름 변경').click();
  await flush();
  assert.ok(ctx.doc.querySelector('.vt-dialog input'), '입력 대화상자');
  assert.strictEqual(ctx.doc.querySelector('.vt-wtab-menu'), null);
});
