// URL 의 ?token= 은 더 이상 인증 수단이 아니다 — env.js 는 값을 읽지 않고 주소창에서만 지운다(쿠키 교환·요청 첨부 없음).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const ENV_JS = path.join(__dirname, '../js/core/env.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(url) {
  const env = createDomEnv('<!doctype html><html><body></body></html>', { url });
  _doms.push(env.dom);
  const calls = [];
  env.window.fetch = (...a) => { calls.push(a); return Promise.resolve({ ok: true }); };
  const mod = await importFresh(ENV_JS, env.context, new Map());
  return { env, mod, calls };
}

test('?token= 링크는 토큰을 쓰지 않고 쿠키 교환도 하지 않으며 주소창에서 지운다', async () => {
  const { env, mod, calls } = await load('http://localhost/?token=SECRET&screen=2#x');
  assert.strictEqual(mod.VT_TOKEN, '');
  assert.strictEqual(mod._tokenQuery, '');
  assert.strictEqual(env.window.VT_TOKEN, '');
  assert.strictEqual(env.window._tokenQuery, '');
  assert.deepStrictEqual(calls, []);
  assert.strictEqual(env.window.location.search, '?screen=2');
  assert.strictEqual(env.window.location.hash, '#x');
  assert.ok(!env.window.location.href.includes('SECRET'));
});

test('토큰이 없는 링크는 그대로 둔다', async () => {
  const { env, mod } = await load('http://localhost/?screen=2');
  assert.strictEqual(mod.VT_TOKEN, '');
  assert.strictEqual(env.window.location.search, '?screen=2');
});
