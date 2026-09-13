// D13: 세션 생명주기 — addSession/switchTo/removeSession이 실제로 하는
// 일(레코드 등록·표시 이름·활성 전환·wrapper 정리, activeId 갱신)을 jsdom
// 위에서 검증한다. N37 3단계에서 세션 탭 줄이 사라졌으므로 관심사는 탭 DOM이
// 아니라 **세션 레코드와 터미널 wrapper**다.
//
// terminal.js는 xterm.js(vendor, 실제 캔버스/DOM 렌더링)·WebSocket·여러 다른
// 모듈(picker.js의 updateSessionPicker 등)에 깊게 얽혀 있어, 그 전부를 그대로
// 실행하려 들면 관련 없는 것까지 끝없이 스텁해야 한다. 대신 진짜 index.html
// DOM(진짜 #terminal-container/#keybar 마크업)을 jsdom으로 띄우고,
// term/*.js 자신이 직접 다루는 vendor 전역(Terminal/FitAddon/SearchAddon/
// WebSocket/fetch)만 최소로 대체해 실제 addSession/switchTo/removeSession
// 함수를 그대로 실행한다. 렌더링 결과가 아니라 "세션 생명주기 부기(book-keeping)"가
// 이 테스트의 관심사다.
//
// 테스트 하네스 재설계(F5 백로그): stripEsm(import/export 구문을 정규식으로
// 지운 뒤 classic <script>로 하나의 전역 렉시컬 환경에 주입) 대신
// vm.SourceTextModule로 term/session.js를 엔트리 삼아 실제 모듈 그래프(picker.js
// 와의 순환 import 포함)를 그대로 링크·평가한다 — 소스가 실제로 export하지 않는
// 이름(switchTabByOffset 등)은 이제 그 이름이 정말 window 브리지로 존재할 때만
// window.foo(...)로 부르고, 그렇지 않은 것(openSessionManager)은 실제 소비
// 경로와 같은 core/dom.js의 getAction()으로 부른다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

// 인라인 <script>(src 없는 것)는 걷어내고 마크업만 쓴다 — index.html의 부팅 인라인
// 스크립트(인증/SW 등록 등)는 이 테스트와 무관하고, jsdom이 그 자리에서 실행해
// 버리면(runScripts:'dangerously') 우리 스텁이 붙기 '전에' 실행돼 무관한
// ReferenceError만 콘솔에 남긴다. <script src=...> 태그는 fetch/실행되지 않으므로
// 그대로 둬도 무해하다.
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const KEYSEQ_JS = path.join(__dirname, '../js/lib/keyseq.js');
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');
const ANSILEX_JS = path.join(__dirname, '../js/lib/ansilex.js');
const SESSION_JS = path.join(__dirname, '../js/term/session.js');
const PANES_JS = path.join(__dirname, '../js/layout/panes.js');
const DOM_JS = path.join(__dirname, '../js/core/dom.js');

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

