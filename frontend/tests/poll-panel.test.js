// panels/panel.js setPanelPoll — core/poll.js 위로 옮긴 뒤에도 공개 계약은 그대로다
// (주기 실행, 숨은 탭 건너뛰기, 보이면 즉시 따라잡기, 닫으면 정리). 거기에 더해:
//   - 이전 실행이 안 끝났으면 겹쳐 쏘지 않는다(서버가 답을 안 하던 사고).
//   - 첫 「불러오는 중…」이 마감을 넘겨도 남아 있으면 다시 시도할 수 있는 오류로 바뀐다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const PANEL_JS = path.join(__dirname, '../js/panels/panel.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// 패널 마감(20초)은 30ms로 줄인다. 폴링 간격은 테스트가 짧게 준다.
async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  const real = w.setTimeout.bind(w);
  w.setTimeout = (fn, ms, ...rest) => real(fn, ms >= 15000 ? 30 : ms, ...rest);
  const mod = await importFresh(PANEL_JS, env.context, new Map());
  return { mod, doc: env.context.document, win: w };
}

function setHidden(doc, hidden) {
  Object.defineProperty(doc, 'hidden', { configurable: true, get: () => hidden });
  doc.dispatchEvent(new doc.defaultView.Event('visibilitychange'));
}

const OPTS = { id: 'vt-t', ariaLabel: 't', headHTML: '<div>t</div>', bodyId: 'vt-t-body' };

test('주기적으로 돌고, 진행 중이면 겹쳐 쏘지 않는다', async () => {
  const { mod } = await load();
  const p = mod.openPanel({ ...OPTS, bodyHTML: '<i>x</i>' });
  let calls = 0;
  let release;
  mod.setPanelPoll(p.el, 10, () => { calls++; return new Promise((r) => { release = r; }); });
  await wait(60);
  assert.strictEqual(calls, 1, '첫 실행이 매달린 동안 새 실행이 쌓이지 않는다');
  release();
  await wait(40);
  assert.ok(calls >= 2);
  mod.closePanel(p.el);
});

test('숨으면 쉬고, 보이면 즉시 따라잡는다', async () => {
  const { mod, doc } = await load();
  const p = mod.openPanel({ ...OPTS, bodyHTML: '<i>x</i>' });
  let calls = 0;
  mod.setPanelPoll(p.el, 1000, () => { calls++; });
  setHidden(doc, true);
  setHidden(doc, false);
  await wait(0);
  assert.strictEqual(calls, 1, '보이자마자 한 번');
  mod.closePanel(p.el);
});

test('닫으면 멈추고 visibilitychange 리스너도 뗀다', async () => {
  const { mod, doc } = await load();
  const p = mod.openPanel({ ...OPTS, bodyHTML: '<i>x</i>' });
  let calls = 0;
  mod.setPanelPoll(p.el, 10, () => { calls++; });
  await wait(30);
  mod.closePanel(p.el);
  const n = calls;
  await wait(40);
  setHidden(doc, true); setHidden(doc, false);
  await wait(0);
  assert.strictEqual(calls, n);
});

test('첫 「불러오는 중…」이 마감을 넘기면 다시 시도할 수 있는 오류로 바뀐다', async () => {
  const { mod } = await load();
  const p = mod.openPanel({ ...OPTS });
  const body = p.body;
  assert.ok(body.querySelector('.vt-vw-loading[data-vt-initial]'));
  let calls = 0;
  mod.setPanelPoll(p.el, 10000, () => { calls++; });
  await wait(60);
  const err = body.querySelector('[data-vt-initial-error]');
  assert.ok(err, '자리표시가 오류로 바뀐다');
  assert.match(err.textContent, /불러오지 못했습니다/);
  assert.match(err.textContent, /서버가 응답하지 않습니다/);
  const btn = err.querySelector('button');
  assert.ok(btn, '다시 시도 버튼');
  btn.click();
  assert.ok(body.querySelector('.vt-vw-loading[data-vt-initial]'), '다시 시도하면 자리표시로 돌아간다');
  await wait(0);
  assert.strictEqual(calls, 1, '다시 시도는 한 번 더 불러온다');
  mod.closePanel(p.el);
});

test('실행이 거부되고 자리표시가 남아 있으면 그 오류를 보여 준다', async () => {
  const { mod } = await load();
  const p = mod.openPanel({ ...OPTS });
  mod.setPanelPoll(p.el, 10, () => Promise.reject(Object.assign(new Error('서버가 응답하지 않습니다'), { timeout: true })));
  await wait(40);
  const err = p.body.querySelector('[data-vt-initial-error]');
  assert.ok(err);
  assert.match(err.textContent, /서버가 응답하지 않습니다/);
  mod.closePanel(p.el);
});

test('패널이 자기 내용을 그렸으면 자리표시 오류는 끼어들지 않는다', async () => {
  const { mod } = await load();
  const p = mod.openPanel({ ...OPTS });
  p.body.innerHTML = '<p id="real">ok</p>';
  mod.setPanelPoll(p.el, 10000, () => {});
  await wait(60);
  assert.ok(p.body.querySelector('#real'));
  assert.strictEqual(p.body.querySelector('[data-vt-initial-error]'), null);
  mod.closePanel(p.el);
});
