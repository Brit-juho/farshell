// L7 — term/keybar.js(입력 슬롯 [🎤][큐][📎] + 접기 구조 변경) 검증.
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

function _stubMatchMedia(window, { coarse }) {
  window.matchMedia = (query) => ({
    matches: query.includes('coarse') ? coarse : !coarse,
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

async function buildWindow({ coarse = false, search = '', visualViewport = null } = {}) {
  const env = createDomEnv(INDEX_HTML, { url: `http://localhost/${search}` });
  _doms.push(env.dom);
  const { window } = env;
  window.WebSocket = FakeWebSocket;
  _stubMatchMedia(window, { coarse });
  const vv = visualViewport ? _fakeVisualViewport(window, visualViewport) : null;

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  const domNs = await importFresh(DOM_JS, env.context, cache);
  const store = await importFresh(CORE_STORE_JS, env.context, cache);
  const keybarNs = await importFresh(KEYBAR_JS, env.context, cache);

  return { window, dom: domNs, store, vv, ...keybarNs };
}

test('데스크톱(포인터 정밀) — keybar는 hidden 그대로, 마이크는 rail 설정 슬롯에 남는다', async () => {
  const { window } = await buildWindow({ coarse: false });
  assert.strictEqual(window.document.getElementById('keybar').hidden, true);
  assert.strictEqual(
    window.document.getElementById('mic-btn-wrap').parentElement.id,
    'vt-rail-mic-slot'
  );
});

test('터치(포인터 굵음) — keybar가 보이고, 마이크가 keybar 슬롯으로 옮겨간다', async () => {
  const { window } = await buildWindow({ coarse: true });
  assert.strictEqual(window.document.getElementById('keybar').hidden, false);
  assert.strictEqual(
    window.document.getElementById('mic-btn-wrap').parentElement.id,
    'keybar-slot-mic'
  );
});

test('강제 노출(?keybar=1)이어도 마이크 배치는 포인터 타입만 본다', async () => {
  const { window } = await buildWindow({ coarse: false, search: '?keybar=1' });
  assert.strictEqual(window.document.getElementById('keybar').hidden, false, '강제 노출은 그대로 보여야 한다');
  assert.strictEqual(window.document.getElementById('keybar').classList.contains('force-show'), true);
  assert.strictEqual(
    window.document.getElementById('mic-btn-wrap').parentElement.id,
    'vt-rail-mic-slot',
    '마이크는 여전히 데스크톱 자리에 있어야 한다(강제 노출과 무관)'
  );
});

// 2026-09-23 — 큐·업로드 슬롯을 없앴다(하단 내비 「첨부」·빠른 열기 `:`가
// 이미 같은 액션에 닿는다). 남은 건 마이크뿐이다.
test('큐·업로드 슬롯이 더 이상 없다', async () => {
  const { window } = await buildWindow({ coarse: true });
  assert.strictEqual(window.document.getElementById('keybar-slot-queue'), null);
  assert.strictEqual(window.document.getElementById('keybar-slot-upload'), null);
});

test('한 줄 구성 — 마이크·핵심 키 7개·토글이 모두 #keybar-main 하나에 있다', async () => {
  const { window } = await buildWindow({ coarse: true });
  const main = window.document.getElementById('keybar-main');
  assert.ok(main.contains(window.document.getElementById('keybar-slot-mic')));
  assert.ok(main.contains(window.document.getElementById('keybar-core-keys')));
  assert.ok(main.contains(window.document.getElementById('keybar-toggle')));
  const coreKeys = [...window.document.querySelectorAll('#keybar-core-keys .kb')].map((b) => b.dataset.key || b.dataset.mod);
  assert.deepStrictEqual(coreKeys, ['esc', 'ctrl', 'tab', 'up', 'down', 'left', 'right']);
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

test('접기 토글 — 기본은 접힘(추가 키 줄만), 마이크·핵심 키·토글은 항상 남는다', async () => {
  const { window } = await buildWindow({ coarse: true });
  const bar = window.document.getElementById('keybar');
  const toggle = window.document.getElementById('keybar-toggle');
  // 2026-09-23 — 기본값이 뒤집혔다(settings.js SCHEMA keybar.collapsed
  // def:true와 짝). 접는 대상도 #keybar-extra-keys(덜 쓰는 나머지)뿐이고
  // #keybar-main(마이크·Esc·Ctrl·Tab·화살표·토글)은 접힘과 무관하게 같은
  // 부모 밑에 항상 있다 — 예전처럼 별개 컨테이너로 분리해 둘 필요가 없다
  // (그 분리 자체가 "자주 쓰는 키까지 접힘에 묻힌다"는 회귀의 원인이었다).
  assert.strictEqual(bar.classList.contains('collapsed'), true);
  const main = window.document.getElementById('keybar-main');
  assert.ok(main.contains(toggle));
  assert.ok(main.contains(window.document.getElementById('keybar-core-keys')));

  toggle.dispatchEvent(new window.PointerEvent('pointerdown', { bubbles: true }));
  assert.strictEqual(bar.classList.contains('collapsed'), false);
  // 펼치든 접든 #keybar-main은 그대로 DOM에 있다(감춰지지 않음) — jsdom은
  // app.css를 안 불러오므로 실제 display 계산은 실브라우저 검증으로
  // 확인했다(별도).
  assert.ok(window.document.getElementById('keybar-extra-keys').isConnected);
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

// jsdom 기본 innerHeight(768)를 buildWindow 호출 시점에 알 수 없어서 쓰는 상수.
// createDomEnv가 바꾸지 않는 값이라 안전하다 — 어긋나면 위 테스트가 바로 깨진다.
function window_innerHeight() { return 768; }
