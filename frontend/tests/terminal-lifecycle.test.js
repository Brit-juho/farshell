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
// 이름은 이제 그 이름이 정말 window 브리지로 존재할 때만
// window.foo(...)로 부르고, 그렇지 않은 것(openSessionManager)은 실제 소비
// 경로와 같은 core/dom.js의 getAction()으로 부른다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { answerPrompt } = require('./helpers/dialog-stub');

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
const LAYOUT_STORE_JS = path.join(__dirname, '../js/layout/store.js');
const SESSION_ACTIONS_JS = path.join(__dirname, '../js/term/session-actions.js');

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

async function buildTerminalWindow({ fetchImpl = fakeFetch } = {}) {
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
  window.fetch = (...args) => { window.__fetches.push(args); return fetchImpl(...args); };

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
  // 2026-09-23 — 데스크톱 탭 순서 테스트가 헤더 탭(화면)을 직접 열어야 한다
  // (openLayoutTab은 window 브리지가 없다 — layout/view-actions.js의 openGroupTab만
  // 있고 그건 이 테스트 그래프에 없다). cache를 공유하므로 session.js가 보는
  // 것과 같은 싱글톤이다(layout-dnd.test.js의 loadDnd()와 같은 이유).
  const layoutStore = await importFresh(LAYOUT_STORE_JS, env.context, cache);

  return { window, getAction, cache, context: env.context, layoutStore, ...sessionNs };
}

test('addSession: 세션 레코드가 등록되고 즉시 활성 세션이 된다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  const { document } = window;

  addSession('sess-1', 'my session');

  assert.ok(window.sessions['sess-1'], '세션 레코드가 등록돼야 한다');
  assert.strictEqual(window.sessionDisplayName('sess-1'), 'my session');
  assert.strictEqual(Object.keys(window.sessions).join(','), 'sess-1');
  assert.strictEqual(window.activeId, 'sess-1');

  const wrapper = document.getElementById('term-sess-1');
  assert.ok(wrapper, '터미널 wrapper가 생성돼야 한다');
  // 2026-09-18 — 보임/숨김이 **부모가 누구인가**로 바뀌었다. 그 전에는 인라인
  // visibility였다(표면 레이어 시절: 전부 #vt-surface에 살면서 안 보이는 건
  // 화면 밖으로 translate). 지금은 보이는 세션이 자기 pane-body 안에 실제로
  // 들어가고, 배치 안 된 세션만 대기실(#vt-term-stage)에 남는다.
  assert.strictEqual(wrapper.parentElement.className, 'vt-pane-body', '활성 세션은 pane 안에 있어야 한다');
});

test('addSession: id 없이 호출되면 유령 세션을 만들지 않는다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  addSession('', 'no id');
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.strictEqual(window.document.querySelectorAll('[id^="term-"]').length, 0);
});

test('switchTo: 이전 세션은 숨고, 새 세션이 활성/표시로 전환된다', async () => {
  const { window, addSession, switchTo } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B'); // addSession이 내부에서 switchTo(b)까지 호출

  // 보임 = pane-body의 자식, 숨김 = 대기실(#vt-term-stage)의 자식.
  const where = (id) => window.document.getElementById(`term-${id}`).parentElement;
  assert.strictEqual(window.activeId, 'b');
  assert.strictEqual(where('a').id, 'vt-term-stage');
  assert.strictEqual(where('b').className, 'vt-pane-body');

  switchTo('a');
  assert.strictEqual(window.activeId, 'a');
  assert.strictEqual(where('a').className, 'vt-pane-body');
  assert.strictEqual(where('b').id, 'vt-term-stage');
});

// 2026-10-03(세션·뷰 규칙 "닫기") — 닫은 세션의 칸은 빈 칸으로 남는다. 다른
// 세션(remaining[0])을 그 칸으로 끌어오던 대체 로직은 없앴다.
test('removeSession: 레코드/wrapper를 정리하고 칸은 빈 칸으로 남긴다', async () => {
  const { window, addSession, removeSession, layoutStore } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B'); // 활성 = b

  await removeSession('b');

  assert.strictEqual(window.sessions['b'], undefined, 'sessions 맵에서 제거돼야 한다');
  assert.strictEqual(Object.keys(window.sessions).join(','), 'a', '순서에서도 빠져야 한다');
  assert.strictEqual(window.document.getElementById('term-b'), null);
  assert.strictEqual(window.activeId, null, '활성 세션은 칸에서 파생된다 — 빈 칸이면 null');
  const tabs = layoutStore.getTabsWithTrees();
  assert.ok(tabs.every((t) => JSON.stringify(t.tree).indexOf('"b"') === -1), '트리에 죽은 id가 남지 않는다');
  assert.strictEqual(layoutStore.getTree().session, null, 'b가 있던 칸은 빈 칸');
  assert.strictEqual(layoutStore.findTabBySessionId('a') !== null, true, 'a는 자기 뷰에 그대로');
});

