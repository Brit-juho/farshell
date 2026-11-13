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
  // 탐침(R2): 다섯 소켓이 쓰고, tts는 4000 충돌로 제외
  for (const f of ['term/ws.js', 'agent/status.js', 'core/workspace-ws.js', 'shell/fs-watch.js', 'voice/notify.js']) {
    assert.match(src(f), /startProbe\(/, f);
    assert.match(src(f), /\.check\(reason\)/, f);
  }
  assert.doesNotMatch(src('voice/tts.js'), /startProbe/);
  assert.match(src('voice/tts.js'), /retryDelay\(/);
  assert.doesNotMatch(src('voice/tts.js'), /onWake|CONNECT_TIMEOUT_MS/, 'tts는 지터만(4000은 교체 의미)');
  // 옛 고정 백오프 수식이 남지 않았다
  for (const f of ['term/ws.js', 'agent/status.js', 'core/workspace-ws.js', 'shell/fs-watch.js', 'voice/notify.js', 'voice/tts.js']) {
    assert.doesNotMatch(src(f), /\d+ \* Math\.pow\(2|\*\* Math\.min/, f);
  }
  // 화면 복귀로 retries를 0으로 되돌려도 term.reset이 판정되도록 별도 플래그를 쓴다
  assert.match(src('term/ws.js'), /_retries > 0 \|\| _everOpened/);
});

// ── 생존 탐침(R2) ──────────────────────────────────────────────────────────

function clock() {
  let now = 0, seq = 0;
  const timers = new Map();
  return {
    later: (fn, ms) => { const id = ++seq; timers.set(id, { at: now + ms, fn }); return id; },
    cancel: (id) => { timers.delete(id); },
    size: () => timers.size,
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].fn();
      }
      now = end;
    },
  };
}
function pSock() {
  const s = {
    readyState: 1, sent: [], closes: [], onclose: null, onmessage: null, onerror: null,
    send(t) { s.sent.push(JSON.parse(t)); },
    close(code, reason) { s.closes.push([code, reason]); s.readyState = 2; },
  };
  return s;
}
const PR = pathToFileURL(path.join(JS, 'core/ws-retry.js')).href;

test('탐침 — 열린 직후 ack를 받으면 capable, 이후 long 복귀에서 탐침을 보낸다', async () => {
  const { startProbe } = await import(PR);
  const c = clock(); const s = pSock();
  const p = startProbe(s, { later: c.later, cancel: c.cancel, rand: () => 0 });
  p.start();
  assert.deepEqual(s.sent, [{ type: 'probe', id: 1 }]);
  assert.strictEqual(p.capable(), false);
  p.frame({ type: 'probe_ack', id: 1 });
  assert.strictEqual(p.capable(), true);
  assert.strictEqual(c.size(), 0, '답이 오면 기한 타이머를 치운다');
  p.check('short');
  assert.strictEqual(s.sent.length, 1, 'short은 아무것도 안 함');
  p.check('long');
  assert.deepEqual(s.sent.at(-1), { type: 'probe', id: 2 });
});

test('탐침 — ack가 없으면 capable이 아니고, 그런 소켓은 절대 닫지 않는다(옛 서버)', async () => {
  const { startProbe, PROBE_CAPABLE_MS } = await import(PR);
  const c = clock(); const s = pSock();
  let dead = 0;
  s.onclose = () => { dead++; };
  const p = startProbe(s, { later: c.later, cancel: c.cancel, rand: () => 0, onDead: () => { dead++; } });
  p.start();
  c.advance(PROBE_CAPABLE_MS + 1);
  assert.strictEqual(p.capable(), false);
  p.check('long'); p.check('network');
  assert.strictEqual(s.sent.length, 1, 'capable이 아니면 탐침도 안 보낸다');
  c.advance(60000);
  assert.deepEqual(s.closes, []);
  assert.strictEqual(dead, 0);
});

test('탐침 — ack 말고 다른 프레임만 와도 capable은 아니지만 cap 탐침을 끝낸다', async () => {
  const { startProbe } = await import(PR);
  const c = clock(); const s = pSock();
  const p = startProbe(s, { later: c.later, cancel: c.cancel });
  p.start();
  p.frame({ type: 'ping' });
  assert.strictEqual(p.capable(), false);
  p.frame({ type: 'probe_ack', id: 1 });   // 늦게 온 ack는 그 탐침 것이라 인정
  assert.strictEqual(p.capable(), true);
});

test('탐침 — 기한 안에 아무 프레임이나 오면 살아 있음(교체 없음)', async () => {
  const { startProbe, PROBE_LONG_MS } = await import(PR);
  const c = clock(); const s = pSock();
  let dead = 0;
  const p = startProbe(s, { later: c.later, cancel: c.cancel, rand: () => 0, onDead: () => { dead++; } });
  p.start(); p.frame({ type: 'probe_ack', id: 1 });
  p.check('long');
  c.advance(PROBE_LONG_MS - 1);
  p.frame(null);                           // 바이너리 출력
  c.advance(60000);
  assert.deepEqual(s.closes, []);
  assert.strictEqual(dead, 0);
});

