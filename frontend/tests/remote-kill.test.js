// 다른 맥 세션의 「완전 종료」(term/remote.js killRemoteSession).
// 고정하는 것: 확인 뒤 그 맥의 DELETE를 실제로 보낸다. 예전에는 선언하지 않은 변수(catalogSessionId 등)를 읽어
// 확인을 누르자마자 ReferenceError로 멈췄다 — 아무것도 종료되지 않았다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const vm = require('node:vm');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

function stub(context, exports) {
  return new vm.SyntheticModule(Object.keys(exports), function init() {
    for (const [k, v] of Object.entries(exports)) this.setExport(k, v);
  }, { context, identifier: 'stub:dialog-lazy' });
}

async function load({ confirm = true, row = {} } = {}) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  const calls = [];
  env.window.fetch = (url, opts = {}) => {
    calls.push([opts.method || 'GET', String(url)]);
    const body = String(url).includes('/api/hosts/')
      ? { ok: true }
      : { hosts: [{ id: 'm5', label: 'M5', online: true, sessions: [{ name: 's-0000abcd', alias: 'gpu 작업', ...row }] }] };
    return Promise.resolve({ ok: true, status: 200, headers: new env.window.Headers(), json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
  };
  const cache = new Map();
  cache.set(path.resolve(J('core/dialog-lazy.js')), stub(env.context, {
    confirmDialog: async () => confirm, promptDialog: async () => null, alertDialog: async () => {}, prefetchDialogs: () => {},
  }));
  const remote = await importFresh(J('term/remote.js'), env.context, cache);
  return { remote, calls };
}

test('확인하면 그 맥에 종료 요청(DELETE)을 보내고 true', async () => {
  const { remote, calls } = await load();
  const ok = await remote.killRemoteSession('m5', 's-0000abcd');
  assert.strictEqual(ok, true);
  assert.ok(calls.some(([m, u]) => m === 'DELETE' && u.includes('/api/hosts/m5/sessions/s-0000abcd')), JSON.stringify(calls));
});

test('기록 신원이 있는 세션도 같은 경로로 종료한다', async () => {
  const { remote, calls } = await load({ row: { catalog_session_id: 'c'.repeat(32), runtime_id: 'r'.repeat(32), catalog_host: 'h' } });
  assert.strictEqual(await remote.killRemoteSession('m5', 's-0000abcd'), true);
  assert.ok(calls.some(([m]) => m === 'DELETE'));
});

test('취소하면 아무것도 보내지 않는다', async () => {
  const { remote, calls } = await load({ confirm: false });
  assert.strictEqual(await remote.killRemoteSession('m5', 's-0000abcd'), false);
  assert.ok(!calls.some(([m]) => m === 'DELETE'));
});
