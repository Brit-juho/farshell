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

async function buildWindow({ coarse = false, search = '' } = {}) {
  const env = createDomEnv(INDEX_HTML, { url: `http://localhost/${search}` });
  _doms.push(env.dom);
  const { window } = env;
  window.WebSocket = FakeWebSocket;
  _stubMatchMedia(window, { coarse });

  const cache = new Map();
  await importFresh(KEYSEQ_JS, env.context, cache);
  const domNs = await importFresh(DOM_JS, env.context, cache);
  await importFresh(CORE_STORE_JS, env.context, cache);
  const keybarNs = await importFresh(KEYBAR_JS, env.context, cache);

  return { window, dom: domNs, ...keybarNs };
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
