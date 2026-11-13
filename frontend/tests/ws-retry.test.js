// core/ws-retry.js — 지터 백오프, 화면 복귀 신호, 연결 기한(R1).
// 지키는 것: 지연이 [c/2, c) 안이고 30초를 넘지 않는다, 숨은 동안에는 신호가 없다,
// 10초 이상 숨었다 오면 long·bfcache는 long·online은 network, 대기 중인 소켓은 복귀 즉시 붙고
// 열린 소켓은 건드리지 않는다, 열리다 멈춘 소켓은 10초 기한으로 닫는다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = path.join(__dirname, '../js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function newEnv() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  let hidden = false;
  Object.defineProperty(env.document, 'hidden', { get: () => hidden, configurable: true });
  env.setHidden = (h) => { hidden = h; env.document.dispatchEvent(new env.window.Event('visibilitychange')); };
  return env;
}

function fakeSockets(window) {
  const made = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.sent = []; this.closes = []; this.readyState = 0; made.push(this); }
    send(t) { this.sent.push(JSON.parse(t)); }
    close(code, reason) { this.closes.push([code, reason]); }
    open() { this.readyState = 1; this.onopen && this.onopen(); }
    drop(code) { this.readyState = 3; this.onclose && this.onclose({ code }); }
  }
  window.WebSocket = FakeWebSocket;
  return made;
}

test('retryDelay — [c/2, c) 범위, 30초 상한, 터미널 첫 값 250-500ms', async () => {
  const { retryDelay } = await import(pathToFileURL(path.join(JS, 'core/ws-retry.js')).href);
  assert.strictEqual(retryDelay(0, 500, () => 0), 250);
  assert.ok(Math.abs(retryDelay(0, 500, () => 0.999999) - 500) < 0.01);
  assert.strictEqual(retryDelay(0, 1000, () => 0), 500);
  assert.strictEqual(retryDelay(1, 1000, () => 0), 1000);
  for (const n of [5, 6, 7, 50, 1e9]) {
    assert.ok(retryDelay(n, 1000, () => 0.999999) <= 30000, `n=${n}`);
    assert.ok(retryDelay(n, 500, () => 0) >= 15000 || n < 6, `n=${n}`);
  }
  assert.strictEqual(retryDelay(-3, 1000, () => 0), 500, '음수는 0으로');
  for (let i = 0; i < 200; i++) {
    const d = retryDelay(2, 1000);
    assert.ok(d >= 2000 && d < 4000);
  }
});

test('onWake — short/long/bfcache/network, 숨은 동안은 신호 없음, 해제', async () => {
  const env = newEnv();
  const R = await importFresh(path.join(JS, 'core/ws-retry.js'), env.context, new Map());
  let now = 1000;
  env.window.Date.now = () => now;
  const seen = [];
  const off = R.onWake((e) => seen.push(e));

  env.setHidden(true); now += 9900; env.setHidden(false);
  assert.deepEqual(seen.at(-1), { reason: 'short', hiddenMs: 9900 });
  env.setHidden(true); now += 10000; env.setHidden(false);
  assert.deepEqual(seen.at(-1), { reason: 'long', hiddenMs: 10000 });

  const pageshow = (persisted) => { const e = new env.window.Event('pageshow'); e.persisted = persisted; env.window.dispatchEvent(e); };
  const n = seen.length;
  pageshow(false);
  assert.strictEqual(seen.length, n, 'persisted가 아니면 무시');
  pageshow(true);
  assert.strictEqual(seen.at(-1).reason, 'long');

  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(seen.at(-1).reason, 'network');

  env.setHidden(true);
  const m = seen.length;
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(seen.length, m, '숨은 동안에는 콜백 없음');
  env.setHidden(false);

  off();
  const k = seen.length;
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(seen.length, k, '해제 뒤에는 없음');
});

test('onWake — 구독자 하나가 던져도 나머지는 받는다', async () => {
  const env = newEnv();
  const R = await importFresh(path.join(JS, 'core/ws-retry.js'), env.context, new Map());
  let got = 0;
  R.onWake(() => { throw new Error('x'); });
  R.onWake(() => { got++; });
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(got, 1);
});

