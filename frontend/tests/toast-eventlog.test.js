// ui/toast.js ↔ core/event-log.js — 모든 토스트가 기록되고, 실패 카드·상태 막대 칸이 기록 패널로 이어진다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build(opts) {
  const body = opts && opts.hud ? '<footer id="vt-hud"></footer>' : '';
  const env = createDomEnv(`<!doctype html><html><body>${body}</body></html>`);
  _doms.push(env.dom);
  const cache = new Map();
  const T = await importFresh(JS('ui/toast.js'), env.context, cache);
  const E = await importFresh(JS('core/event-log.js'), env.context, cache);
  const D = await importFresh(JS('core/dom.js'), env.context, cache);
  return { window: env.window, T, E, D };
}

test('모든 토스트가 등급·출처와 함께 기록된다 (info 도 남는다)', async () => {
  const { T, E } = await build();
  T.showToast('복사됨', 'success');
  T.showToast('저장 실패', 'error');
  T.showToast('안내');
  const ev = E.getEvents();
  // vm 컨텍스트의 배열이라 프로토타입이 달라 deepStrictEqual 이 거부한다 — JSON 으로 비교한다.
  assert.strictEqual(JSON.stringify(ev.map((e) => [e.level, e.source, e.msg]).reverse()), JSON.stringify([
    ['success', 'toast', '복사됨'], ['error', 'toast', '저장 실패'], ['info', 'toast', '안내'],
  ]));
});

test('Error 객체를 error 옵션으로 넘기면 스택과 요청 정보가 기록에 붙는다', async () => {
  const { T, E } = await build();
  const err = new Error('권한 없음');
  err.request = { method: 'PUT', path: '/api/fs/file', status: 403, ms: 9, code: 'elevation_required' };
  const el = T.showToast('저장 실패', 'error', { error: err });
  const ev = E.getEvent(el.dataset.logId);
  assert.match(ev.detail, /권한 없음/);
  assert.strictEqual(ev.req.code, 'elevation_required');
});

test('오류 카드에는 「자세히」가 달리고, 누르면 그 항목 id 로 기록 패널을 연다', async () => {
  const { window, T, D } = await build();
  const calls = [];
  D.registerAction('eventlog.open', (id) => calls.push(id));
  const el = T.showToast('저장 실패', 'error', { duration: 0 });
  const btn = [...el.querySelectorAll('button')].find((b) => b.textContent === '자세히');
  assert.ok(btn, '실패 카드에 자세히 단추');
  btn.dispatchEvent(new window.Event('click', { bubbles: true }));
  assert.deepStrictEqual(calls, [el.dataset.logId]);
});

test('동작 단추가 이미 둘이면 자세히를 더하지 않는다 (카드 자리)', async () => {
  const { T } = await build();
  const el = T.showToast('실패', 'error', { duration: 0, actions: [
    { label: 'A', onClick() {} }, { label: 'B', onClick() {} },
  ] });
  assert.deepStrictEqual([...el.querySelectorAll('button')].map((b) => b.textContent).filter((x) => x !== ''), ['A', 'B']);
});

test('성공·정보 카드(동작 없음)에는 자세히를 달지 않는다', async () => {
  const { T } = await build();
  const el = T.showToast('복사됨', 'success');
  assert.strictEqual([...el.querySelectorAll('button')].some((b) => b.textContent === '자세히'), false);
});

test('상태 막대 칸을 누르면 닫는 대신 그 항목으로 기록 패널을 연다', async () => {
  const { window, T, D } = await build({ hud: true });
  const calls = [];
  D.registerAction('eventlog.open', (id) => calls.push(id));
  const el = T.showToast('복사됨', 'success');
  const id = el._vtLogId;
  assert.ok(id);
  el.dispatchEvent(new window.Event('click'));
  assert.deepStrictEqual(calls, [id]);
});

test('패널 액션이 아직 없어도(부팅 직후) 누르다 던지지 않는다', async () => {
  const { window, T } = await build({ hud: true });
  const el = T.showToast('복사됨', 'success');
  assert.doesNotThrow(() => el.dispatchEvent(new window.Event('click')));
});

test('key 가 있는 진행 정보는 debug 로 내려 링 버퍼를 밀지 않는다', async () => {
  const { T, E } = await build();
  T.showToast('올리는 중 10%', 'info', { key: 'up' });
  T.showToast('올리는 중 20%', 'info', { key: 'up' });
  assert.strictEqual(JSON.stringify(E.getEvents().map((e) => e.level)), JSON.stringify(['debug', 'debug']));
});

test('error 옵션이 없는 오류 토스트는 3초 안의 최근 API 실패를 원인으로 "추정"해 묶는다', async () => {
  const { T, E } = await build();
  const api = E.logEvent({ level: 'debug', source: 'api', msg: 'POST /api/x — HTTP 409', req: { method: 'POST', path: '/api/x', status: 409 } });
  const el = T.showToast('열 수 없습니다', 'error');
  const ev = E.getEvent(el.dataset.logId);
  assert.strictEqual(ev.rel, api);
  assert.strictEqual(ev.relGuess, true, '추정은 사실처럼 보이면 안 된다');
});

test('error 옵션으로 이미 기록된 Error 는 추정 대신 확정 연결한다', async () => {
  const { T, E } = await build();
  const err = new Error('x');
  const api = E.logEvent({ level: 'warn', source: 'api', msg: 'GET /a — HTTP 500', req: { method: 'GET', path: '/a', status: 500 }, error: err });
  E.logEvent({ level: 'debug', source: 'api', msg: '다른 요청', req: { method: 'GET', path: '/b', status: 404 } });
  const el = T.showToast('실패', 'error', { error: err });
  const ev = E.getEvent(el.dataset.logId);
  assert.strictEqual(ev.rel, api);
  assert.ok(!ev.relGuess);
});
