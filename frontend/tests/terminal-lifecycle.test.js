// D13: 탭(터미널 세션) 생명주기 — addSession/switchTo/removeSession이 실제로 하는
// 일(탭 DOM 생성/활성화 전환/정리, activeId 갱신)을 jsdom 위에서 검증한다.
//
// terminal.js는 xterm.js(vendor, 실제 캔버스/DOM 렌더링)·WebSocket·여러 다른
// classic script(picker.js의 updateSessionPicker 등)에 깊게 얽혀 있어, 그 전부를
// 그대로 실행하려 들면 관련 없는 것까지 끝없이 스텁해야 한다(grid.js를 vm으로
// 통째로 돌리려다 겪은 것과 같은 함정 — grid-wiring.test.js 주석 참고). 대신
// 진짜 index.html DOM(진짜 #tabs/#terminal-container/#keybar 마크업)을 jsdom으로
// 띄우고, terminal.js 자신이 직접 다루는 것(Terminal/FitAddon/SearchAddon/
// WebSocket/fetch)만 최소로 대체해 실제 addSession/switchTo/removeSession 함수를
// 그대로 실행한다. 렌더링 결과가 아니라 "탭 생명주기 부기(book-keeping)"가
// 이 테스트의 관심사다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

// 인라인 <script>(src 없는 것)는 걷어내고 마크업만 쓴다 — index.html의 부팅 인라인
// 스크립트(인증/SW 등록 등)는 이 테스트와 무관하고, runScripts:'dangerously'로
// JSDOM을 만드는 시점에 우리 스텁(window.fetch 등)이 붙기 '전에' 실행돼 버려서
// 무관한 ReferenceError만 콘솔에 남긴다. <script src=...> 태그는 어차피 resources
// 옵션을 안 줘서 자동 실행되지 않으므로 그대로 둬도 무해하다.
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const THEME_JS = fs.readFileSync(path.join(__dirname, '../js/theme.js'), 'utf8');
const KEYSEQ_JS = fs.readFileSync(path.join(__dirname, '../js/keyseq.js'), 'utf8');
const TERMINAL_JS = fs.readFileSync(path.join(__dirname, '../js/terminal.js'), 'utf8');
const PICKER_JS = fs.readFileSync(path.join(__dirname, '../js/picker.js'), 'utf8');

class FakeTerminal {
  constructor(opts) { this.options = opts; this.cols = 80; this.rows = 24; this._disposed = false; this._dataCb = null; }
  loadAddon() {}
  open(el) { this.element = el; }
  focus() { this._focused = true; }
  reset() {}
  write() {}
  onData(cb) { this._dataCb = cb; }
  getSelection() { return ''; }
  attachCustomKeyEventHandler() {}
  dispose() { this._disposed = true; }
}

class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = FakeWebSocket.CONNECTING; this._listeners = {}; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  send() {}
  close() { this.readyState = FakeWebSocket.CLOSED; if (this.onclose) this.onclose({ code: 1000 }); }
}
FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSING = 2; FakeWebSocket.CLOSED = 3;

// 세션 CRUD(addSession/removeSession)가 부르는 fetch들. 실제 응답 바디는 이
// 테스트의 관심사가 아니라 "빈 상태로 조용히 넘어가게"만 응답한다.
function fakeFetch(url) {
  const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  if (String(url).includes('/api/sessions') || String(url).includes('/api/tmux/sessions')) return ok([]);
  return ok({});
}

// terminal.js는 재연결/리사이즈 디바운스용 setTimeout을 여럿 만든다. window.close()로
// 정리 안 하면 그 타이머들이 다 소진될 때까지 프로세스가 붙잡혀 node --test가 느려진다.
const _windows = [];
after(() => { for (const w of _windows) { try { w.close(); } catch (_) {} } });

