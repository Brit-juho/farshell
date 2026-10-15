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

test('buildSessionTree — 자식은 이름순과 상관없이 부모 밑에, 만든 순서로 선다', async () => {
  const { buildSessionTree } = await mod();
  const t = buildSessionTree([
    row('zeta'), row('main'), row('aaa-kid2', lin('main', 'running', 2)), row('zzz-kid1', lin('main', 'reported', 1)),
  ]);
  assert.deepStrictEqual(names(t.map((e) => e.row)), ['main', 'zeta']);
  assert.deepStrictEqual(names(t[0].kids), ['zzz-kid1', 'aaa-kid2']);
});

test('buildSessionTree — 닫힌 하위는 따로, 부모가 없거나 고아면 최상위', async () => {
  const { buildSessionTree } = await mod();
  const t = buildSessionTree([
    row('main'), row('k-open', lin('main')), row('k-closed', lin('main', 'closed')),
    row('lost', lin('gone')), row('orph', lin('main', 'orphaned')),
  ]);
  const main = t.find((e) => e.row.name === 'main');
  assert.deepStrictEqual(names(main.kids), ['k-open']);
  assert.deepStrictEqual(names(main.closedKids), ['k-closed']);
  assert.deepStrictEqual(names(t.map((e) => e.row)), ['lost', 'main', 'orph']);
});

test('buildSessionTree — 수동 정렬은 저장된 순서, 모르는 이름은 뒤에 이름순', async () => {
  const { buildSessionTree } = await mod();
  const t = buildSessionTree([row('a'), row('b'), row('c'), row('new2'), row('new1')], 'manual', ['c', 'a', 'b']);
  assert.deepStrictEqual(names(t.map((e) => e.row)), ['c', 'a', 'b', 'new1', 'new2']);
});

test('buildSessionTree — 부모가 다시 자식이거나 자기 자신을 가리키면 평평하게', async () => {
  const { buildSessionTree } = await mod();
  const t = buildSessionTree([row('p', lin('q')), row('q'), row('self', lin('self')), row('c', lin('p'))]);
  // c의 부모 p는 그 자신이 자식이라 c는 최상위로(깊이 1)
  assert.ok(t.some((e) => e.row.name === 'c'));
  assert.ok(t.some((e) => e.row.name === 'self'));
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

test('moveInOrder · stepInOrder', async () => {
  const { moveInOrder, stepInOrder } = await mod();
  assert.deepStrictEqual(Array.from(moveInOrder(['a', 'b', 'c'], 'c', 'a', 'before')), ['c', 'a', 'b']);
  assert.deepStrictEqual(Array.from(moveInOrder(['a', 'b', 'c'], 'a', 'c', 'after')), ['b', 'c', 'a']);
  assert.deepStrictEqual(Array.from(moveInOrder(['a', 'b'], 'a', 'zz', 'after')), ['a', 'b']);
  assert.deepStrictEqual(Array.from(stepInOrder(['a', 'b', 'c'], 'b', -1)), ['b', 'a', 'c']);
  assert.deepStrictEqual(Array.from(stepInOrder(['a', 'b'], 'a', -1)), ['a', 'b']);
});

test('parseNameList — 깨진 값은 빈 목록', async () => {
  const { parseNameList } = await mod();
  assert.deepStrictEqual(Array.from(parseNameList('["a",1,"b"]')), ['a', 'b']);
  assert.deepStrictEqual(Array.from(parseNameList('{bad')), []);
  assert.deepStrictEqual(Array.from(parseNameList(undefined)), []);
});

test('rowMenuItems — 하위 세션 메뉴(이 맥에서만)와 수동 정렬 이동', async () => {
  const { rowMenuItems } = await mod();
  const kid = { open: false, tmuxName: 'k', lineage: lin('main', 'reported', 1, { summary: '결과' }) };
  const ids = (items) => Array.from(items, (i) => i.id);
  assert.ok(['report', 'openParent', 'closeDelegation'].every((id) => ids(rowMenuItems(kid, { share: true })).includes(id)));
  assert.ok(!ids(rowMenuItems(kid, { share: false })).includes('closeDelegation'));
  const closed = { ...kid, lineage: lin('main', 'closed') };
  assert.ok(!ids(rowMenuItems(closed, { share: true })).includes('closeDelegation'));
  const top = ids(rowMenuItems({ open: false, tmuxName: 'x' }, { move: { up: false, down: true } }));
  assert.ok(top.includes('moveDown') && !top.includes('moveUp'));
});

test('설정 — 정렬은 전역 키 하나, 접기는 기기별 키 하나(JSON)', async () => {
  const { SETTINGS_SCHEMA, ORDER_KEY, LINEAGE_KEY, encodeOrder, encodeLineagePrefs, readRailPrefs } = await mod();
  assert.strictEqual(SETTINGS_SCHEMA[ORDER_KEY].scope, undefined);
  assert.strictEqual(SETTINGS_SCHEMA[LINEAGE_KEY].scope, 'device');
  const vals = { [ORDER_KEY]: encodeOrder('manual', ['b', 'a']), [LINEAGE_KEY]: encodeLineagePrefs(['main'], []) };
  const p = readRailPrefs((k) => vals[k]);
  assert.deepStrictEqual([p.sort, Array.from(p.order), Array.from(p.kidsCollapsed)], ['manual', ['b', 'a'], ['main']]);
  const empty = readRailPrefs(() => undefined);
  assert.deepStrictEqual([empty.sort, empty.order.length], ['name', 0]);
  assert.strictEqual(readRailPrefs((k) => (k === ORDER_KEY ? '{broken' : undefined)).sort, 'name');
});
