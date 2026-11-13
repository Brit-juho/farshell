// 레일(shell/rail-data.ts) 순수 로직 — docs/ref/terminal.md 「Session and view rules」(Rail).
// 구역 둘(개입 필요 · 세션, 이름순만), 상태 키워드, 만든 곳, 행 메뉴, 설정 기본값, 행 조립.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

let _mod = null;
async function mod() {
  if (!_mod) { _mod = await loadSolidBuild(); _doms.push(_mod); }
  return _mod;
}

const NOW = new Date('2026-09-10T12:00:00Z').getTime();
const PREFS = { attentionShow: true, attentionCollapsed: false, hiddenOrigins: [] };
const row = (over) => ({
  sessionId: '', tmuxName: 'x', name: 'x', open: false, status: 'idle', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null,
  origin: 'local', viewLabel: null, otherScreens: 0, otherClients: 0, ...over,
});
const names = (rows) => Array.from(rows, (r) => r.name);

// ── 구역 ───────────────────────────────────────────────────────────────────

test('buildRailLists — 「세션」은 전체를 이름순으로 낸다(상태 우선순위 정렬이 없다)', async () => {
  const { buildRailLists } = await mod();
  const { sessions } = buildRailLists([
    row({ name: 'delta', status: 'idle' }),
    row({ name: 'alpha', status: 'working' }),
    row({ name: 'charlie', status: 'waiting' }),
    row({ name: 'bravo', status: 'done' }),
  ], PREFS);
  assert.deepStrictEqual(names(sessions), ['alpha', 'bravo', 'charlie', 'delta']);
});

test('buildRailLists — 상태가 바뀌어도 세션 순서는 안 변한다(행이 오락가락하지 않는다)', async () => {
  const { buildRailLists } = await mod();
  const a = [row({ name: 'a', status: 'idle' }), row({ name: 'b', status: 'idle' })];
  const before = names(buildRailLists(a, PREFS).sessions);
  a[1].status = 'waiting'; a[0].status = 'working';
  assert.deepStrictEqual(names(buildRailLists(a, PREFS).sessions), before);
});

test('buildRailLists — 개입 필요는 waiting만, 이름순이고 「세션」에도 그대로 남는다', async () => {
  const { buildRailLists } = await mod();
  const { attention, sessions } = buildRailLists([
    row({ name: 'zed', status: 'waiting' }),
    row({ name: 'amy', status: 'waiting' }),
    row({ name: 'bob', status: 'working' }),
  ], PREFS);
  assert.deepStrictEqual(names(attention), ['amy', 'zed']);
  assert.deepStrictEqual(names(sessions), ['amy', 'bob', 'zed']);
});

test('buildRailLists — error 같은 알 수 없는 상태는 idle로 접혀 개입 필요에 안 든다', async () => {
  const { buildRailLists } = await mod();
  assert.strictEqual(buildRailLists([row({ status: 'error' })], PREFS).attention.length, 0);
});

test('buildRailLists — 개입 필요를 끄면 구역이 비고 waiting은 「세션」 안에 남는다', async () => {
  const { buildRailLists } = await mod();
  const { attention, sessions } = buildRailLists([row({ name: 'a', status: 'waiting' })], { ...PREFS, attentionShow: false });
  assert.strictEqual(attention.length, 0);
  assert.deepStrictEqual(names(sessions), ['a']);
});

test('buildRailLists — 만든 곳을 끄면 숨지만 열려 있는 세션·waiting 세션은 남는다', async () => {
  const { buildRailLists } = await mod();
  const prefs = { ...PREFS, hiddenOrigins: ['external', 'peer'] };
  const { sessions } = buildRailLists([
    row({ name: 'ext-closed', origin: 'external' }),
    row({ name: 'ext-open', origin: 'external', open: true }),
    row({ name: 'ext-wait', origin: 'external', status: 'waiting' }),
    row({ name: 'peer-closed', origin: 'peer' }),
    row({ name: 'mine', origin: 'local' }),
  ], prefs);
  assert.deepStrictEqual(names(sessions), ['ext-open', 'ext-wait', 'mine']);
});

