// core/event-log.js — 기록 저장소. 지키는 것: 던지지 않는다, 비밀값을 가린다, 같은 사건은 합친다,
// 새로고침을 넘어 남는다(sessionStorage), 미확인 수는 "마지막 본 뒤"만 센다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MOD = path.join(__dirname, '../js/core/event-log.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(env) {
  env = env || createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const E = await importFresh(MOD, env.context, new Map());
  return { E, env };
}

test('기록한 항목에 시각·등급·출처·문맥이 붙는다', async () => {
  const { E } = await load();
  const id = E.logEvent({ level: 'error', source: 'toast', msg: '저장 실패' });
  const ev = E.getEvent(id);
  assert.strictEqual(ev.level, 'error');
  assert.strictEqual(ev.source, 'toast');
  assert.strictEqual(ev.count, 1);
  assert.ok(ev.ts > 0 && ev.ctx && 'screen' in ev.ctx);
});

test('Error 객체를 받으면 스택이 detail 로, 요청 메타가 req 로 들어간다', async () => {
  const { E } = await load();
  const err = new Error('권한 없음');
  err.request = { method: 'PUT', path: '/api/fs/file?x=1', status: 403, ms: 12, code: 'elevation_required' };
  const id = E.logEvent({ level: 'error', source: 'toast', msg: '저장 실패', error: err });
  const ev = E.getEvent(id);
  assert.match(ev.detail, /권한 없음/);
  assert.strictEqual(ev.req.status, 403);
  assert.strictEqual(ev.req.code, 'elevation_required');
  assert.strictEqual(err._logId, id, '토스트가 같은 Error 로 원인을 찾아 묶을 수 있다');
});

test('토큰·쿠키는 문구·상세·경로 어디에도 원문으로 남지 않는다', async () => {
  const { E } = await load();
  const id = E.logEvent({
    level: 'warn', source: 'api', msg: 'GET /api/x?token=SECRET1',
    detail: 'Cookie: vt_session=SECRET2; Authorization: Bearer SECRET3',
    req: { method: 'GET', path: '/api/x?ticket=SECRET4', status: 500 },
  });
  const dump = JSON.stringify(E.getEvent(id));
  for (const s of ['SECRET1', 'SECRET2', 'SECRET3', 'SECRET4']) assert.ok(!dump.includes(s), `${s} 가 남았다`);
});

test('60초 안에 같은 사건이 반복되면 한 줄에 횟수만 오른다', async () => {
  const { E } = await load();
  const a = E.logEvent({ level: 'warn', source: 'api', msg: 'GET /api/a — HTTP 500', req: { method: 'GET', path: '/api/a', status: 500 } });
  const b = E.logEvent({ level: 'warn', source: 'api', msg: 'GET /api/a — HTTP 500', req: { method: 'GET', path: '/api/a', status: 500 } });
  assert.strictEqual(a, b);
  assert.strictEqual(E.getEvents().length, 1);
  assert.strictEqual(E.getEvent(a).count, 2);
});

test('등급이나 경로가 다르면 합치지 않는다', async () => {
  const { E } = await load();
  E.logEvent({ level: 'warn', source: 'api', msg: 'm', req: { method: 'GET', path: '/a', status: 500 } });
  E.logEvent({ level: 'error', source: 'api', msg: 'm', req: { method: 'GET', path: '/a', status: 500 } });
  E.logEvent({ level: 'warn', source: 'api', msg: 'm', req: { method: 'GET', path: '/b', status: 500 } });
  assert.strictEqual(E.getEvents().length, 3);
});

test('debug 는 200건, 전체는 500건이 상한이고 debug 가 먼저 밀려난다', async () => {
  const { E } = await load();
  E.logEvent({ level: 'error', source: 'js', msg: 'keep me' });
  for (let i = 0; i < 260; i++) E.logEvent({ level: 'debug', source: 'api', msg: `d${i}` });
  assert.strictEqual(E.getEvents({ levels: ['debug'] }).length, 200);
  assert.strictEqual(E.getEvents({ levels: ['error'] }).length, 1, '오류가 debug 때문에 밀려나면 안 된다');
});