function buildTerminalWindow() {
  // runScripts:'dangerously' — window.eval()이 진짜 브라우저 전역(window 자기참조 등)을
  // 갖게 하기 위함이다. resources 옵션은 안 줘서 index.html의 <script src> 태그(vendor
  // 등)는 자동 fetch/실행되지 않는다 — 우리가 원하는 파일만 아래서 직접 eval한다.
  const dom = new JSDOM(INDEX_HTML, { url: 'http://localhost/', pretendToBeVisual: true, runScripts: 'dangerously' });
  const { window } = dom;
  _windows.push(window);

  window.API_BASE = '';
  window.WS_BASE = 'ws://localhost';
  window.__fetches = [];
  window.fetch = (...args) => { window.__fetches.push(args); return fakeFetch(...args); };
  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  window.showToast = () => {};
  // picker.js(세션 피커 UI)는 이 테스트의 관심사가 아니라 no-op으로 대체한다 —
  // switchTo/removeSession이 무조건 호출하므로 없으면 ReferenceError로 죽는다.
  window.updateSessionPicker = () => {};

  // window.eval()이 아니라 진짜 <script> 엘리먼트를 문서에 붙여서 실행한다 —
  // eval() 호출은 매번 독립된 전역 렉시컬 환경을 만들어 terminal.js의 최상위
  // `const sessions = {}`/`let activeId`가 다음 eval() 호출에서 "sessions is not
  // defined"로 사라진다(실측 확인). 반면 실제 <script> 태그들은 브라우저처럼
  // 같은 전역 렉시컬 환경을 공유한다 — index.html의 진짜 로드 방식과도 일치한다.
  const runScript = (code) => {
    const el = window.document.createElement('script');
    el.textContent = code;
    window.document.body.appendChild(el);
  };
  runScript(THEME_JS);
  runScript(KEYSEQ_JS);
  runScript(TERMINAL_JS);
  runScript(PICKER_JS);
  // sessions(객체 참조)/activeId(매번 재할당되는 원시값)를 테스트에서 들여다볼 수 있게
  // 같은 렉시컬 스코프를 공유하는 스크립트로 다리를 놓는다.
  runScript('window.sessions = sessions; window.__getActiveId = function () { return activeId; };');
  Object.defineProperty(window, 'activeId', { get: () => window.__getActiveId() });

  return window;
}

test('addSession: 탭 DOM이 생성되고 즉시 활성 탭이 된다', () => {
  const window = buildTerminalWindow();
  const { document } = window;

  window.addSession('sess-1', 'my session');

  const tab = document.querySelector('.tab[data-session-id="sess-1"]');
  assert.ok(tab, '탭 DOM이 생성돼야 한다');
  assert.strictEqual(tab.querySelector('.tab-name').textContent, 'my session');
  assert.ok(tab.classList.contains('active'), '새로 연 세션은 즉시 활성 탭이 된다');
  assert.strictEqual(window.activeId, 'sess-1');

  const wrapper = document.getElementById('term-sess-1');
  assert.ok(wrapper, '터미널 wrapper가 생성돼야 한다');
  assert.strictEqual(wrapper.style.display, 'block', '활성 세션의 wrapper는 보여야 한다');
});

test('addSession: id 없이 호출되면 유령 탭을 만들지 않는다', () => {
  const window = buildTerminalWindow();
  window.addSession('', 'no id');
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.strictEqual(window.document.querySelectorAll('.tab').length, 0);
});

test('switchTo: 이전 탭은 비활성/숨김, 새 탭은 활성/표시로 전환된다', () => {
  const window = buildTerminalWindow();
  window.addSession('a', 'A');
  window.addSession('b', 'B'); // addSession이 내부에서 switchTo(b)까지 호출

  const tabA = window.document.querySelector('.tab[data-session-id="a"]');
  const tabB = window.document.querySelector('.tab[data-session-id="b"]');
  assert.strictEqual(window.activeId, 'b');
  assert.ok(!tabA.classList.contains('active'));
  assert.ok(tabB.classList.contains('active'));
  assert.strictEqual(window.document.getElementById('term-a').style.display, 'none');
  assert.strictEqual(window.document.getElementById('term-b').style.display, 'block');

  window.switchTo('a');
  assert.strictEqual(window.activeId, 'a');
  assert.ok(tabA.classList.contains('active'));
  assert.ok(!tabB.classList.contains('active'));
  assert.strictEqual(window.document.getElementById('term-a').style.display, 'block');
  assert.strictEqual(window.document.getElementById('term-b').style.display, 'none');
});

test('switchTabByOffset: 탭 목록 끝에서 순환한다', () => {
  const window = buildTerminalWindow();
  window.addSession('a', 'A');
  window.addSession('b', 'B');
  window.addSession('c', 'C'); // 활성 = c

  window.switchTabByOffset(1); // c → (순환) a
  assert.strictEqual(window.activeId, 'a');

  window.switchTabByOffset(-1); // a → (역순환) c
  assert.strictEqual(window.activeId, 'c');
});

