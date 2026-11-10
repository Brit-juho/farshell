// core/api.js 로그인 전 대기 관문 — 로그인 화면이 떠 있는 동안(`__vtAuthed === false`) 인증이 필요한 API를
// 보내지 않는다(서버가 틀린 자격을 사전 인증 실패로 세어 잠그기 때문). 지키는 것:
//   - vtFetch·vtFetchConditional·apiFetch·timedFetch 모두 `vt:authed` 전에는 fetch를 안 부른다.
//   - `/api/auth*`와 `/api/health/live`는 기다리지 않는다(`/api/authz/*`는 기다린다).
//   - 응답 마감은 기다림이 끝난 뒤 시작한다.
//   - 기다리는 중 pagehide면 요청을 보내지 않고 거부한다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const API_JS = path.join(__dirname, '../js/core/api.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const ok = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

async function load(authed) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  w.API_BASE = 'http://x';
  w._tokenQuery = '';
  if (authed === false) w.__vtAuthed = false;
  const delays = [];
  const realSetTimeout = w.setTimeout.bind(w);
  w.setTimeout = (fn, ms, ...rest) => { delays.push(ms); return realSetTimeout(fn, ms >= 1000 ? 15 : ms, ...rest); };
  const calls = [];
  w.fetch = (url) => { calls.push(url); return ok({ a: 1 }); };
  const A = await importFresh(API_JS, env.context, new Map());
  const authedNow = () => { w.__vtAuthed = true; env.document.dispatchEvent(new w.Event('vt:authed')); };
  return { A, w, calls, delays, authedNow, document: env.document };
}

test('로그인 전에는 네 가지 래퍼 모두 fetch를 부르지 않고, vt:authed 뒤에 부른다', async () => {
  const { A, calls, authedNow } = await load(false);
  const ps = [
    A.vtFetch('/api/a'),
    A.vtFetchConditional('/api/b'),
    A.apiFetch('http://x/api/c'),
    A.timedFetch('/api/d'),
  ];
  await wait(30);
  assert.deepEqual(calls, []);
  authedNow();
  const [j, cond, r1, r2] = await Promise.all(ps);
  assert.deepEqual(j, { a: 1 });
  assert.strictEqual(cond.status, 200);
  assert.strictEqual(r1.status, 200);
  assert.strictEqual(r2.status, 200);
  assert.strictEqual(calls.length, 4);
});

test('/api/auth*와 /api/health/live는 기다리지 않는다', async () => {
  const { A, calls } = await load(false);
  await A.vtFetch('/api/auth/status');
  await A.vtFetch('/api/auth/elevate', { method: 'POST' });
  await A.vtFetch('/api/auth');
  await A.apiFetch('http://x/api/auth/status?x=1');
  await A.timedFetch('/api/health/live');
  assert.strictEqual(calls.length, 5);
});

test('/api/authz/* 는 기다린다(접두 문자열이 같아도 /api/auth가 아니다)', async () => {
  const { A, calls, authedNow } = await load(false);
  const p = A.vtFetch('/api/authz/grants');
  await wait(30);
  assert.strictEqual(calls.length, 0);
  authedNow();
  await p;
  assert.strictEqual(calls.length, 1);
});

test('이미 로그인된 상태(또는 값 없음)에서는 바로 보낸다', async () => {
  const { A, calls } = await load(true);
  await A.vtFetch('/api/a');
  assert.strictEqual(calls.length, 1);
});

test('기다린 시간은 응답 마감에 포함되지 않는다', async () => {
  const { A, calls, delays, authedNow } = await load(false);
  const p = A.vtFetch('/api/a', { timeout: 5000 });
  await wait(60);
  assert.deepEqual(delays.filter((d) => d === 5000), []);   // 기다리는 동안 마감 타이머가 없다
  authedNow();
  assert.deepEqual(await p, { a: 1 });
  assert.ok(delays.includes(5000));                          // 마감은 기다림이 끝난 뒤에 걸렸다
  assert.strictEqual(calls.length, 1);
});

test('기다리는 중 pagehide면 요청을 보내지 않고 거부한다', async () => {
  const { A, w, calls } = await load(false);
  const p = A.vtFetch('/api/a');
  const q = A.apiFetch('http://x/api/b');
  await wait(10);
  w.dispatchEvent(new w.Event('pagehide'));
  await assert.rejects(p, (e) => e.name === 'AbortError');
  await assert.rejects(q, (e) => e.name === 'AbortError');
  assert.strictEqual(calls.length, 0);
});
