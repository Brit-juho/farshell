// 에이전트 세션 공유 — 승인 모달 큐(share/approvals.js)와 이벤트 구독(share-lazy.js).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });

const req = (id, kind, agent, nick, created = 1) => ({
  id, kind, state: 'pending', created, expires: created + 86400,
  agent: { name: agent, id: `$a${id}` }, target: { nick, mode: 'readwrite', name: nick, id: '$t' },
});
const ARM = 450;   // approvals.js ARM_MS(400) + 여유

async function setup(routes = {}) {
  const t = await loadShare(routes);
  _wins.push(t.window);
  const modal = () => t.document.querySelector('.vt-share-approval');
  const act = (a) => modal().querySelector(`[data-act="${a}"]`);
  const text = () => modal().querySelector('.vt-share-approval-body').textContent;
  return { ...t, modal, act, text };
}
const decideRoute = (log) => (b, url) => { log.push({ url, body: b }); return { json: { ok: true, request: {}, grant: { id: 'g' } } }; };

test('요청이 오면 모달이 뜨고 문구·상태 안내가 보이며, 기본 포커스는 「거부」다', async () => {
  const t = await setup();
  t.mod.addRequest(req('r1', 'read', 'agent-a', 'build'));
  assert.ok(t.modal());
  assert.strictEqual(t.modal().querySelector('.vt-viewer-card').getAttribute('role'), 'alertdialog');
  assert.match(t.text(), /「agent-a」이\(가\) 「build」 세션의 읽기 권한을 요청합니다/);
  assert.match(t.text(), /\/clear 후에도 계속 쓸 수 있습니다/);
  assert.match(t.text(), /「에이전트 권한」에서 언제든 철회/);
  assert.strictEqual(t.document.activeElement, t.act('deny'), '안전한 쪽에 포커스');
  assert.ok(t.act('read'), '읽기 요청은 [허용]');
  assert.strictEqual(t.act('readwrite'), null);
});

test('쓰기 요청: 「읽기만 허용」과 「쓰기까지 허용」이 각각 level 로 간다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/agent/share/requests/w1/decision': decideRoute(log), 'POST /api/agent/share/requests/w2/decision': decideRoute(log) });
  t.mod.addRequest(req('w1', 'write', 'ag', 'sh'));
  assert.match(t.text(), /쓰기 권한을 요청합니다/);
  await tick(ARM);
  t.act('read').click();
  await waitFor(() => log.length === 1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(log[0].body)), { decision: 'allow', level: 'read' });
  await waitFor(() => !t.modal());
  t.mod.addRequest(req('w2', 'write', 'ag', 'sh'));
  await tick(ARM);
  t.act('readwrite').click();
  await waitFor(() => log.length === 2);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(log[1].body)), { decision: 'allow', level: 'readwrite' });
});

test('거부는 decision:deny 만 보낸다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/agent/share/requests/r1/decision': decideRoute(log) });
  t.mod.addRequest(req('r1', 'read', 'a', 'n'));
  await tick(ARM);
  t.act('deny').click();
  await waitFor(() => log.length === 1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(log[0].body)), { decision: 'deny' });
});

test('오조작 방지: 뜬 직후에는 어떤 결정 버튼을 눌러도 요청이 나가지 않는다(치던 Enter·Space)', async () => {
  const t = await setup();
  t.mod.addRequest(req('r1', 'read', 'a', 'n'));
  t.act('read').click();
  t.act('deny').click();
  await tick(20);
  assert.strictEqual(t.calls.length, 0);
  assert.ok(t.modal(), '그대로 떠 있다');
});

test('Enter 한 번으로 허용되지 않는다 — 포커스가 「거부」라 Enter(=포커스 버튼 클릭)는 거부 쪽이다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/agent/share/requests/r1/decision': decideRoute(log) });
  t.mod.addRequest(req('r1', 'read', 'a', 'n'));
  await tick(ARM);
  // 브라우저는 포커스된 버튼에 Enter 를 누르면 그 버튼에 click 을 낸다.
  t.document.activeElement.click();
  await waitFor(() => log.length === 1);
  assert.strictEqual(log[0].body.decision, 'deny');
});

