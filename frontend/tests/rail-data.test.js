// N36 §5 — 워크트리 레일 그룹핑/문장 로직(shell/rail-data.ts). 계약은 문서
// 원문 그대로다: "그룹 순서 고정: 개입 필요(waiting·error) → 작업 중(working)
// → 유휴(idle·done). 그룹 안은 최근 활동순."
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
const row = (over) => ({ sessionId: 'x', tmuxName: 'x', name: 'x', status: 'idle', since: null, tool: null, diffFiles: null, ...over });

test('빈 입력이면 섹션이 하나도 없다', async () => {
  const { buildRailSections } = await mod();
  assert.deepStrictEqual(Array.from(buildRailSections([], NOW)), []);
});

test('그룹 순서는 고정이다: 개입 필요 → 작업 중 → 유휴', async () => {
  const { buildRailSections } = await mod();
  const sections = buildRailSections([
    row({ sessionId: 'a', status: 'idle' }),
    row({ sessionId: 'b', status: 'working' }),
    row({ sessionId: 'c', status: 'waiting' }),
  ], NOW);
  assert.deepStrictEqual(Array.from(sections, (s) => s.group), ['attention', 'working', 'idle']);
});

test('waiting과 error는 같은 그룹(개입 필요)에 모인다', async () => {
  const { buildRailSections } = await mod();
  const sections = buildRailSections([
    row({ sessionId: 'a', status: 'waiting' }),
    row({ sessionId: 'b', status: 'error' }),
  ], NOW);
  assert.strictEqual(sections.length, 1);
  assert.strictEqual(sections[0].group, 'attention');
  assert.strictEqual(sections[0].rows.length, 2);
});

// 2026-09-18(98 §6-1) — 10 §5의 "done은 유휴에 남는다"를 **뒤집었다**. 유휴
// 그룹의 이름이 「열려 있지 않음」이 되면서(§2) 완료된 세션이 그 이름에 안 맞다 —
// 완료는 열려 있는 세션이다.
test('done은 작업 중 그룹으로 간다(유휴는 이제 「열려 있지 않음」이다)', async () => {
  const { buildRailSections } = await mod();
  const sections = buildRailSections([row({ sessionId: 'a', status: 'done' })], NOW);
  assert.strictEqual(sections[0].group, 'working');
});

test('유휴 그룹 라벨은 「열려 있지 않음」이다', async () => {
  const { GROUP_LABEL } = await mod();
  assert.strictEqual(GROUP_LABEL.idle, '열려 있지 않음');
});

test('개입 필요는 접을 수 없다(접히면 그 그룹이 존재할 이유가 사라진다)', async () => {
  const { COLLAPSIBLE_GROUPS } = await mod();
  assert.deepStrictEqual(Array.from(COLLAPSIBLE_GROUPS), ['working', 'idle']);
});

// 실측 레이스(2026-09-18, 실서버): Rail이 `window.vtSettingsGet`보다 먼저
// 마운트되면 `Boolean(undefined)`가 false가 되어 「기본은 접힘」이 조용히
// 뒤집혔다. 지연 청크는 core/settings.js를 정적 import할 수 없어 기본값을
// 한 벌 더 들고 있는데, 그 두 벌이 어긋나면 같은 증상이 다시 난다.
test('레일의 그룹 기본값이 설정 스키마의 def와 같다', async () => {
  const { GROUP_COLLAPSED_DEFAULT, COLLAPSIBLE_GROUPS, groupCollapseKey, SETTINGS_SCHEMA } = await mod();
  for (const g of Array.from(COLLAPSIBLE_GROUPS)) {
    const spec = SETTINGS_SCHEMA[groupCollapseKey(g)];
    assert.ok(spec, `${g} 그룹의 설정 키가 스키마에 없다`);
    assert.strictEqual(spec.scope, 'device');
    assert.strictEqual(GROUP_COLLAPSED_DEFAULT[g], spec.def);
  }
  assert.strictEqual(GROUP_COLLAPSED_DEFAULT.idle, true);
});

test('색 배정 키는 소유자다 — 같은 조직의 저장소는 같은 색', async () => {
  const { repoColorKey, hashRepoColorIndex } = await mod();
  const a = repoColorKey('rapa-ai', { host: 'github', owner: 'fornerds', name: 'rapa-ai' });
  const b = repoColorKey('namdongrun', { host: 'github', owner: 'fornerds', name: 'namdongrun' });
  assert.strictEqual(a, b);
  assert.strictEqual(hashRepoColorIndex(a), hashRepoColorIndex(b));
});

