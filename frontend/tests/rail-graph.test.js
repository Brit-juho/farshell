// 레일 저장소 가지 그래프(세션 로직 점검 단계 5) — repo-tree.ts 모델, 둘째 줄 문장, 칸·묶음 DOM.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
async function mod() { const env = await loadSolidBuild(); _envs.push(env); return env; }

const row = (over) => ({
  sessionId: '', tmuxName: over.name, name: over.name, open: false, status: 'idle', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, origin: 'local', viewLabel: null,
  otherScreens: 0, otherClients: 0, ...over,
});
const home = (name, root, id = 'p-' + name) => ({ project_id: id, root, name, repository: true, kind: 'checkout', path: root });
const ctx = (root, linked, repositoryRoot) => ({ status: 'ready', root, worktree: { kind: linked ? 'linked' : 'main', repositoryRoot } });
const names = (xs) => Array.from(xs, (r) => r.name);
const plain = (v) => JSON.parse(JSON.stringify(v));   // vm 컨텍스트 객체를 이 쪽 값으로

test('세션은 카탈로그 자리(home)의 저장소 밑으로 — cd해서 다른 저장소에 있어도 옮겨 가지 않는다', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'a', home: home('farshell', '/r/farshell'), context: ctx('/r/other', false, '/r/other') }),
    row({ name: 'b', home: home('farshell', '/r/farshell'), context: ctx('/r/farshell', false, '/r/farshell') }),
  ] });
  assert.strictEqual(tree.length, 1);
  assert.strictEqual(tree[0].name, 'farshell');
  assert.deepStrictEqual(names(tree[0].lanes[0].sessions.map((s) => s.row)).sort(), ['a', 'b']);
});

test('같은 저장소의 링크드 작업 트리에 있으면 워크트리 가지, 메인 가지가 먼저', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'wt', branch: 'fix/x', home: home('farshell', '/r/farshell'), context: ctx('/r/farshell-wt', true, '/r/farshell') }),
    row({ name: 'main', home: home('farshell', '/r/farshell'), context: ctx('/r/farshell', false, '/r/farshell') }),
  ] });
  const lanes = tree[0].lanes;
  assert.strictEqual(lanes[0].worktree, null);
  assert.deepStrictEqual({ ...lanes[1].worktree }, { key: '/r/farshell-wt', name: 'farshell-wt', branch: 'fix/x' });
  assert.deepStrictEqual(names(lanes[1].sessions.map((s) => s.row)), ['wt']);
});

test('자리가 아직 없으면 지금 위치의 저장소, 그것도 없으면 「저장소 밖」(맨 아래)', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'tmp' }),
    row({ name: 'new', context: ctx('/r/acme', false, '/r/acme') }),
  ] });
  assert.deepStrictEqual(plain(tree.map((g) => [g.name, g.repository])), [['acme', true], ['', false]]);
  assert.strictEqual(tree[1].key, m.OUTSIDE_KEY);
});

test('정렬 — 입력 대기가 있는 저장소가 먼저, 그다음 최근 활동, 저장소 안도 최근 활동순', async () => {
  const m = await mod();
  const act = { a1: 100, a2: 300, b1: 200 };
  const tree = m.buildRepoTree({
    rows: [
      row({ name: 'a1', home: home('alpha', '/r/a') }), row({ name: 'a2', home: home('alpha', '/r/a') }),
      row({ name: 'b1', home: home('beta', '/r/b') }), row({ name: 'c1', status: 'waiting', home: home('gamma', '/r/c') }),
    ],
    activityOf: (r) => act[r.name] || 0,
  });
  assert.deepStrictEqual(plain(tree.map((g) => g.name)), ['gamma', 'alpha', 'beta']);
  assert.deepStrictEqual(names(tree[1].lanes[0].sessions.map((s) => s.row)), ['a2', 'a1']);
});