test('묶음/묶지 않음/잠자는 중 구역의 옛 API는 없다', async () => {
  const m = await mod();
  for (const k of ['buildSessionSections', 'buildSleepingEntries', 'buildRailSections', 'GROUP_LABEL', 'COLLAPSIBLE_GROUPS']) {
    assert.strictEqual(m[k], undefined, `${k}가 남아 있다`);
  }
});

// ── 상태 키워드 ────────────────────────────────────────────────────────────

test('stateKeyword — waiting은 kind별 고정 키워드, kind가 없거나 모르면 질문', async () => {
  const { stateKeyword } = await mod();
  const kw = (kind) => stateKeyword({ status: 'waiting', kind, badge: null, tool: null }).text;
  assert.strictEqual(kw('approval'), '승인 요청');
  assert.strictEqual(kw('choice'), '선택 요청');
  assert.strictEqual(kw('plan'), '계획 확인');
  assert.strictEqual(kw('question'), '질문');
  assert.strictEqual(kw(null), '질문');
  assert.strictEqual(kw('weird'), '질문');
});

test('stateKeyword — 작업 중은 도구 이름을 따로 싣고, 완료는 배지가 있으면 보조 표시', async () => {
  const { stateKeyword } = await mod();
  const w = stateKeyword({ status: 'working', kind: null, badge: null, tool: 'Edit' });
  assert.strictEqual(w.text, '작업 중');
  assert.strictEqual(w.tool, 'Edit');
  assert.strictEqual(stateKeyword({ status: 'done', kind: null, badge: 'question', tool: null }).badge, '질문 있음');
  assert.strictEqual(stateKeyword({ status: 'done', kind: null, badge: 'plan', tool: null }).badge, '계획 있음');
  assert.strictEqual(stateKeyword({ status: 'done', kind: null, badge: null, tool: null }).badge, null);
  // working에도 보조 표시가 붙는다(agy 「계획 있음」은 working 엔트리에 달린다) — 상태·도구는 그대로.
  const wb = stateKeyword({ status: 'working', kind: null, badge: 'plan', tool: 'Edit' });
  assert.strictEqual(wb.badge, '계획 있음');
  assert.strictEqual(wb.text, '작업 중');
  assert.strictEqual(wb.tool, 'Edit');
  assert.strictEqual(stateKeyword({ status: 'working', kind: null, badge: 'question', tool: null }).badge, '질문 있음');
  assert.strictEqual(stateKeyword({ status: 'working', kind: null, badge: null, tool: null }).badge, null);
  // waiting은 유형 키워드가 뜻을 이미 말하고, idle에 남은 배지는 안 보인다.
  assert.strictEqual(stateKeyword({ status: 'waiting', kind: 'plan', badge: 'plan', tool: null }).badge, null);
  assert.strictEqual(stateKeyword({ status: 'idle', kind: null, badge: 'plan', tool: null }).badge, null);
  assert.strictEqual(stateKeyword({ status: 'idle', kind: null, badge: null, tool: null }).text, '유휴');
});

test('statusSentence — 키워드 · 도구 · 배지 · 경과를 한 줄로 잇는다', async () => {
  const { statusSentence } = await mod();
  const since = NOW / 1000 - 120;
  assert.strictEqual(statusSentence({ status: 'waiting', kind: 'approval', badge: null, tool: null, since }, NOW), '승인 요청 · 2분');
  assert.strictEqual(statusSentence({ status: 'working', kind: null, badge: null, tool: 'Bash', since }, NOW), '작업 중 · Bash · 2분');
  assert.strictEqual(statusSentence({ status: 'working', kind: null, badge: 'plan', tool: null, since }, NOW), '작업 중 · 계획 있음 · 2분');
  assert.strictEqual(statusSentence({ status: 'idle', kind: null, badge: null, tool: null, since: null }, NOW), '유휴');
});

test('formatRelativeTime — 초/분/시간/일 경계', async () => {
  const { formatRelativeTime } = await mod();
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 30, NOW), '30초');
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 120, NOW), '2분');
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 7200, NOW), '2시간');
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 172800, NOW), '2일');
  assert.strictEqual(formatRelativeTime(null, NOW), '');
});

// ── 만든 곳·아이콘·메뉴 ────────────────────────────────────────────────────

