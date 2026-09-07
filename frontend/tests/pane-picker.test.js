// L3 5단계 — layout/pane-picker.js(빈 pane 클릭 → 세션 선택 시트) 검증.
// terminal-lifecycle.test.js와 같은 하네스(vm-esm + 진짜 index.html 마크업 +
// 최소 vendor 스텁)를 쓴다 — layout/pane-picker.js가 term/session.js(switchTo·
// createSession)와 agent/preview.js(카드 빌더·attachTmuxSession)를 실제로
// 부르므로, 그 함수들이 실행되는 진짜 그래프 위에서 검증해야 의미가 있다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const KEYSEQ_JS = path.join(__dirname, '../js/lib/keyseq.js');
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');
const ANSILEX_JS = path.join(__dirname, '../js/lib/ansilex.js');
const SESSION_JS = path.join(__dirname, '../js/term/session.js');
const PANES_JS = path.join(__dirname, '../js/layout/panes.js');
const STORE_JS = path.join(__dirname, '../js/layout/store.js');

class FakeTerminal {
  constructor(opts) { this.options = opts; this.cols = 80; this.rows = 24; this._disposed = false; }
  loadAddon() {}
  open(el) { this.element = el; }
  focus() { this._focused = true; }
  reset() {}
  write() {}
  onData() {}
  getSelection() { return ''; }
  attachCustomKeyEventHandler() {}
  dispose() { this._disposed = true; }
}

class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = FakeWebSocket.CONNECTING; }
  addEventListener() {}
  send() {}
  close() { this.readyState = FakeWebSocket.CLOSED; }
}
FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSING = 2; FakeWebSocket.CLOSED = 3;

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// tmuxSessions/agents는 시트가 fetch로 물어보는 목록 — 테스트마다 다르게 준다.
// 새 세션 생성(createSession/attachTmuxSession)이 부르는 POST에는 매번 다른
// id를 발급해 addSession이 중복 id로 헷갈리지 않게 한다.
function buildFetch(state) {
  let seq = 0;
  return (url, opts) => {
    const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    const u = String(url);
    const method = opts?.method;
    if (u.includes('/api/tmux/attach')) return ok({ id: `attached-${++seq}`, name: `tmux:${JSON.parse(opts.body).name}` });
    if (u.includes('/api/tmux/sessions')) return ok(state.tmuxSessions || []);
    if (u.includes('/api/agents')) return ok(state.agents || {});
    if (u.endsWith('/api/sessions') && method === 'POST') return ok({ id: `plain-${++seq}` });
    return ok({});
  };
}

async function buildWindow({ tmuxSessions = [], agents = {} } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;

  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  const state = { tmuxSessions, agents };
  window.fetch = buildFetch(state);

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  await importFresh(TOAST_JS, env.context, cache);
  await importFresh(ANSILEX_JS, env.context, cache);
  const sessionNs = await importFresh(SESSION_JS, env.context, cache);
  await importFresh(PANES_JS, env.context, cache);
  const storeNs = await importFresh(STORE_JS, env.context, cache);

  return { window, ...sessionNs, store: storeNs };
}

// _renderCandidates()는 fire-and-forget으로 시작되는 비동기 함수라(openPanePicker
// 자체는 동기), 테스트가 직접 await할 promise가 없다 — Promise.all(fetch 2개) →
// 각 json() 파싱까지 마이크로태스크 홉이 몇 번인지 세는 대신, setImmediate로
// 매크로태스크 경계를 넘겨 그 사이의 마이크로태스크 큐를 통째로 비운다(몇 홉이든
// 안전).
async function flush() {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
}

test('빈 pane 클릭 → 시트가 열리고 "+ 새 세션" 버튼이 있다', async () => {
  const { window, addSession, store } = await buildWindow();
  addSession('a');
  const emptyPaneId = store.splitActivePane('row'); // 빈 leaf 하나 추가, 활성이 됨

  window.document.getElementById(`vt-pane-${emptyPaneId}`).querySelector('.vt-pane-empty').click();
  await flush();

  assert.ok(window.document.getElementById('vt-pane-pick'), '시트가 열려야 한다');
  assert.ok(window.document.querySelector('.vt-pp-new'), '"+ 새 세션" 버튼이 있어야 한다');
});

