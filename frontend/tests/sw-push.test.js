// Service Worker 의 push/notificationclick 핸들러 단위 테스트.
//   node --test frontend/tests/sw-push.test.js
//
// sw.js 는 브라우저 전역(self/caches/fetch)에 의존하는 최상위 스크립트라 그냥은
// require 할 수 없다. 가짜 self 를 주입해 평가한 뒤 등록된 리스너를 직접 호출한다.
//
// 이걸 테스트하는 이유: 푸시는 실패해도 아무 소리가 안 난다. 특히
// userVisibleOnly:true 로 구독했는데 showNotification 을 안 부르면 브라우저가
// "조용한 푸시"로 판단해 **구독 자체를 폐기한다** — 그러면 그 뒤로 영영 안 온다.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadSw() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8');
  const listeners = {};
  const shown = [];
  const opened = [];
  const focused = [];

  const self = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    registration: {
      showNotification: (title, opts) => { shown.push({ title, opts }); return Promise.resolve(); },
    },
    clients: {
      matchAll: () => Promise.resolve(focused.length ? [] : []),
      openWindow: (url) => { opened.push(url); return Promise.resolve(); },
    },
    skipWaiting: () => {},
  };

  const ctx = {
    self,
    caches: { open: () => Promise.resolve({ addAll: () => Promise.resolve() }), keys: () => Promise.resolve([]), delete: () => Promise.resolve() },
    fetch: () => Promise.resolve({ ok: true, clone: () => ({}) }),
    console,
    URL,
    Promise,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return { listeners, shown, opened };
}

function pushEvent(data) {
  const waits = [];
  return {
    ev: {
      data: data === undefined ? null : { json: () => data },
      waitUntil: (p) => waits.push(p),
    },
    waits,
  };
}

test('push 리스너가 등록돼 있다', () => {
  const { listeners } = loadSw();
  assert.strictEqual(typeof listeners.push, 'function',
    'push 리스너가 없으면 Web Push는 아무 것도 하지 않는다');
});

test('push: 알림을 반드시 띄운다 (userVisibleOnly 계약)', async () => {
  const { listeners, shown } = loadSw();
  const { ev, waits } = pushEvent({ title: '작업 완료', body: '터미널에서 확인하세요' });
  listeners.push(ev);
  await Promise.all(waits);
  assert.strictEqual(shown.length, 1);
  assert.strictEqual(shown[0].title, '작업 완료');
  assert.strictEqual(shown[0].opts.body, '터미널에서 확인하세요');
});

test('push: 페이로드가 비어도 알림을 띄운다', async () => {
  const { listeners, shown } = loadSw();
  const { ev, waits } = pushEvent(undefined);
  listeners.push(ev);
  await Promise.all(waits);
  assert.strictEqual(shown.length, 1, '페이로드가 없다고 조용히 넘어가면 구독이 폐기된다');
  assert.strictEqual(shown[0].title, 'FarShell');
});

test('push: 깨진 JSON 이어도 알림을 띄운다', async () => {
  const { listeners, shown } = loadSw();
  const waits = [];
  listeners.push({
    data: { json: () => { throw new Error('bad json'); } },
    waitUntil: (p) => waits.push(p),
  });
  await Promise.all(waits);
  assert.strictEqual(shown.length, 1);
  assert.strictEqual(shown[0].title, 'FarShell');
});

test('push: 같은 tag 로 묶어 잠금화면을 도배하지 않는다', async () => {
  const { listeners, shown } = loadSw();
  const { ev, waits } = pushEvent({ title: 'a' });
  listeners.push(ev);
  await Promise.all(waits);
  assert.strictEqual(shown[0].opts.tag, 'vt-task');
  assert.strictEqual(shown[0].opts.renotify, true);
});

test('push: url 을 알림 data 에 실어 클릭 시 이동할 수 있게 한다', async () => {
  const { listeners, shown } = loadSw();
  const { ev, waits } = pushEvent({ title: 'a', url: '/#tmux=dev' });
  listeners.push(ev);
  await Promise.all(waits);
  // vm 컨텍스트에서 만들어진 객체라 prototype 이 달라 deepStrictEqual 은 못 쓴다.
  assert.strictEqual(shown[0].opts.data.url, '/#tmux=dev');
});

test('notificationclick: 열린 탭이 없으면 알림의 url 로 연다', async () => {
  const { listeners, opened } = loadSw();
  const waits = [];
  listeners.notificationclick({
    notification: { close: () => {}, data: { url: '/#tmux=dev' } },
    waitUntil: (p) => waits.push(p),
  });
  await Promise.all(waits);
  assert.deepStrictEqual(opened, ['/#tmux=dev']);
});

test('notificationclick: data 가 없으면 루트로 연다', async () => {
  const { listeners, opened } = loadSw();
  const waits = [];
  listeners.notificationclick({
    notification: { close: () => {} },
    waitUntil: (p) => waits.push(p),
  });
  await Promise.all(waits);
  assert.deepStrictEqual(opened, ['/']);
});

test('캐시 키가 bump 돼 있다 (vendor는 SWR이라 필수)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8');
  const m = /const CACHE = 'vt-static-v(\d+)'/.exec(src);
  assert.ok(m, 'CACHE 상수를 찾을 수 없다');
  assert.ok(Number(m[1]) >= 6, `캐시 키가 v6 미만이다(v${m[1]})`);
});

// 2026-09-18 — 실제로 난 사고를 잠근다. icon-192/512.png와 /favicon.ico가
// "vendor immutable" stale-while-revalidate 경로에 있었다. SWR은 캐시가 먼저
// 답하므로, 마이크 아이콘을 FarShell 마크로 바꿨는데도 이미 방문한 브라우저에는
// 계속 마이크가 나왔다(서버는 새 바이트를 주고 있었다). 브랜드 마크는 immutable이
// 아니다 — 바뀐다. CACHE 키를 올리면 그 한 번은 풀리지만 다음 교체에서 같은 일이
//반복되므로 경로 자체를 network-first로 옮겼다.
test('아이콘은 network-first다 (immutable 경로에 두면 교체가 반영되지 않는다)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'sw.js'), 'utf8');
  const m = /const NETWORK_FIRST = (\/.*\/);/.exec(src);
  assert.ok(m, 'NETWORK_FIRST 상수를 찾을 수 없다');
  // eslint-disable-next-line no-eval
  const re = eval(m[1]);
  for (const p of ['/static/icon-192.png', '/static/icon-512.png', '/favicon.ico']) {
    assert.ok(re.test(p), `${p}가 network-first가 아니다`);
  }
  // vendor는 진짜 immutable이라 SWR로 남아야 한다(속도 이득이 이 파일의 목적 중 하나다).
  assert.ok(!re.test('/static/vendor/xterm.min.js'), 'vendor는 SWR로 남아야 한다');
});
