// L8 — layout/persist.js. 핵심은 "웹 세션 id는 부팅마다 바뀐다"는 전제다:
// 직렬화는 tmux 이름을 함께 적고, 복원은 그 이름으로 지금 살아있는 세션을
// 다시 찾는다. 못 찾은 leaf는 유령 pane이 되지 않고 빈 pane으로 강등된다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const PERSIST_JS = path.join(__dirname, '../js/layout/persist.js');
const LAYOUT_STORE_JS = path.join(__dirname, '../js/layout/store.js');
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="tabs"></div></body></html>');
  _doms.push(env.dom);
  // vtFetch는 window.fetch/API_BASE를 쓴다 — PUT은 성공한 척, GET(/api/workspace)은
  // "서버에 저장된 레이아웃 없음"으로 응답해 로컬 경로만 검증한다.
  const calls = [];
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = (url, opts) => {
    calls.push({ url: String(url), opts });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ui: {} }) });
  };
  const cache = new Map();
  const core = await importFresh(CORE_STORE_JS, env.context, cache);
  const S = await importFresh(LAYOUT_STORE_JS, env.context, cache);
  const P = await importFresh(PERSIST_JS, env.context, cache);
  return { window: env.window, core, S, P, calls };
}

// 세션 스토어에 "살아있는 세션" 하나를 심는다(addSession 전체 배선은 이
// 테스트의 관심사가 아니라 레코드만 직접 넣는다 — layout/persist.js가 보는
// 것도 id/tmuxName 두 필드뿐이다).
function addLive(core, id, tmuxName = null) {
  core.registerSession(id, { tmuxName, wrapper: null, tabEl: null });
}

test('serializeTree — leaf에 웹 세션 id와 tmux 이름을 함께 적는다', async () => {
  const { S, P } = await load();
  const rootId = S.getActivePaneId();
  S.setPaneSession('web-1', rootId);
  const snap = P.serializeTree(S.getTree(), (sid) => ({ id: sid, tmux: 'dev' }));
  assert.strictEqual(snap.t, 'leaf');
  assert.deepEqual(snap.session, { id: 'web-1', tmux: 'dev' });
});

test('serializeTree — split의 dir/ratio가 그대로 보존된다', async () => {
  const { S, P } = await load();
  const rootId = S.getActivePaneId();
  const newId = S.splitPane(rootId, 'row');
  S.setRatio(S.getTree().id, 0.3);
  const snap = P.serializeTree(S.getTree(), () => null);
  assert.strictEqual(snap.t, 'split');
  assert.strictEqual(snap.dir, 'row');
  assert.strictEqual(snap.ratio, 0.3);
  assert.strictEqual(snap.a.id, rootId);
  assert.strictEqual(snap.b.id, newId);
});

test('makeResolver — tmux 이름이 새 세션 id로 매핑된다(부팅마다 id가 바뀌어도)', async () => {
  const { core, P } = await load();
  addLive(core, 'new-id-after-reboot', 'dev');
  const resolve = P.makeResolver(core.allSessions());
  // 저장 시점의 id는 이미 죽었지만 tmux 이름이 같으므로 새 id를 찾아야 한다
  assert.strictEqual(resolve({ id: 'old-dead-id', tmux: 'dev' }), 'new-id-after-reboot');
});

test('makeResolver — 사라진 tmux 세션은 null(빈 pane 강등)', async () => {
  const { core, P } = await load();
  addLive(core, 'x', 'other');
  const resolve = P.makeResolver(core.allSessions());
  assert.strictEqual(resolve({ id: 'x', tmux: 'gone' }), null);
});

test('makeResolver — 순수 PTY 세션은 그 id가 아직 살아있을 때만 복원된다', async () => {
  const { core, P } = await load();
  addLive(core, 'pty-alive', null);
  const resolve = P.makeResolver(core.allSessions());
  assert.strictEqual(resolve({ id: 'pty-alive', tmux: null }), 'pty-alive');
  assert.strictEqual(resolve({ id: 'pty-dead', tmux: null }), null);
});

