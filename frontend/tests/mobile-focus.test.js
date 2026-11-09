// 리디자인 8단계 — 폰 수동 집중 모드(layout/mobile-focus.js, 시안 M14 · M14-a · M14-c).
// 들어가기는 상단바 버튼(focus.toggle)만, 나가기는 알약의 나가기 · 위 가장자리 24px에서 64px 넘게 끌어내리기 · 터미널 탭 떠나기.
//   node --experimental-vm-modules --test frontend/tests/mobile-focus.test.js
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = path.join(__dirname, '../js');
const INDEX = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build({ compact = true, desktopToggle = null } = {}) {
  const env = createDomEnv(INDEX);
  _doms.push(env.dom);
  const { window } = env;
  const mqListeners = [];
  const mq = { matches: compact, media: '', addEventListener: (_t, fn) => mqListeners.push(fn), removeEventListener() {}, addListener() {}, removeListener() {} };
  window.matchMedia = (q) => (q.includes('max-width') ? mq : { matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
  const cache = new Map();
  const dom = await importFresh(path.join(JS, 'core/dom.js'), env.context, cache);
  if (desktopToggle) dom.registerAction('focus.toggle', desktopToggle);
  const state = await importFresh(path.join(JS, 'agent/state.js'), env.context, cache);
  const focus = await importFresh(path.join(JS, 'layout/mobile-focus.js'), env.context, cache);
  const events = [];
  window.document.addEventListener('vt:focus-change', (e) => events.push(e.detail.on));
  const setWidth = (isCompact) => { mq.matches = isCompact; for (const fn of mqListeners) fn({ matches: isCompact }); };
  return { ...env, dom, state, focus, events, setWidth };
}

const html = (w) => w.document.documentElement;
const pointer = (w, type, id, y) => new w.PointerEvent(type, { bubbles: true, cancelable: true, pointerId: id, clientX: 100, clientY: y });

test('index.html — 집중 모드 버튼은 상단바에 있고 focus.toggle을 부른다(키바 버튼보다 앞, 최대화 아이콘)', () => {
  const top = INDEX.match(/<header id="topbar">[\s\S]*?<\/header>/)[0];
  const btn = top.match(/<button id="vt-focus-btn"[\s\S]*?<\/button>/)[0];
  assert.match(btn, /data-action="focus\.toggle"/);
  assert.match(btn, /aria-pressed="false"/);
  assert.match(btn, /data-icon="maximize"/);
  assert.ok(top.indexOf('id="vt-focus-btn"') < top.indexOf('id="keybar-open-btn"'), '상단바 첫 버튼');
});

test('버튼으로 들어가면 html.vt-focus · 버튼 눌림 · vt:focus-change, 한 번 더 누르면 나간다', async () => {
  const { window, document, events } = await build();
  document.getElementById('vt-focus-btn').click();
  assert.ok(html(window).classList.contains('vt-focus'));
  assert.strictEqual(document.getElementById('vt-focus-btn').getAttribute('aria-pressed'), 'true');
  assert.strictEqual(document.getElementById('vt-focus-btn').getAttribute('aria-label'), '집중 모드 끝내기');
  document.getElementById('vt-focus-btn').click();
  assert.ok(!html(window).classList.contains('vt-focus'));
  assert.deepStrictEqual(events, [true, false]);
});

test('알약의 나가기 버튼으로 나간다(터치 영역 44는 CSS)', async () => {
  const { window, document, focus } = await build();
  focus.setMobileFocus(true);
  const exit = document.querySelector('#vt-focus-pill .vt-focus-pill-exit');
  assert.strictEqual(exit.getAttribute('aria-label'), '집중 모드 끝내기');
  exit.click();
  assert.ok(!html(window).classList.contains('vt-focus'));
});

test('위 가장자리에서 64px 넘게 끌었다 놓으면 나가고, 덜 끌면 제자리', async () => {
  const { window, document, focus } = await build();
  focus.setMobileFocus(true);
  const edge = document.getElementById('vt-focus-edge');
  const hint = document.getElementById('vt-focus-hint');
  edge.dispatchEvent(pointer(window, 'pointerdown', 1, 4));
  edge.dispatchEvent(pointer(window, 'pointermove', 1, 50));
  assert.strictEqual(hint.hidden, false);
  assert.strictEqual(hint.dataset.armed, 'false');
  edge.dispatchEvent(pointer(window, 'pointerup', 1, 50));
  assert.ok(html(window).classList.contains('vt-focus'), '46px — 제자리');
  assert.strictEqual(hint.hidden, true);
  edge.dispatchEvent(pointer(window, 'pointerdown', 2, 4));
  edge.dispatchEvent(pointer(window, 'pointermove', 2, 4 + focus.PULL_EXIT_PX + 6));
  assert.strictEqual(hint.dataset.armed, 'true');
  assert.strictEqual(hint.textContent, '놓으면 집중 모드를 끝냅니다');
  edge.dispatchEvent(pointer(window, 'pointerup', 2, 4 + focus.PULL_EXIT_PX + 6));
  assert.ok(!html(window).classList.contains('vt-focus'), '70px — 나간다');
  assert.strictEqual(focus.EDGE_PX, 24);
});

test('개입 알약 — 승인 대기 세션이 있으면 「개입 N 이름」, 누르면 그 세션을 연다', async () => {
  const { window, document, state, focus } = await build();
  const opened = [];
  window.vtOpenSessionFromRail = (o) => { opened.push(o); return Promise.resolve(true); };
  focus.setMobileFocus(true);
  const pill = document.getElementById('vt-focus-pill');
  assert.strictEqual(pill.dataset.waiting, '0');
  assert.strictEqual(pill.querySelector('.vt-focus-pill-go').hidden, true);
  state.applySnapshot({ k: { tmux_session: 'api<x>', session_id: 's1', status: 'waiting' } });
  const go = pill.querySelector('.vt-focus-pill-go');
  assert.strictEqual(pill.dataset.waiting, '1');
  assert.strictEqual(go.hidden, false);
  assert.strictEqual(go.querySelector('.vt-focus-pill-n').textContent, '개입 1');
  assert.strictEqual(go.querySelector('.vt-focus-pill-name').textContent, 'api<x>', '이름은 글자로만');
  assert.strictEqual(go.querySelector('.vt-focus-pill-name').children.length, 0);
  go.click();
  assert.strictEqual(JSON.stringify(opened), JSON.stringify([{ tmuxName: 'api<x>' }]));
});

test('터미널 탭을 떠나거나 폭이 720 이상이 되면 나간다', async () => {
  const { window, document, focus, setWidth } = await build();
  focus.setMobileFocus(true);
  document.dispatchEvent(new window.CustomEvent('vt:mobile-tab', { detail: { tab: 'terminal' } }));
  assert.ok(html(window).classList.contains('vt-focus'));
  document.dispatchEvent(new window.CustomEvent('vt:mobile-tab', { detail: { tab: 'fleet' } }));
  assert.ok(!html(window).classList.contains('vt-focus'));
  focus.setMobileFocus(true);
  setWidth(false);
  assert.ok(!html(window).classList.contains('vt-focus'));
});

test('넓은 화면에서는 켜지지 않고, 먼저 등록된 데스크톱 집중 모드 처리기에 넘긴다(같은 액션 이름)', async () => {
  const calls = [];
  const { window, dom, focus } = await build({ compact: false, desktopToggle: () => calls.push('desktop') });
  dom.getAction('focus.toggle')();
  assert.deepStrictEqual(calls, ['desktop']);
  assert.strictEqual(focus.setMobileFocus(true), false);
  assert.ok(!html(window).classList.contains('vt-focus'));
});

test('CSS — 집중 모드는 상단바·아래 탭을 숨기고 키바를 바닥에 남기며, 자동 집중(보이는 높이 < 300)은 그대로다', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../styles/screens/98-mobile.css'), 'utf8');
  assert.match(css, /html\.vt-focus #topbar,\s*html\.vt-focus #vt-mnav-bottom,/);
  assert.match(css, /html\.vt-focus #keybar \{ bottom:0;/);
  assert.match(css, /html\.vt-focus #vt-focus-edge \{[^}]*height:calc\(24px \+ env\(safe-area-inset-top,0px\)\)/);
  assert.match(css, /#vt-focus-pill button::before \{[^}]*top:-8px; bottom:-8px;/, '알약 28 + 위아래 8 = 터치 44');
  const overlay = fs.readFileSync(path.join(__dirname, '../../styles/screens/20-overlay.css'), 'utf8');
  assert.match(overlay, /body\.vt-kb-focus #vt-mnav-bottom/);
});
