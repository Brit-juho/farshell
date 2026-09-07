// L4 — layout/rail.js(좌측 rail) 검증. quickopen.test.js/pane-picker.test.js와
// 같은 하네스(vm-esm + 진짜 index.html 마크업 + 최소 vendor 스텁)를 쓴다.
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
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const DOM_JS = path.join(__dirname, '../js/core/dom.js');
const RAIL_JS = path.join(__dirname, '../js/layout/rail.js');

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

function buildFetch(state) {
  return (url, opts) => {
    const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    const u = String(url);
    if (opts?.method === 'PUT' && u.includes('/api/workspace')) {
      state.putCalls.push(JSON.parse(opts.body));
      state.workspace = { ...state.workspace, ...JSON.parse(opts.body) };
      return ok(state.workspace);
    }
    if (u.includes('/api/workspace')) return ok(state.workspace || {});
    if (u.includes('/api/tmux/sessions')) return ok(state.tmuxSessions || []);
    if (u.includes('/api/agents')) return ok(state.agents || {});
    if (u.includes('/api/queue')) return ok(state.queue || { items: [] });
    if (u.includes('/api/ports')) return ok(state.ports || { ports: [] });
    // agent/status.js가 whenAuthed 즉시 이걸 물어봐서 .needs-*(rail의 파일/포트
    // 버튼 포함)를 실제 capability로 게이팅한다 — 배지 테스트가 뜻하지 않게
    // .needs-ports를 숨겨버리지 않도록 전부 true로 응답한다.
    if (u.includes('/api/capabilities')) return ok({ fs: true, ports: true, push: true, voice: false });
    return ok({});
  };
}

async function buildWindow({ tmuxSessions = [], agents = {}, queue = { items: [] }, ports = { ports: [] }, workspace = {}, authed = true } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;

  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  const state = { tmuxSessions, agents, queue, ports, workspace, putCalls: [] };
  window.fetch = buildFetch(state);
  if (authed) window.__vtAuthed = true; // agent/status.js의 whenAuthed 게이트를 즉시 통과

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  await importFresh(TOAST_JS, env.context, cache);
  await importFresh(ANSILEX_JS, env.context, cache);
  const sessionNs = await importFresh(SESSION_JS, env.context, cache);
  await importFresh(PANES_JS, env.context, cache);
  const storeNs = await importFresh(STORE_JS, env.context, cache);
  const coreStoreNs = await importFresh(CORE_STORE_JS, env.context, cache);
  const domNs = await importFresh(DOM_JS, env.context, cache);
  await importFresh(RAIL_JS, env.context, cache);

  return { window, state, ...sessionNs, ...coreStoreNs, store: storeNs, dom: domNs };
}

async function flush() {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
}

test('rail 항목 6개가 올바른 data-rail/data-action을 갖는다', async () => {
  const { window } = await buildWindow();
  const ids = ['vt-rail-session', 'vt-rail-file', 'vt-rail-queue', 'vt-rail-ports', 'vt-rail-usage', 'vt-rail-settings'];
  for (const id of ids) assert.ok(window.document.getElementById(id), `${id}가 있어야 한다`);
  assert.strictEqual(window.document.getElementById('vt-rail-file').dataset.action, 'viewer.show');
  assert.strictEqual(window.document.getElementById('vt-rail-queue').dataset.action, 'queue.show');
  assert.strictEqual(window.document.getElementById('vt-rail-ports').dataset.action, 'ports.show');
  assert.strictEqual(window.document.getElementById('vt-rail-session').dataset.action, undefined);
  assert.strictEqual(window.document.getElementById('vt-rail-settings').dataset.action, undefined);
});

test('설정 패널 정적 컨텐츠는 부팅 직후(아무 버튼도 안 눌렀을 때) 화면에 없다', async () => {
  // 실브라우저 검증 중 실제로 재현한 회귀: #vt-rail-settings-tpl가 id 셀렉터
  // display:flex 규칙(legacy.css) 때문에 hidden 속성이 있어도 실제로는 보였다
  // — [hidden]에 대한 명시적 display:none 오버라이드가 없으면 재발한다.
  const { window } = await buildWindow();
  const tpl = window.document.getElementById('vt-rail-settings-tpl');
  assert.strictEqual(tpl.hidden, true);
  assert.strictEqual(window.getComputedStyle(tpl).display, 'none');
});

test('세션 버튼 클릭 → 패널이 열리고 목록 + "+ 새 세션" + 세션 전용 동작 2개가 보인다', async () => {
  const { window, addSession } = await buildWindow();
  addSession('a');
  window.document.getElementById('vt-rail-session').click();
  await flush();

  assert.strictEqual(window.document.getElementById('vt-rail-panel').hidden, false);
  assert.strictEqual(window.document.getElementById('vt-rail-panel-title').textContent, '세션');
  assert.ok(window.document.querySelector('.vt-rail-session-new'), '"+ 새 세션" 버튼이 있어야 한다');
  const footerItems = Array.from(window.document.querySelectorAll('.vt-rail-session-footer-item')).map(b => b.textContent);
  assert.ok(footerItems.includes('이 세션 맥에서 열기'));
  assert.ok(footerItems.includes('tmux 세션 목록'));
  assert.ok(Array.from(window.document.querySelectorAll('.vt-rail-session-select')).some(b => b.textContent === 'a'));
});

