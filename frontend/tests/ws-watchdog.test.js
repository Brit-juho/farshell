// core/ws-watchdog.js — 메시지가 45초 동안 하나도 없는 열린 소켓을 닫는다.
// 반쯤 열린 연결은 onclose를 영영 안 불러서 소켓이 전부 "연결됨"으로 보였다(사고).
// 지키는 것: 메시지가 오면 연장, 침묵이면 4000으로 닫기, onclose가 안 오면 한 번만
// 직접 부르기(두 번 재연결 금지), stop 뒤에는 아무것도 안 하기.
// 끝에는 실제 소비처(core/workspace-ws.js, shell/fs-watch.js)에 붙었는지 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const WD = pathToFileURL(path.join(__dirname, '../js/core/ws-watchdog.js')).href;

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

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

// 진짜 소켓처럼 close()가 비동기로 onclose를 부르거나(hangClose=false), 죽은 연결처럼 안 부른다.
function sock({ syncClose = false, hangClose = false } = {}) {
  const s = {
    closes: [],
    onclose: null, onmessage: null, onerror: null,
    close(code, reason) {
      s.closes.push([code, reason]);
      if (hangClose) return;
      if (syncClose && s.onclose) s.onclose({ code, reason });
    },
  };
  return s;
}

test('상수 — 45초, 코드 4000, 이유 문자열', async () => {
  const W = await import(WD);
  assert.strictEqual(W.WS_SILENCE_TIMEOUT_MS, 45000);
  assert.strictEqual(W.WS_HEARTBEAT_CLOSE_CODE, 4000);
  assert.strictEqual(W.WS_HEARTBEAT_CLOSE_REASON, 'client heartbeat timeout');
});

test('메시지가 오는 동안은 닫지 않는다', async () => {
  const { watchSocketLiveness } = await import(WD);
  const c = clock();
  const s = sock();
  const wd = watchSocketLiveness(s, { later: c.later, cancel: c.cancel });
  for (let i = 0; i < 10; i++) { c.advance(15000); wd.alive(); }   // 서버 ping 15초마다
  assert.deepEqual(s.closes, []);
  wd.stop();
  assert.strictEqual(c.size(), 0);
});

test('45초 침묵이면 4000으로 닫는다 — onclose가 오면 그걸로 끝', async () => {
  const { watchSocketLiveness } = await import(WD);
  const c = clock();
  const s = sock();
  let closes = 0;
  const wd = watchSocketLiveness(s, { later: c.later, cancel: c.cancel });
  s.onclose = () => { closes++; wd.stop(); };
  c.advance(44999);
  assert.deepEqual(s.closes, []);
  c.advance(1);
  assert.deepEqual(s.closes, [[4000, 'client heartbeat timeout']]);
  assert.strictEqual(wd.expired(), true);
  s.onclose({ code: 4000 });   // 브라우저가 늦게나마 close를 준다
  c.advance(10000);
  assert.strictEqual(closes, 1, 'onclose는 한 번만');
});

test('close()가 onclose를 동기로 불러도 한 번만', async () => {
  const { watchSocketLiveness } = await import(WD);
  const c = clock();
  const s = sock({ syncClose: true });
  let closes = 0;
  const wd = watchSocketLiveness(s, { later: c.later, cancel: c.cancel });
  s.onclose = () => { closes++; wd.stop(); };
  c.advance(45000);
  c.advance(10000);
  assert.strictEqual(closes, 1);
  assert.strictEqual(c.size(), 0);
});

test('죽은 연결이라 close 이벤트가 안 오면 유예 뒤 직접 부르고, 나중에 와도 두 번 부르지 않는다', async () => {
  const { watchSocketLiveness, CLOSE_GRACE_MS } = await import(WD);
  const c = clock();
  const s = sock({ hangClose: true });
  const seen = [];
  const wd = watchSocketLiveness(s, { later: c.later, cancel: c.cancel });
  const handler = (ev) => { seen.push(ev.code); wd.stop(); };
  s.onclose = handler;
  s.onmessage = () => {};
  c.advance(45000);
  assert.strictEqual(seen.length, 0, 'close()를 부르고 일단 기다린다');
  c.advance(CLOSE_GRACE_MS);
  assert.deepEqual(seen, [4000]);
  assert.strictEqual(s.onclose, null, '핸들러를 떼어 둔다 — 진짜 close가 와도 재연결이 두 번 걸리지 않는다');
  assert.strictEqual(s.onmessage, null);
  c.advance(60000);
  assert.deepEqual(seen, [4000]);
});

test('stop 뒤 alive는 아무 일도 안 한다', async () => {
  const { watchSocketLiveness } = await import(WD);
  const c = clock();
  const s = sock();
  const wd = watchSocketLiveness(s, { later: c.later, cancel: c.cancel });
  wd.stop();
  wd.alive();
  c.advance(100000);
  assert.deepEqual(s.closes, []);
});

// ── 소비처 배선 ────────────────────────────────────────────────────────────

function fakeSockets(window) {
  const made = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.sent = []; this.closes = []; this.readyState = 0; made.push(this); }
    send(t) { this.sent.push(JSON.parse(t)); }
    close(code, reason) { this.closes.push([code, reason]); }
    open() { this.readyState = 1; this.onopen && this.onopen(); }
    push(msg) { this.onmessage && this.onmessage({ data: JSON.stringify(msg) }); }
    drop(code) { this.readyState = 3; this.onclose && this.onclose({ code }); }
  }
  window.WebSocket = FakeWebSocket;
  return made;
}

// 감시 45초·유예 3초·재연결 백오프(1~2초)를 빨리 돌린다. 그 밖의 타이머는 그대로.
function fastTimers(window) {
  const real = window.setTimeout.bind(window);
  window.setTimeout = (fn, ms, ...rest) => real(fn, ms >= 1000 ? 5 : ms, ...rest);
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test('workspace-ws — 침묵하면 닫고, 응답 없는 close여도 재연결은 하나만', async () => {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const made = fakeSockets(env.window);
  env.window.API_BASE = ''; env.window.WS_BASE = 'ws://x';
  const W = await importFresh(path.join(__dirname, '../js/core/workspace-ws.js'), env.context, new Map());
  fastTimers(env.window);
  W.onWorkspaceEvent('tmux_sessions', () => {});
  assert.strictEqual(made.length, 1);
  made[0].open();
  await wait(60);
  assert.deepEqual(made[0].closes[0], [4000, 'client heartbeat timeout']);
  assert.ok(made.length >= 2, '닫은 뒤 재연결한다');
  const count = made.length;
  made[0].drop(1006);   // 늦게 온 진짜 close — 핸들러가 떼어져 있어 재연결을 또 걸지 않는다
  await wait(30);
  assert.strictEqual(made.length, count);
});

test('fs-watch — ping에 pong으로 답하고, 메시지가 오면 닫지 않는다', async () => {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const made = fakeSockets(env.window);
  const F = await importFresh(path.join(__dirname, '../js/shell/fs-watch.js'), env.context, new Map());
  F.watchPath('file', '/a', () => {});
  await wait(0);
  assert.strictEqual(made.length, 1);
  made[0].open();
  made[0].push({ type: 'ping' });
  assert.deepEqual(made[0].sent.at(-1), { type: 'pong' });
  assert.deepEqual(made[0].closes, []);
});