test('workspace-ws — 닫혀 대기 중이면 복귀 즉시 하나만 붙고, 열린 소켓은 건드리지 않는다', async () => {
  const env = newEnv();
  const made = fakeSockets(env.window);
  env.window.API_BASE = ''; env.window.WS_BASE = 'ws://x';
  const W = await importFresh(path.join(JS, 'core/workspace-ws.js'), env.context, new Map());
  W.onWorkspaceEvent('tmux_sessions', () => {});
  assert.strictEqual(made.length, 1);
  made[0].open();

  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(made.length, 1, '열린 소켓은 그대로(탐침은 R2)');

  made[0].drop(1006);              // 백오프 타이머(500-1000ms)가 걸린다
  assert.strictEqual(made.length, 1);
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(made.length, 2, '타이머를 기다리지 않고 즉시');
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(made.length, 2, '연결 중에는 새로 열지 않는다');
  await wait(1200);
  assert.strictEqual(made.length, 2, '취소된 옛 타이머가 또 열지 않는다');
});

test('workspace-ws — 열리다 멈추면 연결 기한으로 4000 닫기 후 재연결', async () => {
  const env = newEnv();
  const made = fakeSockets(env.window);
  env.window.API_BASE = ''; env.window.WS_BASE = 'ws://x';
  const real = env.window.setTimeout.bind(env.window);
  env.window.setTimeout = (fn, ms, ...r) => real(fn, ms === 10000 ? 5 : ms, ...r);   // 연결 기한만 빨리
  const W = await importFresh(path.join(JS, 'core/workspace-ws.js'), env.context, new Map());
  W.onWorkspaceEvent('tmux_sessions', () => {});
  await wait(30);
  assert.deepEqual(made[0].closes[0], [4000, 'client heartbeat timeout']);
});

test('fs-watch — 닫혀 대기 중이면 복귀 즉시 붙는다', async () => {
  const env = newEnv();
  const made = fakeSockets(env.window);
  const F = await importFresh(path.join(JS, 'shell/fs-watch.js'), env.context, new Map());
  F.watchPath('file', '/a', () => {});
  await wait(0);
  made[0].open();
  made[0].drop(1006);
  assert.strictEqual(made.length, 1);
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(made.length, 2);
  env.window.dispatchEvent(new env.window.Event('online'));
  assert.strictEqual(made.length, 2);
});

// term/ws.js·agent/status.js·voice/*는 부팅 의존이 커서 소스 배선만 확인한다(실동작은 격리 서버·실기기 시험).
test('소비처 배선 — 지터·복귀·연결 기한을 모두 ws-retry.js에서 가져온다', () => {
  const src = (f) => fs.readFileSync(path.join(JS, f), 'utf8');
  for (const f of ['term/ws.js', 'agent/status.js', 'core/workspace-ws.js', 'shell/fs-watch.js', 'voice/notify.js']) {
    const s = src(f);
    assert.match(s, /from '\.\.?\/(core\/)?ws-retry\.js'/, f);
    assert.match(s, /retryDelay\(/, f);
    assert.match(s, /onWake\(/, f);
    assert.match(s, /CONNECT_TIMEOUT_MS/, f);
  }
  assert.match(src('voice/tts.js'), /retryDelay\(/);
  assert.doesNotMatch(src('voice/tts.js'), /onWake|CONNECT_TIMEOUT_MS/, 'tts는 지터만(4000은 교체 의미)');
  // 옛 고정 백오프 수식이 남지 않았다
  for (const f of ['term/ws.js', 'agent/status.js', 'core/workspace-ws.js', 'shell/fs-watch.js', 'voice/notify.js', 'voice/tts.js']) {
    assert.doesNotMatch(src(f), /\d+ \* Math\.pow\(2|\*\* Math\.min/, f);
  }
  // 화면 복귀로 retries를 0으로 되돌려도 term.reset이 판정되도록 별도 플래그를 쓴다
  assert.match(src('term/ws.js'), /_retries > 0 \|\| _everOpened/);
});
