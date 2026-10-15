// 감사 타임라인(authz/audit.js): 체인 배지, 필터, 거부 기록 토글, 페이징, 내용 비표시. + 순수 규칙(data.js).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const NOW = Math.floor(Date.now() / 1000);

const entry = (i, over = {}) => ({
  seq: i, ts: NOW - i * 60, source: 'chain', principal: 'human:alice', action: 'authz.decide', resource: 'session:local/$1:1',
  scopes: ['session.read'], decision: 'allow', result: 'allowed', reason: '', grant_id: `ag-${i}`, approval_id: `r${i}`, sizes: { n: 12 }, sha8: 'abcd1234', detail: 'DETAIL-SECRET', ...over,
});

async function mount(res = { entries: [entry(1), entry(2)], chain: 'ok', lines: 2, brokenSeq: null }, routes = {}) {
  const t = await loadShare({ 'GET /api/authz/audit': () => ({ json: res }), ...routes }, { files: ['authz/audit.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  t.mod.mountAudit(host);
  const urls = () => t.calls.filter((c) => c.method === 'GET').map((c) => c.url);
  return { ...t, host, urls, rows: () => [...host.querySelectorAll('.vt-authz-audit-row')], chain: () => host.querySelector('.vt-authz-chain') };
}

test('최근 순 목록: 시각·행위·결과·주체→리소스·scope 가 보인다', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 2);
  assert.match(t.rows()[0].textContent, /authz\.decide/);
  assert.match(t.rows()[0].textContent, /allowed/);
  assert.match(t.rows()[0].textContent, /human:alice → session:local\/\$1:1/);
  assert.match(t.rows()[0].textContent, /session\.read/);
});

test('체인 배지: ok 는 중립(경고 아님), broken 은 경고(role=alert, 끊긴 번호), 모르면 중립', async () => {
  const ok = await mount();
  await waitFor(() => ok.chain().dataset.state === 'ok');
  assert.match(ok.chain().textContent, /기록 무결성 확인됨/);
  assert.strictEqual(ok.chain().getAttribute('role'), 'status');
  const bad = await mount({ entries: [entry(1)], chain: 'broken', lines: 9, brokenSeq: 5 });
  await waitFor(() => bad.chain().dataset.state === 'broken');
  assert.match(bad.chain().textContent, /기록 체인이 끊겼습니다 \(#5 부근\)/);
  assert.strictEqual(bad.chain().getAttribute('role'), 'alert');
  const unk = await mount({ entries: [], chain: 'weird' });
  await waitFor(() => unk.chain().dataset.state === 'unknown');
  assert.doesNotMatch(unk.chain().textContent, /끊겼/);
});

test('내용은 어디에도 그리지 않는다 — 허용 목록 밖 필드(detail·text·sha8 …)는 있어도 보이지 않는다', async () => {
  const t = await mount({ entries: [entry(1, { text: 'TYPED-SECRET', content: 'SCREEN-SECRET', message: 'MSG-SECRET', input: 'cmd' })], chain: 'ok' });
  await waitFor(() => t.rows().length === 1);
  const all = t.host.textContent;
  for (const s of ['DETAIL-SECRET', 'TYPED-SECRET', 'SCREEN-SECRET', 'MSG-SECRET', 'abcd1234']) assert.ok(!all.includes(s), s);
});

test('필터: 주체·리소스(앞부분)·기간이 서버 쿼리로 간다(입력은 디바운스), 항상 limit=500', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 2);
  const type = (sel, v, ev = 'input') => { const el = t.host.querySelector(sel); el.value = v; el.dispatchEvent(new t.window.Event(ev, { bubbles: true })); };
  type('[data-f="principal"]', 'agent:');
  type('[data-f="resource"]', 'session:');
  await tick(20);
  assert.strictEqual(t.urls().length, 1);
  await waitFor(() => t.urls().length === 2);
  assert.match(t.urls()[1], /principal=agent%3A/);
  assert.match(t.urls()[1], /resource=session%3A/);
  assert.match(t.urls()[1], /limit=500/);
  type('[data-f="range"]', '1h', 'change');
  await waitFor(() => t.urls().length === 3);
  const since = Number(new URL(t.urls()[2], 'http://x').searchParams.get('since'));
  assert.ok(Math.abs(since - (NOW - 3600)) < 5, `since=${since}`);
  type('[data-f="range"]', 'all', 'change');
  await waitFor(() => t.urls().length === 4);
  assert.ok(!/since=/.test(t.urls()[3]), '전체 기간은 since 를 보내지 않는다');
});

test('거부 기록 토글: 끄면 denials 출처 행이 빠지고, 다시 읽지 않는다', async () => {
  const t = await mount({ entries: [entry(1), entry(2, { source: 'denials', action: 'auth.fail', result: 'denied' })], chain: 'ok' });
  await waitFor(() => t.rows().length === 2);
  assert.ok(t.host.querySelector('[data-source="denials"] .vt-authz-flag'), '거부 기록 표시');
  const cb = t.host.querySelector('[data-f="denials"]');
  cb.checked = false;
  cb.dispatchEvent(new t.window.Event('change', { bubbles: true }));
  assert.strictEqual(t.rows().length, 1);
  assert.strictEqual(t.host.querySelector('[data-source="denials"]'), null);
  assert.strictEqual(t.urls().length, 1);
  cb.checked = true;
  cb.dispatchEvent(new t.window.Event('change', { bubbles: true }));
  assert.strictEqual(t.rows().length, 2);
});

test('페이징: 50건씩 「더 보기」, 남은 건수를 말하고 끝나면 버튼이 사라진다', async () => {
  const many = Array.from({ length: 120 }, (_, i) => entry(i + 1));
  const t = await mount({ entries: many, chain: 'ok' });
  await waitFor(() => t.rows().length === 50);
  assert.match(t.host.querySelector('.vt-authz-more').textContent, /70건 남음/);
  t.host.querySelector('.vt-authz-more').click();
  assert.strictEqual(t.rows().length, 100);
  t.host.querySelector('.vt-authz-more').click();
  assert.strictEqual(t.rows().length, 120);
  assert.strictEqual(t.host.querySelector('.vt-authz-more'), null);
});

test('500건이면 가장 최근 500건까지라고 알린다', async () => {
  const t = await mount({ entries: Array.from({ length: 500 }, (_, i) => entry(i + 1)), chain: 'ok' });
  await waitFor(() => t.rows().length === 50);
  for (let i = 0; i < 9; i++) t.host.querySelector('.vt-authz-more').click();
  assert.strictEqual(t.rows().length, 500);
  assert.match(t.host.textContent, /가장 최근 500건까지/);
});

test('빈 결과·읽기 실패(다시 시도)', async () => {
  const empty = await mount({ entries: [], chain: 'ok' });
  await waitFor(() => /조건에 맞는 기록이 없습니다/.test(empty.host.textContent));
  let fail = true;
  const t = await mount(undefined, { 'GET /api/authz/audit': () => (fail ? { status: 500, json: { error: 'x', reason: '실패' } } : { json: { entries: [entry(1)], chain: 'ok' } }) });
  await waitFor(() => t.host.querySelector('.vt-authz-retry'));
  assert.match(t.host.querySelector('.vt-authz-err').textContent, /실패/);
  assert.strictEqual(t.chain().dataset.state, 'unknown');
  fail = false;
  t.host.querySelector('.vt-authz-retry').click();
  await waitFor(() => t.rows().length === 1);
});

test('순수 규칙: auditView 허용 목록, chainBadge, sinceFor', async () => {
  const t = await loadShare({}, { files: ['authz/data.js'] });
  _wins.push(t.window);
  const v = t.mod.auditView({ ts: 1, principal: 'p', action: 'a', resource: 'r', scopes: ['s'], result: 'ok', text: 'LEAK', detail: 'LEAK', source: 'denials' });
  assert.deepStrictEqual(Object.keys(v).sort(), ['action', 'outcome', 'principal', 'reason', 'resource', 'scopes', 'source', 'ts']);
  assert.strictEqual(v.source, 'denials');
  assert.strictEqual(t.mod.chainBadge({ chain: 'ok' }).state, 'ok');
  assert.strictEqual(t.mod.chainBadge(null).state, 'unknown');
  assert.strictEqual(t.mod.sinceFor('all', 1000), 0);
  assert.strictEqual(t.mod.sinceFor('1h', 10000), 6400);
});
