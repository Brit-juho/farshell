// 권한 패널(authz/grants.js): 목록·필터·회수 확인·기본 규칙(회수 불가)·빈/오류/불러오는 상태·permissions 의 비상 정지.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');
const { answerConfirm } = require('./helpers/dialog-stub');
const { grant, promptInput } = require('./helpers/authz-fixtures');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const J = (x) => JSON.parse(JSON.stringify(x));
const NOW = Math.floor(Date.now() / 1000);

const GRANTS = [
  grant('ag-1', 'share', { scopes: ['session.input', 'session.read', 'session.view'], preset: { id: 'collab', label: '협업', exact: true }, conds: { 'session.input': { modes: ['line'] } }, lineage: { delegation: 'dg-1' } }),
  grant('gm-1', 'msg', { scopes: ['msg.send'], preset: null, subject: { id: 'x', name: 'agent-b' }, resource: 'msg:local/k', expires: NOW + 7200 }),
  grant('gg-1', 'generic', { scopes: ['session.read'], conds: {}, maxUses: 1, uses: 0, approvedVia: 'window', expires: NOW + 600 }),
];

async function mount(routes = {}, grants = GRANTS) {
  const t = await loadShare({ 'GET /api/authz/grants': () => ({ json: { grants } }), ...routes }, { files: ['authz/grants.js', 'authz/store.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  const api = t.mods[0].mountGrants(host);
  return { ...t, host, api, store: t.mods[1], rows: () => [...host.querySelectorAll('.vt-authz-row')], getUrls: () => t.calls.filter((c) => c.method === 'GET').map((c) => c.url) };
}

test('목록: 종류·프리셋(또는 scope 개수)·주체·리소스·승인자/경로·만료·계보·정확한 scope+조건이 한 행에', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 3);
  const [r1, r2, r3] = t.rows();
  assert.match(r1.textContent, /세션 공유/);
  assert.match(r1.querySelector('.vt-authz-preset').textContent, /협업/);
  assert.match(r1.textContent, /session\.input, session\.read, session\.view · session\.input\[modes=line\]/);
  assert.match(r1.textContent, /user:alice · 직접 승인 · 만료 없음 · 위임된 하위 세션 · 위임 dg-1/);
  assert.match(r2.querySelector('.vt-authz-preset').textContent, /1개 scope/, '프리셋이 없으면 scope 개수');
  assert.match(r2.textContent, /agent-b/);
  assert.match(r2.textContent, /2시간 뒤 만료/);
  assert.match(r3.textContent, /자동 승인 구간/);
  assert.match(r3.textContent, /남은 횟수 1/);
});

test('불러오는 중 상태가 보이고, 빈 목록은 안내, 필터가 있으면 다른 안내', async () => {
  const t = await mount({}, []);
  assert.match(t.host.querySelector('.vt-authz-list').textContent, /불러오는 중/);
  await waitFor(() => /허용된 권한이 없습니다/.test(t.host.querySelector('.vt-authz-list').textContent));
  const f = t.host.querySelector('[data-f="subject"]');
  f.value = 'zzz';
  f.dispatchEvent(new t.window.Event('input', { bubbles: true }));
  await waitFor(() => /조건에 맞는 권한이 없습니다/.test(t.host.querySelector('.vt-authz-list').textContent));
});

test('필터: 주체·리소스·종류가 서버 쿼리로 가고(입력은 디바운스), 최신 응답만 그려진다', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 3);
  const type = (sel, v) => { const el = t.host.querySelector(sel); el.value = v; el.dispatchEvent(new t.window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); };
  type('[data-f="subject"]', 'ag');
  type('[data-f="subject"]', 'agent');
  type('[data-f="resource"]', 'session:');
  await tick(20);
  assert.strictEqual(t.getUrls().length, 1, '디바운스 전에는 다시 읽지 않는다');
  await waitFor(() => t.getUrls().length === 2);
  assert.match(t.getUrls()[1], /subject=agent/);
  assert.match(t.getUrls()[1], /resource=session%3A/);
  type('[data-f="kind"]', 'msg');
  await waitFor(() => t.getUrls().length === 3);
  assert.match(t.getUrls()[2], /kind=msg/);
});

test('회수: 확인 대화상자에서 취소하면 아무것도 보내지 않는다', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 3);
  t.rows()[1].querySelector('.vt-authz-revoke[data-mode="all"]').click();
  await answerConfirm(t.document, { accept: false });
  await tick(30);
  assert.strictEqual(t.calls.filter((c) => c.method === 'DELETE').length, 0);
});

test('회수: 확인하면 DELETE 후 목록을 다시 읽는다', async () => {
  let gone = false;
  const t = await mount({
    'DELETE /api/authz/grants/gm-1': () => { gone = true; return { json: { ok: true } }; },
    'GET /api/authz/grants': () => ({ json: { grants: gone ? GRANTS.filter((g) => g.id !== 'gm-1') : GRANTS } }),
  });
  await waitFor(() => t.rows().length === 3);
  t.rows()[1].querySelector('.vt-authz-revoke').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => t.rows().length === 2);
  assert.strictEqual(t.calls.find((c) => c.method === 'DELETE').url, '/api/authz/grants/gm-1');
});