test('removeSession: 마지막 세션을 닫아도 뷰는 빈 칸으로 남는다 — 온보딩은 뷰를 닫을 때', async () => {
  const { window, addSession, removeSession, layoutStore, cache, context } = await buildTerminalWindow();
  addSession('only', 'Only');

  await removeSession('only');

  assert.strictEqual(window.activeId, null);
  assert.strictEqual(Object.keys(window.sessions).length, 0);
  assert.strictEqual(window.document.getElementById('onboarding'), null, '칸만 비었다 — 뷰는 남아 있다');
  assert.strictEqual(layoutStore.getTree().session, null);

  // 뷰 × — 마지막 뷰를 닫으면 빈 화면(온보딩).
  const actions = await importFresh(SESSION_ACTIONS_JS, context, cache);
  await actions.closeView(layoutStore.getActiveTabId());
  assert.ok(layoutStore.isLayoutEmpty());
  assert.ok(window.document.getElementById('onboarding'), '뷰가 하나도 없으면 온보딩');
});

test('closeView: 묶음 뷰를 닫으면 구성을 기억하고, 칸 ×는 세션만 닫고 빈 칸을 남긴다', async () => {
  const { window, addSession, layoutStore, cache, context } = await buildTerminalWindow();
  addSession('a', 'A');
  window.sessions.a.tmuxName = 'ta';
  const right = layoutStore.splitActivePane('row', null);
  layoutStore.setPaneSession(null, right);
  addSession('b', 'B', { place: 'pane', tmuxName: 'tb' });
  const tab = layoutStore.getActiveTabId();
  assert.strictEqual(JSON.stringify(layoutStore.tabGroups().map((g) => g.sessionIds)), '[["a","b"]]');

  const actions = await importFresh(SESSION_ACTIONS_JS, context, cache);
  // 칸 × — b만 닫히고 칸은 빈 칸, 묶음 판정은 즉시 풀린다.
  await actions.closePaneAction(right);
  assert.strictEqual(window.sessions.b, undefined);
  assert.strictEqual(layoutStore.countLeaves(), 2, '칸은 남는다');
  assert.strictEqual(layoutStore.tabGroups().length, 0, '묶음 판정이 즉시 풀린다');
  // 빈 칸의 × — 칸을 없앤다.
  await actions.closePaneAction(right);
  assert.strictEqual(layoutStore.countLeaves(), 1);

  // 다시 묶고 뷰 ×.
  const r2 = layoutStore.splitActivePane('row', null);
  addSession('c', 'C', { place: 'pane', tmuxName: 'tc' });
  void r2;
  await actions.closeView(tab);
  assert.strictEqual(Object.keys(window.sessions).length, 0, '안의 세션은 전부 닫힌다');
  const remembered = layoutStore.findClosedViewByTmux('tc');
  assert.ok(remembered, '묶음 구성을 기억한다');
  assert.strictEqual(JSON.stringify(layoutStore.closedViewMembers(remembered)), '["ta","tc"]');
  assert.ok(window.document.getElementById('onboarding'));
});

test('removeSession: 비활성 세션을 닫아도 활성 세션은 그대로 유지된다', async () => {
  const { window, addSession, removeSession } = await buildTerminalWindow();
  addSession('a', 'A');
  addSession('b', 'B'); // 활성 = b

  await removeSession('a');

  assert.strictEqual(window.activeId, 'b', '비활성 세션을 닫는 건 현재 활성 세션에 영향을 주면 안 된다');
  assert.strictEqual(Object.keys(window.sessions).join(','), 'b');
});

test('상단 세션 이름은 팝업이 아니라 「세션」 탭 전환 이벤트를 보낸다', async () => {
  const { window, addSession, getAction } = await buildTerminalWindow();
  addSession('a', '첫 세션');
  let n = 0;
  window.document.addEventListener('vt:sessions-toggle', () => { n += 1; });
  getAction('session.manager')();
  assert.strictEqual(n, 1);
  assert.strictEqual(window.document.getElementById('session-manager'), null, '시트는 더 이상 없다');
});

