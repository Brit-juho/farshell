// 승인함 API 래퍼(authz/api.js) — 가짜 fetch 로 경로·메서드·본문·쿠키 전용 호출을 본다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare } = require('./helpers/share-env');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const J = (x) => JSON.parse(JSON.stringify(x));

async function setup(routes) {
  const t = await loadShare(routes, { files: ['authz/api.js'] });
  _wins.push(t.window);
  return t;
}

test('approvals: GET /api/authz/approvals, 쿠키만 보내고 토큰·Authorization 은 싣지 않는다', async () => {
  const t = await setup({ 'GET /api/authz/approvals': () => ({ json: { approvals: [{ id: 'a' }] } }) });
  const list = await t.mod.authzApi.approvals();
  assert.deepStrictEqual(J(list), [{ id: 'a' }]);
  const c = t.calls[0];
  assert.strictEqual(c.opts.credentials, 'same-origin');
  assert.ok(!/token=/.test(c.url));
  assert.ok(!(c.opts.headers && c.opts.headers.Authorization));
});

test('decide: allow+choice 와 deny 의 본문, id 는 인코딩된다', async () => {
  const t = await setup({
    'POST /api/authz/approvals/gq-1/decision': () => ({ json: { ok: true } }),
    'POST /api/authz/approvals/a%2Fb/decision': () => ({ json: { ok: true } }),
  });
  await t.mod.authzApi.decide('gq-1', 'allow', 'once');
  await t.mod.authzApi.decide('gq-1', 'deny');
  await t.mod.authzApi.decide('a/b', 'allow', 'session');
  assert.deepStrictEqual(J(t.calls[0].body), { decision: 'allow', choice: 'once' });
  assert.deepStrictEqual(J(t.calls[1].body), { decision: 'deny' });
  assert.strictEqual(t.calls[2].method, 'POST');
});

test('grants: 필터는 쿼리로, 빈 값은 빼고 보낸다', async () => {
  const t = await setup({ 'GET /api/authz/grants': () => ({ json: { grants: [] } }) });
  await t.mod.authzApi.grants({ subject: 'ag 1', resource: '', kind: 'msg' });
  assert.strictEqual(t.calls[0].url, '/api/authz/grants?subject=ag+1&kind=msg');
  await t.mod.authzApi.grants();
  assert.strictEqual(t.calls[1].url, '/api/authz/grants');
});

test('revoke: DELETE, scopes 는 쉼표로', async () => {
  const t = await setup({ 'DELETE /api/authz/grants/ag-1': () => ({ json: { ok: true } }) });
  await t.mod.authzApi.revoke('ag-1', ['session.input']);
  await t.mod.authzApi.revoke('ag-1');
  assert.strictEqual(t.calls[0].method, 'DELETE');
  assert.strictEqual(t.calls[0].url, '/api/authz/grants/ag-1?scopes=session.input');
  assert.strictEqual(t.calls[1].url, '/api/authz/grants/ag-1');
});

test('audit: 필터·한도 쿼리, 응답 전체(chain 포함)를 돌려준다', async () => {
  const t = await setup({ 'GET /api/authz/audit': () => ({ json: { entries: [], chain: 'broken', brokenSeq: 7 } }) });
  const r = await t.mod.authzApi.audit({ principal: 'agent:', resource: 'session:', since: 100, limit: 500 });
  assert.strictEqual(r.chain, 'broken');
  assert.strictEqual(t.calls[0].url, '/api/authz/audit?principal=agent%3A&resource=session%3A&since=100&limit=500');
});

test('panic: POST /api/authz/panic', async () => {
  const t = await setup({ 'POST /api/authz/panic': () => ({ json: { ok: true, revoked: {}, failed: [] } }) });
  const r = await t.mod.authzApi.panic();
  assert.strictEqual(r.ok, true);
  assert.strictEqual(t.calls[0].method, 'POST');
});

test('오류: status·code·reason 이 실리고, needsElevation 은 코드로 판정한다', async () => {
  const t = await setup({
    'POST /api/authz/panic': () => ({ status: 401, json: { error: 'elevation_required', reason: 'x' } }),
    'GET /api/authz/approvals': () => ({ status: 500, json: {} }),
  });
  let err;
  try { await t.mod.authzApi.panic(); } catch (e) { err = e; }
  assert.strictEqual(err.status, 401);
  assert.strictEqual(err.code, 'elevation_required');
  assert.ok(t.mod.needsElevation(err));
  assert.ok(t.mod.needsElevation({ data: { error: 'elevation_required' } }));
  assert.ok(!t.mod.needsElevation({ code: 'not_found' }));
  assert.ok(!t.mod.needsElevation(null));
  await assert.rejects(() => t.mod.authzApi.approvals(), (e) => e.status === 500);
});
