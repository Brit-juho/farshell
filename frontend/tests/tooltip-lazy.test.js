// ui/tooltip-lazy.js — 본체 청크를 받다 실패해도(재배포 뒤 404 등) 새로고침 전까지 툴팁이 영영 죽지 않는다.
// 실패하면 실패한 약속을 버리고 첫 트리거 감시를 다시 걸며, 다음 시도는 짧게 물러섰다가 한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const LAZY_JS = path.join(__dirname, '../js/ui/tooltip-lazy.js');

// 진짜 tooltip.js 대신 실패 횟수를 정할 수 있는 가짜 본체를 옆에 둔다.
// initTooltips가 던지면 load()의 약속이 거절된다 — 청크 받기 실패와 같은 경로다.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vt-tiplazy-'));
fs.copyFileSync(LAZY_JS, path.join(tmp, 'tooltip-lazy.js'));
fs.writeFileSync(path.join(tmp, 'tooltip.js'), [
  'export function initTooltips() {',
  '  if (globalThis.__fail > 0) { globalThis.__fail--; throw new Error("chunk 404"); }',
  '  globalThis.__inited = (globalThis.__inited || 0) + 1;',
  '}',
  'export function adoptTrigger(el, type) { (globalThis.__adopted = globalThis.__adopted || []).push(el.id + ":" + type); }',
].join('\n'));

const _doms = [];
after(() => {
  for (const d of _doms) { try { d.window.close(); } catch (_) {} }
  fs.rmSync(tmp, { recursive: true, force: true });
});

const tick = () => new Promise((r) => setTimeout(r, 5));

async function boot(failures) {
  const env = createDomEnv('<!doctype html><html><body><button id="a" data-tip="A">a</button><button id="b" data-tip="B">b</button></body></html>');
  _doms.push(env.dom);
  const { window } = env;
  window.matchMedia = () => ({ matches: false }); // hover 없음 → 유휴 미리 받기 없이 트리거로만 받는다
  window.__fail = failures;
  window.console.error = () => {};
  let now = 1_000_000;
  window.Date.now = () => now;
  const ns = await importFresh(path.join(tmp, 'tooltip-lazy.js'), env.context, new Map());
  ns.initTooltips();
  const over = (id) => window.document.getElementById(id).dispatchEvent(new window.Event('focusin', { bubbles: true }));
  return { window, over, advance: (ms) => { now += ms; } };
}

test('본체 받기가 실패해도 감시를 다시 걸고, 물러선 뒤의 트리거에서 다시 받아 첫 툴팁을 넘긴다', async () => {
  const { window, over, advance } = await boot(1);
  over('a');
  await tick();
  assert.strictEqual(window.__inited, undefined, '첫 시도는 실패');
  assert.strictEqual(window.__adopted, undefined);
  // 실패 직후(1s 안)의 트리거는 건너뛴다.
  over('b');
  await tick();
  assert.strictEqual(window.__inited, undefined, '물러서는 동안은 다시 받지 않는다');
  advance(1001);
  over('b');
  await tick();
  assert.strictEqual(window.__inited, 1, '물러선 뒤에는 다시 받는다');
  assert.deepStrictEqual(Array.from(window.__adopted), ['b:focusin'], '그 트리거를 본체에 넘긴다');
  // 성공하면 감시를 뗀다 — 다음 트리거는 본체 몫이다.
  over('a');
  await tick();
  assert.deepStrictEqual(Array.from(window.__adopted), ['b:focusin']);
});

test('연속으로 실패하면 기다리는 시간이 두 배로 는다', async () => {
  const { window, over, advance } = await boot(2);
  over('a');
  await tick();
  advance(1001);
  over('a');
  await tick();
  assert.strictEqual(window.__inited, undefined, '두 번째도 실패');
  advance(1500);
  over('a');
  await tick();
  assert.strictEqual(window.__inited, undefined, '두 번째 실패 뒤에는 2s를 기다린다');
  advance(600);
  over('a');
  await tick();
  assert.strictEqual(window.__inited, 1);
  assert.deepStrictEqual(Array.from(window.__adopted), ['a:focusin']);
});