test('세션 이름 변경은 레코드와 상단 진입점에 함께 반영된다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  addSession('a', '이전 이름');
  await window.renameSession('a', '새 이름');
  assert.strictEqual(window.sessionDisplayName('a'), '새 이름');
  assert.strictEqual(window.document.querySelector('#voice-session-picker .vt-sp-name').textContent, '새 이름');
});

test('모바일 세션 피커 — 윗줄 세션 이름, 아랫줄 기기 · 위치(H1)', async () => {
  const { window, addSession } = await buildTerminalWindow();
  addSession('a', '첫 세션');
  addSession('b', '둘째 세션');
  const picker = window.document.getElementById('voice-session-picker');
  assert.strictEqual(picker.querySelector('.vt-sp-name').textContent, '둘째 세션');
  assert.strictEqual(picker.querySelector('.vt-sp-meta').textContent, '2/2', '기기 이름이 오기 전엔 위치만');

  // env-chip.js가 기기 이름을 늦게 알려도 다시 그린다.
  window.document.documentElement.dataset.vtMachine = 'MacBook Pro M1 Pro';
  window.document.dispatchEvent(new window.CustomEvent('vt:machine'));
  assert.strictEqual(picker.querySelector('.vt-sp-meta').textContent, 'MacBook Pro M1 Pro · 2/2');
});

test('모바일 세션 피커 — 세션 이름은 HTML로 해석하지 않는다', async () => {
  const { window, addSession } = await buildTerminalWindow();
  addSession('x', '<img src=x onerror=alert(1)>');
  const picker = window.document.getElementById('voice-session-picker');
  assert.strictEqual(picker.querySelector('img'), null);
  assert.strictEqual(picker.querySelector('.vt-sp-name').textContent, '<img src=x onerror=alert(1)>');
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

// ADR-45 — 부팅 중 예외가 세션을 만들던 결함. 부팅 도중 새로고침(fetch 중단)·터널
// 끊김만으로 서버에 요청하지 않은 셸이 생겼고, e2e 온보딩 테스트가 간헐적으로 깨졌다.
const BOOT_JS = path.join(__dirname, '../js/term/boot.js');

// 2026-10-03 — 부팅은 이제 서버의 tmux 목록을 읽지 않는다(저장된 레이아웃만
// 복원). 레이아웃 조회 실패는 복원 경로가 흡수하므로, 끝까지 올라오는 실패는
// #tmux= 핸드오프의 attach 요청이다 — 그걸로 실패를 만든다.
function failingTmuxFetch(url, opts) {
  if (String(url).includes('/api/tmux/')) return Promise.reject(new TypeError('Failed to fetch'));
  return fakeFetch(url, opts);
}

async function bootWithFailure({ leaving = false } = {}) {
  const built = await buildTerminalWindow({ fetchImpl: failingTmuxFetch });
  built.window.location.hash = '#tmux=dev';
  const { bootApp } = await importFresh(BOOT_JS, built.context, built.cache);
  if (leaving) built.window.dispatchEvent(new built.window.Event('pagehide'));
  const origError = built.window.console.error;
  built.window.console.error = () => {};
  try { await bootApp(); } finally { built.window.console.error = origError; }
  const posts = built.window.__fetches.filter(([url, opts]) =>
    /\/api\/sessions$/.test(String(url).split('?')[0]) && opts && opts.method === 'POST');
  return { ...built, posts };
}

test('bootApp — 부팅 중 요청이 실패해도 세션을 만들지 않고 온보딩을 띄운다', async () => {
  const { window, posts } = await bootWithFailure();
  assert.strictEqual(posts.length, 0, '부팅 실패가 서버에 셸을 띄우면 안 된다');
  assert.ok(window.document.getElementById('onboarding'), '세션을 만들 수 있는 온보딩이 떠야 한다');
});

test('bootApp — 떠나는 문서(pagehide)의 부팅 실패는 아무것도 하지 않는다', async () => {
  const { window, posts } = await bootWithFailure({ leaving: true });
  assert.strictEqual(posts.length, 0);
  assert.strictEqual(window.document.getElementById('onboarding'), null);
});

test('bootApp — 새 화면에서 저장된 tmux 뷰를 새 PTY id로 다시 연결한다', async () => {
  const snap = {
    v: 5, savedAt: 1, activeTab: 'view-a', closed: [],
    tabs: [
      { id: 'view-a', active: 'pane-a', tree: { t: 'split', id: 'split-a', dir: 'row', ratio: 0.5,
        a: { t: 'leaf', id: 'pane-a', session: { id: 'old-a', tmux: 'ta' } },
        b: { t: 'leaf', id: 'pane-b', session: { id: 'old-b', tmux: 'tb' } },
      } },
      { id: 'view-b', active: 'pane-c', tree: { t: 'leaf', id: 'pane-c', session: { id: 'old-c', tmux: 'tc' } } },
    ],
  };
  const attached = [];
  const fetchImpl = (url, opts) => {
    const u = String(url).split('?')[0];
    const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    if (u.endsWith('/api/workspace')) return ok({ ui: { layout: snap } });
    if (u.endsWith('/api/tmux/attach')) {
      const name = JSON.parse(opts.body).name;
      attached.push(name);
      return ok({ id: `new-${name}`, name, tmux_session: name });
    }
    return fakeFetch(url, opts);
  };
  const { window, cache, context, layoutStore } = await buildTerminalWindow({ fetchImpl });
  const { bootApp } = await importFresh(BOOT_JS, context, cache);
  await bootApp();
  assert.deepStrictEqual(attached, ['ta', 'tb', 'tc']);
  const tabs = layoutStore.getTabsWithTrees();
  assert.strictEqual(tabs[0].tree.a.session, 'new-ta');
  assert.strictEqual(tabs[0].tree.b.session, 'new-tb');
  assert.strictEqual(tabs[1].tree.session, 'new-tc');
  assert.strictEqual(window.activeId, 'new-ta');
});

test('bootApp — tmux 연결이 일시 실패해도 저장된 뷰를 지우지 않고 재시도한다', async () => {
  const snap = {
    v: 5, savedAt: 1, activeTab: 'view-a', closed: [],
    tabs: [{ id: 'view-a', active: 'pane-a', tree: {
      t: 'leaf', id: 'pane-a', session: { id: 'old-a', tmux: 'ta' },
    } }],
  };
  let attempts = 0;
  let prematureSaves = 0;
  const fetchImpl = (url, opts) => {
    const u = String(url).split('?')[0];
    const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    if (u.endsWith('/api/workspace') && opts?.method === 'PUT' && attempts < 3) prematureSaves++;
    if (u.endsWith('/api/workspace')) return ok({ ui: { layout: snap } });
    if (u.endsWith('/api/tmux/attach')) {
      attempts++;
      return attempts <= 2
        ? Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) })
        : ok({ id: 'new-ta', name: 'ta', tmux_session: 'ta' });
    }
    return fakeFetch(url, opts);
  };
  const { window, cache, context, layoutStore } = await buildTerminalWindow({ fetchImpl });
  const { bootApp } = await importFresh(BOOT_JS, context, cache);
  await bootApp();
  assert.strictEqual(attempts, 3);
  assert.strictEqual(prematureSaves, 0, '일시 실패 중 빈 칸을 저장하면 원래 tmux 연결을 잃는다');
  assert.strictEqual(layoutStore.getActiveTabId(), 'view-a');
  assert.strictEqual(layoutStore.getTree().session, 'new-ta');
  assert.strictEqual(window.activeId, 'new-ta');
});

