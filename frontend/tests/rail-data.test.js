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
  origin: 'local', viewLabel: null, otherScreens: 0, ...over,
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
    row({ name: 'agent', origin: 'agent' }),
    row({ name: 'mine', origin: 'local' }),
  ], prefs);
  assert.deepStrictEqual(names(sessions), ['agent', 'ext-open', 'ext-wait', 'mine']);
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
  // 배지는 done에서만 — 다른 상태에 남아 있어도 안 보인다.
  assert.strictEqual(stateKeyword({ status: 'idle', kind: null, badge: 'plan', tool: null }).badge, null);
  assert.strictEqual(stateKeyword({ status: 'idle', kind: null, badge: null, tool: null }).text, '유휴');
});

test('statusSentence — 키워드 · 도구 · 배지 · 경과를 한 줄로 잇는다', async () => {
  const { statusSentence } = await mod();
  const since = NOW / 1000 - 120;
  assert.strictEqual(statusSentence({ status: 'waiting', kind: 'approval', badge: null, tool: null, since }, NOW), '승인 요청 · 2분');
  assert.strictEqual(statusSentence({ status: 'working', kind: null, badge: null, tool: 'Bash', since }, NOW), '작업 중 · Bash · 2분');
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
  assert.strictEqual(normalizeOrigin('agent'), 'agent');
  assert.strictEqual(normalizeOrigin('external'), 'external');
  assert.deepStrictEqual(Array.from(HIDEABLE_ORIGINS), ['peer', 'agent', 'external'], 'local은 끌 수 없다');
});

test('worktreeIconName / diffLabel', async () => {
  const { worktreeIconName, diffLabel } = await mod();
  assert.strictEqual(worktreeIconName({ isMainWorktree: true }), 'git-commit');
  assert.strictEqual(worktreeIconName({ isMainWorktree: false }), 'git-branch');
  assert.strictEqual(diffLabel({ changed: { files: 2, add: 14, del: 3 }, diffFiles: 2 }), '+14 −3');
  assert.strictEqual(diffLabel({ changed: null, diffFiles: 3 }), '파일 3');
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
  const store = { 'rail.attention.show': false, 'rail.attention.collapsed': true, 'rail.origin.agent.show': false };
  const p = readRailPrefs((k) => store[k]);
  assert.strictEqual(p.attentionShow, false);
  assert.strictEqual(p.attentionCollapsed, true);
  assert.deepStrictEqual(Array.from(p.hiddenOrigins), ['agent']);
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

test('collectSessionRows — 브랜치는 main도 담고, origin·열림·묶음 이름·다른 화면을 채운다', async () => {
  const { collectSessionRows, window } = await mod();
  window.allSessions = () => ({ w1: { tmuxName: 'api' }, w2: { tmuxName: 'web' } });
  window.getStatus = (n) => (n === 'api' ? 'waiting' : 'idle');
  window.isUnseen = (n) => n === 'api';
  window.agentDetails = () => ({ api: { since: 5, tool: null, kind: 'choice', badge: null, question: 'q?', options: null, promptId: null } });
  window.agentNames = () => ({ api: 'claude' });
  const rows = collectSessionRows({
    tmux: [
      { name: 'api', cwd: '/r', wt_id: 'wt-main', attached: 3, origin: 'agent' },
      { name: 'web', cwd: '/r', wt_id: 'wt-b', attached: 1, origin: 'peer' },
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
  assert.strictEqual(by.api.origin, 'agent');
  assert.strictEqual(by.web.origin, 'peer');
  assert.strictEqual(by.cli.origin, 'local', 'origin 필드가 없으면 local');
  assert.strictEqual(by.api.open, true);
  assert.strictEqual(by.cli.open, false);
  assert.strictEqual(by.api.viewLabel, 'api+web');
  assert.strictEqual(by.cli.viewLabel, null);
  assert.strictEqual(by.api.otherScreens, 2, '붙은 3 중 이 브라우저 탭 PTY 1개를 뺀다');
  assert.strictEqual(by.web.otherScreens, 0);
  assert.strictEqual(by.cli.otherScreens, 0);
  assert.strictEqual(by.api.agent, 'claude');
});
