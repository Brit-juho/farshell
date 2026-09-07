// L6 — quickopen.js(커맨드 팔레트) 검증. terminal-lifecycle.test.js/
// pane-picker.test.js와 같은 하네스(vm-esm + 진짜 index.html 마크업 + 최소
// vendor 스텁)를 쓴다 — quickopen.js가 term/session.js(switchTo)·theme.js
// (setVtSkin)·agent/preview.js(카드 빌더)를 실제로 부르므로, 그 함수들이
// 실행되는 진짜 그래프 위에서 검증해야 의미가 있다.
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
// quickopen.js가 getAction('search.toggle')로 부르는 대상 — main.js가 실서비스에서
// 별도로 정적 import하므로, 이 파일을 명시적으로 안 불러오면 액션이 등록되지 않아
// `/` 접두사(터미널 내 검색)가 조용히 no-op이 된다.
const SEARCH_JS = path.join(__dirname, '../js/search.js');
const QUICKOPEN_JS = path.join(__dirname, '../js/quickopen.js');

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
  return (url) => {
    const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    const u = String(url);
    if (u.includes('/api/tmux/sessions')) return ok(state.tmuxSessions || []);
    if (u.includes('/api/agents')) return ok(state.agents || {});
    if (u.includes('/api/ports')) return ok(state.ports || { ports: [] });
    return ok({});
  };
}

async function buildWindow({ tmuxSessions = [], agents = {}, ports = { ports: [] } } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;

  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  const state = { tmuxSessions, agents, ports };
  window.fetch = buildFetch(state);

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  await importFresh(TOAST_JS, env.context, cache);
  await importFresh(ANSILEX_JS, env.context, cache);
  const sessionNs = await importFresh(SESSION_JS, env.context, cache);
  await importFresh(PANES_JS, env.context, cache);
  const storeNs = await importFresh(STORE_JS, env.context, cache);
  const coreStoreNs = await importFresh(CORE_STORE_JS, env.context, cache);
  const domNs = await importFresh(DOM_JS, env.context, cache);
  await importFresh(SEARCH_JS, env.context, cache);
  const qoNs = await importFresh(QUICKOPEN_JS, env.context, cache);

  return { window, ...sessionNs, ...coreStoreNs, store: storeNs, dom: domNs, quickopen: qoNs };
}

async function flush() {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
}

function openPalette(window) {
  window.document.getElementById('palette-toggle').click();
}

test('상단바 돋보기(#palette-toggle) 클릭 → 팔레트가 열린다', async () => {
  const { window } = await buildWindow();
  openPalette(window);
  await flush();
  assert.ok(window.document.getElementById('vt-qopen'), '팔레트가 열려야 한다');
});

test('Mod+K(Ctrl/Cmd+K) → 팔레트가 열리고, 다시 누르면 닫힌다(토글)', async () => {
  const { window } = await buildWindow();
  const kd = (opts) => window.document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'k', bubbles: true, cancelable: true, ...opts }));
  kd({ ctrlKey: true });
  await flush();
  assert.ok(window.document.getElementById('vt-qopen'), 'Ctrl+K로 열려야 한다');
  kd({ ctrlKey: true });
  await flush();
  assert.strictEqual(window.document.getElementById('vt-qopen'), null, '다시 누르면 닫혀야(토글) 한다');

  kd({ metaKey: true });
  await flush();
  assert.ok(window.document.getElementById('vt-qopen'), 'Cmd+K(metaKey)로도 열려야 한다');
});

test('접두사 없음 — 열린 세션·명령이 함께 나열되고, 세션 클릭 시 전환된다', async () => {
  const { window, addSession, activeSessionId } = await buildWindow();
  addSession('a'); addSession('b');
  openPalette(window);
  await flush();

  const rows = Array.from(window.document.querySelectorAll('.vt-qo-row'));
  assert.ok(rows.some(r => r.textContent.includes('b')), '세션 행이 보여야 한다');
  assert.ok(rows.some(r => r.textContent.includes('코드 뷰어 열기')), '명령 행(코드 뷰어)이 보여야 한다');
  assert.ok(rows.some(r => r.textContent.includes('새 세션')), '명령 행(새 세션)이 보여야 한다');
  // 설정 그룹(테마·푸시 등)은 접두사 없이는 안 보여야 한다(> 전용).
  assert.ok(!rows.some(r => r.textContent.startsWith('테마')), '테마는 기본 목록에 없어야 한다');

  const sessionRow = rows.find(r => r.textContent.trim() === 'a');
  sessionRow.click();
  assert.strictEqual(activeSessionId(), 'a');
  assert.strictEqual(window.document.getElementById('vt-qopen'), null, '선택하면 팔레트가 닫혀야 한다');
});

test('capability 게이팅 — .needs-fs가 숨겨져 있으면 "코드 뷰어 열기" 명령이 안 나온다', async () => {
  const { window } = await buildWindow();
  window.document.querySelectorAll('.needs-fs').forEach(el => { el.style.display = 'none'; });
  openPalette(window);
  await flush();
  const rows = Array.from(window.document.querySelectorAll('.vt-qo-row'));
  assert.ok(!rows.some(r => r.textContent.includes('코드 뷰어 열기')), 'fs 미지원이면 숨겨야 한다');
});