test('bootApp — 새 환경에서 저장본 조회가 일시 실패하면 빈 뷰로 확정하지 않는다', async () => {
  const snap = { v: 5, savedAt: 1, activeTab: 'view-a', closed: [], tabs: [
    { id: 'view-a', active: 'pane-a', tree: { t: 'leaf', id: 'pane-a', session: { id: 'old-a', tmux: 'ta' } } },
  ] };
  let reads = 0;
  const fetchImpl = (url, opts) => {
    const u = String(url).split('?')[0];
    const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    if (u.endsWith('/api/workspace') && opts?.method !== 'PUT') {
      reads++;
      return reads <= 2
        ? Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) })
        : ok({ ui: { layout: snap } });
    }
    if (u.endsWith('/api/tmux/attach')) return ok({ id: 'new-ta', name: 'ta', tmux_session: 'ta' });
    return fakeFetch(url, opts);
  };
  const { window, cache, context, layoutStore } = await buildTerminalWindow({ fetchImpl });
  const { bootApp } = await importFresh(BOOT_JS, context, cache);
  await bootApp();
  assert.strictEqual(reads, 4, '설정 조회와 레이아웃 조회를 재시도한 뒤 복원해야 한다');
  assert.strictEqual(layoutStore.getActiveTabId(), 'view-a');
  assert.strictEqual(layoutStore.getTree().session, 'new-ta');
  assert.strictEqual(window.document.getElementById('onboarding'), null);
});

