// L3 0단계 — layout/tree.js 순수 함수 단위 테스트.
// DOM을 전혀 안 쓰는 파일이지만, 이 저장소는 --experimental-vm-modules로 진짜
// ESM을 링크·평가하는 vm-esm.js 하네스를 표준으로 쓴다(toast.test.js와 동일
// 패턴) — 여기서도 같은 방식을 따라 이 파일이 실제 ES 모듈로 남을 수 있게 한다
// (UMD로 바꾸면 core/store.js를 import하는 layout/store.js 쪽 테스트와 방식이
// 갈라진다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const TREE_JS = path.join(__dirname, '../js/layout/tree.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function loadTree() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(TREE_JS, env.context, new Map());
}

test('makeLeaf/makeSplit — 기본 필드', async () => {
  const T = await loadTree();
  const leaf = T.makeLeaf('p1', 's1');
  assert.deepEqual(leaf, { t: 'leaf', id: 'p1', session: 's1', worktree: null, host: 'local' });

  const split = T.makeSplit('sp1', 'row', leaf, T.makeLeaf('p2'), 0.5);
  assert.strictEqual(split.t, 'split');
  assert.strictEqual(split.dir, 'row');
  assert.strictEqual(split.ratio, 0.5);
});

test('countLeaves — leaf 하나면 1, 분할할 때마다 1씩 증가', async () => {
  const T = await loadTree();
  const one = T.makeLeaf('p1');
  assert.strictEqual(T.countLeaves(one), 1);

  const two = T.splitPane(one, 'p1', 'row', T.makeLeaf('p2'), 's1');
  assert.strictEqual(T.countLeaves(two), 2);

  const three = T.splitPane(two, 'p2', 'col', T.makeLeaf('p3'), 's2');
  assert.strictEqual(T.countLeaves(three), 3);
});

test('splitPane — 대상 leaf를 split(a=기존, b=새 leaf)으로 바꾼다', async () => {
  const T = await loadTree();
  const tree = T.makeLeaf('p1', 'sess-a');
  const next = T.splitPane(tree, 'p1', 'row', T.makeLeaf('p2', 'sess-b'), 'sp1');

  assert.strictEqual(next.t, 'split');
  assert.strictEqual(next.id, 'sp1');
  assert.strictEqual(next.dir, 'row');
  assert.deepEqual(next.a, { t: 'leaf', id: 'p1', session: 'sess-a', worktree: null, host: 'local' });
  assert.deepEqual(next.b, { t: 'leaf', id: 'p2', session: 'sess-b', worktree: null, host: 'local' });
  // 원본은 변형되지 않는다(불변성)
  assert.strictEqual(tree.t, 'leaf');
});

test('splitPane — 존재하지 않는 paneId는 원본을 그대로 반환한다', async () => {
  const T = await loadTree();
  const tree = T.makeLeaf('p1');
  const next = T.splitPane(tree, 'no-such-id', 'row', T.makeLeaf('p2'), 'sp1');
  assert.strictEqual(next, tree);
});

test('splitPane — 중첩 분할이 depth 제한 없이 동작한다', async () => {
  const T = await loadTree();
  let tree = T.makeLeaf('p1');
  tree = T.splitPane(tree, 'p1', 'row', T.makeLeaf('p2'), 's1');
  tree = T.splitPane(tree, 'p2', 'col', T.makeLeaf('p3'), 's2');
  tree = T.splitPane(tree, 'p3', 'row', T.makeLeaf('p4'), 's3');

  assert.strictEqual(T.countLeaves(tree), 4);
  // p4는 s1 -> b(s2) -> b(s3) -> b 경로 아래 있어야 한다
  assert.strictEqual(tree.b.b.b.id, 'p4');
});

test('closePane — root(유일한 pane)는 지울 수 없고 session만 비운다', async () => {
  const T = await loadTree();
  const tree = T.makeLeaf('p1', 'sess-a');
  const next = T.closePane(tree, 'p1');
  assert.deepEqual(next, { t: 'leaf', id: 'p1', session: null, worktree: null, host: 'local' });
});

