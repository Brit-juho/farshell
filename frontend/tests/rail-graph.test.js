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
const home = (name, root, id = 'p-' + name, over = {}) => ({ project_id: id, root, name, repository: true, kind: 'checkout', path: root, registered: true, hidden: false, ...over });
const ctx = (root, linked, repositoryRoot) => ({ status: 'ready', root, worktree: { kind: linked ? 'linked' : 'main', repositoryRoot } });
const names = (xs) => Array.from(xs, (r) => r.name);
const plain = (v) => JSON.parse(JSON.stringify(v));   // vm 컨텍스트 객체를 이 쪽 값으로

test('세션은 서버가 정한 자리(home — CLI 위치 또는 셸 위치)의 저장소 밑으로', async () => {
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

test('머리 브랜치 — /api/repos가 먼저, 없으면 메인 작업 트리에 있는 세션의 브랜치(워크트리·다른 곳에 있는 세션 브랜치는 안 씀)', async () => {
  const m = await mod();
  const rows = [
    row({ name: 'wt', branch: 'fix/x', home: home('farshell', '/r/farshell'), context: ctx('/r/farshell-wt', true, '/r/farshell') }),
    row({ name: 'away', branch: 'other', home: home('farshell', '/r/farshell'), context: ctx('/r/other', false, '/r/other') }),
  ];
  assert.strictEqual(m.buildRepoTree({ rows }).find((g) => g.name === 'farshell').mainBranch, null);
  const withMain = [...rows, row({ name: 'main', branch: 'dev', home: home('farshell', '/r/farshell'), context: ctx('/r/farshell', false, '/r/farshell') })];
  assert.strictEqual(m.buildRepoTree({ rows: withMain })[0].mainBranch, 'dev');
  assert.strictEqual(m.buildRepoTree({ rows: withMain, mainBranches: new Map([['/r/farshell', 'master']]) })[0].mainBranch, 'master');
  const listed = m.buildRepoTree({ rows: [], projects: [{ id: 'A', name: 'acme', path: '/r/acme', kind: 'git', branch: 'feat/auth' }] });
  assert.strictEqual(listed[0].mainBranch, 'feat/auth', '저장소 목록의 브랜치가 먼저');
});

test('목록의 저장소가 아니면(자리 모름·셸로 들어간 미등록 저장소·숨김) 「저장소 밖」(맨 아래)', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'tmp' }),
    row({ name: 'new', context: ctx('/r/acme', false, '/r/acme'), cwd: '/r/acme' }),
    row({ name: 'unreg', home: home('acme', '/r/acme', 'A', { registered: false }), cwd: '/r/acme' }),
    row({ name: 'hid', home: home('beta', '/r/beta', 'B', { hidden: true }), cwd: '/r/beta' }),
    row({ name: 'ok', home: home('gamma', '/r/gamma') }),
  ] });
  assert.deepStrictEqual(plain(tree.map((g) => [g.name, g.repository])), [['gamma', true], ['', false]]);
  assert.strictEqual(tree[1].key, m.OUTSIDE_KEY);
  assert.deepStrictEqual(names(tree[1].lanes.flatMap((l) => l.sessions.map((s) => s.row))).sort(), ['hid', 'new', 'tmp', 'unreg']);
});

test('저장소 밖 — 지금 폴더별 가지로 묶고(같은 폴더는 한 가지), 폴더를 모르면 가지 없이', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'a', cwd: '/Users/me/Downloads/scratch' }),
    row({ name: 'b', cwd: '/Users/me/Downloads/scratch/' }),
    row({ name: 'c', cwd: '/Users/me' }),
    row({ name: 'd' }),
    row({ name: 'r', cwd: '/r/acme', home: home('acme', '/r/acme') }),
  ] });
  const out = tree.find((g) => g.key === m.OUTSIDE_KEY);
  const lanes = out.lanes.map((l) => [l.worktree ? l.worktree.folder : null, names(l.sessions.map((s) => s.row)).sort()]);
  assert.deepStrictEqual(plain(lanes.sort((x, y) => String(x[0]).localeCompare(String(y[0])))),
    [['/Users/me', ['c']], ['/Users/me/Downloads/scratch', ['a', 'b']], [null, ['d']]]);
  const acme = tree.find((g) => g.name === 'acme');
  assert.strictEqual(acme.lanes[0].worktree, null, '목록의 저장소 안 세션은 폴더 가지를 만들지 않는다');
});

