// N16 §1 수용 기준 4 — layout/surface.js의 순수 로직 두 조각을 DOM/
// ResizeObserver 없이 검증한다. DOM 배선(adoptWrapper/setPlacement)은 실제
// 레이아웃 엔진(getBoundingClientRect)이 필요해 jsdom에서는 의미 있게 못
// 재현하므로, rectToPlacement·createRefitGate 순수 함수만 단위 테스트하고
// 실제 배치·refit 게이트 동작은 실브라우저 스모크(계획서 §5)로 검증한다.
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
// window가 있는 환경이 필요하다(다른 테스트와 같은 하네스). 실제 DOM 조작
// 함수(adoptWrapper 등)는 이 테스트에서 안 부른다 — 순수 함수만 검증한다.
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function loadSurface() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  return importFresh(SURFACE_JS, env.context, cache);
}

test('rectToPlacement: surface 기준 상대좌표로 변환한다', async () => {
  const { rectToPlacement } = await loadSurface();
  const surfaceRect = { left: 100, top: 40, width: 800, height: 600 };
  const bodyRect = { left: 140, top: 76, width: 320, height: 240 };
  // vm 모듈 안에서 만든 객체는 이 테스트 realm과 Object 생성자가 달라
  // deepStrictEqual이 "구조는 같은데 참조는 다르다"로 실패한다(cross-realm) —
  // 스프레드로 이 realm의 평범한 객체로 복사해서 비교한다.
  assert.deepStrictEqual({ ...rectToPlacement(surfaceRect, bodyRect) }, { x: 40, y: 36, w: 320, h: 240 });
});

test('rectToPlacement: surface와 body가 같은 원점이면 (0,0)', async () => {
  const { rectToPlacement } = await loadSurface();
  const r = { left: 0, top: 0, width: 1000, height: 700 };
  assert.deepStrictEqual({ ...rectToPlacement(r, r) }, { x: 0, y: 0, w: 1000, h: 700 });
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