test('위임 하위 세션은 부모 밑(만든 순서), 부모가 목록에 없으면 최상위로', async () => {
  const m = await mod();
  const lin = (parent, createdAt) => ({ delegation: 'd', parent, state: 'running', outcome: null, createdAt, task: '', summary: '' });
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'p', home: home('x', '/r/x') }),
    row({ name: 'k2', lineage: lin('p', 2), home: home('x', '/r/x') }),
    row({ name: 'k1', lineage: lin('p', 1), home: home('x', '/r/x') }),
    row({ name: 'orphan', lineage: lin('gone', 1), home: home('x', '/r/x') }),
  ] });
  const s = tree[0].lanes[0].sessions;
  const p = s.find((e) => e.row.name === 'p');
  assert.deepStrictEqual(names(p.kids), ['k1', 'k2']);
  assert.ok(s.some((e) => e.row.name === 'orphan'), '부모 없는 하위는 최상위');
  assert.strictEqual(tree[0].count, 4);
  const flat = plain(Array.from(m.flattenRows(tree), (e) => [e.row.name, e.kid]));
  const at = flat.findIndex(([n]) => n === 'p');
  assert.deepStrictEqual(flat.slice(at, at + 3), [['p', false], ['k1', true], ['k2', true]], '접힌 띠도 하위가 부모 바로 밑');
});

test('종료(보관 안 함)는 카탈로그 자리의 저장소 「종료 N」으로, 실행 중으로 그려진 것·보관된 것은 빠진다', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({
    rows: [row({ name: 'live', home: home('farshell', '/r/farshell', 'P') })],
    ended: [
      { id: 'e1', workspace_id: 'W', ended: 10, last_activity: 50 },
      { id: 'e2', workspace_id: 'W', ended: 10, last_activity: 90 },
      { id: 'e3', workspace_id: 'W', ended: 10, archived: 5 },
      { id: 'e4', workspace_id: null, ended: 10 },
      { id: 'run', workspace_id: 'W', ended: null },
    ],
    workspaces: [{ id: 'W', project_id: 'P' }],
    projects: [{ id: 'P', name: 'farshell', path: '/r/farshell', kind: 'git' }],
  });
  assert.deepStrictEqual(plain(tree[0].ended.map((s) => s.id)), ['e2', 'e1'], '최근 활동순');
  assert.deepStrictEqual(plain(tree[1].ended.map((s) => s.id)), ['e4'], '자리가 없으면 저장소 밖');
});

test('nodeLine — 「유휴」 없이 할 말이 있을 때만: 대기 유형 · 도구 요약 · 끝남(확인 안 함) · 마지막 지시', async () => {
  const m = await mod();
  assert.strictEqual(m.nodeLine({ status: 'idle' }), null);
  assert.deepStrictEqual({ ...m.nodeLine({ status: 'idle', lastPrompt: '고쳐줘' }) }, { tone: 'muted', text: '「고쳐줘」', detail: null });
  assert.deepStrictEqual({ ...m.nodeLine({ status: 'waiting', kind: 'approval', summary: 'npm' }) }, { tone: 'waiting', text: '승인 요청', detail: 'npm' });
  assert.deepStrictEqual({ ...m.nodeLine({ status: 'working', tool: 'Edit', summary: 'a.ts' }) }, { tone: 'working', text: 'Edit', detail: 'a.ts' });
  assert.strictEqual(m.nodeLine({ status: 'done', unseen: true }).text, '끝남 · 확인 안 함');
  assert.strictEqual(m.nodeLine({ status: 'done', unseen: false }).tone, 'muted');
});

test('kidLabel — 보고가 왔는데 부모가 아직 안 읽었으면 「보고 도착」', async () => {
  const m = await mod();
  assert.strictEqual(m.kidLabel({ state: 'reported', outcome: 'succeeded', reportUnread: true }).text, '보고 도착');
  assert.strictEqual(m.kidLabel({ state: 'reported', outcome: 'succeeded', reportUnread: false }).text, '보고 · 성공');
  assert.strictEqual(m.kidLabel({ state: 'reported', outcome: 'failed', reportUnread: false }).tone, 'error');
  assert.strictEqual(m.kidLabel({ state: 'running' }).text, '진행 중');
});

test('normalizeHome — 모양이 다르면 없는 것으로', async () => {
  const m = await mod();
  assert.strictEqual(m.normalizeHome(null), null);
  assert.strictEqual(m.normalizeHome({ project_id: 1, root: '/r', name: 'x' }), null);
  assert.deepStrictEqual({ ...m.normalizeHome({ project_id: 'p', root: '/r', name: 'x', repository: true }) },
    { project_id: 'p', root: '/r', name: 'x', repository: true, workspace_id: undefined, kind: undefined, path: undefined });
});

