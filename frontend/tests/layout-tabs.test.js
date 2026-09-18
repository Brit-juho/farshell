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

// D4("저장소 1급화" 4단계) — 탭의 정체성이 워크트리에서 저장소로 바뀌었다.
test('같은 저장소를 두 번 열면 새 탭이 아니라 전환이다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ repoId: 'repo-a', worktreeId: 'wt-1', label: 'a' });
  S.switchLayoutTab(S.getTabs()[0].id);
  const b = S.openLayoutTab({ repoId: 'repo-a', worktreeId: 'wt-1', label: 'a' });
  assert.strictEqual(a, b);
  assert.strictEqual(S.getTabs().length, 2, '탭이 늘어나면 안 된다(기본 탭 + repo-a)');
  assert.strictEqual(S.getActiveTabId(), a);
});

test('같은 저장소의 다른 워크트리를 열면 탭은 그대로, worktreeId만 바뀐다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ repoId: 'repo-a', worktreeId: 'wt-main', label: 'a' });
  const b = S.openLayoutTab({ repoId: 'repo-a', worktreeId: 'wt-feature', label: 'a' });
  assert.strictEqual(a, b, '탭 자체는 저장소 단위라 새로 생기지 않는다');
  assert.strictEqual(S.getTabs().length, 2, '기본 탭 + repo-a, 늘어나지 않는다');
  assert.strictEqual(S.getTabs().find((t) => t.id === a).worktreeId, 'wt-feature',
    '지금 보는 워크트리가 갱신돼야 pane 헤더의 브랜치 칩이 맞는 걸 보여준다');
});

test('저장소가 다르면 워크트리가 없어도(repoId만 있어도) 별도 탭이다', async () => {
  const { S } = await load();
  const a = S.openLayoutTab({ repoId: 'repo-a', label: 'a' });
  const b = S.openLayoutTab({ repoId: 'repo-b', label: 'b' });
  assert.notStrictEqual(a, b);
  assert.strictEqual(S.getTabs().length, 3);
});

test('마지막 탭은 닫히지 않는다 — 트리가 0개면 그릴 대상이 없다', async () => {
  const { S } = await load();
  assert.strictEqual(S.closeLayoutTab(S.getActiveTabId()), false);
  assert.strictEqual(S.getTabs().length, 1);
});

test('활성 탭을 닫으면 남은 탭으로 활성이 옮겨간다', async () => {
  const { S } = await load();
  const first = S.getActiveTabId();
  const second = S.openLayoutTab({ worktreeId: 'wt-2', label: 'b' });
  assert.strictEqual(S.closeLayoutTab(second), true);
  assert.strictEqual(S.getActiveTabId(), first);
  assert.strictEqual(S.getTabs().length, 1);
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
