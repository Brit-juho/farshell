// 10-shell-layout.md §4 2단계 — **탭 = 워크트리**, 탭마다 자기 pane 트리.
// 1단계(탭 이름만 워크트리 단위)와의 차이가 여기서 갈린다: 탭을 바꿨다 돌아와도
// 그 워크트리에서 쓰던 분할이 그대로 있어야 한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const LAYOUT_STORE_JS = path.join(__dirname, '../js/layout/store.js');
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="tabs"></div></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  const core = await importFresh(CORE_STORE_JS, env.context, cache);
  const S = await importFresh(LAYOUT_STORE_JS, env.context, cache);
  return { core, S };
}

test('기본은 탭 하나 — 2.1.1까지의 화면과 같다', async () => {
  const { S } = await load();
  assert.strictEqual(S.getTabs().length, 1);
  assert.strictEqual(S.getActiveTabId(), S.getTabs()[0].id);
});

test('탭마다 자기 트리를 가진다 — 돌아오면 분할이 그대로다', async () => {
  const { S } = await load();
  const first = S.getActiveTabId();
  S.splitActivePane('row');
  assert.strictEqual(S.countLeaves(), 2, '첫 탭은 두 칸');

  const second = S.openLayoutTab({ worktreeId: 'wt-2', label: 'farshell/feat' });
  assert.strictEqual(S.countLeaves(), 1, '새 탭은 빈 한 칸에서 시작한다');

  S.switchLayoutTab(first);
  assert.strictEqual(S.countLeaves(), 2, '돌아오면 그 탭의 분할이 그대로 있어야 한다');
  S.switchLayoutTab(second);
  assert.strictEqual(S.countLeaves(), 1);
});

// D4("저장소 1급화" 4단계) → ADR-29 D → 2026-09-18 후속(그룹 재정의) —
// 탭의 정체성이 워크트리→저장소→그룹(태그)을 거쳐, 이제 "이 세션이 지금
// 어느 탭의 pane 트리에 있는가"(findTabBySessionId)로 정착했다.
test('같은 세션을 두 번 열면 새 탭이 아니라 전환이다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ sessionId: 's1', worktreeId: 'wt-1', label: 'a' });
  S.switchLayoutTab(S.getTabs()[0].id);
  const b = S.openLayoutTab({ sessionId: 's1', worktreeId: 'wt-1', label: 'a' });
  assert.strictEqual(a, b);
  // 2026-10-03 — 첫 세션은 "뷰 없음" 자리 탭을 그대로 쓴다(빈 탭이 남지 않는다).
  assert.strictEqual(S.getTabs().length, 1, '탭이 늘어나면 안 된다(s1의 탭 하나)');
  assert.strictEqual(S.getActiveTabId(), a);
});

test('같은 세션이 있는 탭을 다른 워크트리로 열면 탭은 그대로, worktreeId만 바뀐다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ sessionId: 's1', worktreeId: 'wt-main', label: 'a' });
  const b = S.openLayoutTab({ sessionId: 's1', worktreeId: 'wt-feature', label: 'a' });
  assert.strictEqual(a, b, '세션이 이미 있는 탭이라 새로 생기지 않는다');
  assert.strictEqual(S.getTabs().length, 1, 's1의 탭 하나, 늘어나지 않는다');
  assert.strictEqual(S.getTabs().find((t) => t.id === a).worktreeId, 'wt-feature',
    '지금 보는 워크트리가 갱신돼야 pane 헤더의 브랜치 칩이 맞는 걸 보여준다');
});

test('다른 세션을 열면(워크트리가 같아도) 별도 탭이다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ sessionId: 's1', label: 'a' });
  const b = S.openLayoutTab({ sessionId: 's2', label: 'b' });
  assert.notStrictEqual(a, b);
  assert.strictEqual(S.getTabs().length, 2);
});

// 2026-10-03(세션·뷰 규칙 "뷰 ×") — 마지막 뷰도 닫힌다. 스토어는 탭을 0개로
// 두지 않고 빈 칸 하나짜리 자리 탭(= 뷰 없음)으로 바꾼다.
test('마지막 탭을 닫으면 "뷰 없음" 자리 탭이 된다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ sessionId: 's1', label: 'a' });
  assert.strictEqual(S.isLayoutEmpty(), false);
  assert.strictEqual(S.closeLayoutTab(a), true);
  assert.strictEqual(S.getTabs().length, 1);
  assert.notStrictEqual(S.getActiveTabId(), a);
  assert.strictEqual(S.isLayoutEmpty(), true);
  assert.strictEqual(S.getTree().session, null);
});

