// 세션 이동 순서 — 출처는 저장되는 레이아웃 **하나**다(뷰 탭 순서 → 칸 순서).
// 별도 순서 배열(core/store.js order)은 없다. 여기서 고정하는 것: 펼친 목록,
// 이동(경계 넘기·순환·빈 칸 건너뛰기), 새로고침 뒤 불변, 시트 n/m 일치,
// 이동이 세션을 뷰 사이로 옮기지 않는 것(switchTo와 달리 배정이 아니다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const COMPACT_JS = path.join(__dirname, '../js/layout/compact.js');
const STORE_JS = path.join(__dirname, '../js/layout/store.js');
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const PERSIST_JS = path.join(__dirname, '../js/layout/persist.js');
const PICKER_JS = path.join(__dirname, '../js/picker.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(html = '<!doctype html><html><body></body></html>', storage = null) {
  const env = createDomEnv(html);
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ui: {} }) });
  if (storage) for (const [k, v] of storage) env.window.localStorage.setItem(k, v);
  const cache = new Map();
  const core = await importFresh(CORE_STORE_JS, env.context, cache);
  const S = await importFresh(STORE_JS, env.context, cache);
  const C = await importFresh(COMPACT_JS, env.context, cache);
  const P = await importFresh(PERSIST_JS, env.context, cache);
  return { window: env.window, env, cache, core, S, C, P };
}

function live(core, id, tmuxName = id) {
  core.registerSession(id, { tmuxName, term: { focus() {} } });
}

// 뷰 3개: [a] · [b | c] · [d] — 가운데는 묶음(칸 2개).
async function threeViews(opts) {
  const w = await load(undefined, opts && opts.storage);
  const { core, S } = w;
  for (const id of ['a', 'b', 'c', 'd']) live(core, id);
  S.setPaneSession('a');
  const t1 = S.getActiveTabId();
  S.openLayoutTab({ sessionId: 'b' });
  const t2 = S.getActiveTabId();
  const right = S.splitActivePane('row', 'c');
  S.openLayoutTab({ sessionId: 'd' });
  const t3 = S.getActiveTabId();
  return { ...w, t1, t2, t3, right };
}

const ids = (C) => C.openSessionIds().join(',');

test('순서 목록 — 뷰 순서 → 칸 순서로 펼친다', async () => {
  const { C } = await threeViews();
  assert.strictEqual(ids(C), 'a,b,c,d');
});

test('순서 목록 — 뷰 탭을 재정렬하면 목록이 따라온다', async () => {
  const { C, S, t3 } = await threeViews();
  S.moveLayoutTab(t3, 0);
  assert.strictEqual(ids(C), 'd,a,b,c');
});

test('순서 목록 — 빈 칸·뷰어 칸·스토어에 없는 세션은 빠진다', async () => {
  const { C, S, right } = await threeViews();
  S.switchLayoutTab(S.getTabs()[1].id);
  S.setPaneSession(null, right);                 // c 자리를 비운다
  assert.strictEqual(ids(C), 'a,b,d');
  S.openViewerPane('x.txt', right);              // 뷰어 칸
  assert.strictEqual(ids(C), 'a,b,d');
});

test('순서 목록 — 세션 하나뿐이면 그것만', async () => {
  const { core, S, C } = await load();
  live(core, 'solo');
  S.setPaneSession('solo');
  assert.strictEqual(ids(C), 'solo');
  assert.strictEqual(C.nextOpenSession(1), null, '갈 곳이 없다');
});

test('이동 — 다음/이전은 뷰 경계를 넘고 칸 순서를 지킨다', async () => {
  const { C, S, t1, t2, t3 } = await threeViews();
  S.switchLayoutTab(t1);
  const seq = [];
  for (let i = 0; i < 4; i++) { C.stepSession(1); seq.push(core_active(S)); }
  assert.strictEqual(seq.join(','), 'b,c,d,a', '끝(d)에서 처음(a)으로 순환');
  assert.strictEqual(S.getActiveTabId(), t1);
  C.stepSession(-1);                              // a → d (역순환)
  assert.strictEqual(S.getActiveTabId(), t3);
  C.stepSession(-1);                              // d → c: 뷰 t2의 끝 칸
  assert.strictEqual(S.getActiveTabId(), t2);
  assert.strictEqual(core_active(S), 'c');
  C.stepSession(-1);                              // c → b: 같은 뷰 안 앞 칸
  assert.strictEqual(core_active(S), 'b');
});

