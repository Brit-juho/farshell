// layout/snap.js — 셀 배수 스냅의 순수 계산.
//
// 이 파일이 막는 사고: 스냅이 0이나 음수를 돌려주면 pane이 사라진다. 그리고
// "스냅할 수 없음"을 0으로 표현하면 호출부가 그걸 유효한 폭으로 받아 쓴다 —
// 그래서 못 하는 경우는 반드시 null이다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const SNAP_JS = path.join(__dirname, '../js/layout/snap.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(SNAP_JS, env.context, new Map());
}

test('snapPx: 셀 배수로 내림한다', async () => {
  const { snapPx } = await load();
  assert.strictEqual(snapPx(575.5, 8), 568);   // 실측값: 분할선 때문에 생기는 소수 폭
  assert.strictEqual(snapPx(576, 8), 576);     // 이미 배수면 그대로
  assert.strictEqual(snapPx(583, 8), 576);
});

test('snapPx: chrome(pane 헤더)을 빼고 재고 다시 더한다', async () => {
  const { snapPx } = await load();
  // 세로 분할 실측: 헤더 24 + 22행 × 18 = 420
  assert.strictEqual(snapPx(430, 18, 24), 420);
  assert.strictEqual(snapPx(420, 18, 24), 420);
});

test('snapPx: 한 칸도 못 들어가면 null (0을 주면 pane이 사라진다)', async () => {
  const { snapPx } = await load();
  assert.strictEqual(snapPx(7, 8), null);
  assert.strictEqual(snapPx(0, 8), null);
  assert.strictEqual(snapPx(30, 18, 24), null, 'chrome을 빼면 한 행도 안 남는다');
});

test('snapPx: 셀 크기를 모르면 null — 스냅을 건너뛰고 비율 flex로 떨어진다', async () => {
  const { snapPx } = await load();
  assert.strictEqual(snapPx(500, 0), null);
  assert.strictEqual(snapPx(500, NaN), null);
  assert.strictEqual(snapPx(NaN, 8), null);
});

test('snapPx: idempotent — 되풀이해도 값이 안 바뀐다(ResizeObserver 루프 방지)', async () => {
  const { snapPx } = await load();
  const once = snapPx(575.5, 8);
  assert.strictEqual(snapPx(once, 8), once);
  const v = snapPx(430, 18, 24);
  assert.strictEqual(snapPx(v, 18, 24), v);
});

test('cellSizeFrom: 살아 있는 터미널에서 셀을 읽고, 없으면 null', async () => {
  const { cellSizeFrom } = await load();
  const good = { term: { _core: { _renderService: { dimensions: { css: { cell: { width: 8, height: 18 } } } } } } };
  assert.deepStrictEqual({ ...cellSizeFrom([null, {}, good]) }, { w: 8, h: 18 });
  assert.strictEqual(cellSizeFrom([]), null);
  // 아직 렌더 전이라 0인 터미널은 건너뛴다(0으로 나누면 칸 수가 Infinity가 된다).
  const zero = { term: { _core: { _renderService: { dimensions: { css: { cell: { width: 0, height: 0 } } } } } } };
  assert.strictEqual(cellSizeFrom([zero]), null);
});
