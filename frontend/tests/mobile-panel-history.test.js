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
  assert.strictEqual(window.history.state.vtPanel, undefined);
});