test('`>` 접두사 — 설정 전용 목록(테마·자동복사 등)이 나오고, 테마 선택 시 스킨이 바뀐다', async () => {
  const { window } = await buildWindow();
  openPalette(window);
  await flush();
  const input = window.document.getElementById('vt-qo-input');
  input.value = '> farshell';
  input.dispatchEvent(new window.Event('input'));

  const rows = Array.from(window.document.querySelectorAll('.vt-qo-row'));
  const themeRow = rows.find(r => r.textContent.includes('FarShell'));
  assert.ok(themeRow, '테마 · FarShell 행이 나와야 한다');
  themeRow.click();
  assert.strictEqual(window.document.documentElement.getAttribute('data-skin'), 'farshell');
  assert.strictEqual(window.document.getElementById('vt-qopen'), null);
});

test('`>` 접두사 — "드래그 시 자동 복사" 선택 시 체크박스가 토글되고 change 이벤트가 난다', async () => {
  const { window } = await buildWindow();
  const cb = window.document.getElementById('autocopy-checkbox');
  cb.checked = true;
  let changed = false;
  cb.addEventListener('change', () => { changed = true; });

  openPalette(window);
  await flush();
  const input = window.document.getElementById('vt-qo-input');
  input.value = '>자동 복사';
  input.dispatchEvent(new window.Event('input'));
  const row = Array.from(window.document.querySelectorAll('.vt-qo-row')).find(r => r.textContent.includes('자동 복사'));
  assert.ok(row, '자동 복사 행이 있어야 한다');
  row.click();

  assert.strictEqual(cb.checked, false, '체크 상태가 토글돼야 한다');
  assert.ok(changed, 'change 이벤트가 발화해 localStorage 동기화 로직을 타야 한다');
});

test('`/` 접두사 — 터미널 내 검색을 열고 입력값을 검색창에 채운다', async () => {
  const { window } = await buildWindow();
  const searchBar = window.document.getElementById('search-bar');
  const searchInput = window.document.getElementById('search-input');
  assert.ok(!searchBar.classList.contains('visible'));

  openPalette(window);
  await flush();
  const input = window.document.getElementById('vt-qo-input');
  input.value = '/needle';
  input.dispatchEvent(new window.Event('input'));

  const row = window.document.querySelector('.vt-qo-row');
  assert.match(row.textContent, /needle/);
  row.click();

  assert.ok(searchBar.classList.contains('visible'), '검색바가 열려야 한다');
  assert.strictEqual(searchInput.value, 'needle');
  assert.strictEqual(window.document.getElementById('vt-qopen'), null);
});

test('`:` 접두사 — 포트 목록을 보여주고, 선택하면 포트 대시보드를 연다', async () => {
  const { window, dom } = await buildWindow({ ports: { ports: [{ port: 3000, cmd: 'next dev', pid: 111 }] } });
  let portsShown = 0;
  dom.registerAction('ports.show', () => { portsShown++; });

  openPalette(window);
  await flush();
  const input = window.document.getElementById('vt-qo-input');
  input.value = ':3000';
  input.dispatchEvent(new window.Event('input'));
  await flush();

  const row = Array.from(window.document.querySelectorAll('.vt-qo-row')).find(r => r.textContent.includes('3000'));
  assert.ok(row, '포트 3000 행이 나와야 한다');
  row.click();
  assert.strictEqual(portsShown, 1, 'ports.show 액션이 불려야 한다');
  assert.strictEqual(window.document.getElementById('vt-qopen'), null);
});

test('`:` 접두사 — 포트 기능이 꺼져 있으면(.needs-ports 숨김) 안내 문구만 보인다', async () => {
  const { window } = await buildWindow();
  window.document.querySelectorAll('.needs-ports').forEach(el => { el.style.display = 'none'; });
  openPalette(window);
  await flush();
  const input = window.document.getElementById('vt-qo-input');
  input.value = ':';
  input.dispatchEvent(new window.Event('input'));

  assert.match(window.document.getElementById('vt-qo-body').textContent, /사용할 수 없는/);
});

test('세션 항목 — tmux로 이미 열려 있는 세션은 라이브 프리뷰 카드(썸네일)로 나온다', async () => {
  const { window, addSession } = await buildWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo', web_session_id: 'a' }],
  });
  addSession('a');
  openPalette(window);
  await flush(); // tmux 후보 fetch가 도착할 때까지

  const card = window.document.querySelector('.vt-qo-session-card');
  assert.ok(card, '세션이 tmux로 열려 있으면 카드형 썸네일이어야 한다');
  assert.ok(card.classList.contains('vt-card'));
  assert.match(card.querySelector('.card-title').textContent, /dev/);
});

test('일반 터미널 세션은 평문 행으로 나오고(카드 아님), 클릭하면 전환된다', async () => {
  const { window, addSession, activeSessionId } = await buildWindow({ tmuxSessions: [] });
  addSession('a');
  openPalette(window);
  await flush();

  assert.strictEqual(window.document.querySelectorAll('.vt-qo-session-card').length, 0);
  const row = Array.from(window.document.querySelectorAll('.vt-qo-row')).find(r => r.textContent.trim() === 'a');
  row.click();
  assert.strictEqual(activeSessionId(), 'a');
});

test('파일 업로드 명령 — file-input을 클릭한다(네이티브 피커 트리거)', async () => {
  const { window } = await buildWindow();
  const fileInput = window.document.getElementById('file-input');
  let clicked = false;
  fileInput.addEventListener('click', () => { clicked = true; });

  openPalette(window);
  await flush();
  const row = Array.from(window.document.querySelectorAll('.vt-qo-row')).find(r => r.textContent.includes('파일 업로드'));
  row.click();
  assert.ok(clicked);
});