test('normalizeOrigin — 없거나 모르는 값은 local(구버전 서버에서 모든 행에 아이콘이 붙지 않는다)', async () => {
  const { normalizeOrigin, HIDEABLE_ORIGINS } = await mod();
  assert.strictEqual(normalizeOrigin(undefined), 'local');
  assert.strictEqual(normalizeOrigin('whatever'), 'local');
  assert.strictEqual(normalizeOrigin('peer'), 'peer');
  assert.strictEqual(normalizeOrigin('agent'), 'local', '폐기된 값 — 모르는 값과 같다');
  assert.strictEqual(normalizeOrigin('external'), 'external');
  assert.deepStrictEqual(Array.from(HIDEABLE_ORIGINS), ['peer', 'external'], 'local은 끌 수 없다');
});

test('worktreeIconName / diffLabel', async () => {
  const { worktreeIconName, diffLabel } = await mod();
  assert.strictEqual(worktreeIconName({ isMainWorktree: true }), 'git-commit');
  assert.strictEqual(worktreeIconName({ isMainWorktree: false }), 'git-branch');
  assert.strictEqual(diffLabel({ changed: { files: 2, add: 14, del: 3 }, diffFiles: 2 }), '+14 −3');
  assert.strictEqual(diffLabel({ changed: null, diffFiles: 3 }), '파일 3');
  // 0인 쪽은 찍지 않는다("-0"은 의미 없는 잡음) — 둘 다 0이면 파일 수로 폴백.
  assert.strictEqual(diffLabel({ changed: { files: 1, add: 5, del: 0 }, diffFiles: 1 }), '+5');
  assert.strictEqual(diffLabel({ changed: { files: 1, add: 0, del: 7 }, diffFiles: 1 }), '−7');
  assert.strictEqual(diffLabel({ changed: { files: 2, add: 0, del: 0 }, diffFiles: 2 }), '파일 2');
  assert.strictEqual(diffLabel({ changed: null, diffFiles: 0 }), null);
  assert.strictEqual(diffLabel({ changed: null, diffFiles: null }), null);
});

test('rowMenuItems — 열린 세션은 닫기, 열리지 않은 세션은 열기, tmux면 종료가 붙는다', async () => {
  const { rowMenuItems } = await mod();
  const ids = (r) => Array.from(rowMenuItems(r), (i) => i.id);
  assert.deepStrictEqual(ids({ open: true, tmuxName: 't' }), ['close', 'rename', 'openOnMac', 'screens', 'kill']);
  assert.deepStrictEqual(ids({ open: false, tmuxName: 't' }), ['open', 'kill']);
  assert.deepStrictEqual(ids({ open: true, tmuxName: null }), ['close', 'rename', 'openOnMac', 'screens']);
  const kill = rowMenuItems({ open: true, tmuxName: 't' }).find((i) => i.id === 'kill');
  assert.strictEqual(kill.label, '종료');
  assert.strictEqual(kill.danger, true);
  // 재우기/깨우기 어휘는 없다.
  const labels = rowMenuItems({ open: true, tmuxName: 't' }).concat(rowMenuItems({ open: false, tmuxName: 't' })).map((i) => i.label).join(' ');
  assert.ok(!/재우기|깨우기|잠/.test(labels));
});

test('runRowMenu — 항목이 session-actions 브리지를 부른다', async () => {
  const { runRowMenu } = await mod();
  const calls = [];
  const w = new Proxy({}, { get: (_, k) => (...a) => calls.push([k, ...a]) });
  const r = { sessionId: 's1', tmuxName: 't1', name: 'n' };
  let renamed = 0;
  for (const id of ['open', 'close', 'rename', 'openOnMac', 'screens', 'kill']) runRowMenu(id, r, w, () => { renamed++; });
  assert.deepStrictEqual(calls.map((c) => c[0]), [
    'vtOpenSessionFromRail', 'vtCloseSession', 'vtOpenSessionOnMac', 'vtShowClients', 'vtConfirmAndKillSession',
  ]);
  assert.strictEqual(renamed, 1);
  assert.deepStrictEqual(calls[4].slice(1), ['t1', 's1']);
});

// ── 설정 ───────────────────────────────────────────────────────────────────