test('remote가 없으면 예전처럼 이름 해시로 떨어진다', async () => {
  const { repoColorKey, hashRepoColorIndex } = await mod();
  assert.strictEqual(repoColorKey('solo', null), 'solo');
  assert.strictEqual(hashRepoColorIndex(repoColorKey('solo', null)), hashRepoColorIndex('solo'));
});

test('둘째 줄 라벨은 host/owner — remote가 없으면 빈 문자열', async () => {
  const { remoteLabel } = await mod();
  assert.strictEqual(remoteLabel({ host: 'github', owner: 'fornerds', name: 'x' }), 'github/fornerds');
  assert.strictEqual(remoteLabel(null), '');
  assert.strictEqual(remoteLabel({ host: '', owner: '', name: '' }), '');
});

test('없는 그룹은 아예 안 나온다(빈 섹션 헤더를 그리지 않는다)', async () => {
  const { buildRailSections } = await mod();
  const sections = buildRailSections([row({ sessionId: 'a', status: 'working' })], NOW);
  assert.deepStrictEqual(Array.from(sections, (s) => s.group), ['working']);
});

test('그룹 안은 최근 활동순(since 내림차순) — since 없는 건 맨 뒤', async () => {
  const { buildRailSections } = await mod();
  const sections = buildRailSections([
    row({ sessionId: 'old', status: 'idle', since: NOW / 1000 - 3600 }),
    row({ sessionId: 'none', status: 'idle', since: null }),
    row({ sessionId: 'new', status: 'idle', since: NOW / 1000 - 60 }),
  ], NOW);
  assert.deepStrictEqual(Array.from(sections[0].rows, (r) => r.sessionId), ['new', 'old', 'none']);
});

test('formatRelativeTime — 초/분/시간/일 경계', async () => {
  const { formatRelativeTime } = await mod();
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 30, NOW), '30초');
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 120, NOW), '2분');
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 7200, NOW), '2시간');
  assert.strictEqual(formatRelativeTime(NOW / 1000 - 172800, NOW), '2일');
  assert.strictEqual(formatRelativeTime(null, NOW), '');
});

test('statusSentence — waiting/working/error/done/idle', async () => {
  const { statusSentence } = await mod();
  const since = NOW / 1000 - 120;
  assert.strictEqual(statusSentence('waiting', since, null, NOW), '승인 대기 · 2분');
  assert.strictEqual(statusSentence('waiting', null, null, NOW), '승인 대기');
  assert.strictEqual(statusSentence('working', since, 'Edit', NOW), 'Edit 실행 · 2분');
  assert.strictEqual(statusSentence('working', since, null, NOW), '작업 중 · 2분');
  assert.strictEqual(statusSentence('error', since, null, NOW), '에러');
  assert.strictEqual(statusSentence('done', since, null, NOW), '완료');
  // idle은 그룹 헤더가 이미 「유휴」라고 말하므로 행에서는 반복하지 않는다.
  // 남기는 것은 그룹 헤더가 말해주지 않는 "마지막 활동 시각"뿐이다.
  assert.strictEqual(statusSentence('idle', since, null, NOW), '2분');
  assert.strictEqual(statusSentence('idle', null, null, NOW), '');
});

// N8/N44(30-worktree.md §4) — "상태는 sessions 중 가장 '시급한' 것(waiting >
// error > working > done > idle)". 워크트리 행이 여러 세션을 대표할 때 쓴다.
test('mostUrgentStatus — waiting > error > working > done > idle, 세션 없으면 idle', async () => {
  const { mostUrgentStatus } = await mod();
  assert.strictEqual(mostUrgentStatus([]), 'idle');
  assert.strictEqual(mostUrgentStatus(['idle']), 'idle');
  assert.strictEqual(mostUrgentStatus(['idle', 'done']), 'done');
  assert.strictEqual(mostUrgentStatus(['done', 'working']), 'working');
  assert.strictEqual(mostUrgentStatus(['working', 'error']), 'error');
  assert.strictEqual(mostUrgentStatus(['error', 'waiting']), 'waiting');
  assert.strictEqual(mostUrgentStatus(['waiting', 'idle', 'working', 'error', 'done']), 'waiting');
});

