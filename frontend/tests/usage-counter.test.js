// N41(60-settings-palette.md §5) — 누적형(CounterProvider) dock 렌더링의 순수
// 계산부. 서버 쪽 집계(server/tests/test_usage_counter.py)와 짝인 프런트
// 테스트로, 여기서는 **DOM/네트워크 없이** 포맷·스파크라인 좌표만 고정한다
// (ports.js의 _groupByPid와 같은 패턴 — panels/panel.js 등은 그대로 import되지만
// 호출하지 않는 순수 함수만 검증).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const USAGE_JS = path.join(__dirname, '../js/panels/usage.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="app"></div></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  return importFresh(USAGE_JS, env.context, cache);
}

test('humanTokens — K/M 단위로 접는다', async () => {
  const { humanTokens } = await load();
  assert.strictEqual(humanTokens(0), '0 tok');
  assert.strictEqual(humanTokens(840), '840 tok');
  assert.strictEqual(humanTokens(1840), '1.8K tok');
  assert.strictEqual(humanTokens(4_120_000), '4.1M tok');
});

test('humanDuration — 초/분/시간', async () => {
  const { humanDuration } = await load();
  assert.strictEqual(humanDuration(41), '41s');
  assert.strictEqual(humanDuration(125), '2m');
  assert.strictEqual(humanDuration(3600), '1h');
  assert.strictEqual(humanDuration(4080), '1h 8m');
});

test('_buildSparkline — 값이 없으면 빈 포인트, 다 0이어도 기준선을 그린다', async () => {
  const { _buildSparkline } = await load();
  assert.strictEqual(_buildSparkline([]).points, '');

  const zeros = _buildSparkline([{ day: 'a', tokens: 0 }, { day: 'b', tokens: 0 }], 10, 10);
  assert.ok(zeros.points.length > 0);   // "데이터 없음"과 "0만 있음"을 구분 — 편평선이라도 그린다

  const rising = _buildSparkline(
    [{ tokens: 0 }, { tokens: 50 }, { tokens: 100 }],
    100, 20,
  );
  const pts = rising.points.split(' ').map((p) => p.split(',').map(Number));
  assert.strictEqual(pts.length, 3);
  // x는 등간격 증가, y는 값이 클수록 작아진다(SVG 좌표계 — 위가 0).
  assert.ok(pts[0][0] < pts[1][0] && pts[1][0] < pts[2][0]);
  assert.ok(pts[0][1] > pts[2][1]);
  assert.strictEqual(rising.max, 100);
});