test('레일 설정 기본값이 설정 스키마의 def와 같다(두 벌이 어긋나면 기본 표시가 뒤집힌다)', async () => {
  const { RAIL_PREF_DEFAULTS } = await mod();
  const { SETTINGS_SCHEMA } = await mod();
  for (const [key, def] of Object.entries(RAIL_PREF_DEFAULTS)) {
    const spec = SETTINGS_SCHEMA[key];
    assert.ok(spec, `${key}가 스키마에 없다`);
    assert.strictEqual(spec.scope, 'device', `${key}는 기기별 설정이다`);
    assert.strictEqual(spec.type, 'bool');
    assert.strictEqual(spec.def, def, key);
  }
  assert.strictEqual(SETTINGS_SCHEMA['rail.group.idle.collapsed'], undefined, '옛 그룹 접힘 키는 없다');
});

test('readRailPrefs — 값이 없으면 기본값, 있으면 그 값(undefined가 false로 뒤집히지 않는다)', async () => {
  const { readRailPrefs } = await mod();
  const d = readRailPrefs(() => undefined);
  assert.strictEqual(d.attentionShow, true);
  assert.strictEqual(d.attentionCollapsed, false);
  assert.deepStrictEqual(Array.from(d.hiddenOrigins), []);
  const store = { 'rail.attention.show': false, 'rail.attention.collapsed': true, 'rail.origin.agent.show': false, 'rail.origin.peer.show': false };
  const p = readRailPrefs((k) => store[k]);
  assert.strictEqual(p.attentionShow, false);
  assert.strictEqual(p.attentionCollapsed, true);
  assert.deepStrictEqual(Array.from(p.hiddenOrigins), ['peer'], '폐기된 rail.origin.agent.show 는 무시된다');
});

// ── 레일 접힘 기본값 ───────────────────────────────────────────────────────

test('defaultRailCollapsed — wide 미만은 접고, wide 이상은 편다', async () => {
  const { defaultRailCollapsed } = await mod();
  const REGULAR_MAX = 1280;
  assert.strictEqual(defaultRailCollapsed(760, REGULAR_MAX), true);
  assert.strictEqual(defaultRailCollapsed(1279, REGULAR_MAX), true);
  assert.strictEqual(defaultRailCollapsed(1280, REGULAR_MAX), false);
  assert.strictEqual(defaultRailCollapsed(1733, REGULAR_MAX), false);
});

// ── 행 조립(collectSessionRows) ────────────────────────────────────────────

test('CLI마다 같은 현재 위치 context를 사용하고 오래된 워크트리 정보는 섞지 않는다', async () => {
  const { collectSessionRows, window } = await mod();
  window.allSessions = () => ({});
  window.agentNames = () => ({ a: 'claude', b: 'codex', c: 'agy' });
  const tmux = ['a', 'b', 'c'].map((name) => ({ name, cwd: '/new/src', wt_id: 'old',
    context: { cwd: '/new/src', root: '/new', status: 'ready', branch: 'current', changed: { files: 2, add: 3, del: 1 } } }));
  let requests = 0;
  const rows = collectSessionRows({ tmux, repos: [{ worktrees: [{ id: 'old', branch: 'wrong', changed: { files: 99, add: 99, del: 99 } }] }],
    deps: { vtFetch: async () => { requests++; return {}; } }, onDiff: () => {} });
  for (const row of rows) {
    assert.strictEqual(row.branch, 'current');
    assert.strictEqual(row.directory, 'src');
    assert.strictEqual(row.changed.files, 2);
  }
  assert.strictEqual(requests, 0, '서버 context를 받은 뒤 별도 Git 값을 섞지 않는다');
});

test('cwd가 바뀐 context와 nonrepo는 이전 브랜치/변경량을 폴백하지 않는다', async () => {
  const { collectSessionRows, window } = await mod();
  window.allSessions = () => ({});
  const rows = collectSessionRows({ tmux: [
    { name: 'moved', cwd: '/new', wt_id: 'old', context: { cwd: '/old', status: 'ready', branch: 'wrong' } },
    { name: 'shell', cwd: '/new', wt_id: 'old', context: { cwd: '/new', status: 'nonrepo', branch: null } },
  ], repos: [{ worktrees: [{ id: 'old', branch: 'old-branch' }] }], deps: { vtFetch: async () => ({}) }, onDiff: () => {} });
  for (const row of rows) assert.strictEqual(row.branch, null);
  assert.strictEqual(rows[0].contextStatus, 'unavailable');
  assert.strictEqual(rows[1].contextStatus, 'nonrepo');
});