test('여러 요청은 순서대로 — 하나 처리하면 다음이 뜨고(1 / N 표시), 포커스는 다시 「거부」로', async () => {
  const log = [];
  const t = await setup({ 'POST /api/agent/share/requests/a/decision': decideRoute(log), 'POST /api/agent/share/requests/b/decision': decideRoute(log) });
  t.mod.addRequest(req('b', 'write', 'agent-b', 'two', 2));
  t.mod.addRequest(req('a', 'read', 'agent-a', 'one', 1));
  assert.strictEqual(t.modal().querySelector('.vt-share-count').textContent, '1 / 2');
  assert.match(t.text(), /「one」/, '오래된 것이 먼저');
  await tick(ARM);
  t.act('read').click();
  await waitFor(() => /「two」/.test(t.text()));
  assert.strictEqual(t.modal().querySelector('.vt-share-count').textContent, '');
  assert.strictEqual(t.document.activeElement, t.act('deny'));
  assert.ok(t.act('readwrite'));
  // 새로 그려진 직후에도 다시 무장 대기(연타가 다음 요청을 허용하지 않는다)
  t.act('readwrite').click();
  await tick(20);
  assert.strictEqual(log.length, 1);
});

test('다른 화면에서 처리되면(resolved) 자동으로 닫히고, 현재 것이 아니면 큐에서만 빠진다', async () => {
  const t = await setup();
  t.mod.addRequest(req('r1', 'read', 'a', 'one', 1));
  t.mod.addRequest(req('r2', 'read', 'b', 'two', 2));
  t.mod.resolveRequest('r2');
  assert.strictEqual(t.modal().querySelector('.vt-share-count').textContent, '', '큐에서 빠져 1건');
  t.mod.resolveRequest('r1');
  assert.strictEqual(t.modal(), null, '마지막이 처리되면 닫힘');
  assert.strictEqual(t.calls.length, 0, '이 화면은 아무것도 보내지 않았다');
});

test('Esc·「나중에」는 결정이 아니다 — 요청은 안 나가고 안내가 뜨며, 새 요청이 오면 다시 뜬다', async () => {
  const t = await setup();
  t.mod.addRequest(req('r1', 'read', 'a', 'one', 1));
  t.document.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.strictEqual(t.modal(), null);
  assert.strictEqual(t.calls.length, 0);
  assert.match(t.toasts[0][0], /승인 대기 1건/);
  t.mod.addRequest(req('r2', 'read', 'b', 'two', 2));
  assert.ok(t.modal(), '새 요청은 다시 띄운다');
  t.modal().querySelector('.vt-share-snooze').click();
  assert.strictEqual(t.modal(), null);
  t.mod.showPending();
  assert.ok(t.modal(), '「에이전트 권한」에서 다시 열 수 있다');
});

test('서버 목록으로 맞추면(재연결) 처리된 것은 빠지고, 이미 닫아 둔 요청만 남았으면 다시 띄우지 않는다', async () => {
  const t = await setup();
  t.mod.setPending([req('r1', 'read', 'a', 'one', 1)]);
  assert.ok(t.modal());
  t.modal().querySelector('.vt-share-snooze').click();
  t.mod.setPending([req('r1', 'read', 'a', 'one', 1)]);
  assert.strictEqual(t.modal(), null, '같은 요청은 나중에 상태 유지');
  t.mod.setPending([]);
  assert.strictEqual(t.modal(), null);
  t.mod.setPending([req('r9', 'read', 'a', 'nine', 3)]);
  assert.ok(t.modal(), '새 요청은 띄운다');
});

