// core/poll.js — 폴링 도우미. 서버가 답을 안 하던 사고에서 폴러가 tick마다 새 요청을
// 쌓았다. 지키는 것: 진행 중이면 건너뛰기, 실패 시 간격 늘리기(상한), 성공하면 원래
// 간격, 숨은 탭에서 쉬고 보이면 바로 따라잡기, stop 뒤에는 아무것도 안 하기.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const POLL = pathToFileURL(path.join(__dirname, '../js/core/poll.js')).href;

// 가짜 시계 — later/cancel을 주입한다. advance(ms)는 그 사이 만기된 타이머를 순서대로 실행.
function clock() {
  let now = 0, seq = 0;
  const timers = new Map();
  return {
    later: (fn, ms) => { const id = ++seq; timers.set(id, { at: now + ms, fn }); return id; },
    cancel: (id) => { timers.delete(id); },
    pendingDelays: () => [...timers.values()].map((t) => t.at - now),
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].fn();
        await flush();
      }
      now = end;
      await flush();
    },
  };
}
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

function fakeDoc() {
  const listeners = new Set();
  return {
    hidden: false,
    addEventListener: (type, fn) => { if (type === 'visibilitychange') listeners.add(fn); },
    removeEventListener: (type, fn) => { listeners.delete(fn); },
    set(hidden) { this.hidden = hidden; for (const fn of [...listeners]) fn(); },
    count: () => listeners.size,
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

async function make(fn, opts = {}) {
  const { createPoll } = await import(POLL);
  const c = clock();
  const doc = fakeDoc();
  const p = createPoll(fn, { interval: 1000, doc, later: c.later, cancel: c.cancel, ...opts });
  return { p, c, doc };
}

test('진행 중이면 다음 tick을 쏘지 않는다', async () => {
  let calls = 0;
  const d = deferred();
  const { p, c } = await make(() => { calls++; return d.promise; });
  p.start();
  await c.advance(1000);
  assert.strictEqual(calls, 1);
  await c.advance(10000);
  assert.strictEqual(calls, 1, '매달린 요청 위에 새 요청을 쌓지 않는다');
  d.resolve();
  await flush();
  await c.advance(1000);
  assert.strictEqual(calls, 2);
  p.stop();
});

test('실패하면 간격이 두 배씩 늘고 상한에서 멈춘다, 성공하면 원래대로', async () => {
  let fail = true;
  let calls = 0;
  const { p, c } = await make(() => { calls++; return fail ? Promise.reject(new Error('x')) : Promise.resolve(); }, { maxInterval: 5000 });
  p.start();
  await c.advance(1000); // 1회 실패 → 2000
  assert.deepEqual(c.pendingDelays(), [2000]);
  await c.advance(2000); // 2회 → 4000
  assert.deepEqual(c.pendingDelays(), [4000]);
  await c.advance(4000); // 3회 → 8000이지만 상한 5000
  assert.deepEqual(c.pendingDelays(), [5000]);
  assert.strictEqual(p.state().failures, 3);
  fail = false;
  await c.advance(5000);
  assert.deepEqual(c.pendingDelays(), [1000], '한 번 성공하면 원래 간격');
  assert.strictEqual(p.state().failures, 0);
  assert.strictEqual(calls, 4);
  p.stop();
});

test('동기 예외도 실패로 센다', async () => {
  const { p, c } = await make(() => { throw new Error('boom'); });
  p.start();
  await c.advance(1000);
  assert.strictEqual(p.state().failures, 1);
  p.stop();
});

test('기본 상한은 60초', async () => {
  const { DEFAULT_MAX_INTERVAL } = await import(POLL);
  assert.strictEqual(DEFAULT_MAX_INTERVAL, 60000);
  const { p, c } = await make(() => Promise.reject(new Error('x')), { interval: 20000 });
  p.start();
  await c.advance(20000);
  await c.advance(40000);
  assert.deepEqual(c.pendingDelays(), [60000]);
  p.stop();
});

test('숨은 동안은 쉬고, 보이면 기다리지 않고 한 번 따라잡는다', async () => {
  let calls = 0;
  const { p, c, doc } = await make(() => { calls++; });
  p.start();
  doc.hidden = true;
  await c.advance(10000);
  assert.strictEqual(calls, 0, '숨은 탭에서는 쏘지 않는다');
  assert.deepEqual(c.pendingDelays(), [], '숨은 동안은 예약도 하지 않는다');
  doc.set(false);
  await flush();
  assert.strictEqual(calls, 1, '보이자마자 한 번');
  await c.advance(1000);
  assert.strictEqual(calls, 2, '그 뒤로는 원래 간격');
  p.stop();
});

test('짧게 숨었다 보여도 따라잡는다(panel.js의 예전 동작)', async () => {
  let calls = 0;
  const { p, c, doc } = await make(() => { calls++; });
  p.start();
  await c.advance(500);
  doc.set(true);
  doc.set(false);
  await flush();
  assert.strictEqual(calls, 1);
  assert.deepEqual(c.pendingDelays(), [1000], '따라잡은 뒤 예약이 하나만 남는다');
  p.stop();
});

test('보이게 됐을 때 이미 실행 중이면 겹쳐 쏘지 않는다', async () => {
  let calls = 0;
  const d = deferred();
  const { p, c, doc } = await make(() => { calls++; return d.promise; });
  p.start();
  await c.advance(1000);
  doc.set(true); doc.set(false);
  await flush();
  assert.strictEqual(calls, 1);
  d.resolve();
  p.stop();
});

test('pauseWhenHidden: false면 숨어도 돈다', async () => {
  let calls = 0;
  const { p, c, doc } = await make(() => { calls++; }, { pauseWhenHidden: false });
  p.start();
  doc.hidden = true;
  await c.advance(3000);
  assert.strictEqual(calls, 3);
  assert.strictEqual(doc.count(), 0);
  p.stop();
});

test('immediate면 start에서 바로 한 번', async () => {
  let calls = 0;
  const { p } = await make(() => { calls++; }, { immediate: true });
  p.start();
  await flush();
  assert.strictEqual(calls, 1);
  p.stop();
});

test('kick — 쉬고 있으면 바로, 실행 중이면 끝난 뒤 한 번 더', async () => {
  let calls = 0;
  let d = deferred();
  const { p, c } = await make(() => { calls++; return d.promise; });
  p.start();
  p.kick();
  assert.strictEqual(calls, 1);
  p.kick(); p.kick();
  assert.strictEqual(calls, 1, '실행 중에는 겹치지 않는다');
  const first = d; d = deferred();
  first.resolve();
  await flush();
  assert.strictEqual(calls, 2, '끝나면 밀린 kick을 한 번만');
  d.resolve();
  await flush();
  assert.deepEqual(c.pendingDelays(), [1000]);
  p.stop();
});

test('stop 뒤에는 늦게 끝난 실행이 예약을 만들지 않고, 리스너도 뗀다', async () => {
  let calls = 0;
  const d = deferred();
  const { p, c, doc } = await make(() => { calls++; return d.promise; });
  p.start();
  await c.advance(1000);
  assert.strictEqual(doc.count(), 1);
  p.stop();
  assert.strictEqual(doc.count(), 0);
  d.resolve();
  await flush();
  assert.deepEqual(c.pendingDelays(), []);
  await c.advance(10000);
  assert.strictEqual(calls, 1);
  p.kick();
  assert.strictEqual(calls, 1, '멈춘 뒤 kick은 무시');
});

test('stop 후 start하면 새로 시작한다(이전 실행의 결과와 섞이지 않는다)', async () => {
  let calls = 0;
  const ds = [deferred(), deferred()];
  const { p, c } = await make(() => ds[calls++].promise);
  p.start();
  await c.advance(1000);
  p.stop();
  p.start();
  await c.advance(1000);
  assert.strictEqual(calls, 2, '이전 세대의 pending이 새 세대를 막지 않는다');
  ds[0].reject(new Error('old'));
  await flush();
  assert.strictEqual(p.state().failures, 0, '이전 세대의 실패는 세지 않는다');
  ds[1].resolve();
  await flush();
  assert.ok(p.state().lastOkAt > 0);
  p.stop();
});

test('onSettled가 결과마다 불리고, 던져도 폴링은 계속된다', async () => {
  const seen = [];
  let n = 0;
  const { p, c } = await make(() => (n++ % 2 ? Promise.resolve() : Promise.reject(new Error('e'))), {
    onSettled: (ok) => { seen.push(ok); throw new Error('render'); },
  });
  p.start();
  await c.advance(1000);
  await c.advance(2000);
  assert.deepEqual(seen, [false, true]);
  assert.deepEqual(c.pendingDelays(), [1000]);
  p.stop();
});