test('collectSessionRows — 브랜치는 main도 담고, origin·열림·묶음 이름·다른 화면을 채운다', async () => {
  const { collectSessionRows, window } = await mod();
  window.allSessions = () => ({ w1: { tmuxName: 'api' }, w2: { tmuxName: 'web' } });
  window.getStatus = (n) => (n === 'api' ? 'waiting' : 'idle');
  window.isUnseen = (n) => n === 'api';
  window.agentDetails = () => ({ api: { since: 5, tool: null, kind: 'choice', badge: null, question: 'q?', options: null, promptId: null } });
  window.agentNames = () => ({ api: 'claude' });
  const rows = collectSessionRows({
    tmux: [
      { name: 'api', cwd: '/r', wt_id: 'wt-main', attached: 3, origin: 'external' },
      { name: 'web', cwd: '/r', wt_id: 'wt-b', attached: 1, origin: 'peer', originHost: 'm5-studio' },
      { name: 'cli', cwd: null, wt_id: null, attached: 0 },
    ],
    repos: [{ worktrees: [
      { id: 'wt-main', branch: 'main', isMain: true, changed: { files: 1, add: 4, del: 2 } },
      { id: 'wt-b', branch: 'feat/x', isMain: false, changed: null },
    ] }],
    groups: [{ id: 'g', label: 'api+web', sessionIds: ['w1', 'w2'] }],
    deps: { vtFetch: async () => ({}) },
    onDiff: () => {},
  });
  const by = Object.fromEntries(Array.from(rows, (r) => [r.name, r]));
  assert.strictEqual(by.api.branch, 'main', 'main 브랜치도 항상 담는다');
  assert.strictEqual(by.api.isMainWorktree, true);
  assert.strictEqual(by.web.branch, 'feat/x');
  assert.strictEqual(by.api.kind, 'choice');
  assert.strictEqual(by.api.unseen, true);
  assert.strictEqual(by.api.origin, 'external');
  assert.strictEqual(by.web.origin, 'peer');
  assert.strictEqual(by.cli.origin, 'local', 'origin 필드가 없으면 local');
  assert.strictEqual(by.web.originHost, 'm5-studio');
  assert.strictEqual(by.api.originHost, '', 'originHost 필드가 없으면(구버전 서버) 빈 값');
  assert.strictEqual(by.api.open, true);
  assert.strictEqual(by.cli.open, false);
  assert.strictEqual(by.api.viewLabel, 'api+web');
  assert.strictEqual(by.cli.viewLabel, null);
  assert.strictEqual(by.api.otherScreens, 2, '붙은 3 중 이 브라우저 탭 PTY 1개를 뺀다');
  assert.strictEqual(by.web.otherScreens, 0);
  assert.strictEqual(by.cli.otherScreens, 0);
  assert.strictEqual(by.api.agent, 'claude');
});

test('collectSessionRows — 서버의 webScreens/otherClients로 배지를 센다(이 탭 제외), 구버전 서버는 예전 계산', async () => {
  const { collectSessionRows, window } = await mod();
  window.allSessions = () => ({ w1: { tmuxName: 'a' }, w2: { tmuxName: 'b' } });
  const rows = collectSessionRows({
    tmux: [
      { name: 'a', attached: 4, webScreens: 3, otherClients: 1 },   // 이 탭 + 다른 웹 2 + 맥 터미널 1
      { name: 'b', attached: 1, webScreens: 1, otherClients: 0 },   // 이 탭뿐
      { name: 'c', attached: 2, webScreens: 0, otherClients: 2 },   // 열려 있지 않고 터미널 2
      { name: 'old', attached: 3 },                                  // 구버전 서버 — 필드 없음
    ],
    repos: [], groups: [], deps: { vtFetch: async () => ({}) }, onDiff: () => {},
  });
  const by = Object.fromEntries(Array.from(rows, (r) => [r.name, r]));
  assert.deepStrictEqual([by.a.otherScreens, by.a.otherClients], [2, 1]);
  assert.deepStrictEqual([by.b.otherScreens, by.b.otherClients], [0, 0]);
  assert.deepStrictEqual([by.c.otherScreens, by.c.otherClients], [0, 2]);
  assert.deepStrictEqual([by.old.otherScreens, by.old.otherClients], [3, 0]);
});

