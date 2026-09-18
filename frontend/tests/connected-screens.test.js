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
