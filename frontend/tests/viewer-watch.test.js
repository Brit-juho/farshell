'use strict';
// 코드 뷰어 자동 갱신 — shell/fs-watch.js(`/ws-fs-watch` 구독)와 파일
// 오버레이의 새로고침. 서버 쪽 감시·매칭은 server/tests/test_fs_watch.py가 맡는다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const WATCH_JS = path.join(__dirname, '../js/shell/fs-watch.js');
const OVERLAY_JS = path.join(__dirname, '../js/panels/viewer/overlay.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

function fakeSockets(window) {
  const made = [];
  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.sent = [];
      this.closed = false;
      made.push(this);
    }
    send(t) { this.sent.push(JSON.parse(t)); }
    close() { this.closed = true; }
    // 테스트가 서버 역할을 한다.
    open() { this.onopen && this.onopen(); }
    push(msg) { this.onmessage && this.onmessage({ data: JSON.stringify(msg) }); }
    drop() { this.onclose && this.onclose(); }
  }
  window.WebSocket = FakeWebSocket;
  return made;
}

async function env() {
  const e = createDomEnv('<!doctype html><html><body><div id="terminal-container"></div></body></html>');
  _doms.push(e.dom);
  const sockets = fakeSockets(e.window);
  return { ...e, sockets };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('구독이 생길 때만 연결하고, 열리면 전체 목록을 보낸다. 같은 경로는 한 번만', async () => {
  const { context, sockets } = await env();
  const W = await importFresh(WATCH_JS, context, new Map());
  assert.strictEqual(sockets.length, 0, '구독 전에는 연결하지 않는다');
  const offA = W.watchPath('file', '/r/a.md', () => {});
  const offA2 = W.watchPath('file', '/r/a.md', () => {});
  W.watchPath('dir', '/r', () => {});
  await tick();
  assert.strictEqual(sockets.length, 1);
  assert.match(sockets[0].url, /\/ws-fs-watch$/);
  sockets[0].open();
  assert.deepStrictEqual(sockets[0].sent.at(-1), {
    type: 'watch',
    subs: [{ kind: 'file', path: '/r/a.md' }, { kind: 'dir', path: '/r' }],
  });
  offA();
  await tick();
  assert.strictEqual(sockets[0].sent.length, 1, '아직 다른 구독자가 있으면 목록이 안 바뀐다');
  offA2();
  await tick();
  assert.deepStrictEqual(sockets[0].sent.at(-1).subs, [{ kind: 'dir', path: '/r' }]);
});

test('마지막 구독이 풀리면 연결을 닫고 다시 붙지 않는다', async () => {
  const { context, sockets } = await env();
  const W = await importFresh(WATCH_JS, context, new Map());
  const off = W.watchPath('file', '/r/a.md', () => {});
  await tick();
  sockets[0].open();
  off();
  await tick();
  assert.strictEqual(sockets[0].closed, true);
  await new Promise((r) => setTimeout(r, 1100));
  assert.strictEqual(sockets.length, 1, '재연결하지 않는다');
});

test('changed는 해당 구독의 콜백만 부른다', async () => {
  const { context, sockets } = await env();
  const W = await importFresh(WATCH_JS, context, new Map());
  const calls = [];
  W.watchPath('file', '/r/a.md', () => calls.push('a'));
  W.watchPath('file', '/r/b.md', () => calls.push('b'));
  W.watchPath('repo', '/r/a.md', () => calls.push('repo'));
  await tick();
  sockets[0].open();
  sockets[0].push({ type: 'changed', subs: [{ kind: 'file', path: '/r/a.md' }] });
  sockets[0].push({ type: 'watching', rejected: [] });
  sockets[0].push({ type: 'changed', subs: 'garbage' });
  assert.deepStrictEqual(calls, ['a'], '종류가 다르면(repo) 같은 경로라도 별개다');
});

test('끊겼다 다시 붙으면 목록을 다시 보내고, 놓쳤을 변경을 위해 콜백을 한 번씩 부른다', async () => {
  const { context, sockets } = await env();
  const W = await importFresh(WATCH_JS, context, new Map());
  let n = 0;
  W.watchPath('file', '/r/a.md', () => { n += 1; });
  await tick();
  sockets[0].open();
  assert.strictEqual(n, 0, '첫 연결은 따라잡을 게 없다');
  sockets[0].drop();
  await new Promise((r) => setTimeout(r, 1100));
  assert.strictEqual(sockets.length, 2);
  sockets[1].open();
  assert.deepStrictEqual(sockets[1].sent.at(-1).subs, [{ kind: 'file', path: '/r/a.md' }]);
  assert.strictEqual(n, 1);
});

test('coalesce — 도는 중에 여러 번 불려도 끝난 뒤 한 번만 더 돈다', async () => {
  const { context } = await env();
  const W = await importFresh(WATCH_JS, context, new Map());
  let runs = 0;
  let release;
  const f = W.coalesce(async () => {
    runs += 1;
    if (runs === 1) await new Promise((r) => { release = r; });
  });
  const first = f();
  f(); f(); f();
  release();
  await first;
  assert.strictEqual(runs, 2);
});

test('coalesce minGap — 첫 부름은 바로, 그 뒤 간격 안의 부름은 한 번으로 모아 간격이 지나서 돈다', async () => {
  const { context } = await env();
  const W = await importFresh(WATCH_JS, context, new Map());
  const at = [];
  let gap = 80;
  const f = W.coalesce(async () => { at.push(Date.now()); }, { minGap: () => gap });
  const t0 = Date.now();
  await f();
  assert.strictEqual(at.length, 1, '첫 부름은 기다리지 않는다');
  f(); f(); f();
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(at.length, 1, '간격 안에서는 돌지 않는다');
  await new Promise((r) => setTimeout(r, 120));
  assert.strictEqual(at.length, 2, '모은 부름은 한 번');
  assert.ok(at[1] - t0 >= 75, `간격을 지킨다 (${at[1] - t0}ms)`);
  gap = 0;   // 작은 파일로 바뀌면 바로 돈다
  await f();
  assert.strictEqual(at.length, 3);
});

// --- 파일 오버레이 --------------------------------------------------------------

async function overlayEnv() {
  const e = await env();
  const { window } = e;
  window.fitAndResize = () => {};
  window.API_BASE = '';
  window._tokenQuery = '';
  window.showToast = () => {};
  window.VTDiffLex = { normalize: (s) => s, langForPath: () => null };
  let version = 1;
  const fetched = [];
  window.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/fs/file')) {
      fetched.push(u);
      return { ok: true, status: 200, json: async () => ({ content: `v${version}\nline2` }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const overlay = await importFresh(OVERLAY_JS, e.context, new Map());
  return { ...e, overlay, fetched, bump: () => { version += 1; } };
}

const settle = () => new Promise((r) => setTimeout(r, 20));

test('오버레이 새로고침 버튼 — 같은 파일을 다시 읽고 스크롤 위치를 지킨다', async () => {
  const { document, overlay, fetched, bump } = await overlayEnv();
  overlay.openFileOverlay('/repo/doc.md');
  await settle();
  const el = document.getElementById('vt-file');
  const body = el.querySelector('.vt-vw-body');
  assert.match(body.textContent, /v1/);
  body.scrollTop = 120;
  bump();
  el.querySelector('.vt-file-refresh').click();
  await settle();
  assert.strictEqual(fetched.length, 2);
  assert.match(body.textContent, /v2/);
  assert.doesNotMatch(body.textContent, /불러오는 중/);
  assert.strictEqual(body.scrollTop, 120);
});

test('오버레이 — 디스크 변경 알림이 오면 다시 그리고, 닫으면 구독을 푼다', async () => {
  const { document, overlay, sockets, fetched, bump } = await overlayEnv();
  overlay.openFileOverlay('/repo/doc.md');
  await settle();
  sockets[0].open();
  assert.deepStrictEqual(sockets[0].sent.at(-1).subs, [{ kind: 'file', path: '/repo/doc.md' }]);
  bump();
  sockets[0].push({ type: 'changed', subs: [{ kind: 'file', path: '/repo/doc.md' }] });
  await settle();
  assert.strictEqual(fetched.length, 2);
  assert.match(document.querySelector('#vt-file .vt-vw-body').textContent, /v2/);

  // 다른 파일로 바꾸면 구독도 옮긴다.
  overlay.openFileOverlay('/repo/other.md');
  await settle();
  assert.deepStrictEqual(sockets[0].sent.at(-1).subs, [{ kind: 'file', path: '/repo/other.md' }]);

  const body = document.querySelector('#vt-file .vt-vw-body');
  // 닫으면 편집기(CodeMirror — 창에 수신기를 건다)를 바로 destroy한다. jsdom에는 CM이 없어 같은 모양의 핸들을 붙인다.
  let destroyed = 0;
  const pane = body.querySelector('.vt-vw-code-pane');
  pane._vtEditor = { view: { dom: pane }, destroy: () => { destroyed += 1; } };
  body.focus();
  body.dispatchEvent(new document.defaultView.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await settle();
  assert.strictEqual(document.getElementById('vt-file'), null);
  assert.strictEqual(sockets[0].closed, true, '마지막 구독이 풀려 연결을 닫는다');
  assert.strictEqual(destroyed, 1, '닫을 때 편집기를 거둔다');
});