// term/*.js는 재연결/리사이즈 디바운스용 setTimeout을 여럿 만든다. window.close()로
// 정리 안 하면 그 타이머들이 다 소진될 때까지 프로세스가 붙잡혀 node --test가
// 느려진다.
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function buildTerminalWindow() {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;

  // vendor 전역(xterm.js/WebSocket) — term/xterm-setup.js·term/ws.js가 bare
  // identifier로 참조한다(<script src>로 로드되던 실제 vendor 스크립트 자리).
  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  window.__fetches = [];
  window.fetch = (...args) => { window.__fetches.push(args); return fakeFetch(...args); };

  // 한 buildTerminalWindow() 호출 = 한 테스트 = 새 cache. core/store.js의
  // sessions/activeId 같은 모듈 스코프 싱글톤이 테스트마다 새로 시작한다.
  const cache = new Map();
  // term/keybar.js가 bare identifier VTKeySeq로 참조하는 UMD 전역을 먼저 심는다.
  await importFresh(KEYSEQ_JS, env.context, cache);
  // picker.js/term/*.js 다수가 bare identifier showToast로 참조한다(voice.js와
  // 같은 cross-bundle 브리지 이유로 window 전역 유지 — ui/toast.js 상단 주석 참고).
  await importFresh(TOAST_JS, env.context, cache);
  // L3 5단계: layout/panes.js가 layout/pane-picker.js를 거쳐 agent/preview.js를
  // 끌어오게 됐다 — 그 파일이 모듈 최상단에서 bare identifier VTAnsiLex를
  // 즉시 읽으므로(grid-cards.test.js와 같은 이유), PANES_JS를 평가하기 전에
  // 먼저 심어둬야 한다. 안 그러면 import 시점에 ReferenceError로 죽는다.
  await importFresh(ANSILEX_JS, env.context, cache);
  // term/session.js를 엔트리로 실제 그래프 전체(picker.js·core/*·term/* 나머지)가
  // 링크·평가된다 — picker.js↔term/session.js 순환 import도 vm.Module이 표준
  // ES 모듈 순환 참조 규칙대로 정상 처리한다.
  const sessionNs = await importFresh(SESSION_JS, env.context, cache);
  // L3 1단계: session.js는 layout/store.js(트리 상태)만 알고 layout/panes.js
  // (실제 DOM 반영)는 모른다 — 실서비스에서는 main.js가 둘 다 독립적으로
  // import해서 onLayoutChange 구독이 걸리는 구조다. 이 파일을 안 불러오면
  // setPaneSession()이 상태만 바꾸고 아무도 wrapper의 display를 바꾸지 않는다.
  await importFresh(PANES_JS, env.context, cache);
  // openSessionManager는 export되지 않고 core/dom.js의 액션 레지스트리로만
  // 노출된다(data-action="session.manager") — 실제 소비 경로와 같은 방식으로 호출.
  const { getAction } = await importFresh(DOM_JS, env.context, cache);

  return { window, getAction, ...sessionNs };
}

test('addSession: 세션 레코드가 등록되고 즉시 활성 세션이 된다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  const { document } = window;

  addSession('sess-1', 'my session');

  assert.ok(window.sessions['sess-1'], '세션 레코드가 등록돼야 한다');
  assert.strictEqual(window.sessionDisplayName('sess-1'), 'my session');
  assert.strictEqual(window.orderedSessionIds().join(','), 'sess-1');
  assert.strictEqual(window.activeId, 'sess-1');

  const wrapper = document.getElementById('term-sess-1');
  assert.ok(wrapper, '터미널 wrapper가 생성돼야 한다');
  // N16: 표면 레이어부터는 display가 아니라 visibility로 보임/숨김을 표현한다
  // (10-shell-layout.md §1) — DOM에서 떼지 않고 화면 밖으로만 옮긴다.
  assert.strictEqual(wrapper.style.visibility, 'visible', '활성 세션의 wrapper는 보여야 한다');
});

test('addSession: id 없이 호출되면 유령 세션을 만들지 않는다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  addSession('', 'no id');
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.strictEqual(window.orderedSessionIds().length, 0);
  assert.strictEqual(window.document.querySelectorAll('[id^="term-"]').length, 0);
});

test('switchTo: 이전 세션은 숨고, 새 세션이 활성/표시로 전환된다', async () => {
  const { window, addSession, switchTo } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B'); // addSession이 내부에서 switchTo(b)까지 호출

  assert.strictEqual(window.activeId, 'b');
  assert.strictEqual(window.document.getElementById('term-a').style.visibility, 'hidden');
  assert.strictEqual(window.document.getElementById('term-b').style.visibility, 'visible');

  switchTo('a');
  assert.strictEqual(window.activeId, 'a');
  assert.strictEqual(window.document.getElementById('term-a').style.visibility, 'visible');
  assert.strictEqual(window.document.getElementById('term-b').style.visibility, 'hidden');
});

test('switchTabByOffset: 세션 목록 끝에서 순환한다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B');
  addSession('c', 'C'); // 활성 = c

  // switchTabByOffset은 export되지 않지만, 키보드 단축키 없이 테스트가 직접
  // 부를 수 있도록 term/session.js가 의도적으로 window 브리지를 유지한다
  // (term/session.js 하단 주석 참고).
  window.switchTabByOffset(1); // c → (순환) a
  assert.strictEqual(window.activeId, 'a');

  window.switchTabByOffset(-1); // a → (역순환) c
  assert.strictEqual(window.activeId, 'c');
});