test('결정 오류: 404 는 목록에서 빼고, 그 밖의 오류는 이유를 보이며 요청을 남긴다', async () => {
  const t = await setup({
    'POST /api/agent/share/requests/gone/decision': () => ({ status: 404, json: { error: 'not_found' } }),
    'POST /api/agent/share/requests/ro/decision': () => ({ status: 409, json: { error: 'read_only_share', reason: 'x' } }),
  });
  t.mod.addRequest(req('gone', 'read', 'a', 'one', 1));
  await tick(ARM);
  t.act('read').click();
  await waitFor(() => !t.modal());
  t.mod.addRequest(req('ro', 'write', 'a', 'two', 2));
  await tick(ARM);
  t.act('readwrite').click();
  await waitFor(() => /읽기 전용/.test(t.modal().querySelector('.vt-share-err').textContent));
  assert.ok(t.modal(), '남아 있다');
  assert.ok(!t.act('deny').disabled, '다시 결정할 수 있다');
});

test('모달은 포커스 트랩 안에 있다 — Tab 이 터미널로 새지 않는다', async () => {
  const t = await setup();
  const term = t.document.createElement('textarea');
  t.document.body.appendChild(term);
  term.focus();
  t.mod.addRequest(req('r1', 'read', 'a', 'n'));
  assert.notStrictEqual(t.document.activeElement, term, '터미널에서 모달로 포커스가 옮겨졌다');
  assert.ok(t.modal().contains(t.document.activeElement));
});

// ── 이벤트 구독(share-lazy.js) ────────────────────────────────────────────
test('이벤트: agent_share_request 로 모달이 뜨고 agent_share_request_resolved 로 닫힌다', async () => {
  const t = await loadShareWithWS({});
  const ws = t.sockets[0];
  ws.onmessage({ data: JSON.stringify({ type: 'agent_share_request', request: req('e1', 'read', 'agent-x', 'nick-x') }) });
  await waitFor(() => t.document.querySelector('.vt-share-approval'));
  assert.match(t.document.querySelector('.vt-share-approval-body').textContent, /agent-x/);
  ws.onmessage({ data: JSON.stringify({ type: 'agent_share_request_resolved', id: 'e1', state: 'allowed' }) });
  await waitFor(() => !t.document.querySelector('.vt-share-approval'));
});
// share-lazy 는 import 시점에 구독하고 workspace-ws 가 그때 WebSocket 을 만든다 — 미리 흉내를 달아 둔다.
async function loadShareWithWS(routes) {
  const path = require('node:path');
  const { createDomEnv } = require('./helpers/dom-env');
  const { importFresh } = require('./helpers/vm-esm');
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _wins.push(env.window);
  const { window } = env;
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
  return { window, document: env.document, sockets, calls };
}

test('이벤트: 연결이 열릴 때 대기 목록을 받아 맞춘다(부팅·재연결에 놓친 요청)', async () => {
  const t = await loadShareWithWS({
    'GET /api/agent/share/requests': () => ({ json: { requests: [req('p1', 'write', 'late-agent', 'sh')] } }),
  });
  t.sockets[0].onopen();
  await waitFor(() => t.document.querySelector('.vt-share-approval'));
  assert.match(t.document.querySelector('.vt-share-approval-body').textContent, /late-agent/);
  assert.ok(t.calls.some((c) => c.url.includes('/api/agent/share/requests')));
});

test('이벤트: 대기 요청이 없으면 청크를 받지 않고 아무것도 뜨지 않는다', async () => {
  const t = await loadShareWithWS({ 'GET /api/agent/share/requests': () => ({ json: { requests: [] } }) });
  t.sockets[0].onopen();
  await tick(30);
  assert.strictEqual(t.document.querySelector('.vt-share-approval'), null);
});

test('이 화면이 다른 맥을 보고 있으면(중계) 이벤트를 듣지 않는다', async () => {
  const path = require('node:path');
  const { createDomEnv } = require('./helpers/dom-env');
  const { importFresh } = require('./helpers/vm-esm');
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _wins.push(env.window);
  env.window.localStorage.setItem('vt.relayHost', 'other-mac');
  const sockets = [];
  env.window.WebSocket = class { constructor() { sockets.push(this); } send() {} close() {} };
  env.window.API_BASE = '';
  await importFresh(path.join(__dirname, '../js/share-lazy.js'), env.context, new Map());
  assert.strictEqual(sockets.length, 0, '구독이 없어 채널을 열지도 않는다');
});
