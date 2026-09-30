'use strict';
// 2026-09-30 — 레일·플릿 git 배지 캐시(shell/rail-fetch.ts). 변경 알림
// (/ws-fs-watch `repo`)이 살아 있으면 바뀌었을 때만 다시 세고, 알림이 없으면
// 예전 60초 규칙으로 돌아간다. 시간은 Date.now를 바꿔 흉내 낸다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });

const MIN = 60000;

async function setup({ repo = true } = {}) {
  const env = await loadSolidBuild();
  _envs.push(env);
  const { window } = env;
  let now = 1_000_000;
  window.Date.now = () => now;
  const sockets = [];
  window.WebSocket = class {
    constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
    send(t) { this.sent.push(JSON.parse(t)); }
    close() {}
  };
  let gitCalls = 0;
  const deps = {
    vtFetch: async (p) => {
      if (p.startsWith('/api/git/status')) { gitCalls += 1; return repo ? { repo: true, files: [1, 2] } : { repo: false }; }
      return {};
    },
  };
  const ws = () => sockets[sockets.length - 1];
  const open = () => { ws().onopen(); };
  const reply = (msg) => ws().onmessage({ data: JSON.stringify(msg) });
  return {
    env, deps, ws, open, reply,
    tick: (ms) => { now += ms; },
    calls: () => gitCalls,
    flush: () => new Promise((r) => setTimeout(r, 0)),
  };
}

test('감시가 없으면 예전 그대로 60초 캐시', async () => {
  const t = await setup();
  assert.strictEqual(await t.env.fetchDiffCount(t.deps, '/r'), 2);
  t.tick(59000);
  assert.strictEqual(t.env.diffCountStale('/r'), false);
  t.tick(1000);
  assert.strictEqual(t.env.diffCountStale('/r'), true);
});

test('감시 중이고 변경이 없으면 10분 동안 다시 세지 않는다', async () => {
  const t = await setup();
  const w = t.env.createDiffWatcher(() => {});
  w.sync(['/r']);
  await t.flush();
  t.open();
  t.reply({ type: 'watching', rejected: [] });
  await t.env.fetchDiffCount(t.deps, '/r');
  t.tick(9 * MIN);
  assert.strictEqual(t.env.diffCountStale('/r'), false);
  t.tick(MIN);
  assert.strictEqual(t.env.diffCountStale('/r'), true, '놓친 알림 대비로 10분에 한 번은 센다');
});

test('변경 알림이 오면 다시 센다 — 단 마지막 조회 30초 뒤부터', async () => {
  const t = await setup();
  const w = t.env.createDiffWatcher(() => {});
  w.sync(['/r']);
  await t.flush();
  t.open();
  t.reply({ type: 'watching', rejected: [] });
  await t.env.fetchDiffCount(t.deps, '/r');
  t.tick(10000);
  t.reply({ type: 'changed', subs: [{ kind: 'repo', path: '/r' }] });
  assert.strictEqual(t.env.diffCountStale('/r'), false, '에이전트가 쉴 새 없이 고쳐도 30초보다 자주는 안 센다');
  assert.strictEqual(t.env.msUntilDiffRefresh('/r'), 20000);
  t.tick(20000);
  assert.strictEqual(t.env.diffCountStale('/r'), true);
  await t.env.fetchDiffCount(t.deps, '/r');
  assert.strictEqual(t.calls(), 2);
  assert.strictEqual(t.env.diffCountStale('/r'), false, '다시 센 뒤로는 깨끗하다');
});

test('서버가 거절하면(저장소 아님·경계 밖) 60초 규칙으로 돌아간다', async () => {
  const t = await setup();
  const w = t.env.createDiffWatcher(() => {});
  w.sync(['/r']);
  await t.flush();
  t.open();
  t.reply({ type: 'watching', rejected: [{ kind: 'repo', path: '/r', reason: 'not a git repository' }] });
  await t.env.fetchDiffCount(t.deps, '/r');
  t.tick(MIN);
  assert.strictEqual(t.env.diffCountStale('/r'), true);
});

test('감시가 실패했다고 알려 오면 60초 규칙으로 돌아간다', async () => {
  const t = await setup();
  const w = t.env.createDiffWatcher(() => {});
  w.sync(['/r']);
  await t.flush();
  t.open();
  t.reply({ type: 'watching', rejected: [] });
  await t.env.fetchDiffCount(t.deps, '/r');
  t.reply({ type: 'error', reason: 'watch failed' });
  t.tick(MIN);
  assert.strictEqual(t.env.diffCountStale('/r'), true);
});

test('git 저장소가 아닌 곳은 5분 캐시', async () => {
  const t = await setup({ repo: false });
  assert.strictEqual(await t.env.fetchDiffCount(t.deps, '/home'), null);
  t.tick(4 * MIN);
  assert.strictEqual(t.env.diffCountStale('/home'), false);
  t.tick(MIN);
  assert.strictEqual(t.env.diffCountStale('/home'), true);
});

test('세션이 사라진 cwd는 구독을 푼다', async () => {
  const t = await setup();
  const w = t.env.createDiffWatcher(() => {});
  w.sync(['/a', '/b']);
  await t.flush();
  t.open();
  assert.strictEqual(t.ws().sent.at(-1).subs.length, 2);
  w.sync(['/a']);
  await t.flush();
  assert.deepStrictEqual(t.ws().sent.at(-1).subs.map((s) => s.path), ['/a']);
});

test('다시 셀 때가 되면 다시 그리라고 알린다 — 탭이 숨어 있으면 보일 때까지 미룬다', async () => {
  const t = await setup();
  let due = 0;
  const w = t.env.createDiffWatcher(() => { due += 1; });
  w.sync(['/r']);
  await t.flush();
  t.open();
  t.reply({ type: 'watching', rejected: [] });
  await t.env.fetchDiffCount(t.deps, '/r');
  t.tick(40000);   // 30초 간격이 이미 지났다 — 알림이 오면 곧바로(0ms) 때가 된다
  let hidden = true;
  Object.defineProperty(t.env.document, 'hidden', { get: () => hidden, configurable: true });
  t.reply({ type: 'changed', subs: [{ kind: 'repo', path: '/r' }] });
  await new Promise((res) => setTimeout(res, 20));
  assert.strictEqual(due, 0, '숨은 탭에서는 git을 다시 세지 않는다');
  hidden = false;
  t.env.document.dispatchEvent(new t.env.window.Event('visibilitychange'));
  assert.strictEqual(due, 1, '보이게 되면 그때 한 번');
  w.dispose();
});

test('같은 cwd를 여러 행이 동시에 불러도 git status는 한 번', async () => {
  const t = await setup();
  const results = await Promise.all([1, 2, 3].map(() => t.env.fetchDiffCount(t.deps, '/r')));
  assert.deepStrictEqual(results, [2, 2, 2]);
  assert.strictEqual(t.calls(), 1);
});
