// term/keybar.js 검증 — 구성, 누름 판정, 수식키, 선택 모드, 키보드 겹침.
// quickopen.test.js/rail.test.js와 같은 하네스(vm-esm + 진짜 index.html
// 마크업)를 쓴다. keybar.js는 모듈 평가 시점에 즉시 initKeybar()를 부르므로
// (파일 하단 관행), window.matchMedia를 먼저 심어둔 뒤 임포트해야 한다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const KEYSEQ_JS = path.join(__dirname, '../js/lib/keyseq.js');
const DOM_JS = path.join(__dirname, '../js/core/dom.js');
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const KEYBAR_JS = path.join(__dirname, '../js/term/keybar.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

class FakeWebSocket {}
FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSING = 2; FakeWebSocket.CLOSED = 3;

function _stubMatchMedia(window, { coarse, compact = false }) {
  window.matchMedia = (query) => ({
    // 폭 문턱(max-width:719px)은 포인터와 따로 본다 — 폰 폭인지는 compact가 정한다.
    matches: query.includes('max-width') ? compact : query.includes('coarse') ? coarse : !coarse,
    media: query,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {},
  });
}

// jsdom에는 visualViewport가 없다. keybar.js는 모듈 평가 시점에 리스너를 달므로
// 임포트 "전에" 심어야 한다 — 그래서 buildWindow의 옵션으로 받는다.
function _fakeVisualViewport(window, { height, offsetTop = 0 }) {
  const listeners = new Map();
  const vv = {
    height, offsetTop,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    // 테스트에서 키보드가 뜨고 지는 것을 흉내내는 헬퍼
    _emit(type) { for (const fn of listeners.get(type) || []) fn(); },
  };
  window.visualViewport = vv;
  return vv;
}

async function buildWindow({ coarse = false, compact = false, search = '', visualViewport = null } = {}) {
  const env = createDomEnv(INDEX_HTML, { url: `http://localhost/${search}` });
  _doms.push(env.dom);
  const { window } = env;
  window.WebSocket = FakeWebSocket;
  _stubMatchMedia(window, { coarse, compact });
  const vv = visualViewport ? _fakeVisualViewport(window, visualViewport) : null;

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  const domNs = await importFresh(DOM_JS, env.context, cache);
  const store = await importFresh(CORE_STORE_JS, env.context, cache);
  const keybarNs = await importFresh(KEYBAR_JS, env.context, cache);
  await keybarNs.initKeybar();
  const keyboard = await importFresh(path.join(__dirname, '../js/term/keyboard-input.js'), env.context, cache);

  return { window, dom: domNs, store, vv, keyboard, ...keybarNs };
}

test('데스크톱(포인터 정밀) — keybar는 hidden 그대로, 마이크는 숨은 대기 자리(#vt-mic-park)에 남는다', async () => {
  const { window } = await buildWindow({ coarse: false });
  assert.strictEqual(window.document.getElementById('keybar').hidden, true);
  assert.strictEqual(
    window.document.getElementById('mic-btn-wrap').parentElement.id,
    'vt-mic-park'
  );
});

test('터치(포인터 굵음) — keybar가 보이고, 마이크는 키바로 오지 않는다', async () => {
  const { window } = await buildWindow({ coarse: true });
  assert.strictEqual(window.document.getElementById('keybar').hidden, false);
  assert.strictEqual(window.document.getElementById('keybar-slot-mic'), null);
  assert.strictEqual(
    window.document.getElementById('mic-btn-wrap').parentElement.id,
    'vt-mic-park'
  );
});

test('강제 노출(?keybar=1)이어도 마이크 배치는 포인터 타입만 본다', async () => {
  const { window } = await buildWindow({ coarse: false, search: '?keybar=1' });
  assert.strictEqual(window.document.getElementById('keybar').hidden, false, '강제 노출은 그대로 보여야 한다');
  assert.strictEqual(window.document.getElementById('keybar').classList.contains('force-show'), true);
  assert.strictEqual(
    window.document.getElementById('mic-btn-wrap').parentElement.id,
    'vt-mic-park',
    '마이크는 여전히 데스크톱 자리에 있어야 한다(강제 노출과 무관)'
  );
});

// 리디자인 8단계 — 폰 폭에는 활동 막대가 보이지 않아 마이크 자리가 없었다(알려진 구멍).
test('폰 폭 — 마이크는 떠 있는 자리(#vt-mic-fab)로, 집중 모드에서는 키바 첫 키로 옮겼다가 돌아온다', async () => {
  const { window, placeMicButton } = await buildWindow({ coarse: true, compact: true });
  const doc = window.document;
  const mic = doc.getElementById('mic-btn-wrap');
  assert.strictEqual(mic.parentElement.id, 'vt-mic-fab');
  assert.strictEqual(mic.dataset.action, 'voice.record', '녹음 토글 액션·id는 그대로(voice.js 계약)');
  doc.documentElement.classList.add('vt-focus');
  doc.dispatchEvent(new window.CustomEvent('vt:focus-change', { detail: { on: true } }));
  assert.strictEqual(mic.parentElement.id, 'keybar-mic-slot');
  assert.strictEqual(doc.getElementById('keybar-mods').firstElementChild.id, 'keybar-mic-slot', '키바 첫 키 자리');
  doc.documentElement.classList.remove('vt-focus');
  doc.dispatchEvent(new window.CustomEvent('vt:focus-change', { detail: { on: false } }));
  assert.strictEqual(mic.parentElement.id, 'vt-mic-fab');
  // 음성 전용 모드가 #topbar로 데려간 동안은 건드리지 않는다.
  doc.getElementById('topbar').appendChild(mic);
  doc.body.classList.add('voice-only-mode');
  placeMicButton();
  assert.strictEqual(mic.parentElement.id, 'topbar');
});

test('폰 집중 모드의 키바 마이크 — 누름에서 기본 동작을 막아 터미널 포커스(소프트 키보드)를 지킨다', async () => {
  const { window } = await buildWindow({ coarse: true, compact: true });
  const doc = window.document;
  doc.documentElement.classList.add('vt-focus');
  doc.dispatchEvent(new window.CustomEvent('vt:focus-change'));
  const ev = new window.PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 3 });
  doc.querySelector('#keybar-mic-slot #mic-btn-wrap').dispatchEvent(ev);
  assert.strictEqual(ev.defaultPrevented, true);
});

