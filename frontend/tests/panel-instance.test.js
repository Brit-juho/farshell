// N4 — 패널 인스턴스 계약(panels/panel.js). 같은 패널이 dock과 pane 칸에
// **동시에** 뜰 수 있어야 한다(2026-09-20 사용자 결정).
//
// 이 파일이 지키는 것은 "두 벌이 서로를 밟지 않는다"는 불변식이다. 밟는 경로가
// 셋 있었고 전부 조용히 깨진다:
//   1. `openPanel`이 같은 id를 토글로 해석해 먼저 있던 것을 닫는다
//   2. 안쪽 id가 중복돼 `getElementById`가 항상 먼저 것만 돌려준다
//   3. `setPanelPoll`이 id로 생존을 확인해 남의 인스턴스를 보고 "살아있다"고 오판
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const PANEL_JS = path.join(__dirname, '../js/panels/panel.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv(
    '<!doctype html><html><body><div id="pane-host"></div></body></html>');
  _doms.push(env.dom);
  const mod = await importFresh(PANEL_JS, env.context, new Map());
  return { mod, doc: env.context.document, win: env.window };
}

const OPTS = {
  id: 'vt-queue', ariaLabel: '큐', headHTML: '<div class="vt-vw-title">큐</div>',
  bodyId: 'vt-q-body', bodyHTML: '<span id="vt-q-mark">x</span>',
};

test('pane 인스턴스는 모달을 토글로 닫지 않는다', async () => {
  const { mod, doc } = await load();
  const modal = mod.openPanel({ ...OPTS });
  assert.ok(modal, '첫 번째(모달)가 열려야 한다');

  const pane = mod.openPanel({ ...OPTS, paneHost: doc.getElementById('pane-host'), paneId: 'p3' });
  assert.ok(pane, 'pane 인스턴스도 열려야 한다 — 토글로 해석하면 null이 온다');
  assert.ok(doc.getElementById('vt-queue'), '모달이 살아 있어야 한다');
});

test('안쪽 id가 인스턴스별로 갈린다 — 전역 조회가 남의 것을 잡지 않는다', async () => {
  const { mod, doc } = await load();
  mod.openPanel({ ...OPTS });
  const pane = mod.openPanel({ ...OPTS, paneHost: doc.getElementById('pane-host'), paneId: 'p3' });

  // 전역 조회는 **모달 것**을 찾는다(기존 동작 그대로).
  assert.ok(!doc.getElementById('vt-q-body').closest('#pane-host'));
  // pane 인스턴스는 자기 ctx로만 자기 것을 찾는다.
  assert.ok(pane.ctx.byId('vt-q-body').closest('#pane-host'));
  assert.notStrictEqual(pane.ctx.byId('vt-q-body').id, 'vt-q-body');
  // 같은 이름의 요소가 문서에 둘이지만 id는 유일하다.
  assert.strictEqual(doc.querySelectorAll('[id^=vt-q-body]').length, 2);
  assert.strictEqual(new Set([...doc.querySelectorAll('[id^=vt-q-body]')].map((e) => e.id)).size, 2);
});

test('루트 id에 접미사가 두 번 붙지 않는다', async () => {
  const { mod, doc } = await load();
  const pane = mod.openPanel({ ...OPTS, paneHost: doc.getElementById('pane-host'), paneId: 'p3' });
  assert.strictEqual(pane.el.id, 'vt-queue--pane-p3');
});

test('closePanel은 엘리먼트로도 닫힌다 — pane 인스턴스는 id를 조립할 필요가 없다', async () => {
  const { mod, doc } = await load();
  mod.openPanel({ ...OPTS });
  const pane = mod.openPanel({ ...OPTS, paneHost: doc.getElementById('pane-host'), paneId: 'p3' });

  mod.closePanel(pane.el);
  assert.strictEqual(doc.getElementById('pane-host').children.length, 0);
  assert.ok(doc.getElementById('vt-queue'), 'pane을 닫아도 모달은 남는다');
});

test('setPanelPoll은 엘리먼트의 생존을 본다 — 같은 id의 남의 인스턴스를 보지 않는다', async () => {
  const { mod, doc } = await load();
  const modal = mod.openPanel({ ...OPTS });
  const pane = mod.openPanel({ ...OPTS, paneHost: doc.getElementById('pane-host'), paneId: 'p3' });

  let ticks = 0;
  mod.setPanelPoll(pane.el, 5, () => { ticks += 1; });
  pane.el.remove();   // DOM에서 떨어뜨린다 — 모달(id 'vt-queue')은 그대로다
  await new Promise((r) => setTimeout(r, 30));
  const after1 = ticks;
  await new Promise((r) => setTimeout(r, 30));
  assert.strictEqual(ticks, after1, '떨어진 인스턴스의 폴링이 계속 돌면 안 된다');
  assert.ok(modal.el.isConnected);
});