test('deserializeTree — 같은 세션이 두 leaf에 배정되지 않는다', async () => {
  const { P } = await load();
  const snap = {
    t: 'split', id: 's1', dir: 'row', ratio: 0.5,
    a: { t: 'leaf', id: 'p1', session: { id: 'a', tmux: 'dev' } },
    b: { t: 'leaf', id: 'p2', session: { id: 'a', tmux: 'dev' } },
  };
  const tree = P.deserializeTree(snap, () => 'live-1');
  assert.strictEqual(tree.a.session, 'live-1');
  assert.strictEqual(tree.b.session, null, '두 번째 leaf는 비워야 한다(중복 attach 금지)');
});

test('deserializeTree — 깨진 스냅샷은 null(복원 안 함)', async () => {
  const { P } = await load();
  assert.strictEqual(P.deserializeTree(null, () => null), null);
  assert.strictEqual(P.deserializeTree({ t: 'split', id: 's', a: { t: 'leaf', id: 'p' } }, () => null), null);
  assert.strictEqual(P.deserializeTree({ t: 'leaf' }, () => null), null, 'id 없는 leaf는 거부');
});

test('deserializeTree — ratio가 범위를 벗어나거나 없으면 0.5로 정규화', async () => {
  const { P } = await load();
  const mk = (ratio) => P.deserializeTree({
    t: 'split', id: 's', dir: 'col', ratio,
    a: { t: 'leaf', id: 'p1', session: null }, b: { t: 'leaf', id: 'p2', session: null },
  }, () => null);
  assert.strictEqual(mk(undefined).ratio, 0.5);
  assert.strictEqual(mk(0).ratio, 0.5);
  assert.strictEqual(mk(1.4).ratio, 0.5);
});

test('restoreLayout — localStorage 스냅샷을 트리로 되돌린다 + 죽은 leaf는 빈 pane', async () => {
  const { window, core, S, P } = await load();
  addLive(core, 'live-dev', 'dev');
  window.localStorage.setItem('vt-layout-v1', JSON.stringify({
    v: 1, savedAt: 100, active: 'p2',
    tree: {
      t: 'split', id: 's1', dir: 'row', ratio: 0.4,
      a: { t: 'leaf', id: 'p1', session: { id: 'stale-id', tmux: 'dev' } },
      b: { t: 'leaf', id: 'p2', session: { id: 'stale-2', tmux: 'killed' } },
    },
  }));

  await P.restoreLayout();

  const tree = S.getTree();
  assert.strictEqual(tree.t, 'split');
  assert.strictEqual(tree.ratio, 0.4);
  assert.strictEqual(tree.a.session, 'live-dev', 'tmux 이름으로 새 id를 찾아야 한다');
  assert.strictEqual(tree.b.session, null, '죽은 세션 참조는 빈 pane으로 강등');
  assert.strictEqual(S.getActivePaneId(), 'p2', '저장된 활성 pane이 복원된다');
});

test('restoreLayout — 스냅샷이 없으면 기본 트리(leaf 1개)를 유지한다', async () => {
  const { S, P } = await load();
  const before = S.getTree().id;
  await P.restoreLayout();
  assert.strictEqual(S.countLeaves(), 1);
  assert.strictEqual(S.getTree().id, before);
});

test('restoreLayout — 저장된 active pane이 트리에 없으면 첫 leaf로 떨어진다', async () => {
  const { window, P, S } = await load();
  window.localStorage.setItem('vt-layout-v1', JSON.stringify({
    v: 1, savedAt: 1, active: 'no-such-pane',
    tree: { t: 'leaf', id: 'only', session: null },
  }));
  await P.restoreLayout();
  assert.strictEqual(S.getActivePaneId(), 'only');
});

test('복원 전에는 저장하지 않는다 — 빈 초기 트리로 정본을 덮어쓰지 않게', async () => {
  const { S, P, calls, window } = await load();
  S.splitActivePane('row');
  P.saveLayoutNow();
  assert.strictEqual(calls.filter((c) => c.url.includes('/api/workspace')).length, 0);
  assert.strictEqual(window.localStorage.getItem('vt-layout-v1'), null);

  await P.restoreLayout();
  P.saveLayoutNow();
  const puts = calls.filter((c) => c.url.includes('/api/workspace') && c.opts && c.opts.method === 'PUT');
  assert.strictEqual(puts.length, 1);
  const body = JSON.parse(puts[0].opts.body);
  assert.strictEqual(body.ui.layout.v, 1);
  assert.strictEqual(body.ui.layout.tree.t, 'split');
  assert.ok(window.localStorage.getItem('vt-layout-v1'), 'localStorage에도 같이 써야 한다');
});

