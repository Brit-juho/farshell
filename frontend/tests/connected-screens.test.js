// ADR-29 E — layout/rail.js를 지우며 유일하게 살아남은 조각(clients.show)의
// 새 집. 레일의 큰 세션 패널 대신 다른 시트들과 같은 backdrop+card 하나뿐이라
// 검증할 것도 그만큼 작다: 토글이 되는지, 활성 세션이 tmux가 아니면 아무것도
// 안 뜨는지, Esc/배경 클릭으로 닫히는지.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const DOM_JS = path.join(__dirname, '../js/core/dom.js');
const CONNECTED_SCREENS_JS = path.join(__dirname, '../js/layout/connected-screens.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  // clients.js의 mountClients가 부르는 vtFetch — 클라이언트 목록은 이 테스트의
  // 관심사가 아니므로(그 로직은 clients.js 자신의 몫) 항상 빈 목록으로 응답한다.
  env.window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ clients: [] }) });
  const toasts = [];
  env.window.showToast = (msg) => toasts.push(msg);
  const cache = new Map();
  const core = await importFresh(CORE_STORE_JS, env.context, cache);
  const dom = await importFresh(DOM_JS, env.context, cache);
  await importFresh(CONNECTED_SCREENS_JS, env.context, cache);
  return { window: env.window, core, dom, toasts };
}

test('clients.show 액션이 등록된다', async () => {
  const { dom } = await load();
  assert.strictEqual(typeof dom.getAction('clients.show'), 'function');
});

test('활성 세션이 tmux가 아니면 아무 패널도 안 뜨고 토스트만 뜬다', async () => {
  const { window, core, dom, toasts } = await load();
  core.registerSession('plain-1', { tmuxName: null });
  core.setActive('plain-1');
  dom.getAction('clients.show')();
  assert.strictEqual(window.document.getElementById('vt-connected-screens'), null);
  assert.deepStrictEqual(toasts, ['tmux 세션이 아닙니다']);
});

test('활성 세션이 tmux면 backdrop+card가 뜬다', async () => {
  const { window, core, dom } = await load();
  core.registerSession('web-1', { tmuxName: 'dev' });
  core.setActive('web-1');
  dom.getAction('clients.show')();
  const el = window.document.getElementById('vt-connected-screens');
  assert.ok(el, '백드롭이 떠야 한다');
  assert.ok(el.querySelector('.vt-wtd-title')?.textContent === '연결된 화면');
});

test('같은 액션을 다시 부르면 토글로 닫힌다', async () => {
  const { window, core, dom } = await load();
  core.registerSession('web-1', { tmuxName: 'dev' });
  core.setActive('web-1');
  dom.getAction('clients.show')();
  assert.ok(window.document.getElementById('vt-connected-screens'));
  dom.getAction('clients.show')();
  assert.strictEqual(window.document.getElementById('vt-connected-screens'), null);
});

test('닫기 버튼을 누르면 사라진다', async () => {
  const { window, core, dom } = await load();
  core.registerSession('web-1', { tmuxName: 'dev' });
  core.setActive('web-1');
  dom.getAction('clients.show')();
  window.document.querySelector('.vt-wtd-x').click();
  assert.strictEqual(window.document.getElementById('vt-connected-screens'), null);
});

test('Escape 키로 닫힌다', async () => {
  const { window, core, dom } = await load();
  core.registerSession('web-1', { tmuxName: 'dev' });
  core.setActive('web-1');
  dom.getAction('clients.show')();
  window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.strictEqual(window.document.getElementById('vt-connected-screens'), null);
});

test('배경(backdrop) 클릭으로 닫힌다 — 카드 안 클릭은 안 닫힌다', async () => {
  const { window, core, dom } = await load();
  core.registerSession('web-1', { tmuxName: 'dev' });
  core.setActive('web-1');
  dom.getAction('clients.show')();
  const card = window.document.querySelector('.vt-wtd-card');
  card.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.ok(window.document.getElementById('vt-connected-screens'), '카드 클릭으로는 안 닫혀야 한다');
  window.document.getElementById('vt-connected-screens').click();
  assert.strictEqual(window.document.getElementById('vt-connected-screens'), null);
});

test('다른 세션의 메뉴에서 열면 그 세션 ID로 나를 조회하고 끊는다', async () => {
  const { window, core } = await load();
  const calls = [];
  window.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, json: async () => opts?.method === 'POST'
      ? { ok: true } : { me_tty: '/dev/pts/2', clients: [
        { tty: '/dev/pts/2', label: '이 화면', is_me: true },
        { tty: '/dev/pts/3', label: '다른 화면', is_me: false },
      ] } };
  };
  core.registerSession('web-1', { tmuxName: 'first' });
  core.registerSession('web-2', { tmuxName: 'second' });
  core.setActive('web-1');
  window.vtShowClients('web-2');
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(calls[0].url, /session=second&me=web-2/);
  window.document.querySelector('.vt-clients-detach').click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepStrictEqual(JSON.parse(calls[1].opts.body), { tty: '/dev/pts/3', me: 'web-2' });
});

test('열린 상태에서 다른 세션을 선택하면 그 세션의 목록으로 전환한다', async () => {
  const { window, core } = await load();
  const urls = [];
  window.fetch = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => ({ clients: [] }) };
  };
  core.registerSession('web-1', { tmuxName: 'first' });
  core.registerSession('web-2', { tmuxName: 'second' });
  window.vtShowClients('web-1');
  window.vtShowClients('web-2');
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(window.document.getElementById('vt-connected-screens'));
  assert.match(urls[urls.length - 1], /session=second&me=web-2/);
});

test('목록 실패를 보여주고 다시 시도로 복구한다', async () => {
  const { window, core } = await load();
  let count = 0;
  window.fetch = async () => {
    count++;
    return count === 1
      ? { ok: false, status: 503, json: async () => ({ reason: '서버 일시 중단' }) }
      : { ok: true, json: async () => ({ me_tty: null, clients: [] }) };
  };
  core.registerSession('web-1', { tmuxName: 'dev' });
  core.setActive('web-1');
  window.vtShowClients();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(window.document.querySelector('.vt-clients-status').textContent, /서버 일시 중단/);
  window.document.querySelector('.vt-clients-status button').click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(window.document.querySelector('.vt-clients-status').textContent, '연결된 화면이 없습니다');
});

test('원격 끊기 403은 읽기 전용 안내로 바뀐다', async () => {
  const { window, core } = await load();
  window.fetch = async (_url, opts) => opts?.method === 'POST'
    ? { ok: false, status: 403, json: async () => ({ reason: '권한이 없습니다' }) }
    : { ok: true, json: async () => ({ me_tty: '/dev/pts/2', clients: [
      { tty: '/dev/pts/2', label: '이 화면', is_me: true },
      { tty: '/dev/pts/3', label: '다른 화면', is_me: false },
    ] }) };
  core.registerSession('remote-1', { tmuxName: 'dev', remote: { host: 'peer', screen: 'screen1' } });
  core.setActive('remote-1');
  window.vtShowClients();
  await new Promise((resolve) => setTimeout(resolve, 0));
  window.document.querySelector('.vt-clients-detach').click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(window.document.querySelector('.vt-clients-note').textContent, /읽기 전용/);
  assert.strictEqual(window.document.querySelector('.vt-clients-detach'), null);
  assert.strictEqual(window.document.querySelector('.vt-clients-head button').hidden, true);
});
