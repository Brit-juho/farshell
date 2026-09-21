// core/reachability.js — 서버 도달 불가 배너의 판정 규칙.
//
// 이 테스트가 지키는 것은 "배너가 예쁘게 뜨는가"가 아니라 **언제 뜨고 언제 안
// 뜨는가**다. 2026-09-21 사고에서 실제로 헷갈렸던 두 가지를 고정한다:
//   1. 죽은 주소(fetch 거부) → 떠야 한다. sw.js가 캐시 셸을 돌려주는 바람에
//      화면이 정상처럼 보이던 그 상태다.
//   2. 401(쿠키 24h 만료) → **뜨면 안 된다.** 서버는 멀쩡히 답한 것이고, 그건
//      로그인 게이트가 처리할 일이다. 이 둘을 한 화면에서 헷갈려 "서버가
//      죽었나"로 오진했다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MOD = path.join(__dirname, '../js/core/reachability.js');
const BANNER = '#vt-unreachable-banner';

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// cache를 매번 새로 만들어 모듈 스코프 카운터(_fails/_reason)를 테스트마다
// 초기 상태로 되돌린다 — layout-store.test.js와 같은 이유.
async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const R = await importFresh(MOD, env.context, new Map());
  return { R, document: env.document };
}

function banner(document) {
  return document.querySelector(BANNER);
}

test('연속 실패가 임계치에 닿기 전에는 배너를 띄우지 않는다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD - 1; i++) R.noteNetworkError();
  assert.strictEqual(banner(document), null, '찰나의 끊김에 화면이 번쩍이면 안 된다');
  assert.strictEqual(R.isUnreachable(), false);
});

test('fetch 거부가 임계치만큼 쌓이면 offline 배너가 뜬다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteNetworkError();
  const el = banner(document);
  assert.ok(el, '주소에 못 닿는 상태는 화면이 말해야 한다');
  assert.strictEqual(el.dataset.reason, 'offline');
  assert.strictEqual(R.unreachableReason(), 'offline');
  assert.match(el.textContent, /연결할 수 없습니다/);
  // 옛 주소로 들어온 경우를 위해 "현재 주소를 확인하라"는 다음 행동이 있어야 한다.
  assert.match(el.textContent, /fsh status/);
});

test('502/503/504는 gateway 배너 — 입구는 살아있고 서버만 내려간 상태', async () => {
  for (const status of [502, 503, 504]) {
    const { R, document } = await load();
    for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteResponse(status);
    const el = banner(document);
    assert.ok(el, `${status}는 도달 불가로 잡혀야 한다`);
    assert.strictEqual(el.dataset.reason, 'gateway');
    assert.match(el.textContent, /복구됩니다/, '재시작 중이면 기다리면 된다고 알려야 한다');
  }
});

test('401은 도달 불가가 아니다 — 로그인 게이트가 처리할 일이다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD + 3; i++) R.noteResponse(401);
  assert.strictEqual(banner(document), null,
    '쿠키 만료로 401이 쏟아질 때 배너가 뜨면 "서버가 죽었나"로 오진하게 된다');
  assert.strictEqual(R.isUnreachable(), false);
});

test('403·404·500도 서버가 답한 것이므로 배너를 띄우지 않는다', async () => {
  for (const status of [403, 404, 500]) {
    const { R, document } = await load();
    for (let i = 0; i < R.FAIL_THRESHOLD + 1; i++) R.noteResponse(status);
    assert.strictEqual(banner(document), null, `${status}: 응답이 왔다는 건 도달했다는 뜻이다`);
  }
});

test('정상 응답 하나로 배너가 내려가고 카운터도 리셋된다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteNetworkError();
  assert.ok(banner(document));

  R.noteResponse(200);
  assert.strictEqual(banner(document), null, '복구되면 즉시 사라져야 한다');
  assert.strictEqual(R.isUnreachable(), false);

  // 카운터가 리셋됐는지 — 안 됐다면 실패 1회로 바로 다시 뜬다.
  R.noteNetworkError();
  assert.strictEqual(banner(document), null, '카운터가 리셋되지 않았다');
});

test('같은 사유로 계속 실패해도 배너를 다시 만들지 않는다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteNetworkError();
  const first = banner(document);
  for (let i = 0; i < 5; i++) R.noteNetworkError();
  assert.strictEqual(banner(document), first, '같은 노드가 그대로 유지돼야 한다');
  assert.strictEqual(document.querySelectorAll(BANNER).length, 1, '배너가 쌓이면 안 된다');
});

test('offline → gateway로 사유가 바뀌면 문구가 갱신된다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteNetworkError();
  assert.strictEqual(banner(document).dataset.reason, 'offline');

  // 터널이 다시 뜨고 서버만 아직 안 올라온 상황 — 사유가 바뀐다.
  for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteResponse(502);
  const el = banner(document);
  assert.strictEqual(el.dataset.reason, 'gateway');
  assert.match(el.textContent, /응답하지 않습니다/);
  assert.strictEqual(document.querySelectorAll(BANNER).length, 1);
});

test('배너는 스크린리더에 상태로 알려진다', async () => {
  const { R, document } = await load();
  for (let i = 0; i < R.FAIL_THRESHOLD; i++) R.noteNetworkError();
  const el = banner(document);
  assert.strictEqual(el.getAttribute('role'), 'status');
  assert.strictEqual(el.getAttribute('aria-live'), 'polite');
});