// buildRailSections는 제네릭이라(status/since/tool만 요구) Rail.tsx의 워크트리
// 행 모양(kind:'worktree', sessionId 없음)도 그대로 그룹핑한다 — Fleet.tsx의
// 세션 행 계약(RailRowInput)을 바꾸지 않고도 두 모양을 같은 함수로 처리한다는
// 게 이 설계의 핵심이다.
test('buildRailSections — 세션 필드가 없는 워크트리 모양도 그룹핑한다(제네릭)', async () => {
  const { buildRailSections } = await mod();
  const wtRow = (over) => ({ kind: 'worktree', worktreeId: 'w1', label: 'farshell', status: 'idle', since: null, tool: null, changed: null, ...over });
  const sections = buildRailSections([
    wtRow({ worktreeId: 'a', status: 'waiting', since: NOW / 1000 - 60 }),
    wtRow({ worktreeId: 'b', status: 'working', since: NOW / 1000 - 30 }),
  ], NOW);
  assert.deepStrictEqual(Array.from(sections, (s) => s.group), ['attention', 'working']);
  assert.strictEqual(sections[0].rows[0].worktreeId, 'a');
  assert.strictEqual(sections[0].rows[0].statusSentence, '승인 대기 · 1분');
});

// 20-design-system.md §5(O2) — 색점 램프. fnv1a는 순수 함수라 고정 입력에
// 고정 출력이 나와야 한다(§5 원문 "테스트: 고정 입력 → 고정 인덱스"). 기대값은
// 표준 FNV-1a 32비트(offset basis 2166136261 / prime 16777619)를 독립적으로
// (node의 python3 참조 구현) 계산해 박아 뒀다 — 구현이 이 표준과 어긋나면 실패.
test('fnv1a — 고정 입력에 고정 32비트 해시가 나온다(FNV-1a 표준값)', async () => {
  const { fnv1a } = await mod();
  assert.strictEqual(fnv1a('farshell'), 1331226144);
  assert.strictEqual(fnv1a('dotfiles'), 2503752351);
  assert.strictEqual(fnv1a('embed-lab'), 527131960);
  assert.strictEqual(fnv1a('notes'), 2206293706);
  assert.strictEqual(fnv1a(''), 2166136261); // 빈 문자열 = offset basis 그대로
});

test('hashRepoColorIndex — fnv1a % 8, 항상 0~7', async () => {
  const { hashRepoColorIndex } = await mod();
  assert.strictEqual(hashRepoColorIndex('farshell'), 0);
  assert.strictEqual(hashRepoColorIndex('dotfiles'), 7);
  assert.strictEqual(hashRepoColorIndex('embed-lab'), 0);
  assert.strictEqual(hashRepoColorIndex('notes'), 2);
  for (const name of ['a', 'ab', 'abc', 'farshell-2', 'x'.repeat(200)]) {
    const idx = hashRepoColorIndex(name);
    assert.ok(Number.isInteger(idx) && idx >= 0 && idx < 8, `${name} → ${idx}`);
  }
});

// §5 원문: "같은 저장소의 워크트리들은 같은 색. 호스트가 달라도 저장소가
// 같으면 같은 색." — 입력은 repoName 문자열뿐이므로 호출을 반복해도(다른
// 워크트리·다른 브랜치·다른 머신을 흉내 내도) 항상 같은 인덱스가 나와야 한다.
test('hashRepoColorIndex — 같은 저장소 이름은 몇 번을 불러도(=다른 워크트리·호스트를 대표해도) 같은 색', async () => {
  const { hashRepoColorIndex } = await mod();
  const a1 = hashRepoColorIndex('farshell');
  const a2 = hashRepoColorIndex('farshell'); // 다른 워크트리(예: feat/rail-redesign)를 흉내
  const a3 = hashRepoColorIndex('farshell'); // 다른 호스트를 흉내(입력에 호스트명이 안 섞인다)
  assert.strictEqual(a1, a2);
  assert.strictEqual(a2, a3);

  const b = hashRepoColorIndex('dotfiles');
  assert.notStrictEqual(a1, b); // 다른 저장소는 (8색 한도 내에서) 다른 인덱스
});

// ── 레일 접힘 기본값 ───────────────────────────────────────────────────────
// 저장값이 있으면 그게 이긴다(그 분기는 Rail.tsx에 있다) — 여기서 잠그는 건
// "아직 아무 말도 안 한 기기"에서 티어가 무엇을 고르는지다.

test('defaultRailCollapsed — wide 미만은 접고, wide 이상은 편다', async () => {
  const { defaultRailCollapsed } = await mod();
  const REGULAR_MAX = 1280;
  assert.strictEqual(defaultRailCollapsed(760, REGULAR_MAX), true, 'regular 하단은 접힘');
  assert.strictEqual(defaultRailCollapsed(1279, REGULAR_MAX), true, '경계 바로 아래는 접힘');
  assert.strictEqual(defaultRailCollapsed(1280, REGULAR_MAX), false, '경계는 펼침');
  assert.strictEqual(defaultRailCollapsed(1733, REGULAR_MAX), false, 'xwide는 펼침');
});
