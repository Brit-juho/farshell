// layout/surface.js — refit 게이트(순수 함수)와 배치(DOM 이동)를 검증한다.
//
// 2026-09-18, 표면 레이어를 걷어내면서 여기 있던 `rectToPlacement` 테스트 2개를
// **배치 테스트로 바꿨다**. 그 함수는 pane-body의 rect를 표면 레이어 좌표로
// 옮기는 계산이었는데, 터미널이 자기 pane 안에 실제로 들어가면서 옮길 좌표
// 자체가 없어졌다. 대신 이제 검증할 수 있는 게 생겼다 — 배치가 좌표 계산이
// 아니라 **부모가 누구인가**로 표현되므로 jsdom에서 그대로 확인된다
// (getBoundingClientRect가 0을 돌려줘도 무관하다).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const SURFACE_JS = path.join(__dirname, '../js/layout/surface.js');

// surface.js가 최상단에서 term/resize.js를 import한다 — 그 파일은 core/store.js
// (getSession)를 더 끌어오고, core/store.js는 구식 classic script 호환을 위해
// window.sessions 등을 노출한다(store.js 주석 참고) — 그래서 진짜 jsdom
// window가 있는 환경이 필요하다(다른 테스트와 같은 하네스). 배치 테스트는 그
// window.sessions에 가짜 세션을 꽂아 getSession()이 실제 경로로 찾게 한다.
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function loadSurface() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  return importFresh(SURFACE_JS, env.context, cache);
}

// 배치 테스트용 — #terminal-container(대기실이 붙을 자리)와 pane-body 두 개,
// 그리고 wrapper를 든 가짜 세션을 심어둔 환경.
async function loadWithPanes() {
  const env = createDomEnv(
    '<!doctype html><html><body><div id="terminal-container">'
    + '<div id="body-a" class="vt-pane-body"></div>'
    + '<div id="body-b" class="vt-pane-body"></div>'
    + '</div></body></html>',
  );
  _doms.push(env.dom);
  const cache = new Map();
  const ns = await importFresh(SURFACE_JS, env.context, cache);
  const { window } = env;
  const mk = (id) => {
    const el = window.document.createElement('div');
    el.id = `term-${id}`;
    window.sessions[id] = { wrapper: el };
    ns.adoptWrapper(id, el);
    return el;
  };
  return { ns, window, mk, doc: window.document };
}

test('adoptWrapper: 배치 전 wrapper는 대기실에서 기다린다', async () => {
  const { mk, doc } = await loadWithPanes();
  const el = mk('a');
  assert.strictEqual(el.parentElement.id, 'vt-term-stage');
  // 대기실은 컨테이너의 자식이어야 한다 — 형제가 아니면 panes.js의
  // replaceChildren 한 번에 같이 날아간다(그 함정은 panes.js 주석 참고).
  assert.strictEqual(doc.getElementById('vt-term-stage').parentElement.id, 'terminal-container');
});

test('setPlacement: 배치된 세션은 그 pane-body 안으로 들어간다', async () => {
  const { ns, mk, doc } = await loadWithPanes();
  const el = mk('a');
  const bodyA = doc.getElementById('body-a');
  ns.setPlacement(new Map([['a', bodyA]]));
  assert.strictEqual(el.parentElement, bodyA, 'wrapper가 pane-body의 자식이어야 한다');
  assert.strictEqual(bodyA.dataset.vtSessionId, 'a');
});

test('setPlacement: 배치가 바뀌면 wrapper가 새 pane으로 따라 옮겨간다', async () => {
  const { ns, mk, doc } = await loadWithPanes();
  const el = mk('a');
  const [bodyA, bodyB] = [doc.getElementById('body-a'), doc.getElementById('body-b')];
  ns.setPlacement(new Map([['a', bodyA]]));
  ns.setPlacement(new Map([['a', bodyB]]));
  assert.strictEqual(el.parentElement, bodyB);
  assert.strictEqual(bodyA.dataset.vtSessionId, undefined, '떠난 pane의 표식은 지워져야 한다');
});

test('setPlacement: 배치에서 빠진 세션은 지워지지 않고 대기실로 돌아간다', async () => {
  const { ns, mk, doc } = await loadWithPanes();
  const el = mk('a');
  ns.setPlacement(new Map([['a', doc.getElementById('body-a')]]));
  ns.setPlacement(new Map());
  // DOM에서 제거하면 xterm 인스턴스와 스크롤백을 잃는다 — 대기실로만 옮긴다.
  assert.strictEqual(el.parentElement.id, 'vt-term-stage');
  assert.ok(el.isConnected, 'wrapper는 DOM에 남아 있어야 한다');
});

