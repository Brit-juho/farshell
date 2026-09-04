// L3 0단계 — layout/store.js. tree.js를 감싸는 상태 계층 자체를 검증한다:
// activePaneId가 focus 경로로만 바뀌는지(클릭 등 다른 경로로 직접 바뀌면 안 됨),
// 구독자 알림, closePane 후 활성 pane 재배정.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const STORE_JS = path.join(__dirname, '../js/layout/store.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// core/store.js↔layout/store.js는 순환 import가 아니라 단방향이라(layout이
// core를 참조) cache를 매번 새로 만들면 store 모듈 스코프 싱글톤(activeId 등)이
// 테스트마다 새로 시작한다 — toast.test.js와 같은 이유.
async function loadStore() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(STORE_JS, env.context, new Map());
}

test('기본 상태 — leaf 하나뿐이고 activePaneId가 그 leaf를 가리킨다', async () => {
  const S = await loadStore();
  const tree = S.getTree();
  assert.strictEqual(tree.t, 'leaf');
  assert.strictEqual(S.getActivePaneId(), tree.id);
  assert.strictEqual(S.countLeaves(), 1);
});

test('splitActivePane — leaf가 늘고 새 leaf가 활성이 된다', async () => {
  const S = await loadStore();
  const rootId = S.getActivePaneId();
  const newId = S.splitActivePane('row', 'sess-b');

  assert.strictEqual(S.countLeaves(), 2);
  assert.strictEqual(S.getActivePaneId(), newId, '방금 만든 pane이 활성이어야 한다');
  assert.notStrictEqual(newId, rootId);
});

test('setActivePane — 존재하는 pane으로만 바뀐다, 존재하지 않으면 무시', async () => {
  const S = await loadStore();
  const rootId = S.getActivePaneId();
  const newId = S.splitActivePane('row');

  S.setActivePane(rootId);
  assert.strictEqual(S.getActivePaneId(), rootId);

  S.setActivePane('no-such-pane');
  assert.strictEqual(S.getActivePaneId(), rootId, '없는 pane으로는 안 바뀐다');
  void newId;
});

test('setPaneSession — 기본은 활성 pane에 배정된다', async () => {
  const S = await loadStore();
  S.setPaneSession('sess-x');
  assert.strictEqual(S.getTree().session, 'sess-x');
});

test('closePane — 활성 pane을 닫으면 남은 leaf로 활성이 옮겨간다', async () => {
  const S = await loadStore();
  const rootId = S.getActivePaneId();
  const newId = S.splitActivePane('row', 'sess-b'); // 활성 = newId

  S.closePane(newId);
  assert.strictEqual(S.countLeaves(), 1);
  assert.strictEqual(S.getActivePaneId(), rootId, '남은 leaf(root)로 활성이 옮겨가야 한다');
});

test('closePane — 유일한 pane을 닫으면 세션만 비워지고 pane 자체는 남는다', async () => {
  const S = await loadStore();
  S.setPaneSession('sess-x');
  const id = S.getActivePaneId();
  S.closePane(id);
  assert.strictEqual(S.countLeaves(), 1);
  assert.strictEqual(S.getActivePaneId(), id);
  assert.strictEqual(S.getTree().session, null);
});

test('onLayoutChange — 트리가 바뀔 때마다 구독자에게 알린다', async () => {
  const S = await loadStore();
  let calls = 0;
  const unsub = S.onLayoutChange(() => { calls++; });

  S.splitActivePane('row');
  S.setPaneSession('sess-y');
  assert.strictEqual(calls, 2);

  unsub();
  S.splitActivePane('col');
  assert.strictEqual(calls, 2, 'unsubscribe 후에는 더 안 불려야 한다');
});

test('setRatio — 스토어를 거쳐도 clamp가 그대로 적용된다', async () => {
  const S = await loadStore();
  // splitActivePane이 만든 split의 id를 얻으려면 getTree()로 직접 확인해야 한다.
  S.splitActivePane('row');
  const splitId = S.getTree().id;
  S.setRatio(splitId, 5);
  assert.strictEqual(S.getTree().ratio, 0.9);
});