test('폰 CSS — 떠 있는 마이크 56은 터미널 탭에서만, 아래 탭 + 키바 위 16에 뜬다(98-mobile.css)', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../styles/screens/98-mobile.css'), 'utf8');
  assert.match(css, /#vt-mic-fab \{[^}]*display:none;[^}]*position:fixed/);
  assert.match(css, /bottom:calc\(var\(--m-nav-h\) \+ var\(--kb-height, 0px\) \+ var\(--kb-overlap, 0px\) \+ 16px\)/);
  assert.match(css, /html\[data-mobile-tab="terminal"\] #vt-mic-fab:not\(:empty\) \{ display:block; \}/);
  assert.match(css, /#vt-mic-fab #mic-btn-wrap\.mic \{[^}]*width:56px; height:56px;/);
  assert.match(css, /html\.vt-focus #keybar\.closed:not\(\[hidden\]\) \{ display:flex; \}/, '집중 모드에서는 닫힌 키바도 보인다');
});

// 2026-09-23 — 큐·업로드 슬롯을 없앴다(하단 내비 「첨부」·빠른 열기 `:`가
// 이미 같은 액션에 닿는다). 남은 건 마이크뿐이다.
test('큐·업로드 슬롯이 더 이상 없다', async () => {
  const { window } = await buildWindow({ coarse: true });
  assert.strictEqual(window.document.getElementById('keybar-slot-queue'), null);
  assert.strictEqual(window.document.getElementById('keybar-slot-upload'), null);
});

test('구성 — 수식키 고정 줄 | 스크롤 키 줄 | 방향키, 마이크·접기 토글·문자 키는 없다', async () => {
  const { window } = await buildWindow({ coarse: true });
  const doc = window.document;
  const main = doc.getElementById('keybar-main');
  assert.deepStrictEqual([...main.children].map((c) => c.id), ['keybar-mods', 'keybar-scroll', 'keybar-arrows']);
  assert.deepStrictEqual([...doc.querySelectorAll('#keybar-mods .kb')].map((b) => b.dataset.mod), ['ctrl', 'alt', 'shift']);
  const keys = [...doc.querySelectorAll('#keybar-scroll .kb')].map((b) => b.dataset.key);
  assert.deepStrictEqual(keys, [
    'esc', 'tab', 'backtab', 'ctrlc', 'home', 'end', 'pgup', 'pgdn', 'del', 'prefix',
    'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12',
  ]);
  const arrows = [...doc.querySelectorAll('#keybar-arrows .kb')].map((b) => b.dataset.key);
  assert.deepStrictEqual(arrows, ['up', 'left', 'down', 'right']);
  assert.strictEqual(doc.getElementById('keybar-toggle'), null);
  assert.strictEqual(doc.getElementById('keybar-extra-keys'), null);
  assert.strictEqual(doc.querySelectorAll('#keybar [data-seq]').length, 0, '소프트 키보드에 있는 문자 키는 두지 않는다');
});

// --- 누름 판정 · 수식키 ---------------------------------------------------
// sendToPty를 가로채려고 활성 세션에 열린 가짜 WebSocket을 단다.
async function buildWired() {
  const env = await buildWindow({ coarse: true });
  const sent = [];
  const decoder = new TextDecoder();
  env.store.registerSession('s1', {
    term: { focus() {}, blur() {}, getSelection() { return ''; }, clearSelection() {}, input(data) { sent.push(data); }, attachCustomKeyEventHandler() {} },
    wsHandle: { readyState: 1, send(data) { sent.push(typeof data === 'string' ? data : decoder.decode(data)); } },
  });
  env.keyboard.wireKeyboardInput('s1', env.store.getSession('s1').term, env.window.document.getElementById('keybar'));
  env.store.setActive('s1');
  const doc = env.window.document;
  const btn = (sel) => doc.querySelector(sel);
  const fire = (el, type, id, x = 10, y = 10) => el.dispatchEvent(
    new env.window.PointerEvent(type, { bubbles: true, cancelable: true, pointerId: id, clientX: x, clientY: y }));
  const tap = (el, id = 1) => { fire(el, 'pointerdown', id); fire(el, 'pointerup', id); };
  return { ...env, sent, btn, fire, tap };
}

test('스크롤 줄 키 — 누르는 순간이 아니라 손을 뗄 때 보낸다', async () => {
  const { sent, btn, fire } = await buildWired();
  const home = btn('#keybar [data-key="home"]');
  fire(home, 'pointerdown', 1);
  assert.deepStrictEqual(sent, []);
  fire(home, 'pointerup', 1);
  assert.deepStrictEqual(sent, ['\x1b[H']);
});

test('스크롤 줄 키 — 밀면(8px 초과) 보내지 않는다, 스크롤 시작(pointercancel)도 마찬가지', async () => {
  const { sent, btn, fire } = await buildWired();
  const end = btn('#keybar [data-key="end"]');
  fire(end, 'pointerdown', 1, 10, 10);
  fire(end, 'pointermove', 1, 30, 10);
  fire(end, 'pointerup', 1, 30, 10);
  fire(end, 'pointerdown', 2);
  fire(end, 'pointercancel', 2);
  fire(end, 'pointerup', 2);
  assert.deepStrictEqual(sent, []);
});

test('방향키 — 누르는 즉시 보낸다', async () => {
  const { sent, btn, fire } = await buildWired();
  fire(btn('#keybar-arrows [data-key="up"]'), 'pointerdown', 1);
  assert.deepStrictEqual(sent, ['\x1b[A']);
});

test('수식키 탭 — 다음 한 키에만 붙고 풀린다', async () => {
  const { sent, btn, tap, applyStickyMod } = await buildWired();
  const ctrl = btn('#keybar [data-mod="ctrl"]');
  tap(ctrl);
  assert.strictEqual(ctrl.getAttribute('aria-pressed'), 'true');
  tap(btn('#keybar [data-key="del"]'));
  tap(btn('#keybar [data-key="del"]'));
  assert.deepStrictEqual(sent, ['\x1b[3;5~', '\x1b[3~']);
  assert.strictEqual(ctrl.getAttribute('aria-pressed'), 'false');
  tap(ctrl);
  assert.strictEqual(applyStickyMod('c'), '\x03', '소프트 키보드 입력에도 붙는다');
  assert.strictEqual(applyStickyMod('c'), 'c');
});

test('수식키 두 번 탭 — 고정, 세 번째 탭에 풀린다', async () => {
  const { sent, btn, tap, fire } = await buildWired();
  const shift = btn('#keybar [data-mod="shift"]');
  tap(shift); tap(shift);
  assert.ok(shift.classList.contains('locked'));
  fire(btn('#keybar-arrows [data-key="left"]'), 'pointerdown', 5);
  fire(btn('#keybar-arrows [data-key="left"]'), 'pointerup', 5);
  fire(btn('#keybar-arrows [data-key="left"]'), 'pointerdown', 6);
  assert.deepStrictEqual(sent, ['\x1b[1;2D', '\x1b[1;2D']);
  tap(shift);
  assert.ok(!shift.classList.contains('locked'));
  assert.strictEqual(shift.getAttribute('aria-pressed'), 'false');
});

test('수식키 누르고 있기 — 다른 손가락 키와 동시 입력, 떼면 꺼지고 탭으로 치지 않는다', async () => {
  const { sent, btn, fire, tap, applyStickyMod } = await buildWired();
  const ctrl = btn('#keybar [data-mod="ctrl"]');
  const alt = btn('#keybar [data-mod="alt"]');
  fire(ctrl, 'pointerdown', 1);
  fire(alt, 'pointerdown', 2);
  fire(btn('#keybar-arrows [data-key="right"]'), 'pointerdown', 3);
  assert.strictEqual(applyStickyMod('x'), '\x1b\x18', '누르는 동안 소프트 키보드에도 Ctrl+Alt');
  fire(ctrl, 'pointerup', 1);
  fire(alt, 'pointerup', 2);
  assert.deepStrictEqual(sent, ['\x1b[1;7C']);
  assert.strictEqual(ctrl.getAttribute('aria-pressed'), 'false', '조합에 쓰였으니 떼도 한 번용으로 남지 않는다');
  assert.strictEqual(alt.getAttribute('aria-pressed'), 'false');
  tap(btn('#keybar [data-key="home"]'), 9);
  assert.strictEqual(sent.at(-1), '\x1b[H');
});

test('수식키 — 누른 손가락이 조금 흘러도 누름이 유지된다(스크롤 줄 밖이라)', async () => {
  const { sent, btn, fire } = await buildWired();
  const ctrl = btn('#keybar [data-mod="ctrl"]');
  fire(ctrl, 'pointerdown', 1, 10, 10);
  fire(ctrl, 'pointermove', 1, 40, 10);
  assert.strictEqual(ctrl.getAttribute('aria-pressed'), 'true');
  fire(btn('#keybar-arrows [data-key="up"]'), 'pointerdown', 2);
  fire(ctrl, 'pointerup', 1, 40, 10);
  assert.deepStrictEqual(sent, ['\x1b[1;5A']);
  assert.strictEqual(ctrl.getAttribute('aria-pressed'), 'false');
});

test('수식키 — 브라우저가 누름을 취소하면(pointercancel) 꺼진다', async () => {
  const { btn, fire } = await buildWired();
  const ctrl = btn('#keybar [data-mod="ctrl"]');
  fire(ctrl, 'pointerdown', 1);
  fire(ctrl, 'pointercancel', 1);
  assert.strictEqual(ctrl.getAttribute('aria-pressed'), 'false');
});

test('텍스트 선택 모드 — 터미널 포커스를 해제하고 xterm 자신의 선택 하이라이트를 켠다', async () => {
  const { window, store, setTerminalSelectionMode, isTerminalSelectionMode } = await buildWindow({ coarse: true });
  let blurred = 0;
  let cleared = 0;
  store.registerSession('select-me', {
    term: { blur() { blurred++; }, getSelection() { return ''; }, clearSelection() { cleared++; } },
  });
  store.setActive('select-me');
  const wrapper = window.document.createElement('div');
  wrapper.id = 'term-select-me';
  wrapper.className = 'vt-term-wrap';
  const helper = window.document.createElement('textarea');
  helper.className = 'xterm-helper-textarea';
  wrapper.appendChild(helper);
  window.document.body.appendChild(wrapper);

  setTerminalSelectionMode(true);
  assert.strictEqual(isTerminalSelectionMode(), true);
  assert.strictEqual(blurred, 1, '숨은 xterm textarea의 포커스를 해제해야 키보드가 닫힌다');
  assert.ok(wrapper.classList.contains('vt-term-select-mode'));
  assert.strictEqual(helper.disabled, true, 'xterm이 롱프레스 때 숨은 입력을 다시 focus하지 못해야 한다');
  assert.ok(window.document.body.classList.contains('vt-term-selecting'));
  assert.strictEqual(window.document.getElementById('keybar-selection-actions').hidden, false);
  // 2026-09-23 — 선택 모드에 "들어가는" keybar 버튼(#keybar-slot-select)은
  // 없앴다(term/touch-select.js의 꾹 누르기가 대신한다) — 더는 존재하지
  // 않아야 한다.
  assert.strictEqual(window.document.getElementById('keybar-slot-select'), null);

  setTerminalSelectionMode(false);
  assert.strictEqual(isTerminalSelectionMode(), false);
  assert.ok(!wrapper.classList.contains('vt-term-select-mode'));
  assert.strictEqual(helper.disabled, false, '선택을 끝내면 터미널 입력을 다시 받을 수 있어야 한다');
  assert.strictEqual(window.document.getElementById('keybar-selection-actions').hidden, true);
  assert.strictEqual(cleared, 1, '끌 때 xterm 자신의 선택 하이라이트도 지워야 다음번에 이전 범위가 겹쳐 보이지 않는다');
});

test('텍스트 선택 복사 — xterm 자신의 선택(term.getSelection)만 읽는다', async () => {
  // 2026-09-23 — 예전엔 브라우저 네이티브 Range(window.getSelection())를
  // 먼저 읽었다. 이제 선택은 전부 xterm의 select() 모델로만 만들어지므로
  // (term/touch-select.js 머리말 — 접근성 레이어 재렌더가 네이티브 Range를
  // 끊는다), window.getSelection이 뭘 돌려주든 무시하고 term.getSelection()만
  // 봐야 한다.
  const { window, store, setTerminalSelectionMode, isTerminalSelectionMode } = await buildWindow({ coarse: true });
  store.registerSession('copy-me', {
    term: { blur() {}, getSelection() { return '선택한 범위'; }, clearSelection() {} },
  });
  store.setActive('copy-me');
  const wrapper = window.document.createElement('div');
  wrapper.id = 'term-copy-me';
  window.document.body.appendChild(wrapper);
  // 네이티브 Range가 뭔가를 들고 있어도(예: 다른 위젯이 남긴 찌꺼기) 무시돼야 한다.
  window.getSelection = () => ({ toString: () => '이건 무시돼야 한다', removeAllRanges() {} });
  window.showToast = () => {};
  let copied = 0;
  window.document.execCommand = (command) => { if (command === 'copy') copied++; return true; };

  setTerminalSelectionMode(true);
  window.document.getElementById('keybar-selection-copy')
    .dispatchEvent(new window.PointerEvent('pointerdown', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.strictEqual(copied, 1);
  assert.strictEqual(isTerminalSelectionMode(), false, '복사 성공 뒤에는 입력 가능한 상태로 돌아가야 한다');
});

test('모두 선택 — 뷰포트 전체(rows×cols)를 term.select로 고른다, 스크롤백은 포함하지 않는다', async () => {
  const { window, store, setTerminalSelectionMode } = await buildWindow({ coarse: true });
  const calls = [];
  store.registerSession('all-me', {
    term: {
      rows: 24, cols: 80,
      blur() {}, getSelection() { return ''; }, clearSelection() {},
      select(col, row, length) { calls.push([col, row, length]); },
    },
  });
  store.setActive('all-me');
  const wrapper = window.document.createElement('div');
  wrapper.id = 'term-all-me';
  window.document.body.appendChild(wrapper);

  setTerminalSelectionMode(true);
  window.document.getElementById('keybar-selection-all')
    .dispatchEvent(new window.PointerEvent('pointerdown', { bubbles: true }));

  assert.deepStrictEqual(calls, [[0, 0, 24 * 80]]);
});

test('여닫기 — 기본은 닫힘, 헤더 버튼으로 열고 닫는다, 닫으면 켜진 수식키도 풀린다', async () => {
  const { window, btn, tap } = await buildWired();
  const doc = window.document;
  const bar = doc.getElementById('keybar');
  const openBtn = doc.getElementById('keybar-open-btn');
  assert.ok(doc.documentElement.classList.contains('vt-has-keybar'));
  assert.ok(bar.classList.contains('closed'));
  assert.strictEqual(openBtn.getAttribute('aria-pressed'), 'false');
  openBtn.click();
  assert.ok(!bar.classList.contains('closed'));
  assert.strictEqual(openBtn.getAttribute('aria-pressed'), 'true');
  tap(btn('#keybar [data-mod="ctrl"]'));
  openBtn.click();
  assert.ok(bar.classList.contains('closed'));
  assert.strictEqual(btn('#keybar [data-mod="ctrl"]').getAttribute('aria-pressed'), 'false');
});

test('데스크톱 — 키바가 없으면 여닫기 버튼도 드러낼 표시가 없다', async () => {
  const { window } = await buildWindow({ coarse: false });
  assert.ok(!window.document.documentElement.classList.contains('vt-has-keybar'));
});

// --- 소프트 키보드 겹침 → 터미널 하단 여백 -------------------------------
// 회귀 대상: keybar는 transform으로 키보드 위에 떠오르는데 #terminal-container의
// 하단 여백은 상수(--spacing*24)였다. 그래서 키보드가 올라오면 터미널 마지막
// 줄(프롬프트)이 키보드 뒤에 깔렸고, 증상은 "액세서리 행이 터미널을 가린다"로
// 보고됐다. 이제 positionBar()가 실측 두 값을 :root 변수로 내보내고 CSS가
// 그걸 더해서 여백을 만든다.
function _stubHeight(el, px) {
  Object.defineProperty(el, 'offsetHeight', { value: px, configurable: true });
}

test('키보드가 없을 때 — kb-height는 바의 실측 높이, kb-overlap은 0', async () => {
  const { window, vv } = await buildWindow({
    coarse: true, visualViewport: { height: window_innerHeight() },
  });
  const bar = window.document.getElementById('keybar');
  _stubHeight(bar, 96);
  vv.height = window.innerHeight;   // 키보드 없음
  vv._emit('resize');

  const root = window.document.documentElement;
  assert.strictEqual(root.style.getPropertyValue('--kb-height'), '96px');
  assert.strictEqual(root.style.getPropertyValue('--kb-overlap'), '0px');
  assert.strictEqual(bar.style.transform, '');
  assert.strictEqual(window.document.body.classList.contains('vt-keyboard-open'), false);
});

test('키보드가 올라오면 — kb-overlap에 키보드 높이가 실리고 바도 그만큼 떠오른다', async () => {
  const { window, vv } = await buildWindow({
    coarse: true, visualViewport: { height: window_innerHeight() },
  });
  const bar = window.document.getElementById('keybar');
  _stubHeight(bar, 96);
  const kbHeight = 300;
  vv.height = window.innerHeight - kbHeight;
  vv._emit('resize');

  const root = window.document.documentElement;
  assert.strictEqual(root.style.getPropertyValue('--kb-overlap'), `${kbHeight}px`);
  assert.strictEqual(bar.style.transform, `translateY(-${kbHeight}px)`);
  assert.strictEqual(window.document.body.classList.contains('vt-keyboard-open'), true,
    '키보드가 열린 동안 하단 내비를 숨길 상태를 표시해야 한다');
  // 여백 = 바 높이 + 키보드 높이. 둘 중 하나라도 빠지면 프롬프트가 가려진다.
  assert.strictEqual(root.style.getPropertyValue('--kb-height'), '96px');
});

test('iOS 대응 — resize 없이 scroll만 와도 겹침이 갱신된다', async () => {
  const { window, vv } = await buildWindow({
    coarse: true, visualViewport: { height: window_innerHeight() },
  });
  _stubHeight(window.document.getElementById('keybar'), 96);
  vv.height = window.innerHeight - 260;
  vv._emit('scroll');   // iOS는 키보드가 뜰 때 resize를 안 쏘는 경우가 있다
  assert.strictEqual(
    window.document.documentElement.style.getPropertyValue('--kb-overlap'), '260px');
});

// --- 포커스 모드(가로 + 소프트 키보드로 보이는 높이가 작을 때) -------------------
test('보이는 높이가 300px 미만이면 포커스 모드, 키보드가 내려가 높이가 돌아오면 복귀한다', async () => {
  const { window, vv, FOCUS_MAX_VISIBLE_H } = await buildWindow({
    coarse: true, visualViewport: { height: 400 },
  });
  assert.strictEqual(FOCUS_MAX_VISIBLE_H, 300);
  const body = window.document.body;
  const focus = () => body.classList.contains('vt-kb-focus');
  _stubHeight(window.document.getElementById('keybar'), 96);
  window.innerHeight = 400;             // 가로 폰
  vv.height = 400; vv._emit('resize');
  assert.strictEqual(focus(), false, '키보드 없음 — 평소 화면');
  vv.height = 330; vv._emit('resize');
  assert.strictEqual(focus(), false, '키보드가 떠도 300px 이상이면 그대로');
  assert.strictEqual(body.classList.contains('vt-keyboard-open'), true);
  vv.height = 200; vv._emit('resize');
  assert.strictEqual(focus(), true, '200px — 포커스 모드');
  vv.height = 299; vv._emit('resize');
  assert.strictEqual(focus(), true);
  vv.height = 300; vv._emit('resize');
  assert.strictEqual(focus(), false, '경계: 300px 는 평소 화면');
  vv.height = 200; vv._emit('resize');
  assert.strictEqual(focus(), true);
  vv.height = 400; vv._emit('resize');
  assert.strictEqual(focus(), false, '키보드가 내려가면 복귀');
  assert.strictEqual(body.classList.contains('vt-keyboard-open'), false);
});

test('포커스 모드 — iOS 처럼 scroll 만 와도 갱신되고, 세로 폰(키보드 올라와도 큼)은 안 켜진다', async () => {
  const { window, vv } = await buildWindow({ coarse: true, visualViewport: { height: 800 } });
  _stubHeight(window.document.getElementById('keybar'), 96);
  window.innerHeight = 800;
  vv.height = 480; vv._emit('scroll');   // 세로 폰 + 키보드
  assert.strictEqual(window.document.body.classList.contains('vt-kb-focus'), false);
  window.innerHeight = 400;
  vv.height = 190; vv._emit('scroll');
  assert.strictEqual(window.document.body.classList.contains('vt-kb-focus'), true);
});

test('포커스 모드 — 데스크톱(키바 없음)에서는 켜지지 않는다', async () => {
  const { window } = await buildWindow({ coarse: false, visualViewport: { height: 200 } });
  window.visualViewport._emit('resize');
  assert.strictEqual(window.document.body.classList.contains('vt-kb-focus'), false);
});

test('포커스 모드 CSS — 탭 바·칸 헤더·레일을 숨기고 레일/dock 의 body padding 을 푼다, keybar 는 남긴다', () => {
  const css = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', '..', 'styles/screens/20-overlay.css'), 'utf8');
  const m = css.match(/body\.vt-kb-focus #topbar,([^{]*)\{\s*display:\s*none;\s*\}/);
  assert.ok(m, '숨김 규칙이 있다');
  const sel = `#topbar,${m[1]}`;
  for (const part of ['#topbar', '#vt-wgrail', '#vt-workbench', '.vt-pane-head']) assert.ok(sel.includes(part), part);
  assert.ok(!sel.includes('#keybar') && !sel.includes('terminal-container'), '입력 수단과 터미널은 남긴다');
  assert.match(css, /body\.vt-kb-focus\s*\{[^}]*padding-left:[^;]*!important[^}]*padding-right:[^;]*!important/);
});

// jsdom 기본 innerHeight(768)를 buildWindow 호출 시점에 알 수 없어서 쓰는 상수.
// createDomEnv가 바꾸지 않는 값이라 안전하다 — 어긋나면 위 테스트가 바로 깨진다.
function window_innerHeight() { return 768; }