test('저장소 밖 폴더 가지 — 머리는 폴더 아이콘과 ~로 줄인 경로, 전체 경로는 툴팁', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountRepoBranch(root, { key: m.OUTSIDE_KEY, name: '', repository: false, kind: null, root: null, mainBranch: null, count: 1, attention: false,
    lastActivity: 0, endedCount: 0, registered: 0, lanes: [{ worktree: { key: 'dir:/Users/me/Downloads/scratch', name: 'scratch', branch: null, folder: '/Users/me/Downloads/scratch' },
      sessions: [{ row: row({ name: 'notes', cwd: '/Users/me/Downloads/scratch' }), kids: [] }] }] });
  const head = root.querySelector('.vt-rg-dir');
  assert.ok(head);
  assert.strictEqual(head.querySelector('.vt-rg-path').textContent, '~/Downloads/scratch');
  assert.strictEqual(head.getAttribute('data-tip'), '/Users/me/Downloads/scratch', '툴팁은 줄이지 않은 절대 경로');
  // 마지막 가지(뒤에 가지·종료가 없음)의 머리는 줄기를 아래로 잇지 않는다.
  assert.ok(!/y2="100%"/.test(head.querySelector('svg').innerHTML.split('<path')[0]), head.querySelector('svg').innerHTML);
  assert.ok(!/워크트리/.test(head.textContent));
});

