// 레일 계보 트리·정렬(shell/rail-data.ts) — 에이전트 위임 3단계, 방향 A 「계보선」.
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

const row = (name, lineage) => ({ name, tmuxName: name, lineage: lineage || null });
const lin = (parent, state = 'running', createdAt = 1, extra = {}) =>
  ({ delegation: `d-${parent}-${createdAt}`, parent, state, outcome: null, createdAt, task: '', summary: '', ...extra });
const names = (rows) => Array.from(rows, (r) => r.name);

test('normalizeLineage — 서버 값을 받아들이고 모르는 상태는 진행 중, id 없으면 null', async () => {
  const { normalizeLineage } = await mod();
  assert.strictEqual(normalizeLineage(null), null);
  assert.strictEqual(normalizeLineage({ parent: 'a' }), null);
  const l = normalizeLineage({ delegation: 'x', parent: '', state: 'weird', outcome: 'maybe', createdAt: '5' });
  assert.deepStrictEqual([l.parent, l.state, l.outcome, l.createdAt], [null, 'running', null, 5]);
});

test('kidsProgress · lineageLabel', async () => {
  const { kidsProgress, lineageLabel } = await mod();
  assert.strictEqual(kidsProgress([]), null);
  const p = kidsProgress([row('a', lin('m', 'reported')), row('b', lin('m'))]);
  assert.deepStrictEqual([p.reported, p.open], [1, 2]);   // vm 컨텍스트 객체라 구조만 비교
  assert.strictEqual(lineageLabel({ state: 'reported', outcome: 'failed' }), '보고 · 실패');
  assert.strictEqual(lineageLabel({ state: 'reported', outcome: 'succeeded' }), '보고 · 성공');
  assert.strictEqual(lineageLabel({ state: 'closed', outcome: null }), '닫힘');
});

test('parseNameList — 깨진 값은 빈 목록', async () => {
  const { parseNameList } = await mod();
  assert.deepStrictEqual(Array.from(parseNameList('["a",1,"b"]')), ['a', 'b']);
  assert.deepStrictEqual(Array.from(parseNameList('{bad')), []);
  assert.deepStrictEqual(Array.from(parseNameList(undefined)), []);
});

test('rowMenuItems — 하위 세션 메뉴(이 맥에서만), 수동 정렬 이동은 없다', async () => {
  const { rowMenuItems } = await mod();
  const kid = { open: false, tmuxName: 'k', lineage: lin('main', 'reported', 1, { summary: '결과' }) };
  const ids = (items) => Array.from(items, (i) => i.id);
  assert.ok(['report', 'openParent', 'closeDelegation'].every((id) => ids(rowMenuItems(kid, { share: true })).includes(id)));
  assert.ok(!ids(rowMenuItems(kid, { share: false })).includes('closeDelegation'));
  const closed = { ...kid, lineage: lin('main', 'closed') };
  assert.ok(!ids(rowMenuItems(closed, { share: true })).includes('closeDelegation'));
  const top = ids(rowMenuItems({ open: false, tmuxName: 'x' }, {}));
  assert.ok(!top.includes('moveDown') && !top.includes('moveUp'), '목록은 저장소·최근 활동순이라 손으로 옮기지 않는다');
});

test('설정 — 위임 접기는 기기별 키 하나(JSON), 정렬 값은 더 읽지 않는다', async () => {
  const { SETTINGS_SCHEMA, LINEAGE_KEY, encodeLineagePrefs, readRailPrefs } = await mod();
  assert.strictEqual(SETTINGS_SCHEMA[LINEAGE_KEY].scope, 'device');
  const p = readRailPrefs((k) => (k === LINEAGE_KEY ? encodeLineagePrefs(['main']) : '{"sort":"manual","order":["b"]}'));
  assert.deepStrictEqual(Array.from(p.kidsCollapsed), ['main']);
  assert.strictEqual(p.sort, undefined);
  assert.deepStrictEqual(Array.from(readRailPrefs(() => undefined).kidsCollapsed), []);
});
