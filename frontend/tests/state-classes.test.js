// design/state-classes.js — 상태 라벨·정규화·대표 상태(탭·레일·Fleet·파비콘이 같이 쓴다),
// 그리고 ui/icons.js의 stateIcon(4상태 SVG, 이모지 금지).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const SC_JS = path.join(__dirname, '../js/design/state-classes.js');
const ICONS_JS = path.join(__dirname, '../js/ui/icons.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(file) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(file, env.context, new Map());
}

test('라벨 4종 — waiting은 어디서나 「입력 대기」, error는 없다', async () => {
  const { STATE_LABEL, STATES } = await load(SC_JS);
  assert.deepStrictEqual(Array.from(STATES), ['idle', 'working', 'waiting', 'done']);
  assert.deepStrictEqual({ ...STATE_LABEL }, { idle: '유휴', working: '작업 중', waiting: '입력 대기', done: '완료' });
});

test('normalizeState — 알 수 없는 값은 전부 idle', async () => {
  const { normalizeState, stateLabel } = await load(SC_JS);
  for (const v of ['error', 'bogus', '', null, undefined, 42, 'toString', '__proto__']) {
    assert.strictEqual(normalizeState(v), 'idle', String(v));
  }
  for (const v of ['idle', 'working', 'waiting', 'done']) assert.strictEqual(normalizeState(v), v);
  assert.strictEqual(stateLabel('error'), '유휴');
});

test('stateRank — waiting > done(읽지 않음) > working > done(확인함) = idle', async () => {
  const { stateRank } = await load(SC_JS);
  assert.ok(stateRank('waiting') < stateRank('done', true));
  assert.ok(stateRank('done', true) < stateRank('working'));
  assert.ok(stateRank('working') < stateRank('done', false));
  assert.strictEqual(stateRank('done', false), stateRank('idle'));
  assert.strictEqual(stateRank('error', true), stateRank('idle'));
});

test('representativeState — 모든 상태 조합(읽지 않음 포함)에서 순위대로 이긴다', async () => {
  const { representativeState, stateRank } = await load(SC_JS);
  const kinds = [
    { status: 'idle', unseen: false }, { status: 'working', unseen: false },
    { status: 'waiting', unseen: false }, { status: 'done', unseen: false },
    { status: 'done', unseen: true }, { status: 'bogus', unseen: false },
  ];
  for (const a of kinds) for (const b of kinds) {
    const r = representativeState([a, b]);
    const best = stateRank(a.status, a.unseen) <= stateRank(b.status, b.unseen) ? a : b;
    const expectRank = Math.min(stateRank(a.status, a.unseen), stateRank(b.status, b.unseen));
    assert.strictEqual(stateRank(r.status, r.unseen), expectRank, `${a.status}/${a.unseen} vs ${b.status}/${b.unseen}`);
    assert.ok(['idle', 'working', 'waiting', 'done'].includes(r.status));
    void best;
  }
});

test('representativeState — 구체 사례', async () => {
  const { representativeState: rep } = await load(SC_JS);
  const eq = (got, status, unseen) => assert.deepStrictEqual({ ...got }, { status, unseen });
  eq(rep([]), 'idle', false);
  eq(rep(undefined), 'idle', false);
  eq(rep([{ status: 'idle' }, { status: 'working' }]), 'working', false);
  eq(rep([{ status: 'working' }, { status: 'done', unseen: true }]), 'done', true);
  eq(rep([{ status: 'working' }, { status: 'done', unseen: false }]), 'working', false);
  eq(rep([{ status: 'done', unseen: false }, { status: 'idle' }]), 'idle', false);   // 확인함 done = idle
  eq(rep([{ status: 'done', unseen: true }, { status: 'waiting' }]), 'waiting', false);
  eq(rep([{ status: 'working', unseen: true }, { status: 'working' }]), 'working', true);
  eq(rep([{ status: 'idle', unseen: true }]), 'idle', true);
  eq(rep([{ status: 'error' }]), 'idle', false);
});

