// 레일 행(shell/RailRow.tsx) DOM — 3줄 구성, 우클릭과 ⋯ 버튼이 같은 핸들러, 접힘(compact).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
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
  const { mountRow, document, window } = env;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const calls = [];
  mountRow(root, {
    row: { ...base, ...rowOver },
    active: false,
    onOpen: () => calls.push('open'),
    onContext: (e) => calls.push(e.type),
    ...propsOver,
  });
  return { root, window, document, calls };
}
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('우클릭(contextmenu)과 ⋯ 버튼이 같은 onContext를 부른다', async () => {
  const { root, window, calls } = await mount({});
  const rowEl = root.querySelector('.vt-wgrail-row');
  const ev = new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 5, clientY: 6 });
  rowEl.dispatchEvent(ev);
  assert.strictEqual(ev.defaultPrevented, true, '브라우저 기본 메뉴는 막는다');
  root.querySelector('.vt-srow-menu').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(calls, ['contextmenu', 'click'], '둘 다 onContext 하나로 들어가고 행 열기는 안 일어난다');
});

test('3줄 — 1줄 상태 아이콘·이름, 2줄 브랜치(main도)·변경량, 3줄 키워드·경과·만든 곳', async () => {
  const { root } = await mount({
    name: 'api', status: 'waiting', kind: 'approval', unseen: true, since: Date.now() / 1000 - 120,
    branch: 'main', isMainWorktree: true, changed: { files: 2, add: 10, del: 1 },
    origin: 'external', agent: 'claude',
  });
  const top = root.querySelector('.vt-srow-top');
  assert.ok(top.querySelector('.vt-state-icon[data-state="waiting"].unseen'), '읽지 않음은 아이콘 링');
  assert.strictEqual(top.querySelector('.vt-srow-name').textContent, 'api');
  const l2 = root.querySelector('.vt-srow-line2');
  assert.strictEqual(l2.querySelector('.vt-srow-branch').textContent, 'main');
  assert.strictEqual(l2.querySelector('.vt-srow-wt'), null, '워크트리 아이콘 대신 작업 위치 텍스트');
  assert.ok(text(l2).includes('+10 −1'));
  const l3 = root.querySelector('.vt-srow-line3');
  assert.strictEqual(l3.querySelector('.vt-srow-kw').textContent, '승인 요청');
  assert.ok(l3.querySelector('.vt-srow-since'));
  assert.strictEqual(l3.querySelector('.vt-srow-origin').dataset.origin, 'external');
  // 옛 요소는 없다.
  assert.strictEqual(root.querySelector('.vt-wgrail-bar'), null);
  assert.strictEqual(root.querySelector('.vt-srow-tmux'), null);
  assert.strictEqual(root.querySelector('.vt-srow-question'), null);
});

test('local은 만든 곳 아이콘이 없고, 브랜치가 없으면 2줄이 없다', async () => {
  const { root } = await mount({ origin: 'local' });
  assert.strictEqual(root.querySelector('.vt-srow-origin'), null);
  assert.strictEqual(root.querySelector('.vt-srow-line2'), null);
  assert.strictEqual(root.querySelector('.vt-srow-line4'), null);
});

test('질문 원문은 행에 안 나오고 키워드만 — kind 없으면 질문', async () => {
  const { root } = await mount({ status: 'waiting', kind: null, question: '아주 긴 질문 문장입니다?' });
  assert.strictEqual(root.querySelector('.vt-srow-kw').textContent, '질문');
  assert.ok(!text(root).includes('아주 긴 질문'));
});

test('창 아이콘·반복 묶음 제목은 없고 다른 화면 N은 해당할 때만', async () => {
  const on = await mount({ open: true, viewLabel: 'api+web', otherScreens: 2 });
  assert.strictEqual(on.root.querySelector('.vt-srow-open'), null);
  assert.strictEqual(on.root.querySelector('.vt-srow-view'), null, '묶음 제목은 헤더에서 한 번만 표시');
  assert.strictEqual(on.root.querySelector('.vt-srow-screens').textContent.trim(), '다른 화면 2');
  const off = await mount({ open: false, sessionId: '' });
  assert.strictEqual(off.root.querySelector('.vt-srow-open'), null);
  assert.strictEqual(off.root.querySelector('.vt-srow-screens'), null);
  assert.ok(off.root.querySelector('.vt-wgrail-row.closed'));
});

test('웹이 아닌 attach(맥 터미널 등)는 터미널 아이콘 + 개수로 따로 나온다', async () => {
  const both = await mount({ open: true, otherScreens: 1, otherClients: 2 });
  assert.strictEqual(both.root.querySelector('.vt-srow-screens').textContent.trim(), '다른 화면 1');
  const c = both.root.querySelector('.vt-srow-clients');
  assert.ok(c.querySelector('svg'));
  assert.strictEqual(c.textContent.trim(), '2');
  assert.match(c.getAttribute('data-tip'), /맥 터미널/);
  const only = await mount({ open: true, otherScreens: 0, otherClients: 1 });
  assert.ok(only.root.querySelector('.vt-srow-line4'));
  assert.strictEqual(only.root.querySelector('.vt-srow-screens'), null);
});

test('done 배지·작업 중 도구', async () => {
  const done = await mount({ status: 'done', badge: 'question' });
  assert.strictEqual(done.root.querySelector('.vt-srow-badge').textContent, '질문 있음');
  const work = await mount({ status: 'working', tool: 'Edit' });
  assert.strictEqual(work.root.querySelector('.vt-srow-tool').textContent, 'Edit');
});

test('compact — 상태 아이콘이 행 직속에 있고 ⋯ 버튼은 없다, 우클릭은 된다', async () => {
  const { root, window, calls } = await mount({ status: 'working', agent: 'claude' }, { compact: true });
  assert.ok(root.querySelector('.vt-wgrail-row > .vt-srow-state .vt-state-icon[data-state="working"]'));
  assert.strictEqual(root.querySelector('.vt-srow-menu'), null);
  root.querySelector('.vt-wgrail-row').dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(calls, ['contextmenu']);
});

test('행 메뉴가 뷰포트 아래로 넘치면 안으로 당긴다 — 가로 폰(높이 331px) 실측 2026-10-06', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const { mountMenu, document, window } = env;
  Object.defineProperty(window, 'innerHeight', { value: 331, configurable: true });
  Object.defineProperty(window, 'innerWidth', { value: 832, configurable: true });
  // jsdom 은 레이아웃이 없어 크기가 0 이다 — 메뉴 높이 190, 폭 200 으로 가장한다.
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    return { width: 200, height: 190, top: 0, left: 0, right: 200, bottom: 190, x: 0, y: 0, toJSON() {} };
  };
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountMenu(root, { x: 700, y: 276, onClose() {}, items: [{ label: '열기', run() {} }] });
  const el = root.querySelector('.vt-menu');
  assert.ok(el);
  // 276 + 190 > 331 → 위로 당겨 하단 여백(8px)을 남긴다: 331 - 190 - 8 = 133
  assert.strictEqual(parseFloat(el.style.top), 133);
  // 700 + 200 > 832 → 왼쪽으로: 832 - 200 - 8 = 624
  assert.strictEqual(parseFloat(el.style.left), 624);
  assert.strictEqual(el.style.overflowY, 'auto');
});