function core_active(S) {
  const leaf = findLeaf(S.getTree(), S.getActivePaneId());
  return leaf && leaf.session;
}
function findLeaf(n, id) {
  if (n.t === 'leaf') return n.id === id ? n : null;
  return findLeaf(n.a, id) || findLeaf(n.b, id);
}

test('이동 — 빈 칸은 건너뛴다(활성 칸이 비어 있어도 그 자리 기준)', async () => {
  const { C, S, t2, right } = await threeViews();
  S.switchLayoutTab(t2);
  S.setPaneSession(null, right);                  // c 비움, b 칸 활성
  S.setActivePane(right);                         // 빈 칸이 활성
  C.stepSession(1);                               // 빈 칸 다음 = d
  assert.strictEqual(core_active(S), 'd');
  S.switchLayoutTab(t2);
  S.setActivePane(right);
  C.stepSession(-1);                              // 빈 칸 이전 = b
  assert.strictEqual(core_active(S), 'b');
});

test('이동 — 세션을 다른 뷰로 옮기지 않는다(배정이 아니라 이동)', async () => {
  const { C, S, t1 } = await threeViews();
  S.switchLayoutTab(t1);
  const shape = () => JSON.stringify(S.getTabsWithTrees().map((t) => [t.id, S.getTabsWithTrees().length, JSON.stringify(t.tree)]));
  const before = shape();
  for (let i = 0; i < 9; i++) C.stepSession(1);
  for (let i = 0; i < 5; i++) C.stepSession(-1);
  assert.strictEqual(shape(), before, '트리는 그대로(세션 배치·뷰 수 불변)');
  assert.strictEqual(ids(C), 'a,b,c,d');
});

test('새로고침 — 레이아웃 직렬화→복원 뒤에도 같은 목록(tmux 이름은 같고 웹 id는 새로)', async () => {
  const first = await threeViews();
  await first.P.restoreLayout(null);              // 저장 가드를 연다
  first.S.moveLayoutTab(first.t3, 0);             // 사용자가 뷰 순서를 바꿨다
  first.P.saveLayoutNow();
  const stored = [];
  for (let i = 0; i < first.window.localStorage.length; i++) {
    const k = first.window.localStorage.key(i);
    stored.push([k, first.window.localStorage.getItem(k)]);
  }
  const want = ids(first.C);
  assert.strictEqual(want, 'd,a,b,c');

  const second = await load(undefined, stored);
  for (const [id, tmux] of [['n1', 'a'], ['n2', 'b'], ['n3', 'c'], ['n4', 'd']]) live(second.core, id, tmux);
  await second.P.restoreLayout();
  const tmuxOf = (id) => second.core.getSession(id).tmuxName;
  assert.strictEqual(second.C.openSessionIds().map(tmuxOf).join(','), want);
});

test('시트·n/m — 같은 화면순 목록을 쓴다', async () => {
  const html = '<!doctype html><html><body><div id="voice-session-picker"></div></body></html>';
  const w = await load(html);
  const { core, S, C, cache, env } = w;
  for (const id of ['z', 'y', 'x']) live(core, id, id);
  S.setPaneSession('z');
  S.openLayoutTab({ sessionId: 'x' });
  S.openLayoutTab({ sessionId: 'y' });            // 등록 순서(z,y,x) ≠ 화면 순서(z,x,y)
  const picker = await importFresh(PICKER_JS, env.context, cache);
  S.switchLayoutTab(S.getTabs()[1].id);           // x
  picker.updateSessionPicker();
  assert.strictEqual(C.openSessionIds().join(','), 'z,x,y');
  assert.match(w.window.document.querySelector('.vt-sp-meta').textContent, /2\/3$/);
});
