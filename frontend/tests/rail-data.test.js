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

test('done은 유휴 그룹에 남는다(완료 자체는 급하지 않다 — 문서 원문)', async () => {
  const { buildRailSections } = await mod();
  const sections = buildRailSections([row({ sessionId: 'a', status: 'done' })], NOW);
  assert.strictEqual(sections[0].group, 'idle');
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
  assert.strictEqual(statusSentence('idle', since, null, NOW), '유휴 · 2분');
  assert.strictEqual(statusSentence('idle', null, null, NOW), '유휴');
});
