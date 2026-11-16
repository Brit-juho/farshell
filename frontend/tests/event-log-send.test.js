// core/event-log-send.js — 오류·경고를 서버로 보내는 규칙.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const resp = (status, headers) => Promise.resolve({
  ok: status < 400, status, headers: { get: (k) => (headers || {})[k] ?? null }, json: () => Promise.resolve({}),
});

async function load(handler) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  w.API_BASE = 'http://x';
  const delays = [];
  const real = w.setTimeout.bind(w);
  w.setTimeout = (fn, ms, ...rest) => { delays.push(ms); // 30000 은 쓰기 요청의 마감(core/api.js)이다 — 줄이면 멈춘 요청 시험이 마감으로 끝나 버린다.
  return real(fn, ms >= 1000 && ms !== 30000 ? 12 : ms, ...rest); };
  const calls = [];
  w.fetch = (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, opts, events: body.events });
    return handler(calls.length, body.events);
  };
  const cache = new Map();
  const E = await importFresh(JS('core/event-log.js'), env.context, cache);
  const S = await importFresh(JS('core/event-log-send.js'), env.context, cache);
  const R = await importFresh(JS('core/reachability.js'), env.context, cache);
  const A = await importFresh(JS('core/api.js'), env.context, cache);
  S.startEventSender();
  return { E, S, R, A, w, calls, delays };
}

const err = (E, msg = 'x', extra = {}) => E.logEvent({ level: 'error', source: 'js', msg, ...extra });

test('오류는 모아서 한 번에, 같은 서버 주소로 쿠키만 실어 보낸다', async () => {
  const { E, calls } = await load(() => resp(200));
  err(E, 'a'); err(E, 'b');
  await wait(60);
  assert.strictEqual(calls.length, 1, '두 건이 한 요청');
  assert.strictEqual(calls[0].url, 'http://x/api/client-log');
  assert.strictEqual(calls[0].events.length, 2);
  assert.ok(!('Authorization' in (calls[0].opts.headers || {})), '주소·헤더를 바꾸지 않는다(중계 없음)');
});

test('info·success·debug 는 보내지 않는다', async () => {
  const { E, calls } = await load(() => resp(200));
  E.logEvent({ level: 'info', source: 'toast', msg: 'i' });
  E.logEvent({ level: 'success', source: 'toast', msg: 's' });
  E.logEvent({ level: 'debug', source: 'api', msg: 'd' });
  await wait(60);
  assert.strictEqual(calls.length, 0);
});

test('보낸 뒤에는 다시 보내지 않는다', async () => {
  const { E, calls } = await load(() => resp(200));
  err(E, 'a');
  await wait(60);
  await wait(60);
  assert.strictEqual(calls.length, 1);
});

test('반복 횟수가 2의 거듭제곱이 될 때만 다시 보낸다 (2, 4 — 3은 아니다)', async () => {
  const { E, calls } = await load(() => resp(200));
  err(E, 'dup');
  await wait(60);                       // count 1 전송
  err(E, 'dup');                        // count 2 -> 전송
  await wait(60);
  err(E, 'dup');                        // count 3 -> 보내지 않음
  await wait(60);
  assert.deepStrictEqual(calls.map((c) => c.events[0].count), [1, 2]);
  err(E, 'dup');                        // count 4
  await wait(60);
  assert.strictEqual(calls.at(-1).events[0].count, 4);
});

test('실패하면 5초→15초→60초로 늘려 다시 보내고, 성공하면 비운다', async () => {
  let n = 0;
  const { E, calls, delays } = await load(() => (++n < 4 ? resp(500) : resp(200)));
  err(E, 'a');
  await wait(300);
  assert.strictEqual(calls.length, 4);
  assert.deepStrictEqual(delays.filter((d) => d >= 1000 && d !== 30000), [2000, 5000, 15000, 60000]);
});

test('401/403 이면 이 탭에서 보내기를 끈다', async () => {
  const { E, S, calls } = await load(() => resp(403));
  err(E, 'a');
  await wait(60);
  assert.strictEqual(S.sendState(), 'disabled');
  err(E, 'b');
  await wait(60);
  assert.strictEqual(calls.length, 1, '꺼진 뒤에는 시도하지 않는다');
});

test('429 는 Retry-After 만큼 기다린다', async () => {
  let n = 0;
  const { E, calls, delays } = await load(() => (++n === 1 ? resp(429, { 'Retry-After': '7' }) : resp(200)));
  err(E, 'a');
  await wait(100);
  assert.strictEqual(calls.length, 2);
  assert.ok(delays.includes(7000));
});

test('413 이면 묶음을 반으로 줄여 다시 보낸다', async () => {
  const { E, calls } = await load((n, events) => (events.length > 1 ? resp(413) : resp(200)));
  err(E, 'a'); err(E, 'b'); err(E, 'c'); err(E, 'd');
  await wait(100);
  assert.deepStrictEqual(calls.map((c) => c.events.length).slice(0, 3), [4, 2, 1]);
});

test('400 은 같은 묶음을 다시 보내도 같으니 버린다 (고리 방지)', async () => {
  const { E, calls } = await load(() => resp(400));
  err(E, 'a');
  await wait(100);
  assert.strictEqual(calls.length, 1);
});

test('서버에 못 닿는 동안(도달 불가)에는 보내지 않는다', async () => {
  const { E, R, calls } = await load(() => resp(200));
  R.noteNetworkError(); R.noteNetworkError();
  assert.ok(R.isUnreachable());
  err(E, 'a');
  await wait(60);
  assert.strictEqual(calls.length, 0);
});

test('전송이 실패해도 그 실패는 기록되지 않는다 (전송→실패→기록→전송 고리 방지)', async () => {
  const { E, calls } = await load(() => resp(500));
  err(E, 'a');
  await wait(100);
  assert.ok(calls.length >= 2);
  assert.strictEqual(E.getEvents().length, 1, '기록은 처음 오류 하나뿐');
});

test('페이지를 떠날 때는 keepalive 로 남은 것을 바로 비운다', async () => {
  const { E, w, calls } = await load(() => resp(200));
  err(E, 'a');
  w.dispatchEvent(new w.Event('pagehide'));
  await wait(5);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].opts.keepalive, true);
});

test('보내는 동안 쌓인 것이 두 번 나가지 않는다', async () => {
  let release;
  const { E, calls } = await load((n) => (n === 1 ? new Promise((r) => { release = () => r({ ok: true, status: 200, headers: { get: () => null } }); }) : resp(200)));
  err(E, 'a');
  await wait(40);
  err(E, 'b');
  await wait(40);
  assert.strictEqual(calls.length, 1, '첫 요청이 끝나기 전에 두 번째가 나가면 안 된다');
  release();
  await wait(60);
  assert.deepStrictEqual(calls.map((c) => c.events.map((e) => e.msg)), [['a'], ['b']]);
});
