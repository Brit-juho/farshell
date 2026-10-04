// 모바일 세션 시트의 뷰 순서 편집(layout/session-order.js + picker.js 토글, 10-session-order.md C안).
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

async function openSheet(ctx) {
  const picker = await importFresh(J('picker.js'), ctx.env.context, ctx.cache);
  const dom = await importFresh(J('core/dom.js'), ctx.env.context, ctx.cache);
  const doc = ctx.env.window.document;
  picker.updateSessionPicker();
  dom.getAction('session.manager')();
  await flush();
  return { picker, doc };
}
const rows = (doc) => [...doc.querySelectorAll('.vt-order-view')].map((r) => [...r.querySelectorAll('.vt-order-body > span')].map((x) => x.textContent).join(' '));

test('시트 — 평소엔 세션 행과 토글, 토글을 켜면 뷰 블록만(행 클릭 이동 없음)', async () => {
  const ctx = await setup();
  const { doc } = await openSheet(ctx);
  assert.strictEqual(doc.querySelectorAll('.vt-session-select').length, 4);
  const tg = doc.querySelector('.vt-session-order-toggle');
  assert.ok(tg, '뷰가 2개 이상이면 토글이 보인다');
  assert.strictEqual(tg.getAttribute('aria-pressed'), 'false');
  assert.strictEqual(doc.querySelectorAll('.vt-order-btn').length, 0);
  tg.click(); await flush();
  assert.strictEqual(doc.querySelector('.vt-session-order-toggle').getAttribute('aria-pressed'), 'true');
  assert.strictEqual(doc.querySelectorAll('.vt-session-select').length, 0, '편집 중엔 행 클릭 이동이 없다');
  assert.strictEqual(doc.querySelectorAll('.vt-order-view').length, 3);
  assert.deepStrictEqual(rows(doc), ['a', '묶음 뷰 b c', 'd']);
  const btn = (tab, dir) => doc.querySelector(`.vt-order-btn[data-tab="${tab}"][data-dir="${dir}"]`);
  assert.strictEqual(btn(ctx.t1, -1).getAttribute('aria-disabled'), 'true', '맨 위의 위 버튼');
  assert.strictEqual(btn(ctx.t3, 1).getAttribute('aria-disabled'), 'true', '맨 아래의 아래 버튼');
  assert.strictEqual(btn(ctx.t2, -1).getAttribute('aria-disabled'), null);
});

test('시트 — 위 버튼: 순서·n/m 즉시 갱신, 활성 뷰 불변, 포커스 유지', async () => {
  const ctx = await setup();
  const { doc } = await openSheet(ctx);
  doc.querySelector('.vt-session-order-toggle').click(); await flush();
  const activeBefore = ctx.S.getActiveTabId();                       // d 뷰
  doc.querySelector(`.vt-order-btn[data-tab="${ctx.t2}"][data-dir="-1"]`).click(); await flush();
  assert.deepStrictEqual(rows(doc), ['묶음 뷰 b c', 'a', 'd']);
  assert.strictEqual(ctx.S.getActiveTabId(), activeBefore);
  assert.strictEqual(ids(ctx.C), 'b,c,a,d');
  assert.match(doc.querySelector('.vt-sp-meta').textContent, /4\/4$/, 'd는 여전히 4번째');
  const f = doc.activeElement;
  assert.strictEqual(f.dataset.tab, ctx.t2, '포커스가 옮긴 뷰의 버튼에 남는다');
  assert.match(doc.querySelector('.vt-order-note').textContent, /1번째/);
  // 맨 위에 닿았으니 포커스는 아래 버튼으로 넘어간다
  assert.strictEqual(f.dataset.dir, '1');
});

test('시트 — 활성 뷰를 옮기면 n/m이 따라 바뀐다', async () => {
  const ctx = await setup();
  const { doc } = await openSheet(ctx);
  doc.querySelector('.vt-session-order-toggle').click(); await flush();
  assert.match(doc.querySelector('.vt-sp-meta').textContent, /4\/4$/);
  doc.querySelector(`.vt-order-btn[data-tab="${ctx.t3}"][data-dir="-1"]`).click(); await flush();
  assert.strictEqual(ids(ctx.C), 'a,d,b,c');
  assert.match(doc.querySelector('.vt-sp-meta').textContent, /2\/4$/);
});

test('시트 — 닫으면 편집 모드가 풀린다, 뷰가 1개면 토글이 없다', async () => {
  const ctx = await setup();
  const { doc } = await openSheet(ctx);
  doc.querySelector('.vt-session-order-toggle').click(); await flush();
  doc.querySelector('.vt-session-close').click();
  const dom = await importFresh(J('core/dom.js'), ctx.env.context, ctx.cache);
  dom.getAction('session.manager')(); await flush();
  assert.strictEqual(doc.querySelector('.vt-session-order-toggle').getAttribute('aria-pressed'), 'false');
  assert.strictEqual(doc.querySelectorAll('.vt-order-view').length, 0);
  doc.querySelector('.vt-session-close').click();
  ctx.S.closeLayoutTab(ctx.t1); ctx.S.closeLayoutTab(ctx.t2);
  dom.getAction('session.manager')(); await flush();
  assert.strictEqual(doc.querySelector('.vt-session-order-toggle'), null);
});