test('필터: 등급·검색어', async () => {
  const { E } = await load();
  E.logEvent({ level: 'error', source: 'toast', msg: '업로드 실패' });
  E.logEvent({ level: 'info', source: 'toast', msg: '복사됨' });
  assert.strictEqual(E.getEvents({ levels: ['error'] }).length, 1);
  assert.strictEqual(E.getEvents({ q: '업로드' }).length, 1);
  assert.strictEqual(E.getEvents({ q: '없는말' }).length, 0);
});

test('미확인 수는 마지막으로 본 뒤의 오류·경고만 센다', async () => {
  const { E } = await load();
  E.logEvent({ level: 'error', source: 'js', msg: 'a' });
  E.logEvent({ level: 'info', source: 'toast', msg: 'b' });
  assert.strictEqual(E.unseenCount(), 1);
  E.markSeen();
  assert.strictEqual(E.unseenCount(), 0);
  await new Promise((r) => setTimeout(r, 5));
  E.logEvent({ level: 'warn', source: 'net', msg: 'c' });
  assert.strictEqual(E.unseenCount(), 1);
});

test('구독자는 new / merge / seen / clear 를 받고, 구독자가 던져도 기록은 계속된다', async () => {
  const { E } = await load();
  const seen = [];
  E.subscribeEvents((ev, kind) => { seen.push(kind); throw new Error('구독자 오류'); });
  E.logEvent({ level: 'error', source: 'js', msg: 'x' });
  E.logEvent({ level: 'error', source: 'js', msg: 'x' });
  E.markSeen();
  E.clearLocal();
  assert.deepStrictEqual(seen, ['new', 'merge', 'seen', 'clear']);
});

test('기록은 sessionStorage 로 새로고침을 넘어 남는다', async () => {
  const first = await load();
  first.E.logEvent({ level: 'error', source: 'js', msg: '새로고침 전' });
  await new Promise((r) => setTimeout(r, 300));   // 저장은 250ms 로 묶는다
  const raw = first.env.window.sessionStorage.getItem('vt.eventlog.v1');
  assert.ok(raw && raw.includes('새로고침 전'));
  // 같은 sessionStorage 를 가진 새 모듈 인스턴스 = 새로고침
  const second = createDomEnv('<!doctype html><html><body></body></html>');
  second.window.sessionStorage.setItem('vt.eventlog.v1', raw);
  const { E } = await load(second);
  assert.strictEqual(E.getEvents()[0].msg, '새로고침 전');
});

test('저장소 접근이 막혀도(throw) 던지지 않고 동작한다', async () => {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  Object.defineProperty(env.window, 'sessionStorage', { get() { throw new Error('blocked'); } });
  Object.defineProperty(env.window, 'localStorage', { get() { throw new Error('blocked'); } });
  const { E } = await load(env);
  assert.doesNotThrow(() => E.logEvent({ level: 'error', source: 'js', msg: 'ok' }));
  assert.doesNotThrow(() => E.markSeen());
  assert.strictEqual(E.getEvents().length, 1);
});

test('recentApiId: 최근 API 실패만 돌려준다', async () => {
  const { E } = await load();
  assert.strictEqual(E.recentApiId(3000), '');
  E.logEvent({ level: 'debug', source: 'toast', msg: 't' });
  const api = E.logEvent({ level: 'debug', source: 'api', msg: 'GET /a — HTTP 404', req: { method: 'GET', path: '/a', status: 404 } });
  assert.strictEqual(E.recentApiId(3000), api);
});

test('reportError: 콘솔 출력과 기록을 함께 한다', async () => {
  const { E, env } = await load();
  const calls = [];
  env.window.console.error = (...a) => calls.push(a);
  const id = E.reportError('HUD', new Error('boom'));
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0][0], '[FarShell HUD]');
  assert.match(E.getEvent(id).msg, /HUD: boom/);
});