// ── 행 메뉴 요약 한 줄(터치에는 호버 툴팁이 없다) ──────────────────────────

test('rowSummary — 상태 · (대기면) 유형 · 웹 외 N · 만든 곳 · 브랜치, 값 없는 조각은 건너뛴다', async () => {
  const { rowSummary } = await mod();
  const r = (o) => ({ status: 'idle', kind: null, otherClients: 0, origin: 'local', branch: null, ...o });
  assert.strictEqual(rowSummary(r({})), '유휴');
  assert.strictEqual(
    rowSummary(r({ status: 'waiting', kind: 'approval', otherClients: 2, origin: 'peer', branch: 'feat/x' })),
    '입력 대기 · 승인 요청 · 웹 외 2 · 다른 맥에서 만듦 · feat/x');
  assert.strictEqual(rowSummary(r({ status: 'waiting', kind: null })), '입력 대기 · 질문');
  assert.strictEqual(rowSummary(r({ status: 'working', origin: 'peer', branch: 'main' })), '작업 중 · 다른 맥에서 만듦 · main');
  assert.strictEqual(rowSummary(r({ status: 'done', origin: 'external' })), '완료', '만든 곳은 다른 맥일 때만');
  // 입력 대기가 아니면 유형은 안 붙는다, 모르는 상태는 유휴
  assert.ok(!rowSummary(r({ status: 'working', kind: 'plan' })).includes('계획 확인'));
  assert.ok(rowSummary(r({ status: 'error' })).startsWith('유휴'));
});

test('rowMenuItems — split 옵션이 있을 때만 「오른쪽에 나란히 열기」「아래에 열기」가 들어간다', async () => {
  const { rowMenuItems } = await mod();
  const ids = (r, o) => Array.from(rowMenuItems(r, o), (i) => i.id);
  assert.deepStrictEqual(ids({ open: true, tmuxName: 't' }, { split: true }),
    ['close', 'splitRight', 'splitDown', 'rename', 'openOnMac', 'screens', 'kill']);
  assert.deepStrictEqual(ids({ open: false, tmuxName: 't' }, { split: true }), ['open', 'splitRight', 'splitDown', 'kill']);
  assert.deepStrictEqual(ids({ open: false, tmuxName: 't' }, { split: false }), ['open', 'kill']);
  const labels = rowMenuItems({ open: false, tmuxName: 't' }, { split: true }).map((i) => i.label);
  assert.ok(labels.includes('오른쪽에 나란히 열기') && labels.includes('아래에 열기'));
});

test('runRowMenu — 분할 항목은 vtSplitOpenSession(방향, 세션)을 부른다', async () => {
  const { runRowMenu } = await mod();
  const calls = [];
  const w = new Proxy({}, { get: (_, k) => (...a) => calls.push([k, ...a]) });
  const r = { sessionId: 's1', tmuxName: 't1', name: 'n' };
  runRowMenu('splitRight', r, w, () => {});
  runRowMenu('splitDown', { ...r, sessionId: undefined }, w, () => {});
  assert.deepStrictEqual(JSON.parse(JSON.stringify(calls)), [
    ['vtSplitOpenSession', 'right', { sessionId: 's1', tmuxName: 't1' }],
    ['vtSplitOpenSession', 'bottom', { sessionId: null, tmuxName: 't1' }],
  ]);
});

// ── 상태 구역형(실행 중 · 최근 종료 · 보관함) ──────────────────────────────

test('splitRunning — 개입 필요에 올라간 행은 실행 중에서 빠진다(중복 없음)', async () => {
  const { buildRailLists, splitRunning } = await mod();
  const rows = [row({ name: 'a', status: 'idle' }), row({ name: 'b', status: 'waiting' }), row({ name: 'c', status: 'working' })];
  const lists = buildRailLists(rows, PREFS);
  assert.deepStrictEqual(names(splitRunning(lists)), ['a', 'c']);
  assert.deepStrictEqual(names(lists.attention), ['b']);
});