test('정렬 — 기본은 생성된 순(저장소 등록순·세션 만든 순, 최근 것 먼저), 입력 대기 먼저는 끔', async () => {
  const m = await mod();
  const rows = [
    row({ name: 'a1', created: 10, home: home('alpha', '/r/a', 'A') }), row({ name: 'a2', created: 30, home: home('alpha', '/r/a', 'A') }),
    row({ name: 'b1', created: 20, home: home('beta', '/r/b', 'B') }),
    row({ name: 'c1', created: 5, status: 'waiting', home: home('gamma', '/r/c', 'C') }),
  ];
  const projects = [{ id: 'A', name: 'alpha', path: '/r/a', kind: 'git', registered: 300, last_activity: 1 },
    { id: 'B', name: 'beta', path: '/r/b', kind: 'git', registered: 100, last_activity: 9 },
    { id: 'C', name: 'gamma', path: '/r/c', kind: 'directory', registered: 200, last_activity: 5 }];
  const tree = m.buildRepoTree({ rows, projects });
  assert.deepStrictEqual(plain(tree.map((g) => g.name)), ['alpha', 'gamma', 'beta'], '등록순(최근 등록 먼저) — 입력 대기도 순서를 바꾸지 않는다');
  assert.deepStrictEqual(names(tree[0].lanes[0].sessions.map((s) => s.row)), ['a2', 'a1'], '만든 순(최근 것 먼저)');
  assert.strictEqual(tree[1].kind, 'directory');
  const byName = m.buildRepoTree({ rows, projects, sort: { repos: 'name', sessions: 'name', waitingFirst: false } });
  assert.deepStrictEqual(plain(byName.map((g) => g.name)), ['alpha', 'beta', 'gamma']);
  assert.deepStrictEqual(names(byName[0].lanes[0].sessions.map((s) => s.row)), ['a1', 'a2']);
  const act = m.buildRepoTree({ rows, projects, sort: { repos: 'activity', sessions: 'activity', waitingFirst: false }, activityOf: (r) => ({ a1: 2, a2: 1 })[r.name] || 0 });
  assert.deepStrictEqual(plain(act.map((g) => g.name)), ['beta', 'gamma', 'alpha']);
  assert.deepStrictEqual(names(act[2].lanes[0].sessions.map((s) => s.row)), ['a1', 'a2']);
  const wait = m.buildRepoTree({ rows, projects, sort: { repos: 'registered', sessions: 'created', waitingFirst: true } });
  assert.strictEqual(wait[0].name, 'gamma', '입력 대기 먼저를 켜면 맨 위');
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

test('여러 단계 위임 — 손자도 최상위 세션 밑에 깊이 우선으로, 고리는 한 번만', async () => {
  const m = await mod();
  const lin = (parent, createdAt) => ({ parent, createdAt, state: 'running' });
  const tree = m.buildRepoTree({ rows: [
    row({ name: 'p', home: home('x', '/r/x') }),
    row({ name: 'c2', lineage: lin('p', 2), home: home('x', '/r/x') }),
    row({ name: 'c1', lineage: lin('p', 1), home: home('x', '/r/x') }),
    row({ name: 'g1', lineage: lin('c1', 3), home: home('x', '/r/x'), status: 'waiting' }),
    row({ name: 'loopA', lineage: lin('loopB', 1), home: home('x', '/r/x') }),
    row({ name: 'loopB', lineage: lin('loopA', 1), home: home('x', '/r/x') }),
    row({ name: 'hang', lineage: lin('loopA', 2), home: home('x', '/r/x') }),
    row({ name: 'hang2', lineage: lin('hang', 3), home: home('x', '/r/x') }),
  ] });
  const p = tree[0].lanes[0].sessions.find((e) => e.row.name === 'p');
  assert.deepStrictEqual(names(p.kids), ['c1', 'g1', 'c2']);
  assert.ok(tree[0].attention, '손자가 입력 대기여도 저장소가 위로');
  const top = names(tree[0].lanes[0].sessions.map((e) => e.row)).sort();
  assert.deepStrictEqual(top, ['loopA', 'loopB', 'p'], '계보 고리(데이터 이상)는 최상위로 — 목록에서 사라지지 않는다');
  const a = tree[0].lanes[0].sessions.find((e) => e.row.name === 'loopA');
  assert.deepStrictEqual(names(a.kids), ['hang', 'hang2'], '고리에 매달린 자손은 그대로 하위');
  assert.strictEqual(tree[0].count, 8);
});

test('저장소 목록에서 묶음이 생긴다 — 세션이 없어도 남고, 「종료 N」은 끝난 대화 수', async () => {
  const m = await mod();
  const tree = m.buildRepoTree({
    rows: [row({ name: 'live', home: home('farshell', '/r/farshell', 'P') })],
    projects: [{ id: 'P', name: 'farshell', path: '/r/farshell', kind: 'git', ended_count: 12, registered: 2, last_activity: 50 },
      { id: 'Q', name: 'notes', path: '/r/notes', kind: 'directory', ended_count: 0, registered: 1 }],
  });
  assert.deepStrictEqual(plain(tree.map((g) => [g.name, g.count, g.endedCount, g.lanes.length])), [['farshell', 1, 12, 1], ['notes', 0, 0, 0]]);
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
    { project_id: 'p', root: '/r', name: 'x', repository: true, workspace_id: undefined, kind: undefined, path: undefined, registered: true, hidden: false },
    '옛 서버(등록 칸 없음)는 git 저장소를 등록된 것으로 본다');
  assert.strictEqual(m.normalizeHome({ project_id: 'p', root: '/r', name: 'x', repository: true, registered: false }).registered, false);
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

// ── 교차 검토(Codex)에서 나온 보강 ─────────────────────────────────────────────

const lin = (parent) => ({ delegation: 'd', parent, state: 'running', outcome: null, createdAt: 1, task: '', summary: '' });
const groupWithKid = (stamp = 0) => ({
  key: 'P', name: 'farshell', repository: true, root: '/r/f', mainBranch: 'dev', count: 2, attention: false, lastActivity: stamp,
  lanes: [{ worktree: null, sessions: [{ row: row({ name: 'p', since: stamp }), kids: [row({ name: 'k', lineage: lin('p'), since: stamp })] }] }],
  ended: [],
});

test('하위 칸도 ⋯ 메뉴 버튼(키보드)·이름 편집을 받는다', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  const calls = [];
  m.mountRepoBranch(root, groupWithKid(), { calls, editing: 'k' });
  const kid = root.querySelector('.vt-rg-kid');
  assert.ok(kid.querySelector('input.vt-wgrail-name-edit'), '하위 칸도 제자리 편집 입력창');
  kid.querySelector('.vt-rg-more').click();
  assert.deepStrictEqual(calls, ['menu:k']);
});

test('폴링으로 새 묶음 객체가 와도 같은 열쇠의 칸 DOM은 그대로다(편집 입력창·열린 메뉴가 안 뜯긴다)', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  const { setGroup } = m.mountRepoBranch(root, groupWithKid(1));
  const before = [root.querySelector('.vt-rg-node'), root.querySelector('.vt-rg-kid')];
  setGroup(groupWithKid(2));
  const after = [root.querySelector('.vt-rg-node'), root.querySelector('.vt-rg-kid')];
  assert.strictEqual(after[0], before[0]);
  assert.strictEqual(after[1], before[1]);
});

test('종료 칸 — 칸은 버튼이 아니고, 펼치기·이어서·메뉴가 형제 버튼이다', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountRepoBranch(root, { ...groupWithKid(), lanes: [] }, { endedOpen: true,
    ended: [{ id: 'e', title: '끝난 일', ended: 1, last_activity: 1, resume_conversation_id: 'c', provider: 'claude' }] });
  const er = root.querySelector('.vt-rg-er');
  assert.strictEqual(er.getAttribute('role'), null);
  const toggle = er.querySelector('button.vt-rg-er-toggle');
  assert.strictEqual(toggle.getAttribute('aria-expanded'), 'false');
  assert.ok(!toggle.querySelector('button'), '버튼 안에 버튼이 없다');
  assert.ok(er.querySelector('button.vt-rg-rs'), '「이어서」는 늘 보인다');
  assert.ok(er.querySelector('button.vt-rg-more'));
});

test('입력 대기 먼저 — 대기 세션이 있는 워크트리 가지가 메인 가지보다 위로', async () => {
  const m = await mod();
  const rows = [
    row({ name: 'main', created: 9, home: home('farshell', '/r/f', 'P'), context: ctx('/r/f', false, '/r/f') }),
    row({ name: 'wt', created: 1, status: 'waiting', branch: 'fix/x', home: home('farshell', '/r/f', 'P'), context: ctx('/r/f-wt', true, '/r/f') }),
  ];
  const off = m.buildRepoTree({ rows });
  assert.strictEqual(off[0].lanes[0].worktree, null, '끄면 메인 가지가 먼저');
  const on = m.buildRepoTree({ rows, sort: { repos: 'registered', sessions: 'created', waitingFirst: true } });
  assert.strictEqual(on[0].lanes[0].worktree.branch, 'fix/x');
});
