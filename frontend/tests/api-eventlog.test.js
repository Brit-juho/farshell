// core/api.js ↔ core/event-log.js — 실패마다 요청 메타가 Error.request 에 붙고 기록에 남는다.
// 지키는 것: (1) 기존 계약(던지는 예외·반환값)은 그대로, (2) 5xx·네트워크·마감은 warn, 4xx는 debug,
// (3) 호출부가 끊은 요청은 debug, (4) noLog 는 기록하지 않는다(전송 실패가 전송을 부르는 고리 방지),
// (5) 토큰은 어디에도 남지 않는다, (6) 본문은 기록하지 않는다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const res = (body, status) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

async function load(fetchImpl) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  w.API_BASE = 'http://x';
  w._tokenQuery = '?token=SECRETQ';
  w.fetch = fetchImpl;
  const cache = new Map();
  const A = await importFresh(JS('core/api.js'), env.context, cache);
  const E = await importFresh(JS('core/event-log.js'), env.context, cache);
  return { A, E };
}

test('4xx: 던지는 Error 는 그대로, 요청 메타가 붙고 debug 로 기록된다', async () => {
  const { A, E } = await load(() => res({ error: 'elevation_required', reason: '비밀번호 재확인' }, 403));
  const err = await A.vtFetch('/api/fs/file', { method: 'PUT', body: 'SECRETBODY' }).then(() => null, (e) => e);
  assert.strictEqual(err.status, 403);
  assert.strictEqual(err.message, '비밀번호 재확인', '기존 메시지 계약');
  assert.strictEqual(err.request.method, 'PUT');
  assert.strictEqual(err.request.path, '/api/fs/file');
  assert.strictEqual(err.request.code, 'elevation_required');
  const ev = E.getEvents()[0];
  assert.strictEqual(ev.level, 'debug');
  assert.strictEqual(ev.source, 'api');
  assert.strictEqual(ev.req.status, 403);
  assert.strictEqual(err._logId, ev.id);
});

test('5xx 는 warn 이다', async () => {
  const { A, E } = await load(() => res({ error: 'boom' }, 500));
  await A.vtFetch('/api/x').catch(() => {});
  assert.strictEqual(E.getEvents()[0].level, 'warn');
});

test('네트워크 거부는 warn, status 0', async () => {
  const { A, E } = await load(() => Promise.reject(new TypeError('Failed to fetch')));
  const err = await A.vtFetch('/api/x').then(() => null, (e) => e);
  assert.ok(err instanceof Error || err.name === 'TypeError');
  const ev = E.getEvents()[0];
  assert.strictEqual(ev.level, 'warn');
  assert.strictEqual(ev.req.status, 0);
});

test('호출부가 끊은 요청(AbortError)은 debug 다', async () => {
  const { A, E } = await load(() => { const e = new Error('aborted'); e.name = 'AbortError'; return Promise.reject(e); });
  await A.vtFetch('/api/x').catch(() => {});
  assert.strictEqual(E.getEvents()[0].level, 'debug');
});

test('쿼리의 토큰·요청 본문은 기록 어디에도 없다', async () => {
  const { A, E } = await load(() => res({ error: 'boom', reason: 'x' }, 500));
  await A.vtFetch('/api/x?ticket=SECRETT', { method: 'POST', body: JSON.stringify({ password: 'SECRETBODY' }) }).catch(() => {});
  const dump = JSON.stringify(E.getEvents());
  for (const s of ['SECRETT', 'SECRETQ', 'SECRETBODY']) assert.ok(!dump.includes(s), `${s} 가 기록에 남았다`);
});

test('noLog 옵션이면 실패해도 기록하지 않는다 (전송 → 실패 → 기록 → 전송 고리 방지)', async () => {
  const { A, E } = await load(() => res({ error: 'boom' }, 500));
  await A.vtFetch('/api/client-log', { method: 'POST', noLog: true }).catch(() => {});
  assert.strictEqual(E.getEvents().length, 0);
});

test('timedFetch: Response 를 그대로 돌려주고 비정상 상태만 기록한다', async () => {
  const { A, E } = await load(() => res({}, 502));
  const r = await A.timedFetch('http://x/api/y?token=SECRETQ2', { method: 'GET' });
  assert.strictEqual(r.status, 502);
  const ev = E.getEvents()[0];
  assert.strictEqual(ev.level, 'warn');
  assert.ok(!JSON.stringify(ev).includes('SECRETQ2'));
  assert.strictEqual(ev.req.path.split('?')[0], '/api/y');
});

test('정상 응답은 기록하지 않는다', async () => {
  const { A, E } = await load(() => res({ ok: 1 }, 200));
  await A.vtFetch('/api/x');
  await A.timedFetch('http://x/api/y');
  assert.strictEqual(E.getEvents().length, 0);
});
