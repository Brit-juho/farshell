// 터치 칸 분할(2026-10-06) — 폭 720px 이상 + pointer: coarse 에서만 행 메뉴에 「오른쪽에 나란히/아래에 열기」.
// 분할 자체는 드래그 드롭과 같은 applyPaneDrop 가장자리 처리를 쓴다(layout/dnd.js) —
// 다른 뷰의 세션은 옮겨 오고, 원래 뷰가 비면 닫힌다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

class FakeTerminal {
  constructor(opts) { this.options = opts; this.cols = 80; this.rows = 24; }
  loadAddon() {} open(el) { this.element = el; } focus() {} reset() {} write() {}
  onData() {} getSelection() { return ''; } attachCustomKeyEventHandler() {} dispose() {}
}
class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = 0; }
  addEventListener() {} send() {} close() {}
}
const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function setup({ width = 900, coarse = true } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.innerWidth = width;
  window.matchMedia = (q) => ({ matches: coarse && /pointer:\s*coarse/.test(q), media: q, addEventListener() {}, removeEventListener() {} });
  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  window.fetch = (url) => (String(url).includes('/api/tmux/attach')
    ? ok({ id: 'x1', name: 'tx', tmux_session: 'tx' }) : ok([]));
  const cache = new Map();
  await importFresh(J('lib/keyseq.js'), env.context, cache);
  await importFresh(J('ui/toast.js'), env.context, cache);
  await importFresh(J('lib/ansilex.js'), env.context, cache);
  const sess = await importFresh(J('term/session.js'), env.context, cache);
  await importFresh(J('layout/panes.js'), env.context, cache);
  const S = await importFresh(J('layout/store.js'), env.context, cache);
  const core = await importFresh(J('core/store.js'), env.context, cache);
  const actions = await importFresh(J('term/session-actions.js'), env.context, cache);
  // 뷰 3개: [a] · [b] · [d] (활성 = d)
  sess.addSession('a', 'A', { tmuxName: 'ta' });
  sess.addSession('b', 'B', { tmuxName: 'tb' });
  sess.addSession('d', 'D', { tmuxName: 'td' });
  return { window, sess, S, actions, core };
}
const leaves = (S) => {
  const out = [];
  (function walk(n) { if (n.t === 'leaf') out.push(n); else { walk(n.a); walk(n.b); } })(S.getTree());
  return out;
};

test('폭 720 이상 + 터치일 때만 사용 가능하다', async () => {
  const wide = await setup({ width: 900, coarse: true });
  assert.strictEqual(wide.actions.touchSplitAvailable('a'), true);
  assert.strictEqual(wide.window.vtTouchSplitAvailable('a'), true, 'Rail/Fleet 이 쓰는 window 브리지');
  const narrow = await setup({ width: 500, coarse: true });
  assert.strictEqual(narrow.actions.touchSplitAvailable('a'), false, '720 미만은 넣지 않는다');
  const edge = await setup({ width: 719, coarse: true });
  assert.strictEqual(edge.actions.touchSplitAvailable('a'), false);
  const mouse = await setup({ width: 900, coarse: false });
  assert.strictEqual(mouse.actions.touchSplitAvailable('a'), false, '마우스 기기는 드래그가 있다');
});

test('활성 칸에 이미 있는 세션이거나 칸 상한이면 쓸 수 없다', async () => {
  const { S, actions } = await setup({ width: 900 });
  assert.strictEqual(actions.touchSplitAvailable('d'), false, '제 자리 분할은 빈 칸만 남긴다');
  assert.strictEqual(actions.touchSplitAvailable('a'), true);
  // 상한(regular 4칸)까지 채운다
  for (let i = 0; i < 3; i += 1) S.splitActivePane('row', null);
  assert.strictEqual(S.countLeaves(), 4);
  assert.strictEqual(actions.touchSplitAvailable('a'), false);
});

test('오른쪽에 나란히 열기 — 다른 뷰의 세션을 옮겨 오고 비게 된 원래 뷰는 닫힌다', async () => {
  const { S, actions } = await setup({ width: 900 });
  const tabsBefore = S.getTabs().length;
  const id = await actions.splitOpenSession('right', { sessionId: 'a', tmuxName: 'ta' });
  assert.strictEqual(id, 'a');
  const tree = S.getTree();
  assert.strictEqual(tree.t, 'split');
  assert.strictEqual(tree.dir, 'row');
  assert.strictEqual(tree.a.session, 'd', '기존 칸은 왼쪽 그대로');
  assert.strictEqual(tree.b.session, 'a', '새 칸은 오른쪽');
  assert.strictEqual(S.getTabs().length, tabsBefore - 1, 'a 가 있던 단독 뷰는 비어서 닫힌다(1·2단계 규칙)');
  assert.strictEqual(S.findTabBySessionId('a'), S.getActiveTabId());
});

test('아래에 열기 — col 분할, 새 칸이 아래(두 번째)', async () => {
  const { S, actions } = await setup({ width: 900 });
  await actions.splitOpenSession('bottom', { sessionId: 'b', tmuxName: 'tb' });
  const tree = S.getTree();
  assert.strictEqual(tree.dir, 'col');
  assert.strictEqual(tree.a.session, 'd');
  assert.strictEqual(tree.b.session, 'b');
});

test('열려 있지 않은 tmux 세션은 attach 한 뒤 새 칸에 놓는다', async () => {
  const { S, actions, core } = await setup({ width: 900 });
  const id = await actions.splitOpenSession('right', { sessionId: null, tmuxName: 'tx' });
  assert.strictEqual(id, 'x1');
  assert.ok(core.getSession('x1'));
  assert.strictEqual(S.getTree().b.session, 'x1');
  assert.strictEqual(S.getTree().a.session, 'd');
});

test('조건이 안 맞으면(폭 720 미만·잘못된 방향) 아무것도 하지 않는다', async () => {
  const narrow = await setup({ width: 500 });
  const before = JSON.stringify(narrow.S.getTree());
  assert.strictEqual(await narrow.actions.splitOpenSession('right', { sessionId: 'a' }), null);
  assert.strictEqual(JSON.stringify(narrow.S.getTree()), before);
  const wide = await setup({ width: 900 });
  assert.strictEqual(await wide.actions.splitOpenSession('left', { sessionId: 'a' }), null, 'left/top 은 없다');
  assert.strictEqual(leaves(wide.S).length, 1);
});