test('splitRunning — 개입 필요 구역을 끄면 waiting도 실행 중에 남는다', async () => {
  const { buildRailLists, splitRunning } = await mod();
  const lists = buildRailLists([row({ name: 'a', status: 'waiting' })], { ...PREFS, attentionShow: false });
  assert.deepStrictEqual(names(splitRunning(lists)), ['a']);
});

test('parseRailView — 접힘 열쇠(저장소·종료 N)만 읽는다, 옛 값(byRepo·recent 등)은 무시, 200개까지', async () => {
  const { parseRailView, encodeRailView, toggleGroupCollapsed } = await mod();
  const old = parseRailView(JSON.stringify({ byRepo: true, recent: 5, recentCollapsed: true }));
  assert.deepStrictEqual(Object.keys(old), ['groupsCollapsed', 'sort'], '저장소별로 묶기·최근 종료 설정은 없앴다');
  assert.deepStrictEqual({ ...old.sort }, { repos: 'registered', sessions: 'created', waitingFirst: false }, '기본은 생성된 순');
  assert.deepStrictEqual({ ...parseRailView(JSON.stringify({ sort: { repos: 'name', sessions: 'bogus', waitingFirst: 'x' } })).sort },
    { repos: 'name', sessions: 'created', waitingFirst: false }, '모르는 값은 기본으로');
  assert.deepStrictEqual(Array.from(old.groupsCollapsed), []);
  assert.deepStrictEqual(Array.from(parseRailView('{').groupsCollapsed), []);
  const mixed = parseRailView(JSON.stringify({ groupsCollapsed: ['a', 1, null, '', 'b'] }));
  assert.deepStrictEqual(Array.from(mixed.groupsCollapsed), ['a', 'b']);
  const many = Array.from({ length: 250 }, (_, i) => `v${i}`);
  const enc = JSON.parse(encodeRailView({ ...old, groupsCollapsed: many }));
  assert.strictEqual(enc.groupsCollapsed.length, 200);
  assert.strictEqual(enc.groupsCollapsed[0], 'v50', '넘치면 오래된 것부터 빠진다');
  assert.deepStrictEqual(Array.from(toggleGroupCollapsed(['a'], 'b')), ['a', 'b']);
  assert.deepStrictEqual(Array.from(toggleGroupCollapsed(['a', 'b'], 'a')), ['b']);
  const same = ['a'];
  assert.strictEqual(toggleGroupCollapsed(same, ''), same, '빈 id(묶음 아님)는 무시');
});

test('rowMenuItems — 카탈로그에 이어진 행만 작업 기록을 갖고, 열려 있지 않아도 이름 변경이 된다', async () => {
  const { rowMenuItems } = await mod();
  const ids = (r, o) => Array.from(rowMenuItems(r, o), (i) => i.id);
  assert.ok(!ids({ open: false, tmuxName: 'x' }, {}).includes('record'));
  assert.ok(!ids({ open: false, tmuxName: 'x' }, {}).includes('rename'));
  const linked = ids({ open: false, tmuxName: 'x' }, { record: true });
  assert.ok(linked.includes('record') && linked.includes('rename'));
  assert.strictEqual(ids({ open: true, tmuxName: 'x' }, { record: true }).filter((i) => i === 'rename').length, 1);
});

test('originLabel — 만든 맥 이름이 있으면 「{host}에서 만듦」, 없으면 지금 문구 그대로(툴팁·메뉴 요약)', async () => {
  const { originLabel, rowSummary } = await mod();
  assert.strictEqual(originLabel('peer', 'm5-studio'), 'm5-studio에서 만듦');
  assert.strictEqual(originLabel('peer', null), '다른 맥에서 만듦');
  assert.strictEqual(originLabel('external', 'x'), '터미널에서 만든 세션');
  const r = { status: 'working', kind: null, otherClients: 0, branch: null };
  assert.strictEqual(rowSummary({ ...r, origin: 'peer', originHost: 'm5-studio' }), '작업 중 · m5-studio에서 만듦');
  assert.strictEqual(rowSummary({ ...r, origin: 'peer' }), '작업 중 · 다른 맥에서 만듦');
});