test('같은 버튼을 다시 누르면 패널이 닫힌다(토글)', async () => {
  const { window } = await buildWindow();
  const btn = window.document.getElementById('vt-rail-session');
  btn.click();
  await flush();
  assert.strictEqual(window.document.getElementById('vt-rail-panel').hidden, false);
  btn.click();
  await flush();
  assert.strictEqual(window.document.getElementById('vt-rail-panel').hidden, true);
});

test('세션 선택 시 전환은 되지만 패널은 열린 채로 남는다(quickopen과 달리)', async () => {
  const { window, addSession, activeSessionId } = await buildWindow();
  addSession('a'); addSession('b');
  window.document.getElementById('vt-rail-session').click();
  await flush();
  const row = Array.from(window.document.querySelectorAll('.vt-rail-session-select')).find(b => b.textContent === 'a');
  row.click();
  assert.strictEqual(activeSessionId(), 'a');
  assert.strictEqual(window.document.getElementById('vt-rail-panel').hidden, false, '패널이 닫히면 안 된다');
});

test('세션 닫기 버튼 → removeSession이 불리고 패널이 다시 그려진다', async () => {
  const { window, addSession } = await buildWindow();
  addSession('a'); addSession('b');
  window.document.getElementById('vt-rail-session').click();
  await flush();
  assert.strictEqual(window.document.querySelectorAll('.vt-rail-session-row').length, 2);

  const rowB = Array.from(window.document.querySelectorAll('.vt-rail-session-row'))
    .find(r => r.querySelector('.vt-rail-session-select')?.textContent === 'b');
  rowB.querySelector('.vt-rail-session-action[aria-label="b 닫기"]').click();
  await flush();
  assert.strictEqual(window.document.querySelectorAll('.vt-rail-session-row').length, 1);
});

test('tmux 세션은 라이브 프리뷰 카드(썸네일)로 나온다', async () => {
  const { window, addSession } = await buildWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo', web_session_id: 'a' }],
  });
  addSession('a');
  window.document.getElementById('vt-rail-session').click();
  await flush();
  const card = window.document.querySelector('.vt-rail-session-card');
  assert.ok(card, 'tmux 세션은 카드형이어야 한다');
  assert.ok(card.classList.contains('vt-card'));
});

test('설정 버튼 클릭 → ⋯ 메뉴에서 옮겨온 정적 컨텐츠(테마 칩 등)가 패널에 보인다', async () => {
  const { window } = await buildWindow();
  window.document.getElementById('vt-rail-settings').click();
  await flush();
  assert.strictEqual(window.document.getElementById('vt-rail-panel-title').textContent, '설정');
  const chips = window.document.querySelectorAll('#vt-rail-panel-body .theme-chip');
  assert.strictEqual(chips.length, 6);
  assert.ok(window.document.getElementById('autocopy-checkbox'), '체크박스 id가 그대로 유지돼야 한다(moreMenu.js 계약)');
});

test('설정 패널을 닫았다 다시 열어도 체크박스 상태가 유지된다(재생성이 아니라 이동)', async () => {
  const { window } = await buildWindow();
  const settingsBtn = window.document.getElementById('vt-rail-settings');
  settingsBtn.click();
  await flush();
  const cb = window.document.getElementById('autocopy-checkbox');
  cb.checked = false;
  cb.dispatchEvent(new window.Event('change'));

  settingsBtn.click(); // 닫기
  await flush();
  settingsBtn.click(); // 다시 열기
  await flush();
  assert.strictEqual(window.document.getElementById('autocopy-checkbox').checked, false);
});

test('패널을 열고 닫으면 /api/workspace에 상태가 저장된다', async () => {
  const { window, state } = await buildWindow();
  window.document.getElementById('vt-rail-session').click();
  await flush();
  const opened = state.putCalls.find(c => c.ui?.rail?.open === 'session');
  assert.ok(opened, '열림 상태가 저장돼야 한다');

  window.document.getElementById('vt-rail-session').click(); // 닫기
  await flush();
  const closed = state.putCalls[state.putCalls.length - 1];
  assert.strictEqual(closed.ui.rail.open, null);
});

test('부팅 시 /api/workspace에 저장된 열림 상태를 복원한다', async () => {
  const { window } = await buildWindow({ workspace: { ui: { rail: { open: 'settings', width: 320 } } } });
  await flush();
  assert.strictEqual(window.document.getElementById('vt-rail-panel').hidden, false);
  assert.strictEqual(window.document.getElementById('vt-rail-panel-title').textContent, '설정');
  assert.strictEqual(
    window.document.documentElement.style.getPropertyValue('--vt-rail-panel-w').trim(),
    '320px'
  );
});

test('큐/포트 배지가 각 API 응답에 맞춰 표시된다', async () => {
  const { window } = await buildWindow({
    queue: { items: [{ id: 1 }, { id: 2 }] },
    ports: { ports: [{ port: 3000, protected: false }, { port: 22, protected: true }] },
  });
  await flush();
  const qb = window.document.getElementById('vt-rail-badge-queue');
  const pb = window.document.getElementById('vt-rail-badge-ports');
  assert.strictEqual(qb.hidden, false);
  assert.strictEqual(qb.textContent, '2');
  assert.strictEqual(pb.hidden, false);
  assert.strictEqual(pb.textContent, '1', 'protected 포트는 배지 수에서 제외돼야 한다');
});

test('큐가 비어 있으면 배지가 숨는다', async () => {
  const { window } = await buildWindow({ queue: { items: [] } });
  await flush();
  assert.strictEqual(window.document.getElementById('vt-rail-badge-queue').hidden, true);
});