test('탐침 — long 기한 2.5초 초과면 핸들러를 뗀 뒤 4000으로 한 번만 닫고, 옛 onclose·onDead 각 1회', async () => {
  const { startProbe, PROBE_LONG_MS } = await import(PR);
  const c = clock(); const s = pSock();
  const seen = []; let dead = 0;
  s.onclose = (ev) => seen.push(ev.code);
  s.onmessage = () => {}; s.onerror = () => {};
  const p = startProbe(s, { later: c.later, cancel: c.cancel, rand: () => 0.5, onDead: () => { dead++; } });
  p.start(); p.frame({ type: 'probe_ack', id: 1 });
  p.check('long');
  c.advance(PROBE_LONG_MS - 1);
  assert.deepEqual(s.closes, []);
  c.advance(1);
  assert.deepEqual(s.closes, [[4000, 'probe timeout']]);
  assert.deepEqual(seen, [4000], '소유자의 onclose가 정리를 위해 한 번 불린다');
  assert.strictEqual(s.onclose, null); assert.strictEqual(s.onmessage, null); assert.strictEqual(s.onerror, null);
  assert.strictEqual(dead, 0, 'onDead는 0-250ms 엇갈려 불린다');
  c.advance(125);
  assert.strictEqual(dead, 1);
  c.advance(60000);
  assert.deepEqual(s.closes.length, 1); assert.strictEqual(dead, 1);
  p.check('long');                          // 끝난 탐침은 다시 동작하지 않는다
  assert.strictEqual(s.sent.length, 2);
});

test('탐침 — network는 3초 기한, 진행 중이면 중복 탐침을 보내지 않고, stop 뒤에는 아무것도 안 함', async () => {
  const { startProbe, PROBE_NETWORK_MS } = await import(PR);
  const c = clock(); const s = pSock();
  const p = startProbe(s, { later: c.later, cancel: c.cancel, rand: () => 0, onDead: () => {} });
  p.start(); p.frame({ type: 'probe_ack', id: 1 });
  p.check('network'); p.check('network');
  assert.strictEqual(s.sent.length, 2, '탐침 하나만 진행');
  c.advance(PROBE_NETWORK_MS - 1);
  assert.deepEqual(s.closes, []);
  p.stop();
  c.advance(60000);
  assert.deepEqual(s.closes, []);
});

test('탐침 — 소켓이 열려 있지 않으면 보내지 않는다', async () => {
  const { startProbe } = await import(PR);
  const c = clock(); const s = pSock(); s.readyState = 0;
  const p = startProbe(s, { later: c.later, cancel: c.cancel });
  p.start();
  assert.deepEqual(s.sent, []);
});

test('workspace-ws — 서버가 ack하면 복귀 탐침이 통과하고, 죽은 소켓이면 교체한다', async () => {
  const env = newEnv();
  const made = fakeSockets(env.window);
  env.window.API_BASE = ''; env.window.WS_BASE = 'ws://x';
  const real = env.window.setTimeout.bind(env.window);
  env.window.setTimeout = (fn, ms, ...r) => real(fn, ms === 2500 ? 5 : ms, ...r);   // 탐침 기한만 빨리
  const W = await importFresh(path.join(JS, 'core/workspace-ws.js'), env.context, new Map());
  W.onWorkspaceEvent('tmux_sessions', () => {});
  made[0].open();
  const probes = () => made[0].sent.filter((m) => m.type === 'probe');
  assert.deepEqual(probes(), [{ type: 'probe', id: 1 }], '열리자마자 capable 확인 탐침');
  made[0].onmessage({ data: JSON.stringify({ type: 'probe_ack', id: 1 }) });

  const pageshow = () => { const e = new env.window.Event('pageshow'); e.persisted = true; env.window.dispatchEvent(e); };
  pageshow();                                           // long 복귀 → 탐침 2
  assert.deepEqual(probes().at(-1), { type: 'probe', id: 2 });
  made[0].onmessage({ data: JSON.stringify({ type: 'ping' }) });   // 아무 프레임이나 오면 생존
  await wait(30);
  assert.strictEqual(made.length, 1);
  assert.deepEqual(made[0].closes, []);

  pageshow();                                           // 탐침 3 — 답 없음
  await wait(350);                                      // 기한(5ms) + 엇갈림(0-250ms)
  assert.deepEqual(made[0].closes[0], [4000, 'probe timeout']);
  assert.strictEqual(made.length, 2, '죽은 소켓을 닫고 즉시 하나만 새로 붙는다');
  await wait(1200);
  assert.strictEqual(made.length, 2, '백오프 타이머가 또 열지 않는다');
});

test('workspace-ws — ack를 모르는 옛 서버 소켓은 복귀해도 닫지 않는다', async () => {
  const env = newEnv();
  const made = fakeSockets(env.window);
  env.window.API_BASE = ''; env.window.WS_BASE = 'ws://x';
  const real = env.window.setTimeout.bind(env.window);
  env.window.setTimeout = (fn, ms, ...r) => real(fn, ms === 5000 || ms === 2500 ? 5 : ms, ...r);
  const W = await importFresh(path.join(JS, 'core/workspace-ws.js'), env.context, new Map());
  W.onWorkspaceEvent('tmux_sessions', () => {});
  made[0].open();
  await wait(30);                                       // cap 탐침 기한이 지나도 ack 없음
  const e = new env.window.Event('pageshow'); e.persisted = true; env.window.dispatchEvent(e);
  await wait(350);
  assert.deepEqual(made[0].closes, []);
  assert.strictEqual(made.length, 1);
  assert.strictEqual(made[0].sent.filter((m) => m.type === 'probe').length, 1, '탐침은 처음 한 번뿐');
});
