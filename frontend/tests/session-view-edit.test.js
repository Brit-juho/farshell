// 모바일 「세션」 탭의 「뷰 편집」 — 뷰 닫기(순서 변경은 session-view-move.test.js), 그리고 빈 뷰 정리(2026-10-06).
// 진짜 index.html + 실제 모듈 그래프(session.js/picker.js/session-actions.js)로 돌린다.
// 닫기는 탭 ×와 같은 closeView(묶음 구성 기억·마지막 뷰면 온보딩)라서 그 결과까지 본다.
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
class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = 0; }
  addEventListener() {} send() {} close() {}
}
const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = () => new Promise((r) => setTimeout(r, 20));

async function setup({ width = 500 } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.innerWidth = 1024; // 기존 PC 묶음 구성
  window.WebSocket = FakeWebSocket;
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
  const dom = await importFresh(J('core/dom.js'), env.context, cache);
  const S = await importFresh(J('layout/store.js'), env.context, cache);
  const core = await importFresh(J('core/store.js'), env.context, cache);
  const actions = await importFresh(J('term/session-actions.js'), env.context, cache);
  const doc = window.document;
  // 뷰 3개: [a] · [b | c] · [d]
  sess.addSession('a', 'A');
  const t1 = S.getActiveTabId();
  sess.addSession('b', 'B', { tmuxName: 'tb' });
  const t2 = S.getActiveTabId();
  S.splitActivePane('row', null);
  sess.addSession('c', 'C', { place: 'pane', tmuxName: 'tc' });
  sess.addSession('d', 'D');
  const t3 = S.getActiveTabId();
  window.innerWidth = width;
  return { env, window, doc, sess, core, dom, S, actions, cache, t1, t2, t3 };
}

// Fleet.tsx가 하는 일의 흉내 — 컨테이너에 편집기를 그리고 레이아웃이 바뀔 때마다 다시 그린다.
async function openEdit(ctx) {
  const O = await importFresh(J('layout/session-order.js'), ctx.env.context, ctx.cache);
  const host = ctx.doc.createElement('div');
  host.id = 'vt-fleet';
  host.innerHTML = '<button class="vt-fleet-edit-btn"></button><div class="vt-fleet-order"></div>';
  ctx.doc.body.appendChild(host);
  const list = host.querySelector('.vt-fleet-order');
  O.renderOrderEditor(list);
  ctx.S.onLayoutChange(() => { if (list.isConnected) O.renderOrderEditor(list); });
}
const closeBtn = (ctx, tab) => ctx.doc.querySelector(`.vt-order-close[data-tab="${tab}"]`);

test('편집 모드의 뷰 행마다 44px 닫기 버튼(SVG x, aria-label)이 있다', async () => {
  const ctx = await setup();
  await openEdit(ctx);
  const xs = [...ctx.doc.querySelectorAll('.vt-order-close')];
  assert.strictEqual(xs.length, 3);
  for (const x of xs) {
    assert.ok(x.classList.contains('xl'), '.xl = 44px');
    assert.ok(x.querySelector('svg'));
    assert.match(x.getAttribute('aria-label'), /뷰 닫기$/);
  }
  assert.match(closeBtn(ctx, ctx.t1).getAttribute('aria-label'), /^A 뷰 닫기$/);
});

test('닫기 — 단독 뷰는 사라지고 세션은 닫히며 모드·나머지 뷰는 유지된다', async () => {
  const ctx = await setup();
  await openEdit(ctx);
  closeBtn(ctx, ctx.t1).click(); await flush();
  assert.strictEqual(ctx.S.getTabs().some((t) => t.id === ctx.t1), false);
  assert.ok(!ctx.core.getSession('a'), '세션 a 가 닫혔다(웹 attach 해제)');
  assert.strictEqual(ctx.doc.querySelectorAll('.vt-order-view').length, 2, '편집 모드 유지');
  assert.strictEqual(closeBtn(ctx, ctx.t1), null);
  assert.match(ctx.doc.querySelector('.vt-order-note').textContent, /A 뷰를 닫았습니다/);
  assert.strictEqual(ctx.doc.activeElement.classList.contains('vt-order-close'), true, '포커스가 body 로 빠지지 않는다');
});

test('닫기 — 묶음 뷰는 탭 ×와 같은 규칙으로 구성을 기억한다', async () => {
  const ctx = await setup();
  await openEdit(ctx);
  closeBtn(ctx, ctx.t2).click(); await flush();
  const remembered = ctx.S.findClosedViewByTmux('tb');
  assert.ok(remembered, '묶음 구성 기억');
  assert.strictEqual(JSON.stringify(ctx.S.closedViewMembers(remembered)), '["tb","tc"]');
});

test('닫기 — 마지막 뷰까지 닫으면 온보딩이 뜬다', async () => {
  const ctx = await setup();
  await openEdit(ctx);
  for (const t of [ctx.t1, ctx.t2, ctx.t3]) { closeBtn(ctx, t).click(); await flush(); }
  assert.ok(ctx.S.isLayoutEmpty());
  assert.ok(ctx.doc.getElementById('onboarding'), '뷰가 없으면 온보딩');
});

test('pruneBlankViews — 묶음·이름 붙은 뷰·하나뿐인 빈 뷰는 건드리지 않는다', async () => {
  const ctx = await setup();
  ctx.S.openLayoutTab({ label: '내 뷰' });
  assert.strictEqual(ctx.S.pruneBlankViews(), 0, '이름 붙은 빈 뷰');
  ctx.S.closeLayoutTab(ctx.S.getActiveTabId());
  for (const t of [ctx.t1, ctx.t2, ctx.t3]) ctx.S.closeLayoutTab(t);
  assert.ok(ctx.S.isLayoutEmpty());
  assert.strictEqual(ctx.S.pruneBlankViews(), 0, '뷰 하나뿐(온보딩 상태)');
  assert.strictEqual(ctx.S.getTabs().length, 1);
});

test('뷰 칸 수는 세션 수와 구분하고 빈 칸 분할 뒤에도 즉시 갱신한다', async () => {
  const ctx = await setup({ width: 1440 });
  ctx.S.switchLayoutTab(ctx.t2);
  ctx.S.splitActivePane('row', null);
  ctx.S.setTabLabel(ctx.t1, '서버 작업');
  await openEdit(ctx);
  assert.deepStrictEqual([...ctx.doc.querySelectorAll('.vt-order-count')].map((n) => n.textContent), ['1칸', '3칸', '1칸']);
  assert.match(ctx.doc.querySelector('.vt-order-heading').textContent, /^서버 작업/);
  assert.match(ctx.doc.querySelector('.vt-order-btn').getAttribute('aria-label'), /^서버 작업/);
});

