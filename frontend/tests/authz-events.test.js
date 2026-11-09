// 승인함 이벤트 — 새 이벤트(authz_*)와 옛 이벤트(agent_share_request*)의 처리, 중복 없음, share-lazy.js 배선.
'use strict';
const path = require('node:path');
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, tick, waitFor } = require('./helpers/share-env');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { item } = require('./helpers/authz-fixtures');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const SETTLE = 150;   // store.COALESCE_MS(40) + 여유

async function store(list = [item('r1')]) {
  let current = list;
  const t = await loadShare({ 'GET /api/authz/approvals': () => ({ json: { approvals: current } }) }, { files: ['authz/store.js'] });
  _wins.push(t.window);
  const gets = () => t.calls.filter((c) => c.url === '/api/authz/approvals').length;
  return { ...t, gets, set: (l) => { current = l; } };
}

test('authz_request: 목록을 서버에서 다시 읽는다(이벤트에는 내용이 없다)', async () => {
  const t = await store();
  t.mod.onEvent({ type: 'authz_request', id: 'r1', kind: 'share' });
  await waitFor(() => t.mod.pending().length === 1);
  assert.strictEqual(t.gets(), 1);
  assert.strictEqual(t.mod.pending()[0].id, 'r1');
});

test('옛 agent_share_request 는 새 이벤트가 오기 전에는 같은 일을 한다(폴백)', async () => {
  const t = await store();
  t.mod.onEvent({ type: 'agent_share_request', request: { id: 'r1' } });
  await waitFor(() => t.mod.pending().length === 1);
  assert.strictEqual(t.mod.sawNewEvents(), false);
});

test('옛·새 이벤트가 잇따라 와도 읽기는 한 번이다(중복 처리 없음)', async () => {
  const t = await store();
  t.mod.onEvent({ type: 'agent_share_request', request: { id: 'r1' } });
  t.mod.onEvent({ type: 'authz_request', id: 'r1', kind: 'share' });
  await tick(SETTLE);
  assert.strictEqual(t.gets(), 1);
  assert.strictEqual(t.mod.pending().length, 1);
});

test('새 이벤트를 한 번 받은 뒤의 옛 이벤트는 무시한다', async () => {
  const t = await store();
  t.mod.onEvent({ type: 'authz_request', id: 'r1', kind: 'share' });
  await tick(SETTLE);
  const before = t.gets();
  t.mod.onEvent({ type: 'agent_share_request', request: { id: 'r2' } });
  t.mod.onEvent({ type: 'agent_share_request_resolved', id: 'r1' });
  await tick(SETTLE);
  assert.strictEqual(t.gets(), before, '추가 읽기 없음');
  assert.strictEqual(t.mod.pending().length, 1, '옛 resolved 로 빠지지도 않는다');
});

test('authz_resolved: 목록에서 빼고 grants 변경을 알린다. id:"*" 는 전부 비운다', async () => {
  const t = await store([item('a', 'share', { created: 1 }), item('b', 'msg', { created: 2 })]);
  const seen = [];
  t.mod.onChange((e) => seen.push(e.type));
  await t.mod.refresh();
  t.mod.onEvent({ type: 'authz_resolved', id: 'a', kind: 'share', state: 'allowed' });
  assert.deepStrictEqual(t.mod.pending().map((r) => r.id), ['b']);
  assert.ok(seen.includes('grants'));
  t.mod.onEvent({ type: 'authz_resolved', id: '*', kind: 'all', state: 'cleared' });
  assert.strictEqual(t.mod.pending().length, 0);
});

test('authz_revoked: grants 변경만 알린다', async () => {
  const t = await store();
  const seen = [];
  t.mod.onChange((e) => seen.push(e.type));
  t.mod.onEvent({ type: 'authz_revoked', id: 'ag-1', kind: 'share' });
  assert.deepStrictEqual(seen, ['grants']);
  assert.strictEqual(t.gets(), 0);
});

test('옛 resolved(폴백)는 목록에서 뺀다', async () => {
  const t = await store();
  await t.mod.refresh();
  t.mod.onEvent({ type: 'agent_share_request_resolved', id: 'r1' });
  assert.strictEqual(t.mod.pending().length, 0);
});

test('읽는 도중 또 요청되면 한 번 더 읽는다(마지막 상태로 수렴)', async () => {
  const t = await store([item('r1')]);
  const p = t.mod.refresh();
  t.set([item('r1'), item('r2', 'msg', { created: 2 })]);
  void t.mod.refresh();
  await p;
  await waitFor(() => t.mod.pending().length === 2);
});