test('공유 grant 의 「입력만 회수」는 scopes=session.input 으로 가고, 입력 scope 가 없는 grant 에는 그 버튼이 없다', async () => {
  const t = await mount({ 'DELETE /api/authz/grants/ag-1': () => ({ json: { ok: true } }) });
  await waitFor(() => t.rows().length === 3);
  assert.ok(t.rows()[0].querySelector('[data-mode="write"]'));
  assert.strictEqual(t.rows()[1].querySelector('[data-mode="write"]'), null);
  t.rows()[0].querySelector('[data-mode="write"]').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => t.calls.some((c) => c.method === 'DELETE'));
  assert.strictEqual(t.calls.find((c) => c.method === 'DELETE').url, '/api/authz/grants/ag-1?scopes=session.input');
});

test('회수 오류는 이유를 보이고 목록은 남는다', async () => {
  const t = await mount({ 'DELETE /api/authz/grants/gm-1': () => ({ status: 404, json: { error: 'not_found', reason: '그런 권한이 없습니다' } }) });
  await waitFor(() => t.rows().length === 3);
  t.rows()[1].querySelector('.vt-authz-revoke').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => /그런 권한이 없습니다/.test(t.host.querySelector('.vt-authz-err').textContent));
  assert.strictEqual(t.rows().length, 3);
});

test('기본 규칙은 따로 보이고 회수 버튼이 없다', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 3);
  const built = [...t.host.querySelectorAll('.vt-authz-builtin li')];
  assert.strictEqual(built.length, 3);
  assert.strictEqual(t.host.querySelector('.vt-authz-builtin button'), null);
  assert.strictEqual(t.host.querySelectorAll('.vt-authz-list .vt-authz-builtin').length, 0, '목록과 섞이지 않는다');
  assert.match(t.host.textContent, /읽기 전용이라 회수할 수 없고/);
});

test('목록 읽기 실패: 오류 문구와 「다시 시도」, 다시 시도하면 복구된다', async () => {
  let fail = true;
  const t = await mount({ 'GET /api/authz/grants': () => (fail ? { status: 500, json: { error: 'x', reason: '서버 오류' } } : { json: { grants: GRANTS } }) });
  await waitFor(() => t.host.querySelector('.vt-authz-retry'));
  assert.match(t.host.querySelector('.vt-authz-err').textContent, /서버 오류/);
  fail = false;
  t.host.querySelector('.vt-authz-retry').click();
  await waitFor(() => t.rows().length === 3);
});

test('authz_revoked 이벤트(grants 변경)가 오면 목록을 다시 읽는다', async () => {
  const t = await mount();
  await waitFor(() => t.rows().length === 3);
  t.store.onEvent({ type: 'authz_revoked', id: 'ag-9', kind: 'share' });
  await waitFor(() => t.getUrls().length === 2);
});

// ── 비상 정지 ─────────────────────────────────────────────────────────────
async function panic(routes) {
  const t = await loadShare(routes, { files: ['authz/permissions.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  t.mod.mountPanic(host);
  return { ...t, host, deletes: () => t.calls.filter((c) => c.url.includes('/api/authz/panic')) };
}

test('비상 정지: 첫 클릭은 확인 단계로 가기만 하고 아무것도 보내지 않는다(기본 포커스는 취소)', async () => {
  const t = await panic({});
  t.host.querySelector('.vt-authz-panic-arm').click();
  assert.strictEqual(t.deletes().length, 0);
  assert.match(t.host.textContent, /정말 모든 에이전트 권한을 회수할까요/);
  assert.strictEqual(t.document.activeElement, t.host.querySelector('.vt-authz-panic-cancel'));
  t.host.querySelector('.vt-authz-panic-cancel').click();
  assert.ok(t.host.querySelector('.vt-authz-panic-arm'), '취소하면 처음으로');
  assert.strictEqual(t.deletes().length, 0);
});

test('비상 정지: 둘째 단계 확인 → 401 elevation_required → 비밀번호 확인 → 재시도 → 결과 건수와 실패 목록', async () => {
  let n = 0; const log = [];
  const t = await panic({
    'POST /api/authz/panic': () => { log.push('panic'); return (n++ === 0) ? { status: 401, json: { error: 'elevation_required' } } : { json: { ok: true, revoked: { share: 2, msg: 1, peer: 0, deleg: 0, generic: 1, requests: 3 }, failed: [{ kind: 'peer', error: 'x' }] } }; },
    'POST /api/auth/elevate': () => { log.push('elevate'); return { json: { ok: true } }; },
  });
  t.host.querySelector('.vt-authz-panic-arm').click();
  t.host.querySelector('.vt-authz-panic-go').click();
  await waitFor(() => promptInput(t.document));
  promptInput(t.document).value = 'pw';
  promptInput(t.document).closest('.vt-dialog').querySelector('.vt-dialog-ok').click();
  await waitFor(() => /회수했습니다 — 7건/.test(t.host.textContent));
  assert.deepStrictEqual(log, ['panic', 'elevate', 'panic']);
  assert.match(t.host.querySelector('.vt-authz-err').textContent, /peer\(x\)/);
});

test('비상 정지: 비밀번호 확인을 취소하면 실행되지 않고 확인 단계로 돌아간다', async () => {
  const t = await panic({ 'POST /api/authz/panic': () => ({ status: 401, json: { error: 'elevation_required' } }) });
  t.host.querySelector('.vt-authz-panic-arm').click();
  t.host.querySelector('.vt-authz-panic-go').click();
  await waitFor(() => promptInput(t.document));
  promptInput(t.document).closest('.vt-dialog').querySelector('.vt-dialog-cancel').click();
  await waitFor(() => t.host.querySelector('.vt-authz-panic-go') && !t.host.querySelector('.vt-authz-panic-go').disabled);
  assert.strictEqual(t.deletes().length, 1);
});