// ── 서버 재시작 복구(2026-10-06 폰 실측) ───────────────────────────────────
// 서버가 재시작되면 PTY(웹 세션 id)가 사라져 열려 있던 페이지의 WS가 4004를 받는다.
// 이걸 곧바로 "종료됨"으로 읽어 칸을 비우면 레이아웃이 tmux 이름 없이 저장돼 새로고침·새
// 탭이 전부 빈 칸이 된다. tmux 세션이 살아 있으면 같은 이름으로 다시 attach해 칸을 지킨다.
function recoverFetch({ alive = ['ta'], listFails = false } = {}) {
  return (url, opts) => {
    const u = String(url).split('?')[0];
    const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    if (u.endsWith('/api/tmux/sessions')) {
      return listFails ? Promise.reject(new TypeError('Failed to fetch')) : ok(alive.map((name) => ({ name })));
    }
    if (u.endsWith('/api/tmux/attach')) return ok({ id: 'new-a', name: 'ta', tmux_session: 'ta' });
    return fakeFetch(url, opts);
  };
}
const _flush = () => new Promise((r) => setTimeout(r, 20));

test('WS 4004 — tmux 세션이 살아 있으면 같은 칸에 다시 attach하고 칸을 비우지 않는다', async () => {
  const { window, addSession, layoutStore, cache, context } = await buildTerminalWindow({ fetchImpl: recoverFetch() });
  await importFresh(SESSION_ACTIONS_JS, context, cache);   // window.vtCloseSession 브리지
  addSession('a', 'A', { tmuxName: 'ta' });
  window.sessions.a.tmuxName = 'ta';
  const paneId = layoutStore.getActivePaneId();

  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();

  assert.strictEqual(window.sessions.a, undefined, '옛 PTY 레코드는 정리된다');
  assert.ok(window.sessions['new-a'], '같은 tmux 이름으로 새 PTY가 붙는다');
  assert.strictEqual(window.sessions['new-a'].tmuxName, 'ta');
  assert.strictEqual(layoutStore.getTree().session, 'new-a', '칸은 새 id를 가리킨다(빈 칸 아님)');
  assert.strictEqual(layoutStore.getActivePaneId(), paneId, '칸 자리는 그대로');
  assert.strictEqual(window.activeId, 'new-a');
});

test('WS 4004 — tmux 세션이 없으면(종료됨) 칸이 빈 칸이 된다', async () => {
  const { window, addSession, layoutStore, cache, context } = await buildTerminalWindow({ fetchImpl: recoverFetch({ alive: [] }) });
  await importFresh(SESSION_ACTIONS_JS, context, cache);
  addSession('a', 'A', { tmuxName: 'ta' });
  window.sessions.a.tmuxName = 'ta';

  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();

  assert.strictEqual(window.sessions.a, undefined);
  assert.strictEqual(window.sessions['new-a'], undefined, '죽은 세션을 다시 붙이지 않는다');
  assert.strictEqual(layoutStore.getTree().session, null, '종료된 세션의 칸은 빈 칸');
});

test('WS 4004 — tmux 목록을 못 받으면(서버 준비 중) 칸을 비우지 않고 재연결 백오프로 간다', async () => {
  const { window, addSession, layoutStore, cache, context } = await buildTerminalWindow({ fetchImpl: recoverFetch({ listFails: true }) });
  await importFresh(SESSION_ACTIONS_JS, context, cache);
  addSession('a', 'A', { tmuxName: 'ta' });
  window.sessions.a.tmuxName = 'ta';

  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();

  assert.ok(window.sessions.a, '확인 못 했으면 세션을 버리지 않는다');
  assert.strictEqual(layoutStore.getTree().session, 'a');
  assert.ok(window.sessions.a.reconnTimer, '일반 재연결 타이머가 걸린다');
  clearTimeout(window.sessions.a.reconnTimer);
});

test('WS 4004 — tmux가 아닌 일반 세션은 PTY가 곧 세션이라 그대로 칸을 비운다', async () => {
  const { window, addSession, layoutStore, cache, context } = await buildTerminalWindow({ fetchImpl: recoverFetch() });
  await importFresh(SESSION_ACTIONS_JS, context, cache);
  addSession('p', 'P');

  window.sessions.p.ws.onclose({ code: 4004 });
  await _flush();

  assert.strictEqual(window.sessions.p, undefined);
  assert.strictEqual(layoutStore.getTree().session, null);
});

