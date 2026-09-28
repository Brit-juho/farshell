// 2026-09-28 — core/host-route.js. 호스트 전환 중이면 서버 호출이 그 맥으로
// 중계되고(`/api/hosts/<id>/relay/...`), 이 기기에 붙은 것(로그인·호스트 목록·
// 푸시·음성 …)만 로컬에 남는다. 이 경계가 틀리면 M1 화면에 M5 데이터가 섞이거나
// 로그인이 상대 맥으로 새어 나간다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const HOST_ROUTE_JS = path.join(__dirname, '../js/core/host-route.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(stored) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.dom.window;
  if (stored !== undefined) w.localStorage.setItem('vt.relayHost', stored);
  w.API_BASE = 'http://x';
  return importFresh(HOST_ROUTE_JS, env.context, new Map());
}

test('로컬이면 경로를 그대로 둔다', async () => {
  const R = await load();
  assert.strictEqual(R.relayHost(), 'local');
  assert.strictEqual(R.relayPath('/api/tmux/sessions'), '/api/tmux/sessions');
  assert.strictEqual(R.relayWsPath('/ws/abc?token=t'), '/ws/abc?token=t');
  assert.strictEqual(R.hostKey('vt-layout-v1'), 'vt-layout-v1');
});

test('전환 중이면 호스트 범위 API·소켓은 그 맥으로 중계한다', async () => {
  const R = await load('m5');
  assert.strictEqual(R.relayHost(), 'm5');
  assert.strictEqual(R.relayPath('/api/tmux/sessions'), '/api/hosts/m5/relay/api/tmux/sessions');
  assert.strictEqual(R.relayPath('/api/fs/raw?path=%2Fa'), '/api/hosts/m5/relay/api/fs/raw?path=%2Fa');
  assert.strictEqual(R.relayUrl('http://x/api/workspace'), 'http://x/api/hosts/m5/relay/api/workspace');
  assert.strictEqual(R.relayWsPath('/ws/abc?token=t'), '/ws/hosts/m5/relay/ws/abc?token=t');
  assert.strictEqual(R.relayWsPath('/ws-workspace'), '/ws/hosts/m5/relay/ws-workspace');
  assert.strictEqual(R.hostKey('vt-layout-v1'), 'vt-layout-v1@m5');
});

test('이 기기에 붙은 것은 전환 중에도 로컬에 남는다', async () => {
  const R = await load('m5');
  for (const p of ['/api/auth', '/api/auth/status', '/api/hosts', '/api/hosts/m5/sessions',
    '/api/peer/ping', '/api/push/key', '/api/device-settings', '/api/devices',
    '/api/clipboard/push', '/voice/input', '/api/notify/test']) {
    assert.strictEqual(R.relayPath(p), p, p);
  }
  assert.strictEqual(R.relayWsPath('/ws-notify'), '/ws-notify');
  assert.strictEqual(R.relayWsPath('/ws/remote/m5/dev'), '/ws/remote/m5/dev');
  assert.strictEqual(R.relayUrl('https://elsewhere/api/tmux/sessions'), 'https://elsewhere/api/tmux/sessions');
});

test('저장된 값이 id 형식이 아니면 로컬로 본다', async () => {
  for (const bad of ['', 'local', '../x', 'A B', 'x/y']) {
    const R = await load(bad);
    assert.strictEqual(R.relayHost(), 'local', bad);
  }
});
