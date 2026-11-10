'use strict';
// 폰: 「파일」 탭(탭 화면 vt-dir) 위에 뷰어(vt-file)를 연 채 X · 뒤로가기로 닫을 때, 뷰어만 한 번에 닫혀야 한다.
// 회귀(2.10.0~2.10.1): popstate가 DOM 순서상 첫 `.vt-mobile-route`(탭 화면)를 닫아 하단 탭이 「터미널」로 돌아가고
// 뷰어는 남았다. 뒤로가기가 닫는 것은 history를 쌓은 맨 위 화면이어야 한다.
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

function openTab(panel) {
  return panel.openPanel({ id: 'vt-dir', ariaLabel: '파일', headHTML: '<strong>파일</strong>', bodyId: 'vt-dir-body' });
}
function openViewer(panel, extra = {}) {
  return panel.openPanel({ id: 'vt-file', ariaLabel: '뷰어', headHTML: '<strong>f.md</strong>', bodyId: 'vt-file-body', ...extra });
}
function openQueue(panel) {
  return panel.openPanel({ id: 'vt-queue', ariaLabel: '큐', headHTML: '<strong>큐</strong>', bodyId: 'vt-q-body' });
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 80));
// history.back()의 popstate는 비동기다 — 부하가 큰 머신에서도 흔들리지 않게 조건이 될 때까지 기다린다.
async function until(fn, ms = 3000) {
  const end = Date.now() + ms;
  while (!fn() && Date.now() < end) await new Promise((resolve) => setTimeout(resolve, 20));
}

test('탭 화면 위에 뷰어가 있을 때 뒤로가기는 탭 화면이 아니라 뷰어를 닫는다', async () => {
  const { window, document, panel } = await build();
  openTab(panel);
  openViewer(panel);
  assert.strictEqual(window.history.state.vtPanel, 'vt-file');
  const closed = [];
  document.addEventListener('vt:panel-close', (e) => closed.push(e.detail.id));
  window.history.back();
  await until(() => !document.getElementById('vt-file'));
  await settle();
  assert.strictEqual(document.getElementById('vt-file'), null, '뷰어가 사라져야 한다');
  assert.ok(document.getElementById('vt-dir'), '탭 화면은 남아야 한다');
  assert.deepStrictEqual(closed, ['vt-file'], '닫힘 이벤트는 뷰어 하나뿐이어야 한다(탭이 「터미널」로 돌아가면 안 된다)');
});

test('뷰어 X는 한 번에 뷰어만 닫고 탭 화면은 그대로 둔다', async () => {
  const { document, panel } = await build();
  openTab(panel);
  openViewer(panel);
  document.querySelector('#vt-file .vt-vw-x').click();
  await until(() => !document.getElementById('vt-file'));
  await settle();
  assert.strictEqual(document.getElementById('vt-file'), null);
  assert.ok(document.getElementById('vt-dir'));
});

test('topHistoryPanel은 history를 쌓은 맨 위 화면을 고른다', async () => {
  const { document, panel } = await build();
  assert.strictEqual(panel.topHistoryPanel(), null);
  openTab(panel);
  assert.strictEqual(panel.topHistoryPanel(), null, '탭 화면은 history를 쌓지 않는다');
  openQueue(panel);
  openViewer(panel);
  assert.strictEqual(panel.topHistoryPanel().id, 'vt-file');
  assert.ok(document.getElementById('vt-queue'));
});

test('저장 안 한 편집을 묻는 중 뒤로가기로 취소하면 뷰어도 탭 화면도 그대로다', async () => {
  const { window, document, panel } = await build();
  openTab(panel);
  openViewer(panel, { beforeClose: () => false });
  const popped = new Promise((resolve) => window.addEventListener('popstate', resolve, { once: true }));
  window.history.back();
  await popped;      // panel.js의 리스너가 먼저 등록돼 있으므로 이 시점엔 이미 처리됐다
  await settle();
  assert.ok(document.getElementById('vt-file'), '취소하면 뷰어가 남는다');
  assert.ok(document.getElementById('vt-dir'));
  assert.strictEqual(window.history.state.vtPanel, 'vt-file', '다음 뒤로가기도 이 뷰어를 닫도록 항목을 다시 쌓는다');
});