test('replaceSessionId: 칸 자리·모양은 그대로, 세션 id만 바뀐다', async () => {
  const { addSession, layoutStore } = await buildTerminalWindow();
  addSession('a', 'A');
  layoutStore.splitActivePane('row', null);
  addSession('b', 'B', { place: 'pane' });
  const before = JSON.stringify(layoutStore.getTree());

  assert.strictEqual(layoutStore.replaceSessionId('b', 'X'), true);
  assert.strictEqual(JSON.stringify(layoutStore.getTree()), before.replace('"session":"b"', '"session":"X"'));
  assert.strictEqual(layoutStore.replaceSessionId('nope', 'Y'), false);
});

// ── 닫은 묶음 재열기(2026-10-06) — attach가 실패해도 기억이 사라지지 않는다 ─────────
function reopenFetch(deadTmux = []) {
  return (url, opts) => {
    const u = String(url).split('?')[0];
    if (u.endsWith('/api/tmux/attach')) {
      const name = JSON.parse(opts.body).name;
      if (deadTmux.includes(name)) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ id: `id-${name}`, name, tmux_session: name }) });
    }
    return fakeFetch(url, opts);
  };
}

async function rememberedGroup(deadTmux) {
  const built = await buildTerminalWindow({ fetchImpl: reopenFetch(deadTmux) });
  const { window, addSession, layoutStore, cache, context } = built;
  const actions = await importFresh(SESSION_ACTIONS_JS, context, cache);
  addSession('a', 'A', { tmuxName: 'ta' });
  window.sessions.a.tmuxName = 'ta';
  layoutStore.splitActivePane('row', null);
  addSession('b', 'B', { place: 'pane', tmuxName: 'tb' });
  await actions.closeView(layoutStore.getActiveTabId());
  const toasts = [];
  window.showToast = (m) => toasts.push(m);
  const view = layoutStore.findClosedViewByTmux('ta');
  assert.ok(view, '묶음이 기억된다');
  return { ...built, actions, view, toasts };
}

test('reopenClosedView — attach가 전부 실패하면 기억을 지우지 않고 알린다', async () => {
  const { window, actions, view, layoutStore, toasts } = await rememberedGroup(['ta', 'tb']);
  assert.strictEqual(await actions.reopenClosedView(view.id, 'ta'), false);
  assert.ok(layoutStore.findClosedViewByTmux('ta'), '기억이 남아 다시 시도할 수 있다');
  assert.ok(toasts.some((m) => /묶음을 열지 못했습니다/.test(m)));
  assert.strictEqual(Object.keys(window.sessions).length, 0);
});

test('reopenClosedView — 일부만 실패하면 열린 만큼 열고 실패한 칸은 빈 칸, 한 줄로 알린다', async () => {
  const { window, actions, view, layoutStore, toasts } = await rememberedGroup(['tb']);
  assert.strictEqual(await actions.reopenClosedView(view.id, 'ta'), true);
  assert.strictEqual(layoutStore.findClosedViewByTmux('ta'), null, '열렸으니 기억에서는 나간다');
  assert.ok(window.sessions['id-ta']);
  assert.strictEqual(layoutStore.countLeaves(), 2, '묶음 모양은 그대로(실패한 칸은 빈 칸)');
  assert.ok(toasts.some((m) => /1개 세션을 열지 못해 빈 칸/.test(m) && /tb/.test(m)));
});

test('reopenClosedView — 모두 성공하면 기억이 나가고 두 칸이 다 찬다', async () => {
  const { window, actions, view, layoutStore, toasts } = await rememberedGroup([]);
  assert.strictEqual(await actions.reopenClosedView(view.id, 'tb'), true);
  assert.strictEqual(layoutStore.getClosedViews().length, 0);
  assert.ok(window.sessions['id-ta'] && window.sessions['id-tb']);
  assert.strictEqual(toasts.length, 0);
});

test('로그인 폼이 떠 있으면 복원 요청과 빈 레이아웃 저장을 하지 않는다', async () => {
  const built = await buildTerminalWindow();
  built.window.__vtAuthed = false;
  const { bootApp } = await importFresh(BOOT_JS, built.context, built.cache);
  const boot = bootApp();
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(built.window.__fetches.length, 0);
  assert.strictEqual(built.window.document.getElementById('onboarding'), null);
  built.window.__vtAuthed = true;
  built.window.document.dispatchEvent(new built.window.Event('vt:authed'));
  await boot;
  assert.ok(built.window.__fetches.some(([url]) => String(url).includes('/api/workspace')));
});

