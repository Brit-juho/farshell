// FarShell 메뉴의 「업데이트 있음」 줄과 자동 업데이트 결과 알림(shell/TitleBar.tsx · shell/update-data.js).
// 서버 계약은 API.md «Update» — 여기서는 GET /api/update/status 응답을 픽스처로 쓴다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const BODY = INDEX_HTML.slice(INDEX_HTML.indexOf('<header id="vt-titlebar"'), INDEX_HTML.indexOf('<header id="topbar">'));

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });
const flush = (w) => new Promise((r) => w.setTimeout(r, 30));

async function fresh() {
  const env = await loadSolidBuild();
  _doms.push(env);
  env.document.body.innerHTML = BODY;
  return env;
}

const status = (over = {}) => ({
  available: false, applicable: false, latest: '2.10.1', latest_tag: 'v2.10.1', current: '2.10.1', last_apply: null,
  settings: { check: true, auto: 'off', auto_hour: 4 }, ...over,
});

function setup(env, st, { first = false } = {}) {
  const calls = []; const toasts = []; const listeners = {}; let current = st; let fetches = 0;
  const actions = { 'update.open': () => calls.push('update.open'), 'quickopen.open': () => {} };
  const deps = {
    vtFetch: async (p) => {
      if (p === '/api/update/status') { fetches += 1; return current; }
      if (p === '/api/capabilities') return { hostname: 'sample-mac' };
      if (p === '/api/hosts') return { hosts: [{ id: 'local', label: 'sample-mac', online: true }] };
      return null;
    },
    getAction: (n) => actions[n],
    toast: (m, type) => toasts.push([m, type]),
    isMac: true,
    crumb: () => ({}),
    subscribeEvent: (type, fn) => { (listeners[type] ||= new Set()).add(fn); return () => listeners[type].delete(fn); },
  };
  return { deps, calls, toasts, emit: (type, msg) => (listeners[type] || []).forEach((f) => f(msg)), set: (s) => { current = s; }, fetches: () => fetches };
}

const menuLabels = (document) => Array.from(document.querySelectorAll('.vt-mb-pop .vt-mb-item .vt-mb-label')).map((e) => e.textContent);

test('업데이트가 없으면 메뉴에 업데이트 줄이 없다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const s = setup(env, status());
  env.mountTitleBar(tb, s.deps);
  await flush(window);
  tb.querySelector('.vt-mb-menu').click();
  await flush(window);
  assert.ok(!menuLabels(document).some((l) => l.includes('업데이트')), menuLabels(document).join('|'));
  assert.deepStrictEqual(s.toasts, []);
});

test('업데이트가 있으면 맨 위에 「업데이트 있음 (vX.Y.Z)」 줄이 뜨고, 누르면 설정 > 업데이트를 연다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const s = setup(env, status({ available: true, applicable: true, latest: '2.10.2', latest_tag: 'v2.10.2' }));
  env.mountTitleBar(tb, s.deps);
  await flush(window);
  tb.querySelector('.vt-mb-menu').click();
  await flush(window);
  const labels = menuLabels(document);
  assert.strictEqual(labels[0], '업데이트 있음 (v2.10.2)');
  assert.ok(labels.includes('새 세션') && labels.includes('설정'), '나머지 항목은 그대로');
  document.querySelector('.vt-mb-pop .vt-mb-item').click();
  assert.deepStrictEqual(s.calls, ['update.open']);
  assert.strictEqual(document.querySelector('.vt-mb-pop'), null);
});

test('서버가 update_status 를 밀어 주면 다시 읽어 줄이 나타난다, 메뉴를 열 때도 다시 읽는다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const s = setup(env, status());
  env.mountTitleBar(tb, s.deps);
  await flush(window);
  const before = s.fetches();
  s.set(status({ available: true, latest: '2.11.0', latest_tag: 'v2.11.0' }));
  s.emit('update_status', { type: 'update_status', kind: 'checked' });
  await flush(window);
  assert.ok(s.fetches() > before);
  tb.querySelector('.vt-mb-menu').click();
  await flush(window);
  assert.strictEqual(menuLabels(document)[0], '업데이트 있음 (v2.11.0)');
});

test('자동 업데이트 결과는 한 번만 토스트로 알린다 (완료 / 실패·되돌림)', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const ok = status({ last_apply: { result: 'ok', at: 1000, from: '2.10.1', to: '2.10.2', reason: null, rolled_back: false } });
  const s = setup(env, ok);
  env.mountTitleBar(tb, s.deps);
  await flush(window);
  assert.deepStrictEqual(s.toasts, [['자동 업데이트 완료 v2.10.2', 'success']]);
  s.emit('update_status', {});
  await flush(window);
  assert.strictEqual(s.toasts.length, 1, '같은 결과를 다시 알리지 않는다');
  s.set(status({ last_apply: { result: 'failed', at: 2000, from: '2.10.2', to: '2.10.3', reason: '프런트엔드 빌드 실패:\nboom', rolled_back: true } }));
  s.emit('update_status', {});
  await flush(window);
  assert.deepStrictEqual(s.toasts[1], ['자동 업데이트 실패: 프런트엔드 빌드 실패: (되돌림)', 'error']);
});

test('서버가 재시작 직전에 update_restarting 을 보내면 재연결을 기다리라고 알린다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const s = setup(env, status());
  env.mountTitleBar(tb, s.deps);
  await flush(window);
  s.emit('update_restarting', { type: 'update_restarting', version: '2.10.2' });
  assert.strictEqual(s.toasts.length, 1);
  assert.match(s.toasts[0][0], /v2\.10\.2/);
  assert.match(s.toasts[0][0], /다시 연결/);
  assert.strictEqual(s.toasts[0][1], 'info');
});

test('상태를 못 읽어도(null) 메뉴는 그대로 열린다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const s = setup(env, null);
  env.mountTitleBar(tb, s.deps);
  tb.querySelector('.vt-mb-menu').click();
  await flush(window);
  assert.strictEqual(menuLabels(document)[0], '새 세션');
});