test('URGENCY — error 없이 서버 순서(waiting < done < working < idle)', async () => {
  const { URGENCY, entryUrgency } = await load(SC_JS);
  assert.deepStrictEqual({ ...URGENCY }, { waiting: 0, done: 1, working: 2, idle: 4 });
  assert.strictEqual(entryUrgency('error'), URGENCY.idle);
});

test('stateIcon — 4상태는 서로 다른 SVG이고 라벨·링을 단다, 이모지·유니코드 체크가 없다', async () => {
  const { stateIcon } = await load(ICONS_JS);
  const svgs = ['idle', 'working', 'waiting', 'done'].map((s) => stateIcon(s, 14, false));
  assert.strictEqual(new Set(svgs.map((h) => h.replace(/data-state="\w+"|aria-label="[^"]*"/g, ''))).size, 4, '모양이 서로 달라야 한다');
  assert.match(svgs[2], /aria-label="입력 대기"/);
  assert.match(svgs[0], /data-state="idle"/);
  assert.match(svgs[1], /stroke-width="1\.75"/);
  const ringed = stateIcon('waiting', 14, true);
  assert.match(ringed, /class="vt-state-icon unseen"/);
  assert.match(ringed, /아직 확인 안 함/);
  assert.match(stateIcon('error', 14), /data-state="idle"/);
  for (const html of [...svgs, ringed]) {
    assert.doesNotMatch(html, /[✓✔✅❓❗]|[\u{1F300}-\u{1FAFF}]/u);
  }
});

test('viewSegments — 세션 수·순서·빈 칸 제외·unseen 굵기', async () => {
  const { viewSegments } = await load(SC_JS);
  assert.deepStrictEqual(viewSegments([]).length, 0);
  assert.strictEqual(viewSegments(null).length, 0);
  // 단독 뷰도 세그먼트 1개
  const one = viewSegments([{ name: 'api', status: 'idle' }]);
  assert.strictEqual(one.length, 1);
  assert.strictEqual(one[0].thick, false);
  // 칸 순서 유지 + 빈 칸(이름 없음)·null 은 세지 않는다
  const segs = viewSegments([
    { name: 'api', status: 'working', unseen: false },
    { name: '', status: 'waiting', unseen: true },
    null,
    { name: 'web', status: 'waiting', unseen: true },
    { name: 'db', status: 'done', unseen: false },
    { name: 'wk', status: 'idle', unseen: true },
  ]);
  assert.deepStrictEqual(Array.from(segs.map((s) => s.name)), ['api', 'web', 'db', 'wk']);
  assert.deepStrictEqual(Array.from(segs.map((s) => s.status)), ['working', 'waiting', 'done', 'idle']);
  assert.deepStrictEqual(Array.from(segs.map((s) => s.thick)), [false, true, false, true]);
});

test('viewSegments — 알 수 없는 상태(error 등)는 idle 로 접는다', async () => {
  const { viewSegments } = await load(SC_JS);
  const s = viewSegments([{ name: 'a', status: 'error' }, { name: 'b', status: undefined }]);
  assert.deepStrictEqual(Array.from(s.map((x) => x.status)), ['idle', 'idle']);
  assert.deepStrictEqual(Array.from(s.map((x) => x.label)), ['유휴', '유휴']);
});

test('segmentsTooltip / segmentsAria — 세션마다 이름·상태, 읽지 않음이면 「확인 안 함」', async () => {
  const { viewSegments, segmentsTooltip, segmentsAria } = await load(SC_JS);
  const segs = viewSegments([
    { name: 'api', status: 'working' },
    { name: 'web', status: 'waiting', unseen: true },
  ]);
  assert.strictEqual(segmentsTooltip(segs), 'api · 작업 중\nweb · 입력 대기 · 확인 안 함');
  assert.strictEqual(segmentsAria(segs), 'api, 작업 중 · web, 입력 대기(확인 안 함)');
});