test('뷰 탭 전환은 가장 왼쪽 위 칸에 포커스한다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ sessionId: 's1' });
  const right = S.splitActivePane('row', 's2');
  assert.strictEqual(S.getActivePaneId(), right);
  S.openLayoutTab({ sessionId: 's3' });
  S.switchLayoutTab(a);
  const tree = S.getTree();
  assert.strictEqual(S.getActivePaneId(), tree.a.id, '왼쪽 위 칸(첫 leaf)');
});

test('기억된 닫힌 묶음 — 한 세션은 묶음 하나에만 속하고, 2명 미만이 되면 버린다', async () => {
  const { S } = await load();
  const leaf = (id, tmux) => ({ t: 'leaf', id, session: tmux ? { id: 'w-' + tmux, tmux } : null });
  const split = (id, a, b) => ({ t: 'split', id, dir: 'row', ratio: 0.5, a, b });
  assert.strictEqual(S.rememberClosedView({ id: 'solo', tree: leaf('p0', 'x') }), false, '단독 뷰는 기억 안 함');
  assert.ok(S.rememberClosedView({ id: 'g1', tree: split('s1', leaf('p1', 'a'), leaf('p2', 'b')) }));
  assert.ok(S.rememberClosedView({ id: 'g2', tree: split('s2', leaf('p3', 'b'), leaf('p4', 'c')) }));
  assert.strictEqual(S.findClosedViewByTmux('b').id, 'g2', 'b는 나중 묶음으로 옮겨 간다');
  assert.strictEqual(S.findClosedViewByTmux('a'), null, 'g1은 a 혼자 남아 버려졌다');
  S.forgetClosedMembers(['c']);
  assert.strictEqual(S.getClosedViews().length, 0);
});

test('활성 탭을 닫으면 남은 탭으로 활성이 옮겨간다', async () => {
  const { S } = await load();
  const first = S.getActiveTabId();
  const second = S.openLayoutTab({ worktreeId: 'wt-2', label: 'b' });
  assert.strictEqual(S.closeLayoutTab(second), true);
  assert.strictEqual(S.getActiveTabId(), first);
  assert.strictEqual(S.getTabs().length, 1);
});

// 2026-09-23 — 헤더 탭 드래그 재정렬(layout/tabbar.js)의 순수 로직.
test('moveLayoutTab — 순서를 옮기고, 활성 탭은 그대로 유지된다(id로 다시 찾는다)', async () => {
  const { S } = await load();
  const a = S.getActiveTabId();
  const b = S.openLayoutTab({ worktreeId: 'wt-b', label: 'b' });
  const c = S.openLayoutTab({ worktreeId: 'wt-c', label: 'c' });
  // 지금 순서: [a, b, c], 활성 = c
  assert.strictEqual(S.getActiveTabId(), c);

  assert.strictEqual(S.moveLayoutTab(a, 2), true);   // [b, c, a]
  assert.strictEqual(S.getTabs().map((t) => t.id).join(','), [b, c, a].join(','));
  assert.strictEqual(S.getActiveTabId(), c, '옮긴 탭이 a인데 활성은 c였다 — 그대로여야 한다');
});

test('moveLayoutTab — 자기 자신 위치로 옮기면(변화 없음) false, 범위 밖 인덱스는 끝으로 clamp', async () => {
  const { S } = await load();
  const a = S.getActiveTabId();
  const b = S.openLayoutTab({ worktreeId: 'wt-b', label: 'b' });
  assert.strictEqual(S.moveLayoutTab(a, 0), false, '이미 0번이라 바뀐 게 없다');
  assert.strictEqual(S.moveLayoutTab(a, 99), true);
  assert.strictEqual(S.getTabs().map((t) => t.id).join(','), [b, a].join(','));
});

test('moveLayoutTab — 탭이 하나뿐이거나 없는 id면 false, 트리는 안 바뀐다', async () => {
  const { S } = await load();
  const only = S.getActiveTabId();
  assert.strictEqual(S.moveLayoutTab(only, 0), false, '탭이 하나뿐이면 옮길 데가 없다');
  const b = S.openLayoutTab({ worktreeId: 'wt-b', label: 'b' });
  assert.strictEqual(S.moveLayoutTab('no-such-tab', 0), false);
  assert.strictEqual(S.getTabs().map((t) => t.id).join(','), [only, b].join(','));
});

