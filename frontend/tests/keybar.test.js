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
  await importFresh(CORE_STORE_JS, env.context, cache);
  const keybarNs = await importFresh(KEYBAR_JS, env.context, cache);

  return { window, dom: domNs, vv, ...keybarNs };
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

test('큐 슬롯 클릭 → queue.show 액션을 부른다', async () => {
  const { window, dom } = await buildWindow({ coarse: true });
  let called = 0;
  dom.registerAction('queue.show', () => { called++; });
  window.document.getElementById('keybar-slot-queue').click();
  assert.strictEqual(called, 1);
});

test('업로드 슬롯 클릭 → #file-input을 클릭한다', async () => {
  const { window } = await buildWindow({ coarse: true });
  let clicked = false;
  window.document.getElementById('file-input').addEventListener('click', () => { clicked = true; });
  window.document.getElementById('keybar-slot-upload').click();
  assert.strictEqual(clicked, true);
});

test('접기 토글 — "키들" 줄만 접히고 입력 슬롯 줄은 클래스 구조상 별도로 남는다', async () => {
  const { window } = await buildWindow({ coarse: true });
  const bar = window.document.getElementById('keybar');
  const toggle = window.document.getElementById('keybar-toggle');
  assert.strictEqual(bar.classList.contains('collapsed'), false);
  // #keybar-top(슬롯+토글)과 #keybar-keys가 별개 자식이라 접힘 클래스가
  // #keybar-keys만 골라 숨길 수 있다 — 이 구조 자체가 회귀 대상(구 M7).
  assert.ok(window.document.getElementById('keybar-top').contains(toggle));
  assert.ok(!window.document.getElementById('keybar-top').contains(window.document.getElementById('keybar-keys')));

  toggle.dispatchEvent(new window.PointerEvent('pointerdown', { bubbles: true }));
  assert.strictEqual(bar.classList.contains('collapsed'), true);
  // 접힌 상태에도 슬롯은 여전히 DOM에 존재(감춰지지 않음) — jsdom은 app.css를
  // 안 불러오므로 실제 display 계산은 실브라우저 검증으로 확인했다(별도).
  assert.ok(window.document.getElementById('keybar-slots').isConnected);
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
