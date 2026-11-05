// core/api.js 요청 마감 — 서버가 연결은 받는데 답을 안 하던 사고에서 fetch가
// 영원히 끝나지 않아 폴러가 요청을 쌓고, 도달 불가 배너도 안 떴다. 지키는 것:
//   - 기본 마감: GET 15초, 그 밖 30초. `timeout`으로 바꾸고 0이면 없음.
//   - 마감 초과는 `.timeout = true`, `.status = 0` 오류이고, 도달 실패로 센다('hung').
//   - 본문 읽기(res.json())도 같은 마감 안이다.
//   - 호출부 signal도 존중하고, 호출부가 끊은 건 서버 탓으로 세지 않는다.
//   - timedFetch는 주소·헤더를 바꾸지 않는다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const API_JS = path.join(__dirname, '../js/core/api.js');
const REACH_JS = path.join(__dirname, '../js/core/reachability.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function hang(opts) {
  return new Promise((_, reject) => {
    if (opts && opts.signal) {
      opts.signal.addEventListener('abort', () => {
        const e = new Error('The operation was aborted.'); e.name = 'AbortError'; reject(e);
      });
    }
  });
}
const ok = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

async function load(fetchImpl) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  w.API_BASE = 'http://x';
  w._tokenQuery = '';
  // 마감 값을 기록하고, 15초/30초짜리는 빨리 터뜨린다.
  const delays = [];
  const realSetTimeout = w.setTimeout.bind(w);
  w.setTimeout = (fn, ms, ...rest) => { delays.push(ms); return realSetTimeout(fn, ms >= 1000 ? 15 : ms, ...rest); };
  const calls = [];
  w.fetch = (url, opts) => { calls.push({ url, opts }); return fetchImpl(url, opts, calls.length); };
  const cache = new Map();
  const A = await importFresh(API_JS, env.context, cache);
  const R = await importFresh(REACH_JS, env.context, cache);
  return { A, R, w, delays, calls, document: env.document };
}

test('GET 기본 마감은 15초, 쓰기는 30초', async () => {
  const { A, delays } = await load(() => ok({ a: 1 }));
  assert.deepEqual(await A.vtFetch('/api/x'), { a: 1 });
  assert.ok(delays.includes(A.GET_TIMEOUT_MS));
  assert.strictEqual(A.GET_TIMEOUT_MS, 15000);
  delays.length = 0;
  await A.vtFetch('/api/x', { method: 'POST', body: '{}' });
  assert.ok(delays.includes(A.WRITE_TIMEOUT_MS));
  assert.strictEqual(A.WRITE_TIMEOUT_MS, 30000);
});

test('응답이 안 오면 timeout 오류로 끝나고, 요청은 abort된다', async () => {
  const { A, calls } = await load((url, opts) => hang(opts));
  await assert.rejects(A.vtFetch('/api/x'), (e) => e.timeout === true && e.status === 0 && /응답하지 않습니다/.test(e.message));
  assert.strictEqual(calls[0].opts.signal.aborted, true);
  assert.ok(!('timeout' in calls[0].opts), 'timeout 키는 fetch에 넘기지 않는다');
});

test('abort를 무시하는 fetch여도 마감은 지켜진다', async () => {
  const { A } = await load(() => new Promise(() => {}));
  await assert.rejects(A.vtFetch('/api/x'), (e) => e.timeout === true);
});

test('본문이 마감 안에 안 오면 timeout — 예전처럼 null로 넘기지 않는다', async () => {
  const { A } = await load(() => Promise.resolve({ ok: true, status: 200, json: () => new Promise(() => {}) }));
  await assert.rejects(A.vtFetch('/api/x'), (e) => e.timeout === true);
});

test('JSON이 아닌 본문은 예전처럼 null', async () => {
  const { A } = await load(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError('x')) }));
  assert.strictEqual(await A.vtFetch('/api/x'), null);
});

test('timeout 옵션 — 숫자는 그 값, 0이면 마감 없음', async () => {
  const { A, delays } = await load(() => ok({}));
  await A.vtFetch('/api/x', { timeout: 120000 });
  assert.ok(delays.includes(120000));
  delays.length = 0;
  await A.vtFetch('/api/x', { timeout: 0 });
  assert.ok(!delays.some((d) => d >= 1000), '마감 타이머를 걸지 않는다');
});