test('removeSession: 탭/wrapper DOM을 정리하고 다른 세션으로 전환한다', async () => {
  const window = buildTerminalWindow();
  window.addSession('a', 'A');
  window.addSession('b', 'B'); // 활성 = b

  await window.removeSession('b');

  assert.strictEqual(window.sessions['b'], undefined, 'sessions 맵에서 제거돼야 한다');
  assert.strictEqual(window.document.querySelector('.tab[data-session-id="b"]'), null);
  assert.strictEqual(window.document.getElementById('term-b'), null);
  assert.strictEqual(window.activeId, 'a', '닫은 탭이 활성 탭이었다면 남은 세션으로 전환돼야 한다');
  assert.ok(window.document.querySelector('.tab[data-session-id="a"]').classList.contains('active'));
});

test('removeSession: 마지막 탭을 닫으면 activeId가 비고 온보딩이 뜬다', async () => {
  const window = buildTerminalWindow();
  window.addSession('only', 'Only');

  await window.removeSession('only');

  assert.strictEqual(window.activeId, null);
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.ok(window.document.getElementById('onboarding'), '세션이 하나도 없으면 온보딩이 표시돼야 한다');
});

test('removeSession: 비활성 탭을 닫아도 활성 탭은 그대로 유지된다', async () => {
  const window = buildTerminalWindow();
  window.addSession('a', 'A');
  window.addSession('b', 'B'); // 활성 = b

  await window.removeSession('a');

  assert.strictEqual(window.activeId, 'b', '비활성 탭을 닫는 건 현재 활성 탭에 영향을 주면 안 된다');
  assert.ok(window.document.querySelector('.tab[data-session-id="b"]').classList.contains('active'));
});

test('모바일 세션 관리: 탭이 보이지 않아도 세션 전환과 닫기가 가능하다', async () => {
  const window = buildTerminalWindow();
  window.addSession('a', '첫 세션');
  window.addSession('b', '둘째 세션');

  window.openSessionManager();
  const manager = window.document.getElementById('session-manager');
  assert.ok(manager, '세션 관리 시트가 열려야 한다');
  assert.strictEqual(manager.querySelectorAll('.vt-session-row').length, 2);
  assert.strictEqual(window.document.getElementById('voice-session-picker').getAttribute('aria-expanded'), 'true');

  manager.querySelector('.vt-session-row .vt-session-select').click();
  assert.strictEqual(window.activeId, 'a', '목록의 세션을 누르면 해당 세션으로 전환돼야 한다');
  assert.strictEqual(window.document.getElementById('session-manager'), null, '전환 후 시트는 닫혀야 한다');

  window.openSessionManager();
  const close = window.document.querySelectorAll('.vt-session-row')[1].querySelectorAll('.vt-session-action')[1];
  close.click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.strictEqual(window.sessions.b, undefined, '시트에서도 개별 세션을 닫을 수 있어야 한다');
});

test('모바일 세션 관리: 이름 변경은 탭과 진입점에 함께 반영된다', async () => {
  const window = buildTerminalWindow();
  window.addSession('a', '이전 이름');
  window.prompt = () => '새 이름';
  window.openSessionManager();
  window.document.querySelector('.vt-session-action').click();
  await new Promise(resolve => setTimeout(resolve, 0));

  assert.strictEqual(window.document.querySelector('.tab-name').textContent, '새 이름');
  assert.strictEqual(window.document.getElementById('voice-session-picker').textContent, '새 이름');
});

test('탭 더블클릭 이름 변경도 공용 API 요청을 보낸다', async () => {
  const window = buildTerminalWindow();
  window.addSession('a', '이전 이름');
  const name = window.document.querySelector('.tab-name');
  name.ondblclick({ stopPropagation() {} });
  name.textContent = '바꾼 이름';
  name.onblur();
  await new Promise(resolve => setTimeout(resolve, 0));

  const renameRequest = window.__fetches.find(([url, options]) => String(url).endsWith('/api/sessions/a') && options?.method === 'PATCH');
  assert.ok(renameRequest, '편집된 탭 이름은 PATCH API로 저장돼야 한다');
  assert.strictEqual(JSON.parse(renameRequest[1].body).name, '바꾼 이름');
});