test('탭 전환은 구독자에게 알린다 — 렌더러가 다시 그려야 한다', async () => {
  const { S } = await load();
  const seen = [];
  S.onLayoutChange((tree, activePaneId, kind) => seen.push(kind));
  const second = S.openLayoutTab({ worktreeId: 'wt-2', label: 'b' });
  S.switchLayoutTab(S.getTabs()[0].id);
  assert.ok(seen.length >= 2);
  assert.ok(seen.every((k) => typeof k === 'string'));
});

test('replaceTabs — 스냅샷 복원(잘못된 입력은 무시)', async () => {
  const { S } = await load();
  const leaf = (id, session = null) => ({ t: 'leaf', id, session, kind: 'terminal', file: null, worktree: null, host: 'local' });
  assert.strictEqual(S.replaceTabs([], 'x'), false);
  assert.strictEqual(S.replaceTabs([{ id: 'bad' }], 'bad'), false, 'tree 없는 항목은 못 쓴다');

  const ok = S.replaceTabs([
    { id: 't1', worktreeId: null, label: '작업 공간', tree: leaf('p1'), activePaneId: 'p1' },
    { id: 't2', worktreeId: 'wt-9', label: 'repo/x', tree: leaf('p2'), activePaneId: 'p2' },
  ], 't2');
  assert.strictEqual(ok, true);
  assert.strictEqual(S.getTabs().length, 2);
  assert.strictEqual(S.getActiveTabId(), 't2');
  assert.strictEqual(S.getTree().id, 'p2');
});

test('getTabs는 트리를 내주지 않는다 — 바깥에서 직접 변형하지 못하게', async () => {
  const { S } = await load();
  assert.ok(!('tree' in S.getTabs()[0]));
  assert.ok('tree' in S.getTabsWithTrees()[0]);
});

// 2026-09-21(사용자 지적) — 레일에서 그룹화된 세션을 클릭했더니 "바로가기"가
// 아니라 활성 pane에 배정돼(switchTo의 실제 동작) 짝 세션을 그 칸에서 밀어냈다.
// revealSessionPane은 트리를 전혀 바꾸지 않고 탭·pane만 이동해야 한다.
test('revealSessionPane — 다른 탭의 그룹 칸으로 이동한다, 트리는 그대로', async () => {
  const { S } = await load();
  const leaf = (id, session = null) => ({ t: 'leaf', id, session, kind: 'terminal', file: null, worktree: null, host: 'local' });
  const split = { t: 'split', id: 'sp1', dir: 'row', ratio: 0.5, a: leaf('pA', 's1'), b: leaf('pB', 's2') };
  const ok = S.replaceTabs([
    { id: 't1', worktreeId: null, label: null, tree: leaf('p1', 's3'), activePaneId: 'p1' },
    { id: 't2', worktreeId: null, label: null, tree: split, activePaneId: 'pA' },
  ], 't1');
  assert.strictEqual(ok, true);
  const beforeTree = S.getTabsWithTrees().find((t) => t.id === 't2').tree;

  assert.strictEqual(S.revealSessionPane('s2'), true);
  assert.strictEqual(S.getActiveTabId(), 't2', '그 세션이 있는 탭으로 옮겨야 한다');
  assert.strictEqual(S.getActivePaneId(), 'pB', '짝(s1)이 아니라 s2가 든 칸이 활성이어야 한다');
  const afterTree = S.getTabsWithTrees().find((t) => t.id === 't2').tree;
  assert.deepStrictEqual(afterTree, beforeTree, '트리 구조는 손대지 않는다 — 짝(s1)이 밀려나면 안 된다');
});

test('revealSessionPane — 같은 탭 안이면 탭 전환 없이 pane만 옮긴다', async () => {
  const { S } = await load();
  S.splitActivePane('row', 's2');
  const rootId = S.getTabsWithTrees()[0].tree.a.id;
  S.setActivePane(rootId); // 짝 칸(s2가 아닌 쪽)을 다시 활성으로

  assert.strictEqual(S.revealSessionPane('s2'), true);
  assert.strictEqual(S.getTabs().length, 1);
  assert.notStrictEqual(S.getActivePaneId(), rootId);
});

test('revealSessionPane — 어느 탭에도 없으면 false, 상태는 그대로', async () => {
  const { S } = await load();
  const before = { tab: S.getActiveTabId(), pane: S.getActivePaneId() };
  assert.strictEqual(S.revealSessionPane('no-such-session'), false);
  assert.strictEqual(S.revealSessionPane(null), false);
  assert.strictEqual(S.getActiveTabId(), before.tab);
  assert.strictEqual(S.getActivePaneId(), before.pane);
});