test('setPlacement: 같은 자리에 다시 배치해도 DOM을 건드리지 않는다', async () => {
  const { ns, mk, doc } = await loadWithPanes();
  const el = mk('a');
  const bodyA = doc.getElementById('body-a');
  ns.setPlacement(new Map([['a', bodyA]]));
  // 같은 자리에 appendChild를 또 부르면 xterm이 한 프레임 깜빡인다 — 렌더는
  // 트리가 바뀔 때마다 도므로 이 가드가 없으면 매 렌더가 깜빡임이다.
  let moved = 0;
  const orig = bodyA.appendChild.bind(bodyA);
  bodyA.appendChild = (n) => { moved += 1; return orig(n); };
  ns.setPlacement(new Map([['a', bodyA]]));
  assert.strictEqual(moved, 0);
  assert.strictEqual(el.parentElement, bodyA);
});

test('refit 게이트: 크기가 그대로면 fit을 부르지 않는다', async () => {
  const { createRefitGate } = await loadSurface();
  const fits = [];
  const gate = createRefitGate((id) => fits.push(id));

  assert.strictEqual(gate.report('a', 100, 40), 'fit'); // 첫 보고는 항상 변경으로 취급
  assert.strictEqual(gate.report('a', 100, 40), 'unchanged'); // 같은 크기 반복
  assert.deepStrictEqual(fits, ['a']);
});

test('refit 게이트: 크기가 바뀌면(드래그 아님) 즉시 fit', async () => {
  const { createRefitGate } = await loadSurface();
  const fits = [];
  const gate = createRefitGate((id) => fits.push(id));

  gate.report('a', 100, 40);
  fits.length = 0;
  assert.strictEqual(gate.report('a', 120, 40), 'fit');
  assert.deepStrictEqual(fits, ['a']);
});

test('refit 게이트: 드래그 중엔 크기가 바뀌어도 fit을 미루고, 드래그 종료 시 한 번만 flush', async () => {
  const { createRefitGate } = await loadSurface();
  const fits = [];
  const gate = createRefitGate((id) => fits.push(id));

  gate.report('a', 100, 40);
  gate.report('b', 200, 60);
  fits.length = 0;

  gate.beginDrag();
  // §1 수용 기준 2: 드래그 중 30프레임 흉내 — 세션 a만 매 프레임 계속 바뀐다.
  for (let w = 101; w <= 130; w++) gate.report('a', w, 40);
  assert.deepStrictEqual(fits, [], '드래그 중엔 fit이 0건이어야 한다');

  const flushed = Array.from(gate.endDrag()); // cross-realm 배열 → 이 realm 배열로
  assert.deepStrictEqual(flushed, ['a'], '드래그 중 실제로 바뀐 세션만, 딱 한 번');
  assert.deepStrictEqual(fits, ['a']);
});

test('refit 게이트: 드래그 중 크기가 안 바뀐 세션은 종료 후에도 flush되지 않는다', async () => {
  const { createRefitGate } = await loadSurface();
  const fits = [];
  const gate = createRefitGate((id) => fits.push(id));

  gate.report('a', 100, 40);
  gate.report('b', 200, 60);
  fits.length = 0;

  gate.beginDrag();
  gate.report('a', 130, 40); // a만 바뀜
  gate.report('b', 200, 60); // b는 그대로
  const flushed = Array.from(gate.endDrag());

  assert.deepStrictEqual(flushed, ['a']);
  assert.deepStrictEqual(fits, ['a']);
});

test('refit 게이트: forget 이후엔 다시 첫 보고처럼 취급한다(재배치)', async () => {
  const { createRefitGate } = await loadSurface();
  const fits = [];
  const gate = createRefitGate((id) => fits.push(id));

  gate.report('a', 100, 40);
  gate.forget('a');
  fits.length = 0;

  assert.strictEqual(gate.report('a', 100, 40), 'fit', 'forget 후엔 같은 크기라도 변경으로 취급');
  assert.deepStrictEqual(fits, ['a']);
});

// N43 §8 — pendingSize()는 리사이즈 오버레이가 "나머지 N개 유예" 문구를 켤지
// 판단하는 유일한 근거다(문서: "실제로 그렇게 동작할 때만" 표시). 게이트
// 내부 Set 크기를 그대로 세는 것뿐이지만, 그 계약이 깨지면 오버레이가 거짓
// 문구를 그리거나 계속 숨어 있게 되므로 직접 고정한다.
test('pendingSize: 드래그 중 유예된 세션 수를 그대로 센다', async () => {
  const { createRefitGate } = await loadSurface();
  const fits = [];
  const gate = createRefitGate((id) => fits.push(id));

  assert.strictEqual(gate.pendingSize(), 0, '드래그 전에는 0');

  gate.beginDrag();
  gate.report('a', 100, 50);
  gate.report('b', 200, 80);
  assert.strictEqual(gate.pendingSize(), 2, '드래그 중 바뀐 세션 수만큼');

  gate.report('a', 100, 50); // 같은 크기 재보고 — 유예 집합에 추가되지 않는다
  assert.strictEqual(gate.pendingSize(), 2);

  gate.endDrag();
  assert.strictEqual(gate.pendingSize(), 0, '드래그가 끝나면 비운다');
  assert.deepStrictEqual(fits.sort(), ['a', 'b']);
});