test('restoreLayout — 서버 정본이 더 최신이면 로컬을 덮어쓴다', async () => {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.localStorage.setItem('vt-layout-v1', JSON.stringify({
    v: 1, savedAt: 100, active: 'local-leaf',
    tree: { t: 'leaf', id: 'local-leaf', session: null },
  }));
  env.window.fetch = (url, opts) => {
    if (opts && opts.method === 'PUT') return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ui: { layout: {
        v: 1, savedAt: 999, active: 'remote-leaf',
        tree: { t: 'leaf', id: 'remote-leaf', session: null },
      } } }),
    });
  };
  const cache = new Map();
  await importFresh(CORE_STORE_JS, env.context, cache);
  const S = await importFresh(LAYOUT_STORE_JS, env.context, cache);
  const P = await importFresh(PERSIST_JS, env.context, cache);

  await P.restoreLayout();
  assert.strictEqual(S.getTree().id, 'remote-leaf');
});

test('replaceTree — 잘못된 입력은 트리를 건드리지 않는다', async () => {
  const { S } = await load();
  const before = S.getTree();
  assert.strictEqual(S.replaceTree(null), false);
  assert.strictEqual(S.replaceTree({ t: 'nope', id: 'x' }), false);
  assert.strictEqual(S.getTree(), before);
});

// ── S5 검증에서 발견한 결함: 빈 leaf + 살아있는 세션 ──────────────────────
test('복원 — leaf가 비어 있는데 살아있는 세션이 있으면 그 자리에 채운다', async () => {
  const { window, core, S, P } = await load();
  addLive(core, 'live-1', 'dev');
  window.localStorage.setItem('vt-layout-v1', JSON.stringify({
    v: 1, savedAt: 100, active: 'p1',
    tree: { t: 'leaf', id: 'p1', session: null },
  }));

  await P.restoreLayout();

  // 이 보정이 없으면 탭에는 세션이 있는데 화면엔 "빈 pane"만 남아, 새로고침
  // 직후 사용자에게는 "세션이 사라졌다"로 읽힌다(실브라우저에서 재현했다).
  assert.strictEqual(S.getTree().session, 'live-1');
});

test('복원 — 이미 배정된 세션을 빈 leaf에 중복으로 넣지 않는다', async () => {
  const { window, core, S, P } = await load();
  addLive(core, 'live-1', 'dev');
  window.localStorage.setItem('vt-layout-v1', JSON.stringify({
    v: 1, savedAt: 100, active: 'p1',
    tree: {
      t: 'split', id: 's1', dir: 'row', ratio: 0.5,
      a: { t: 'leaf', id: 'p1', session: { id: 'old', tmux: 'dev' } },
      b: { t: 'leaf', id: 'p2', session: null },
    },
  }));

  await P.restoreLayout();

  const tree = S.getTree();
  assert.strictEqual(tree.a.session, 'live-1');
  assert.strictEqual(tree.b.session, null, '남는 세션이 없으면 빈 pane 그대로');
});

test('복원 — 빈 leaf가 여러 개면 세션 순서대로 앞에서부터 채운다', async () => {
  const { window, core, S, P } = await load();
  addLive(core, 'live-a', 'a');
  addLive(core, 'live-b', 'b');
  window.localStorage.setItem('vt-layout-v1', JSON.stringify({
    v: 1, savedAt: 100, active: 'p1',
    tree: {
      t: 'split', id: 's1', dir: 'row', ratio: 0.5,
      a: { t: 'leaf', id: 'p1', session: null },
      b: { t: 'leaf', id: 'p2', session: null },
    },
  }));

  await P.restoreLayout();

  const tree = S.getTree();
  assert.deepEqual([tree.a.session, tree.b.session], ['live-a', 'live-b']);
});