test('"+ 새 세션" 클릭 → 그 pane에 새 세션이 배정되고 시트가 닫힌다', async () => {
  const { window, addSession, store } = await buildWindow();
  addSession('a');
  const emptyPaneId = store.splitActivePane('row');

  window.document.getElementById(`vt-pane-${emptyPaneId}`).querySelector('.vt-pane-empty').click();
  await flush();
  window.document.querySelector('.vt-pp-new').click();
  await flush();

  assert.strictEqual(window.document.getElementById('vt-pane-pick'), null, '시트가 닫혀야 한다');
  const node = store.getTree().b?.id === emptyPaneId ? store.getTree().b : store.getTree().a;
  assert.ok(node.session, '빈 leaf에 새 세션이 배정돼야 한다');
});

test('아직 안 열린 tmux 세션 카드 클릭 → attach 후 그 pane에 배정된다', async () => {
  const { window, addSession, store } = await buildWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo' }],
  });
  addSession('a');
  const emptyPaneId = store.splitActivePane('row');

  window.document.getElementById(`vt-pane-${emptyPaneId}`).querySelector('.vt-pane-empty').click();
  await flush();
  window.document.querySelector('[data-name="dev"]').click();
  await flush();

  assert.strictEqual(window.document.getElementById('vt-pane-pick'), null);
  const node = store.getTree().a.id === emptyPaneId ? store.getTree().a : store.getTree().b;
  assert.ok(node.session, 'attach된 세션이 그 pane에 배정돼야 한다');
});

test('이미 다른 pane에 열려 있는 tmux 세션 선택 → 이동(원래 pane은 비워진다)', async () => {
  const { window, addSession, switchTo, store } = await buildWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo', web_session_id: 'a' }],
  });
  addSession('a'); // 세션 a가 tmux 'dev'로 이미 열려 있는 상태를 흉내
  const rootPaneId = store.getActivePaneId();
  const emptyPaneId = store.splitActivePane('row'); // rootPaneId(=a)와 별개인 새 빈 leaf

  window.document.getElementById(`vt-pane-${emptyPaneId}`).querySelector('.vt-pane-empty').click();
  await flush();
  const card = window.document.querySelector('[data-name="dev"]');
  assert.ok(card.classList.contains('open-tab'), '이미 열린 세션은 open-tab 표시가 있어야 한다');
  card.click();
  await flush();

  const tree = store.getTree();
  const rootNode = tree.a.id === rootPaneId ? tree.a : tree.b;
  const emptyNode = tree.a.id === emptyPaneId ? tree.a : tree.b;
  assert.strictEqual(rootNode.session, null, '원래 있던 pane은 비워져야 한다(중복 attach 금지)');
  assert.strictEqual(emptyNode.session, 'a', '선택한 pane으로 옮겨와야 한다');
  void switchTo;
});

test('tmux가 아닌 일반 터미널 열린 세션도 목록에 나오고, 선택하면 이동한다', async () => {
  const { window, addSession, store } = await buildWindow({ tmuxSessions: [] });
  addSession('a'); // tmuxName 없음 — 일반 터미널
  const rootPaneId = store.getActivePaneId();
  const emptyPaneId = store.splitActivePane('row');

  window.document.getElementById(`vt-pane-${emptyPaneId}`).querySelector('.vt-pane-empty').click();
  await flush();

  const plainCard = window.document.querySelector('#vt-pp-cards .vt-card.open-tab');
  assert.ok(plainCard, '일반 터미널 세션도 카드로 나와야 한다');
  assert.match(plainCard.textContent, /미리보기 없음/);
  plainCard.click();
  await flush();

  const tree = store.getTree();
  const emptyNode = tree.a.id === emptyPaneId ? tree.a : tree.b;
  const rootNode = tree.a.id === rootPaneId ? tree.a : tree.b;
  assert.strictEqual(emptyNode.session, 'a');
  assert.strictEqual(rootNode.session, null);
});