test('묶음 복원 도중 401 — 원본 배치를 지키고 재인증 후 전체 묶음을 복원한다', async () => {
  const snap = { v: 5, savedAt: 1, activeTab: 'group', closed: [], tabs: [
    { id: 'group', label: '원래 묶음', active: 'a', tree: { t: 'split', id: 'split', dir: 'col', ratio: 0.35,
      a: { t: 'leaf', id: 'a', session: { id: 'old-a', tmux: 'ta' } },
      b: { t: 'leaf', id: 'b', session: { id: 'old-b', tmux: 'tb' } },
    } },
  ] };
  let expired = true;
  const fetchImpl = (url, opts) => {
    const u = String(url).split('?')[0];
    const ok = (body) => Promise.resolve({ ok: true, status: 200, json: async () => body });
    if (u.endsWith('/api/workspace')) return ok({ ui: { layout: snap } });
    if (u.endsWith('/api/tmux/attach')) {
      const name = JSON.parse(opts.body).name;
      if (expired && name === 'tb') return Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
      return ok({ id: 'new-' + name, name, tmux_session: name });
    }
    return fakeFetch(url, opts);
  };
  const built = await buildTerminalWindow({ fetchImpl });
  const { bootApp } = await importFresh(BOOT_JS, built.context, built.cache);
  const boot = bootApp();
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(built.window.__vtAuthed, false);
  assert.ok(!built.window.__fetches.some(([url, opts]) => String(url).includes('/api/workspace') && opts?.method === 'PUT'));
  assert.strictEqual(built.window.document.getElementById('onboarding'), null);
  expired = false;
  built.window.__vtAuthed = true;
  built.window.document.dispatchEvent(new built.window.Event('vt:authed'));
  await boot;
  const tab = built.layoutStore.getTabsWithTrees()[0];
  assert.strictEqual(tab.label, '원래 묶음');
  assert.strictEqual(tab.tree.ratio, 0.35);
  assert.strictEqual(tab.tree.a.session, 'new-ta');
  assert.strictEqual(tab.tree.b.session, 'new-tb');
});

test('열린 터미널 WS 인증 만료 — 로그인 요청을 표시하고 세션과 뷰를 유지한다', async () => {
  const { window, addSession, layoutStore } = await buildTerminalWindow();
  window.__vtAuthed = true;
  let prompts = 0;
  window.document.addEventListener('vt:auth-required', () => prompts++);
  addSession('a', 'ta', { tmuxName: 'ta' });
  const tab = layoutStore.getActiveTabId();
  window.sessions.a.ws.onclose({ code: 4001 });
  assert.strictEqual(window.__vtAuthed, false);
  assert.strictEqual(prompts, 1);
  assert.ok(window.sessions.a);
  assert.strictEqual(layoutStore.getActiveTabId(), tab);
  assert.strictEqual(layoutStore.getTree().session, 'a');
});

for (const status of [403, 404, 200]) test(`확인되지 않은 attach 응답 ${status} — 원래 세션 배치를 빈 칸으로 확정하지 않는다`, async () => {
  const built = await buildTerminalWindow({ fetchImpl: (url, opts) => String(url).includes('/api/tmux/attach')
    ? Promise.resolve({ ok: status === 200, status, json: async () => ({}) }) : fakeFetch(url, opts) });
  const { attachLayoutSessions } = await importFresh(path.join(__dirname, '../js/term/workspace.js'), built.context, built.cache);
  await assert.rejects(attachLayoutSessions([{ id: 'old', tmux: 'ta' }]));
});

test('확인된 tmux 종료만 복원에서 빠진다', async () => {
  const built = await buildTerminalWindow({ fetchImpl: (url, opts) => String(url).includes('/api/tmux/attach')
    ? Promise.resolve({ ok: false, status: 404, json: async () => ({ error: 'tmux session not found' }) }) : fakeFetch(url, opts) });
  const { attachLayoutSessions } = await importFresh(path.join(__dirname, '../js/term/workspace.js'), built.context, built.cache);
  assert.strictEqual(await attachLayoutSessions([{ id: 'old', tmux: 'ta' }]), 0);
});

// ── 세션이 끝나면 뷰도 정리한다(2026-11, 상단 뷰 리뉴얼) ────────────────────
// 4004 + 서버 tmux 목록에 없음 = 끝난 것이 확인된 경우. 그 세션이 뷰의 마지막 세션이면 뷰째 닫는다.
// 묶음에서 다른 세션이 남아 있으면 예전처럼 그 칸만 빈 칸으로 둔다.
async function endedFixture() {
  const t = await buildTerminalWindow({ fetchImpl: recoverFetch({ alive: [] }) });
  await importFresh(SESSION_ACTIONS_JS, t.context, t.cache);
  return t;
}
const tabIds = (S) => S.getTabs().map((x) => x.id);

