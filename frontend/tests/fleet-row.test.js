// 리디자인 8단계 — 폰 「세션」 탭 카드(shell/Fleet.tsx FleetRow, 시안 M01).
// 두 줄(이름·경과 / 키워드·위치·변경량·글리프), 개입 필요 카드의 질문 한 줄과 44px 답 버튼(번호 따로),
// 길게 누르기 500ms · 8px 취소 · 마우스 제외, 길게 누른 뒤 오는 click은 행을 열지 않는다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });

const base = {
  sessionId: 's1', tmuxName: 'api', name: 'api', open: true, status: 'idle', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, changed: null,
  agent: null, branch: null, isMainWorktree: false, origin: 'local', viewLabel: null, otherScreens: 0, otherClients: 0,
};

async function mount(rowOver, propsOver = {}) {
  const env = await loadSolidBuild();
  _envs.push(env);
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const calls = [];
  env.mountFleetRow(root, {
    row: { ...base, ...rowOver },
    onOpen: () => calls.push('open'),
    onLongPress: () => calls.push('long'),
    onAnswer: (k) => { calls.push(`answer:${k}`); return Promise.resolve(); },
    onSeen: () => calls.push('seen'),
    ...propsOver,
  });
  return { ...env, root, calls };
}
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('두 줄 — 1줄 상태·이름·경과(오른쪽), 2줄 키워드·위치(디렉터리/브랜치)·변경량', async () => {
  const { root } = await mount({
    status: 'working', since: Date.now() / 1000 - 14 * 60, directory: 'api-server', branch: 'feat/auth',
    changed: { files: 2, add: 142, del: 38 },
  });
  const row = root.querySelector('.vt-fleet-row');
  assert.strictEqual(row.dataset.state, 'working');
  const top = row.querySelector('.vt-fleet-row-top');
  assert.ok(top.querySelector('.vt-srow-state .vt-state-icon[data-state="working"]'));
  assert.strictEqual(top.querySelector('.vt-fleet-name').textContent, 'api');
  assert.match(top.querySelector('.vt-fleet-el').textContent, /14분/);
  const l2 = row.querySelector('.vt-fleet-line2');
  assert.strictEqual(l2.querySelector('.vt-srow-kw').dataset.state, 'working');
  assert.strictEqual(text(l2.querySelector('.vt-fleet-loc')), 'api-serverfeat/auth', '디렉터리 + 브랜치(구분자 「/」는 CSS)');
  assert.ok(l2.querySelector('.vt-fleet-diff'));
  assert.strictEqual(row.querySelectorAll('.vt-srow-line3, .vt-srow-line4').length, 0, '옛 3·4줄은 없다');
  assert.ok(!row.classList.contains('vt-fleet-card'), '실행 중 행은 카드가 아니다');
});

test('개입 필요 카드 — 질문 한 줄, 답 버튼은 번호를 따로 그리고 누르면 그 키를 보낸다(행은 안 열린다)', async () => {
  const { root, window, calls } = await mount({
    status: 'waiting', kind: 'approval', question: 'node --test frontend/tests/rail-*.test.js',
    options: [{ key: '1', label: '예' }, { key: '2', label: '항상 허용' }, { key: '3', label: '아니요' }],
  }, { answers: true });
  const row = root.querySelector('.vt-fleet-row');
  assert.ok(row.classList.contains('vt-fleet-card'));
  assert.strictEqual(text(row.querySelector('.vt-fleet-q')), '$node --test frontend/tests/rail-*.test.js');
  const btns = [...row.querySelectorAll('.vt-fleet-answers .vt-btn')];
  assert.deepStrictEqual(btns.map((b) => b.querySelector('.vt-srow-answer-key').textContent), ['1', '2', '3']);
  assert.deepStrictEqual(btns.map((b) => b.querySelector('.vt-srow-answer-label').textContent), ['예', '항상 허용', '아니요']);
  btns[1].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 0));
  assert.deepStrictEqual(calls, ['answer:2']);
});

test('같은 waiting 세션이라도 답 버튼이 없는 구역(실행 중)·닫힌 세션은 카드가 아니다', async () => {
  const a = await mount({ status: 'waiting', options: [{ key: '1', label: '예' }] });
  assert.ok(!a.root.querySelector('.vt-fleet-card'));
  assert.strictEqual(a.root.querySelector('.vt-fleet-answers'), null);
  const b = await mount({ status: 'waiting', open: false, options: [{ key: '1', label: '예' }] }, { answers: true });
  assert.strictEqual(b.root.querySelector('.vt-fleet-answers'), null);
});

test('길게 누르기 — 터치 500ms면 메뉴, 8px 넘게 움직이면 취소, 마우스는 무시, 뒤따르는 click은 행을 열지 않는다', async () => {
  const { root, window, calls } = await mount({});
  const row = root.querySelector('.vt-fleet-row');
  const ev = (type, o = {}) => new window.PointerEvent(type, { bubbles: true, pointerId: 1, pointerType: 'touch', clientX: 10, clientY: 10, ...o });
  row.dispatchEvent(ev('pointerdown'));
  row.dispatchEvent(ev('pointermove', { clientX: 20 }));
  await new Promise((r) => setTimeout(r, 560));
  assert.deepStrictEqual(calls, [], '8px 넘게 움직이면 취소');
  row.dispatchEvent(ev('pointerdown', { pointerType: 'mouse' }));
  await new Promise((r) => setTimeout(r, 560));
  assert.deepStrictEqual(calls, [], '마우스는 데스크톱 ⋯ 몫');
  row.dispatchEvent(ev('pointerdown'));
  await new Promise((r) => setTimeout(r, 560));
  row.dispatchEvent(ev('pointerup'));
  row.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(calls, ['long'], '메뉴만 뜨고 행은 열리지 않는다');
  row.dispatchEvent(ev('pointerdown'));
  row.dispatchEvent(ev('pointerup'));
  row.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(calls, ['long', 'seen', 'open'], '짧게 누르면 연다');
});

test('CSS — 카드 64 · 이름 15/600 · 답 44 · 대기색 테두리와 왼쪽 3px 띠(98-mobile.css)', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../styles/screens/98-mobile.css'), 'utf8');
  assert.match(css, /#vt-fleet \.vt-fleet-row \{[^}]*min-height:64px;/);
  assert.match(css, /#vt-fleet \.vt-fleet-name \{[^}]*font:600 var\(--fs-base\)/);
  assert.match(css, /#vt-fleet \.vt-fleet-answers \.vt-btn \{[^}]*height:44px;/);
  assert.match(css, /\.vt-fleet-row\.vt-fleet-card \{[^}]*border-color:var\(--m-wait-line\)/);
  assert.match(css, /\.vt-fleet-row\.vt-fleet-card::before \{[^}]*width:3px;[^}]*background:var\(--color-st-waiting\)/);
});