test('closePane — 형제로 collapse된다', async () => {
  const T = await loadTree();
  const tree = T.splitPane(T.makeLeaf('p1', 'a'), 'p1', 'row', T.makeLeaf('p2', 'b'), 'sp1');
  const next = T.closePane(tree, 'p1');
  // split이 사라지고 살아남은 형제(p2)가 그 자리를 대신한다
  assert.deepEqual(next, { t: 'leaf', id: 'p2', session: 'b', worktree: null, host: 'local' });
});

test('closePane — 3분할 중 하나를 닫으면 나머지 둘만 남는다', async () => {
  const T = await loadTree();
  let tree = T.makeLeaf('p1', 'a');
  tree = T.splitPane(tree, 'p1', 'row', T.makeLeaf('p2', 'b'), 's1');
  tree = T.splitPane(tree, 'p2', 'col', T.makeLeaf('p3', 'c'), 's2');
  // tree = split(s1, a=p1, b=split(s2, a=p2, b=p3))

  const next = T.closePane(tree, 'p2');
  assert.strictEqual(T.countLeaves(next), 2);
  assert.strictEqual(next.a.id, 'p1');
  assert.strictEqual(next.b.id, 'p3'); // s2가 collapse되어 p3가 그 자리로 올라옴
});

test('closePane — 존재하지 않는 paneId는 원본을 그대로 반환한다', async () => {
  const T = await loadTree();
  const tree = T.makeLeaf('p1');
  assert.strictEqual(T.closePane(tree, 'nope'), tree);
});

test('setSession — 대상 leaf에 세션을 배정한다', async () => {
  const T = await loadTree();
  const tree = T.makeLeaf('p1', null);
  const next = T.setSession(tree, 'p1', 'sess-x');
  assert.strictEqual(next.session, 'sess-x');
});

test('setSession — 중복 attach 금지: 다른 leaf가 이미 그 세션을 갖고 있으면 거기서는 비운다', async () => {
  const T = await loadTree();
  const tree = T.splitPane(T.makeLeaf('p1', 'sess-x'), 'p1', 'row', T.makeLeaf('p2', null), 'sp1');
  const next = T.setSession(tree, 'p2', 'sess-x');
  assert.strictEqual(next.a.session, null, '기존에 갖고 있던 p1은 비워져야 한다');
  assert.strictEqual(next.b.session, 'sess-x', 'p2에 배정돼야 한다');
});

test('setSession — 같은 값이면 참조를 재사용한다(불필요한 리렌더 방지)', async () => {
  const T = await loadTree();
  const tree = T.makeLeaf('p1', 'sess-x');
  const next = T.setSession(tree, 'p1', 'sess-x');
  assert.strictEqual(next, tree);
});

test('setRatio — 대상 split의 ratio만 바뀐다', async () => {
  const T = await loadTree();
  const tree = T.splitPane(T.makeLeaf('p1'), 'p1', 'row', T.makeLeaf('p2'), 'sp1');
  const next = T.setRatio(tree, 'sp1', 0.7);
  assert.strictEqual(next.ratio, 0.7);
});

test('setRatio — 0.1~0.9로 clamp된다', async () => {
  const T = await loadTree();
  const tree = T.splitPane(T.makeLeaf('p1'), 'p1', 'row', T.makeLeaf('p2'), 'sp1');
  assert.strictEqual(T.setRatio(tree, 'sp1', 0).ratio, 0.1);
  assert.strictEqual(T.setRatio(tree, 'sp1', 1).ratio, 0.9);
  assert.strictEqual(T.setRatio(tree, 'sp1', -5).ratio, 0.1);
});

test('findNode — id로 leaf/split 어느 쪽이든 찾는다', async () => {
  const T = await loadTree();
  const tree = T.splitPane(T.makeLeaf('p1'), 'p1', 'row', T.makeLeaf('p2'), 'sp1');
  assert.strictEqual(T.findNode(tree, 'sp1').t, 'split');
  assert.strictEqual(T.findNode(tree, 'p2').t, 'leaf');
  assert.strictEqual(T.findNode(tree, 'nope'), null);
});