test('세션 종료 — 단독 뷰는 다른 뷰가 있으면 뷰째 닫힌다', async () => {
  const { window, addSession, layoutStore } = await endedFixture();
  addSession('a', 'A', { tmuxName: 'ta' }); window.sessions.a.tmuxName = 'ta';
  addSession('b', 'B', { tmuxName: 'tb' }); window.sessions.b.tmuxName = 'tb';
  assert.strictEqual(layoutStore.getTabs().length, 2, '세션마다 단독 뷰');
  const before = tabIds(layoutStore);

  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();

  assert.strictEqual(layoutStore.getTabs().length, 1, '끝난 세션의 뷰가 사라진다');
  assert.ok(!tabIds(layoutStore).includes(before[0]));
  assert.strictEqual(layoutStore.getTree().session, 'b', '남은 뷰는 그대로');
});

test('세션 종료 — 마지막 뷰면 빈 뷰 하나(온보딩)로 돌아간다', async () => {
  const { window, addSession, layoutStore } = await endedFixture();
  addSession('a', 'A', { tmuxName: 'ta' }); window.sessions.a.tmuxName = 'ta';
  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();
  assert.strictEqual(layoutStore.getTabs().length, 1);
  assert.strictEqual(layoutStore.getTree().session, null);
  assert.ok(layoutStore.isLayoutEmpty());
});

test('세션 종료 — 묶음에 다른 세션이 남아 있으면 그 칸만 빈 칸으로 남고 뷰는 유지된다', async () => {
  const { window, addSession, layoutStore } = await endedFixture();
  addSession('a', 'A', { tmuxName: 'ta' }); window.sessions.a.tmuxName = 'ta';
  addSession('b', 'B', { place: 'none', tmuxName: 'tb' }); window.sessions.b.tmuxName = 'tb';
  layoutStore.splitActivePane('row', 'b');                 // 같은 뷰 안에 a | b
  assert.strictEqual(layoutStore.getTabs().length, 1);

  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();

  assert.strictEqual(layoutStore.getTabs().length, 1, '묶음 뷰는 남는다');
  assert.strictEqual(layoutStore.countLeaves(), 2, '칸도 없애지 않는다');
  assert.ok(window.sessions.b, '다른 세션은 그대로');

  // 이어서 마지막 세션도 끝나면 뷰가 정리된다 — 빈 칸만 남은 묶음을 남기지 않는다.
  window.sessions.b.ws.onclose({ code: 4004 });
  await _flush();
  assert.strictEqual(layoutStore.countLeaves(), 1, '빈 칸 두 개짜리 뷰가 아니라 빈 뷰 하나');
  assert.ok(layoutStore.isLayoutEmpty());
});

test('세션 종료 — 뷰어 칸이 있는 뷰는 닫지 않는다', async () => {
  const { window, addSession, layoutStore } = await endedFixture();
  addSession('a', 'A', { tmuxName: 'ta' }); window.sessions.a.tmuxName = 'ta';
  addSession('b', 'B', { tmuxName: 'tb' }); window.sessions.b.tmuxName = 'tb';
  layoutStore.switchLayoutTab(layoutStore.getTabs()[0].id);
  layoutStore.openViewerPane('/tmp/x.md');          // a 칸을 쪼개 옆에 파일 뷰어를 붙인다
  const tabs = layoutStore.getTabs().length;

  window.sessions.a.ws.onclose({ code: 4004 });
  await _flush();

  assert.strictEqual(layoutStore.getTabs().length, tabs, '파일을 보던 뷰는 세션이 끝나도 유지된다');
});

test('뷰어 칸을 닫으면 그 칸의 편집기를 destroy한다(창 수신기가 다음 편집기 때까지 남지 않게)', async () => {
  const { window, addSession, layoutStore } = await buildTerminalWindow();
  addSession('a', 'A');
  layoutStore.openViewerPane('/tmp/x.md');
  await _flush();
  const pane = window.document.querySelector('.vt-pane-viewer .vt-vw-code-pane');
  assert.ok(pane, '뷰어 칸이 그려진다');
  let destroyed = 0;
  pane._vtEditor = { view: { dom: pane }, destroy: () => { destroyed += 1; } };
  const leaf = layoutStore.getTree().t === 'split' ? layoutStore.getTree().b : null;
  assert.ok(leaf && leaf.kind === 'viewer');
  layoutStore.closePane(leaf.id);
  await _flush();
  assert.strictEqual(window.document.querySelector('.vt-pane-viewer'), null);
  assert.strictEqual(destroyed, 1);
});