test('timeout: 0이면 오래 걸려도 끊지 않는다', async () => {
  const { A } = await load(() => new Promise((r) => setTimeout(() => r({ ok: true, status: 200, json: () => Promise.resolve(7) }), 40)));
  assert.strictEqual(await A.vtFetch('/api/x', { timeout: 0 }), 7);
});

test('마감 초과 두 번이면 hung 배너', async () => {
  const { A, R, document } = await load((url, opts) => hang(opts));
  await assert.rejects(A.vtFetch('/api/a'));
  assert.strictEqual(R.isUnreachable(), false, '한 번은 찰나일 수 있다');
  await assert.rejects(A.apiFetch('http://x/api/b'));
  assert.strictEqual(R.unreachableReason(), 'hung');
  const el = document.getElementById('vt-unreachable-banner');
  assert.ok(el);
  assert.match(el.textContent, /서버가 응답하지 않습니다/);
  assert.match(el.textContent, /fsh restart/);
});

test('정상 응답이 오면 hung 배너가 내려간다', async () => {
  const { A, R } = await load((url, opts, n) => (n <= 2 ? hang(opts) : ok({})));
  await assert.rejects(A.vtFetch('/api/a'));
  await assert.rejects(A.vtFetch('/api/a'));
  assert.strictEqual(R.unreachableReason(), 'hung');
  await A.vtFetch('/api/a');
  assert.strictEqual(R.isUnreachable(), false);
});

test('호출부 signal로 끊으면 그대로 AbortError이고 도달 실패로 세지 않는다', async () => {
  const { A, R, w } = await load((url, opts) => hang(opts));
  for (let i = 0; i < 3; i++) {
    const ctl = new w.AbortController();
    const p = A.vtFetch('/api/x', { signal: ctl.signal });
    ctl.abort();
    await assert.rejects(p, (e) => e.name === 'AbortError' && !e.timeout);
  }
  assert.strictEqual(R.isUnreachable(), false);
});

test('이미 끊긴 signal이면 곧바로 끊는다', async () => {
  const { A, w, calls } = await load((url, opts) => (opts.signal.aborted ? Promise.reject(Object.assign(new Error('a'), { name: 'AbortError' })) : hang(opts)));
  const ctl = new w.AbortController(); ctl.abort();
  await assert.rejects(A.vtFetch('/api/x', { signal: ctl.signal }), (e) => e.name === 'AbortError');
  assert.strictEqual(calls[0].opts.signal.aborted, true);
});

test('apiFetch는 Response를 그대로 돌려주고, 매달리면 timeout', async () => {
  const res = { ok: false, status: 404, json: () => Promise.resolve({}) };
  const { A } = await load(() => Promise.resolve(res));
  assert.strictEqual(await A.apiFetch('http://x/api/y'), res);
  const { A: B } = await load((url, opts) => hang(opts));
  await assert.rejects(B.apiFetch('http://x/api/y', { method: 'POST' }), (e) => e.timeout === true);
});

test('timedFetch는 주소·헤더를 바꾸지 않는다(토큰·중계 없음)', async () => {
  const { A, w, calls } = await load(() => ok({}));
  w.VT_TOKEN = 'secret';
  w._tokenQuery = '?token=secret';
  await A.timedFetch('/api/authz/approvals', { credentials: 'same-origin' });
  assert.strictEqual(calls[0].url, '/api/authz/approvals');
  assert.strictEqual(calls[0].opts.credentials, 'same-origin');
  assert.ok(!calls[0].opts.headers, 'Authorization을 붙이지 않는다');
  assert.ok(calls[0].opts.signal);
});

test('예전 오류 모양은 그대로 — {error,reason}을 메시지로, status·data 유지', async () => {
  const { A } = await load(() => ok({ error: 'nope', reason: '이유' }, 409));
  await assert.rejects(A.vtFetch('/api/x'), (e) => e.message === '이유' && e.status === 409 && e.data.error === 'nope' && !e.timeout);
});

test('fetch 거부는 예전처럼 offline으로 센다', async () => {
  const { A, R } = await load(() => Promise.reject(new TypeError('Failed to fetch')));
  await assert.rejects(A.vtFetch('/api/x'));
  await assert.rejects(A.vtFetch('/api/x'));
  assert.strictEqual(R.unreachableReason(), 'offline');
  await wait(0);
});
