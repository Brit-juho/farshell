// 모바일 「세션」 탭의 뷰 옮기기(layout/session-order.js, 10-session-order.md C안; Fleet.tsx가 마운트). 뷰 닫기는 session-view-edit.test.js.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function setup() {
  const env = createDomEnv('<!doctype html><html><body><div id="voice-session-picker"></div></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
  const cache = new Map();
  const core = await importFresh(J('core/store.js'), env.context, cache);
  const S = await importFresh(J('layout/store.js'), env.context, cache);
  const C = await importFresh(J('layout/compact.js'), env.context, cache);
  const P = await importFresh(J('layout/persist.js'), env.context, cache);
  const O = await importFresh(J('layout/session-order.js'), env.context, cache);
  for (const id of ['a', 'b', 'c', 'd']) core.registerSession(id, { tmuxName: id, term: { focus() {} } });
  // 뷰 3개: [a] · [b | c] · [d]
  S.setPaneSession('a');
  const t1 = S.getActiveTabId();
  S.openLayoutTab({ sessionId: 'b' });
  const t2 = S.getActiveTabId();
  S.splitActivePane('row', 'c');
  S.openLayoutTab({ sessionId: 'd' });
  const t3 = S.getActiveTabId();
  await P.restoreLayout(null);   // 저장 가드를 연다
  return { env, cache, core, S, C, P, O, t1, t2, t3 };
}
const ids = (C) => C.openSessionIds().join(',');
const flush = () => new Promise((r) => setTimeout(r, 20));

test('뷰 이동 — 묶음 뷰는 구성원이 함께 움직인다', async () => {
  const { S, C, O, t2 } = await setup();
  assert.strictEqual(ids(C), 'a,b,c,d');
  assert.strictEqual(O.moveView(t2, -1), true);
  assert.strictEqual(ids(C), 'b,c,a,d');
  assert.strictEqual(O.moveView(t2, 1), true);
  assert.strictEqual(ids(C), 'a,b,c,d');
  assert.deepStrictEqual(S.getTabs().map((t) => t.id).length, 3);
});

test('뷰 이동 — 끝에서 멈춘다(순환 없음)', async () => {
  const { C, O, t1, t3 } = await setup();
  assert.strictEqual(O.moveView(t1, -1), false);
  assert.strictEqual(O.moveView(t3, 1), false);
  assert.strictEqual(ids(C), 'a,b,c,d');
});

test('뷰 이동 — 활성 뷰·활성 칸이 그대로고 저장이 불린다', async () => {
  const { env, S, O, t1, t3 } = await setup();
  const paneBefore = S.getActivePaneId();
  assert.strictEqual(S.getActiveTabId(), t3);
  const keysBefore = JSON.stringify([...Array(env.window.localStorage.length).keys()].map((i) => env.window.localStorage.getItem(env.window.localStorage.key(i))));
  assert.strictEqual(O.moveView(t1, 1), true);   // 활성이 아닌 뷰를 옮긴다
  assert.strictEqual(S.getActiveTabId(), t3);
  assert.strictEqual(S.getActivePaneId(), paneBefore);
  const keysAfter = JSON.stringify([...Array(env.window.localStorage.length).keys()].map((i) => env.window.localStorage.getItem(env.window.localStorage.key(i))));
  assert.notStrictEqual(keysAfter, keysBefore, 'saveLayoutNow가 순서를 저장했다');
  assert.strictEqual(S.getTabs()[1].id, t1);
});

test('뷰 이동 — 사이에 빈 뷰가 끼어 있어도 이웃 뷰를 넘는다', async () => {
  const { S, C, O, core, t1 } = await setup();
  S.openLayoutTab({});                           // 빈 뷰 하나(맨 뒤)
  const empty = S.getActiveTabId();
  S.moveLayoutTab(empty, 1);                     // a, (빈), [b|c], d
  assert.strictEqual(O.orderedViews().length, 3, '빈 뷰는 편집 대상이 아니다');
  assert.strictEqual(O.moveView(t1, 1), true);
  assert.strictEqual(ids(C), 'b,c,a,d');
});

// Fleet.tsx가 하는 일의 흉내 — 컨테이너에 편집기를 그리고 레이아웃이 바뀔 때마다 다시 그린다.
async function openEditor(ctx) {
  const doc = ctx.env.window.document;
  const host = doc.createElement('div');
  host.id = 'vt-fleet';
  host.innerHTML = '<button class="vt-fleet-edit-btn"></button><div class="vt-fleet-order"></div>';
  doc.body.appendChild(host);
  const list = host.querySelector('.vt-fleet-order');
  ctx.O.renderOrderEditor(list);
  ctx.S.onLayoutChange(() => ctx.O.renderOrderEditor(list));
  return { doc, list };
}
const rows = (doc) => [...doc.querySelectorAll('.vt-order-view')].map((r) => [...r.querySelectorAll('.vt-order-heading > span, .vt-order-member')].map((x) => x.textContent).join(' '));

test('편집기 — 뷰 블록만 그린다(행 클릭 이동 없음), 끝의 이동 버튼은 비활성', async () => {
  const ctx = await setup();
  const { doc } = await openEditor(ctx);
  assert.strictEqual(doc.querySelectorAll('.vt-order-view').length, 3);
  assert.deepStrictEqual(rows(doc), ['a 1칸', '묶음 뷰 2칸 b c', 'd 1칸']);
  const btn = (tab, dir) => doc.querySelector(`.vt-order-btn[data-tab="${tab}"][data-dir="${dir}"]`);
  assert.strictEqual(btn(ctx.t1, -1).getAttribute('aria-disabled'), 'true', '맨 위의 위 버튼');
  assert.strictEqual(btn(ctx.t3, 1).getAttribute('aria-disabled'), 'true', '맨 아래의 아래 버튼');
  assert.strictEqual(btn(ctx.t2, -1).getAttribute('aria-disabled'), null);
});

test('편집기 — 위 버튼: 순서 즉시 갱신, 활성 뷰 불변, 포커스 유지', async () => {
  const ctx = await setup();
  const { doc } = await openEditor(ctx);
  const activeBefore = ctx.S.getActiveTabId();                       // d 뷰
  doc.querySelector(`.vt-order-btn[data-tab="${ctx.t2}"][data-dir="-1"]`).click(); await flush();
  assert.deepStrictEqual(rows(doc), ['묶음 뷰 2칸 b c', 'a 1칸', 'd 1칸']);
  assert.strictEqual(ctx.S.getActiveTabId(), activeBefore);
  assert.strictEqual(ids(ctx.C), 'b,c,a,d');
  const f = doc.activeElement;
  assert.strictEqual(f.dataset.tab, ctx.t2, '포커스가 옮긴 뷰의 버튼에 남는다');
  assert.match(doc.querySelector('.vt-order-note').textContent, /1번째/);
  // 맨 위에 닿았으니 포커스는 아래 버튼으로 넘어간다
  assert.strictEqual(f.dataset.dir, '1');
});
