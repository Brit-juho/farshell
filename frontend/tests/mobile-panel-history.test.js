'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const PANEL_JS = path.join(__dirname, '../js/panels/panel.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  Object.defineProperty(env.window, 'innerWidth', { value: 390, configurable: true });
  env.window.fitAndResize = () => {};
  env.window.activeId = null;
  const panel = await importFresh(PANEL_JS, env.context, new Map());
  return { ...env, panel };
}

function openQueue(panel) {
  return panel.openPanel({
    id: 'vt-queue', ariaLabel: '큐', headHTML: '<strong>큐</strong>', bodyId: 'vt-q-body',
  });
}

test('모바일 주요 패널은 하단 내비를 남기는 route가 되고 history 한 단계를 만든다', async () => {
  const { window, document, panel } = await build();
  const opened = openQueue(panel);
  assert.ok(opened);
  assert.ok(document.getElementById('vt-queue').classList.contains('vt-mobile-route'));
  assert.strictEqual(window.history.state.vtPanel, 'vt-queue');
});

test('모바일 패널에서 브라우저 뒤로가기는 페이지를 떠나기 전에 패널부터 닫는다', async () => {
  const { window, document, panel } = await build();
  openQueue(panel);
  const closed = new Promise((resolve) => document.addEventListener('vt:panel-close', resolve, { once: true }));
  panel.closePanel('vt-queue');
  await Promise.race([closed, new Promise((resolve) => setTimeout(resolve, 100))]);
  assert.strictEqual(document.getElementById('vt-queue'), null);
  assert.strictEqual(window.location.pathname, '/', '같은 페이지 안에서 패널만 닫혀야 한다');
});

test('하단 내비로 화면을 바꿀 때는 기존 패널과 history 표식을 즉시 교체한다', async () => {
  const { window, document, panel } = await build();
  openQueue(panel);
  panel.closePanel('vt-queue', { history: 'replace' });
  assert.strictEqual(document.getElementById('vt-queue'), null);
  await new Promise((resolve) => setTimeout(resolve, 80)); // 칸 되돌림(popstate)이 끝날 때까지
  assert.strictEqual(window.history.state?.vtPanel, undefined);
});

// ── 'replace' 닫기는 쌓았던 history 칸을 실제로 되돌린다 ──
async function until(cond, ms = 1000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error('until: 시간 초과');
    await new Promise((r) => setTimeout(r, 5));
  }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const open = (panel, id) => panel.openPanel({ id, ariaLabel: id, headHTML: id, bodyId: `${id}-b` });

test('탭 화면 위 뷰어를 replace로 닫으면 첫 칸으로 되돌아가고 탭 화면은 남는다', async () => {
  const { window, document, panel } = await build();
  open(panel, 'vt-dir');
  open(panel, 'vt-file');
  assert.strictEqual(window.history.state.vtPanel, 'vt-file');
  panel.closePanel('vt-file', { history: 'replace' });
  await until(() => !window.history.state?.vtPanel);
  assert.ok(document.getElementById('vt-dir'), '탭 화면은 닫히지 않는다');
  assert.strictEqual(document.getElementById('vt-file'), null);
  window.history.forward();
  await until(() => window.history.state?.vtPanel === 'vt-file');
});

test('칸 쌓는 패널 둘을 replace 루프로 닫으면 첫 칸까지 되돌아간다', async () => {
  const { window, document, panel } = await build();
  open(panel, 'vt-queue');
  open(panel, 'vt-file');
  for (const el of Array.from(document.querySelectorAll('.vt-mobile-route'))) {
    panel.closePanel(el, { history: 'replace' });
  }
  await until(() => !window.history.state?.vtPanel);
  await wait(30);
  assert.strictEqual(document.querySelectorAll('.vt-mobile-route').length, 0);
  assert.ok(!window.history.state?.vtPanel);
  window.history.forward();
  await until(() => window.history.state?.vtPanel === 'vt-queue');
});

test('replace로 닫고 곧바로 다시 열면 vtPanel 칸 하나만 남고 뒤로가기 1회로 닫힌다', async () => {
  const { window, document, panel } = await build();
  open(panel, 'vt-file');
  const len0 = window.history.length;
  panel.closePanel('vt-file', { history: 'replace' });
  open(panel, 'vt-file');
  assert.ok(document.getElementById('vt-file'), '패널은 즉시 열린다');
  await wait(80); // 되돌림 popstate가 오고 큐의 push가 실행될 시간
  assert.strictEqual(window.history.state?.vtPanel, 'vt-file');
  assert.strictEqual(window.history.length, len0);
  window.history.back();
  await until(() => document.getElementById('vt-file') === null);
  assert.ok(!window.history.state?.vtPanel);
});

test('vtPanel 표식이 없는 칸에서는 replace로 닫아도 back하지 않는다', async () => {
  const { window, panel } = await build();
  open(panel, 'vt-file');
  window.history.replaceState({}, '', window.location.href);
  let backs = 0;
  const orig = window.history.back.bind(window.history);
  window.history.back = () => { backs++; orig(); };
  panel.closePanel('vt-file', { history: 'replace' });
  await wait(60);
  assert.strictEqual(backs, 0);
  assert.strictEqual(window.history.length, 2);
});

test('beforeClose가 거절하면 replace 닫기여도 칸을 유지한다', async () => {
  const { window, document, panel } = await build();
  panel.openPanel({ id: 'vt-file', ariaLabel: 'f', headHTML: 'f', bodyId: 'f-b', beforeClose: () => false });
  panel.closePanel('vt-file', { history: 'replace' });
  await wait(60);
  assert.ok(document.getElementById('vt-file'));
  assert.strictEqual(window.history.state.vtPanel, 'vt-file');
});

test('popstate가 안 오면 300ms 뒤 대기를 풀고 이후 push가 정상 동작한다', async () => {
  const { window, document, panel } = await build();
  open(panel, 'vt-file');
  window.history.back = () => {};
  panel.closePanel('vt-file', { history: 'replace' });
  const len0 = window.history.length;
  open(panel, 'vt-queue');
  assert.ok(document.getElementById('vt-queue'));
  assert.strictEqual(window.history.length, len0, '되돌림 대기 중에는 칸이 아직 안 쌓인다');
  await until(() => window.history.length === len0 + 1, 1000);
  assert.strictEqual(window.history.state.vtPanel, 'vt-queue');
});