test('removeSession: 레코드/wrapper를 정리하고 다른 세션으로 전환한다', async () => {
  const { window, addSession, removeSession } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B'); // 활성 = b

  await removeSession('b');

  assert.strictEqual(window.sessions['b'], undefined, 'sessions 맵에서 제거돼야 한다');
  assert.strictEqual(window.orderedSessionIds().join(','), 'a', '순서에서도 빠져야 한다');
  assert.strictEqual(window.document.getElementById('term-b'), null);
  assert.strictEqual(window.activeId, 'a', '닫은 세션이 활성이었다면 남은 세션으로 전환돼야 한다');
});

test('removeSession: 마지막 세션을 닫으면 activeId가 비고 온보딩이 뜬다', async () => {
  const { window, addSession, removeSession } = await buildTerminalWindow();
  addSession('only', 'Only');

  await removeSession('only');

  assert.strictEqual(window.activeId, null);
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.ok(window.document.getElementById('onboarding'), '세션이 하나도 없으면 온보딩이 표시돼야 한다');
});

test('removeSession: 비활성 세션을 닫아도 활성 세션은 그대로 유지된다', async () => {
  const { window, addSession, removeSession } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B'); // 활성 = b

  await removeSession('a');

  assert.strictEqual(window.activeId, 'b', '비활성 세션을 닫는 건 현재 활성 세션에 영향을 주면 안 된다');
  assert.strictEqual(window.orderedSessionIds().join(','), 'b');
});

test('모바일 세션 관리: 좁은 화면에서도 세션 전환과 닫기가 가능하다', async () => {
  const { window, addSession, getAction } = await buildTerminalWindow();
  addSession('a', '첫 세션');
  addSession('b', '둘째 세션');

  getAction('session.manager')();
  const manager = window.document.getElementById('session-manager');
  assert.ok(manager, '세션 관리 시트가 열려야 한다');
  assert.strictEqual(manager.querySelectorAll('.vt-session-row').length, 2);
  assert.strictEqual(window.document.getElementById('voice-session-picker').getAttribute('aria-expanded'), 'true');

  manager.querySelector('.vt-session-row .vt-session-select').click();
  assert.strictEqual(window.activeId, 'a', '목록의 세션을 누르면 해당 세션으로 전환돼야 한다');
  assert.strictEqual(window.document.getElementById('session-manager'), null, '전환 후 시트는 닫혀야 한다');

  getAction('session.manager')();
  const close = window.document.querySelectorAll('.vt-session-row')[1].querySelectorAll('.vt-session-action')[1];
  close.click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.strictEqual(window.sessions.b, undefined, '시트에서도 개별 세션을 닫을 수 있어야 한다');
});

test('모바일 세션 관리: 이름 변경은 레코드와 진입점에 함께 반영된다', async () => {
  const { window, addSession, getAction } = await buildTerminalWindow();
  addSession('a', '이전 이름');
  window.prompt = () => '새 이름';
  getAction('session.manager')();
  window.document.querySelector('.vt-session-action').click();
  await new Promise(resolve => setTimeout(resolve, 0));

  assert.strictEqual(window.sessionDisplayName('a'), '새 이름');
  assert.strictEqual(window.document.getElementById('voice-session-picker').textContent, '새 이름');
});

test('이름 변경은 공용 API 요청을 보내고 레코드를 갱신한다', async () => {
  // N37 3단계 전에는 탭 이름 더블클릭이 이 경로의 진입점이었다. 그 DOM은
  // 사라졌고, 지금 진입점은 레일 세션 행·세션 시트·팔레트다 — 셋 다 같은
  // renameSession()을 부르므로 여기서는 그 함수를 직접 검증한다.
  const { window, addSession, renameSession } = await buildTerminalWindow();
  addSession('a', '이전 이름');
  await renameSession('a', '바꾼 이름');

  const renameRequest = window.__fetches.find(([url, options]) => String(url).endsWith('/api/sessions/a') && options?.method === 'PATCH');
  assert.ok(renameRequest, '바뀐 이름은 PATCH API로 저장돼야 한다');
  assert.strictEqual(JSON.parse(renameRequest[1].body).name, '바꾼 이름');
  assert.strictEqual(window.sessionDisplayName('a'), '바꾼 이름');
});