test('가지선 — 마지막 세션은 마디에서 멈추고, 워크트리 가지는 x=35, 줄기는 뒤에 더 있을 때만', async () => {
  const m = await mod();
  assert.ok(!m.sessionLines(1, { more: false, moreInLane: false, kids: false }).includes('100%'));
  assert.ok(m.sessionLines(1, { more: true, moreInLane: false, kids: false }).includes('y2="100%"'));
  const l2 = m.sessionLines(2, { more: false, moreInLane: false, kids: false });
  assert.ok(l2.includes('x1="35"') && !l2.includes('x1="21"'));
  assert.ok(m.sessionLines(2, { more: true, moreInLane: false, kids: false }).includes('x1="21"'));
  assert.ok(m.kidLines(1, { more: false, continueParent: false }).includes('<path'));
});

// ── DOM ────────────────────────────────────────────────────────────────────

test('세션 칸 — 1줄 이름·경과·⋯(늘 있음), 2줄은 할 말이 있을 때만, 「유휴」·「직접 만듦」 없음', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  const calls = [];
  m.mountSessionNode(root, {
    row: row({ name: '결제 버그', tmuxName: 'farshell', status: 'idle', origin: 'external', agent: 'claude' }),
    active: false, geometry: { lane: 1, lines: '' },
    onOpen: () => calls.push('open'), onContext: (e) => calls.push(e.type),
  });
  const text = root.textContent;
  assert.ok(root.querySelector('.vt-rg-name').textContent === '결제 버그');
  assert.ok(root.querySelector('.vt-rg-more'), '⋯는 호버 없이도 있다');
  assert.strictEqual(root.querySelector('.vt-rg-b'), null, '할 말이 없으면 둘째 줄 없음');
  assert.ok(!/유휴|직접 만듦|이 화면에서 만듦/.test(text));
  root.querySelector('.vt-rg-more').dispatchEvent(new m.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(calls, ['click'], '⋯는 메뉴만 열고 칸을 열지 않는다');
});

test('세션 칸 — 입력 대기는 유형과 요약, 다른 맥에서 만든 세션만 만든 곳 표시', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountSessionNode(root, {
    row: row({ name: 'w', status: 'waiting', kind: 'approval', summary: 'npm', origin: 'peer', originHost: 'M5' }),
    active: false, geometry: { lane: 1, lines: '' }, onOpen() {}, onContext() {},
  });
  assert.match(root.querySelector('.vt-rg-sm').textContent, /승인 요청\s*npm/);
  assert.ok(root.querySelector('.vt-srow-origin[data-origin="peer"]'));
  assert.ok(root.querySelector('.vt-rg-node.is-waiting'));
});

test('저장소 묶음 — 머리(이름·메인 브랜치·수), 워크트리 가지 머리, 종료 접기, 머리 누르면 접기', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  const calls = [];
  const group = {
    key: 'P', name: 'farshell', repository: true, root: '/r/farshell', mainBranch: 'dev', count: 2, attention: false, lastActivity: 0,
    lanes: [
      { worktree: null, sessions: [{ row: row({ name: 'a' }), kids: [] }] },
      { worktree: { key: '/r/wt', name: 'wt', branch: 'fix/x' }, sessions: [{ row: row({ name: 'b' }), kids: [] }] },
    ],
    ended: [],
  };
  m.mountRepoBranch(root, group, { calls, ended: [{ id: 'e', title: '끝난 일', ended: 1, last_activity: 1 }] });
  const head = root.querySelector('.vt-rg-head');
  assert.match(head.textContent, /farshell/);
  assert.match(head.textContent, /dev/);
  assert.match(head.textContent, /2/);
  assert.match(root.querySelector('.vt-rg-wt').textContent, /워크트리\s*fix\/x/);
  assert.ok(root.querySelector('.vt-rg-node.l2'), '워크트리 가지의 세션은 두 번째 가지');
  assert.match(root.querySelector('.vt-rg-fold').textContent, /종료 1/);
  head.click();
  assert.deepStrictEqual(calls, ['toggle']);
});

test('저장소 밖 묶음 — 머리 이름은 「저장소 밖」', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountRepoBranch(root, { key: m.OUTSIDE_KEY, name: '', repository: false, root: null, mainBranch: null, count: 1, attention: false,
    lastActivity: 0, lanes: [{ worktree: null, sessions: [{ row: row({ name: 'n' }), kids: [] }] }], ended: [] });
  assert.match(root.querySelector('.vt-rg-rn').textContent, /저장소 밖/);
  assert.ok(root.querySelector('.vt-rg.outside'));
});
