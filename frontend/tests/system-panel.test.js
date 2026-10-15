// 시스템 패널(panels/system.js)의 순수 함수 — 단위 표기와 스파크라인 점, 렌더링.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const SYSTEM_JS = path.join(__dirname, '../js/panels/system.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(SYSTEM_JS, env.context, new Map());
}

test('fmtBytes / fmtRate — 2^10 단위, 유효하지 않으면 대시', async () => {
  const { fmtBytes, fmtRate } = await load();
  assert.strictEqual(fmtBytes(0), '0 B');
  assert.strictEqual(fmtBytes(1536), '1.5 KB');
  assert.strictEqual(fmtBytes(48 * 1024 ** 3), '48.0 GB');
  assert.strictEqual(fmtBytes(-1), '–');
  assert.strictEqual(fmtBytes(NaN), '–');
  assert.strictEqual(fmtRate(2048), '2.0 KB/s');
});

test('sparkPoints — 값이 적으면 오른쪽(최근)에 붙고, null은 건너뛰며, 눈금 위로 넘지 않는다', async () => {
  const { sparkPoints } = await load();
  const pts = sparkPoints([0, 50, 100], 240, 36, 100).split(' ');
  assert.strictEqual(pts.length, 3);
  const xs = pts.map((p) => Number(p.split(',')[0]));
  assert.ok(xs[0] > 100, '3개뿐이면 왼쪽을 비워 오른쪽에 붙인다');
  assert.ok(Math.abs(xs[2] - 240) < 0.2, '가장 최근 값이 오른쪽 끝');
  const ys = pts.map((p) => Number(p.split(',')[1]));
  assert.ok(ys[0] > ys[1] && ys[1] > ys[2], '값이 클수록 위(작은 y)');
  assert.strictEqual(sparkPoints([null, 10, null], 240, 36, 100).split(' ').length, 1);
  assert.ok(Number(sparkPoints([500], 240, 36, 100).split(',')[1]) >= 0, '눈금을 넘는 값은 맨 위에 붙는다');
});

test('renderStats — 값이 없는 항목은 카드를 그리지 않고, 위험 값은 색 토큰을 단다', async () => {
  const { renderStats } = await load();
  const html = renderStats({ ok: true, platform: 'macOS', cpu: { pct: 95.2, cores: 10 }, ram: null, net: null });
  assert.ok(html.includes('macOS') && html.includes('CPU') && html.includes('95%'));
  assert.ok(html.includes('vt-sys-value err'), 'CPU 90% 이상은 err');
  assert.ok(!html.includes('RAM') && !html.includes('vt-sys-dual'));
  assert.ok(renderStats({ ok: false }).includes('vt-vw-empty'));
  assert.ok(renderStats({ ok: true, platform: 'Linux', cpu: null, ram: null, net: null }).includes('vt-vw-empty'));
});

test('renderStats — 네트워크는 받기·보내기를 따로 보이고 인터페이스 이름을 이스케이프한다', async () => {
  const { renderStats } = await load();
  const html = renderStats({ ok: true, platform: 'Linux', cpu: null, ram: null,
    net: { rx_bps: 2048, tx_bps: 512, interfaces: ['eth0', '<b>x</b>'] } });
  assert.ok(html.includes('2.0 KB/s') && html.includes('512 B/s'));
  assert.ok(html.includes('&lt;b&gt;x&lt;/b&gt;') && !html.includes('<b>x</b>'));
});