test('모르는 이벤트·빈 메시지는 무시한다', async () => {
  const t = await store();
  t.mod.onEvent(null);
  t.mod.onEvent({ type: 'workspace_updated' });
  await tick(SETTLE);
  assert.strictEqual(t.gets(), 0);
});

// ── share-lazy.js 배선 ────────────────────────────────────────────────────
async function lazy(routes, { relayed = false } = {}) {
  const env = createDomEnv('<!doctype html><html><body><button class="vt-act-settings" aria-label="설정"></button></body></html>');
  _wins.push(env.window);
  const { window } = env;
  if (relayed) window.localStorage.setItem('vt.relayHost', 'other-mac');
  const sockets = [];
  window.WebSocket = class { constructor(u) { this.url = u; this.readyState = 1; sockets.push(this); } send() {} close() {} };
  const calls = [];
  window.API_BASE = ''; window._tokenQuery = ''; window.showToast = () => {}; window.fitAndResize = () => {};
  window.fetch = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    calls.push({ method, url: String(url) });
    const h = routes[`${method} ${new URL(String(url), 'http://x').pathname}`];
    const r = h ? await h() : { status: 404, json: {} };
    return { ok: (r.status || 200) < 400, status: r.status || 200, json: async () => r.json };
  };
  await importFresh(path.join(__dirname, '../js/share-lazy.js'), env.context, new Map());
  const send = (m) => sockets[0].onmessage({ data: JSON.stringify(m) });
  return { window, document: env.document, sockets, calls, send };
}

test('share-lazy: authz_request 로 모달이 뜨고 authz_resolved 로 닫힌다', async () => {
  const t = await lazy({ 'GET /api/authz/approvals': () => ({ json: { approvals: [item('e1', 'share', { subject: { name: 'agent-x', id: 'x' } })] } }) });
  t.send({ type: 'authz_request', id: 'e1', kind: 'share' });
  await waitFor(() => t.document.querySelector('.vt-authz-approval'));
  assert.match(t.document.querySelector('.vt-authz-approval-body').textContent, /agent-x/);
  t.send({ type: 'authz_resolved', id: 'e1', kind: 'share', state: 'allowed' });
  await waitFor(() => !t.document.querySelector('.vt-authz-approval'));
});

test('share-lazy: 옛 이벤트만 오는 서버(새 이벤트 없음)에서도 모달이 뜬다', async () => {
  const t = await lazy({ 'GET /api/authz/approvals': () => ({ json: { approvals: [item('e1')] } }) });
  t.send({ type: 'agent_share_request', request: { id: 'e1' } });
  await waitFor(() => t.document.querySelector('.vt-authz-approval'));
});

test('share-lazy: 옛·새 이벤트가 함께 와도 목록 읽기는 한 번이다', async () => {
  const t = await lazy({ 'GET /api/authz/approvals': () => ({ json: { approvals: [item('e1')] } }) });
  t.send({ type: 'agent_share_request', request: { id: 'e1' } });
  t.send({ type: 'authz_request', id: 'e1', kind: 'share' });
  await waitFor(() => t.document.querySelector('.vt-authz-approval'));
  await tick(SETTLE);
  assert.strictEqual(t.calls.filter((c) => new URL(c.url, 'http://x').pathname === '/api/authz/approvals').length, 1);
});

test('share-lazy: 연결이 열릴 때 대기 목록을 맞추고 설정 버튼에 건수 배지를 단다', async () => {
  const t = await lazy({ 'GET /api/authz/approvals': () => ({ json: { approvals: [item('p1'), item('p2', 'msg', { created: 2 })] } }) });
  t.sockets[0].onopen();
  await waitFor(() => t.document.querySelector('.vt-authz-approval'));
  const badge = t.document.querySelector('.vt-act-settings .vt-authz-badge');
  assert.strictEqual(badge.textContent, '2');
  assert.ok(!badge.hidden);
});

test('share-lazy: 대기 요청이 없으면 청크를 받지 않고 모달도 배지도 없다', async () => {
  const t = await lazy({ 'GET /api/authz/approvals': () => ({ json: { approvals: [] } }) });
  t.sockets[0].onopen();
  await tick(60);
  assert.strictEqual(t.document.querySelector('.vt-authz-approval'), null);
  const badge = t.document.querySelector('.vt-act-settings .vt-authz-badge');
  assert.ok(!badge || badge.hidden);
});

test('share-lazy: 다른 맥을 보는 화면(중계)은 이벤트를 듣지 않는다', async () => {
  const t = await lazy({}, { relayed: true });
  assert.strictEqual(t.sockets.length, 0);
});
