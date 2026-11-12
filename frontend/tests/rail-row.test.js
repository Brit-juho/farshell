// 레일 칸 DOM — 펼친 레일은 가지 그래프 칸(shell/SessionNode.tsx), 접힌 48px 띠는 shell/RailRow.tsx Row.
// 우클릭과 ⋯ 버튼이 같은 핸들러. 레일 안의 답 버튼은 없다(세션 로직 점검 단계 5).
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
  const { mountRow, mountSessionNode, document, window } = env;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const calls = [];
  const props = {
    row: { ...base, ...rowOver },
    active: false,
    onOpen: () => calls.push('open'),
    onContext: (e) => calls.push(e.type),
    ...propsOver,
  };
  if (propsOver.compact) mountRow(root, props);
  else mountSessionNode(root, { geometry: { lane: 1, lines: '' }, ...props });
  return { root, window, document, calls };
}
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

test('우클릭(contextmenu)과 ⋯ 버튼이 같은 onContext를 부른다', async () => {
  const { root, window, calls } = await mount({});
  const rowEl = root.querySelector('.vt-rg-node');
  const ev = new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 5, clientY: 6 });
  rowEl.dispatchEvent(ev);
  assert.strictEqual(ev.defaultPrevented, true, '브라우저 기본 메뉴는 막는다');
  root.querySelector('.vt-rg-more').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(calls, ['contextmenu', 'click'], '둘 다 onContext 하나로 들어가고 행 열기는 안 일어난다');
});

test('칸 해부 — 마디(링 + 에이전트) · 1줄 이름·경과·⋯ · 2줄 대기 유형·변경량, 위치·만든 곳(외부)은 없다', async () => {
  const { root } = await mount({
    name: '결제 버그', status: 'waiting', kind: 'approval', unseen: true, since: Date.now() / 1000 - 120,
    branch: 'main', changed: { files: 2, add: 10, del: 1 }, origin: 'external', agent: 'claude', summary: 'npm',
  });
  const rowEl = root.querySelector('.vt-rg-node');
  assert.ok(rowEl.classList.contains('is-waiting'));
  assert.strictEqual(rowEl.dataset.state, 'waiting');
  assert.ok(root.querySelector('.vt-rg-gut .vt-rg-mark[data-state="waiting"] .vt-rg-unseen'), '확인 안 함은 마디 점');
  const top = root.querySelector('.vt-rg-a');
  assert.strictEqual(top.querySelector('.vt-rg-name').textContent, '결제 버그');
  assert.match(top.querySelector('.vt-rg-tm').textContent, /2분/);
  assert.ok(top.lastElementChild.classList.contains('vt-rg-more'), '⋯는 1줄 끝, 늘 같은 자리');
  const l2 = root.querySelector('.vt-rg-b');
  assert.match(l2.querySelector('.vt-rg-sm').textContent, /승인 요청\s*npm/);
  assert.strictEqual(l2.querySelector('.vt-diff-add').textContent, '+10');
  assert.strictEqual(l2.querySelector('.vt-diff-del').textContent, '−1');
  assert.ok(!/main|직접 만듦|유휴/.test(text(root)), '브랜치는 저장소 머리·워크트리 가지가, 만든 곳은 다른 맥일 때만');
  assert.strictEqual(root.querySelector('.vt-srow-origin'), null);
});

test('local은 만든 곳 아이콘이 없고, 할 말이 없으면 2줄이 없다', async () => {
  const { root } = await mount({ origin: 'local' });
  assert.strictEqual(root.querySelector('.vt-srow-origin'), null);
  assert.strictEqual(root.querySelector('.vt-rg-b'), null, '할 말이 없으면 둘째 줄 없음');
});

test('질문 원문은 행에 안 나오고 키워드만 — kind 없으면 질문', async () => {
  const { root } = await mount({ status: 'waiting', kind: null, question: '아주 긴 질문 문장입니다?' });
  assert.strictEqual(root.querySelector('.vt-rg-sm').textContent, '질문');
  assert.ok(!text(root).includes('아주 긴 질문'), '원문은 툴팁에만');
});

test('창 아이콘·반복 묶음 제목은 없고(분할 묶음은 작은 표식) 다른 화면 N은 해당할 때만', async () => {
  const on = await mount({ open: true, viewLabel: 'api+web', otherScreens: 2 }, { groupLabel: 'api+web' });
  assert.strictEqual(on.root.querySelector('.vt-srow-open'), null);
  assert.ok(!text(on.root).includes('api+web'), '묶음 이름은 글로 반복하지 않고 작은 표식(툴팁)만');
  assert.match(on.root.querySelector('.vt-rg-group').getAttribute('aria-label'), /api\+web/);
  assert.strictEqual(on.root.querySelector('.vt-srow-screens').textContent.trim(), '다른 화면 2');
  const off = await mount({ open: false, sessionId: '' });
  assert.strictEqual(off.root.querySelector('.vt-srow-screens'), null);
  assert.ok(off.root.querySelector('.vt-rg-node.closed'));
});

test('웹이 아닌 attach(맥 터미널 등)는 터미널 아이콘 + 개수로 따로 나온다', async () => {
  const both = await mount({ open: true, otherScreens: 1, otherClients: 2 });
  assert.strictEqual(both.root.querySelector('.vt-srow-screens').textContent.trim(), '다른 화면 1');
  const c = both.root.querySelector('.vt-srow-clients');
  assert.ok(c.querySelector('svg'));
  assert.strictEqual(c.textContent.trim(), '2');
  assert.match(c.getAttribute('data-tip'), /맥 터미널/);
  const only = await mount({ open: true, otherScreens: 0, otherClients: 1 });
  assert.ok(only.root.querySelector('.vt-rg-b .vt-srow-clients'));
  assert.strictEqual(only.root.querySelector('.vt-srow-screens'), null);
});

test('done 배지·작업 중 도구', async () => {
  const done = await mount({ status: 'done', badge: 'question', unseen: true });
  assert.strictEqual(done.root.querySelector('.vt-rg-sm').textContent, '끝남 · 확인 안 함 · 질문 있음');
  const work = await mount({ status: 'working', tool: 'Edit', summary: 'a.ts' });
  assert.match(work.root.querySelector('.vt-rg-sm[data-tone="working"]').textContent, /Edit\s*a\.ts/);
});

test('compact — 링 + 가운데 마크가 행 직속에 있고 ⋯ 버튼은 없다, 우클릭은 된다', async () => {
  const { root, window, calls } = await mount({ status: 'working', agent: 'claude' }, { compact: true });
  const ring = root.querySelector('.vt-wgrail-row > .vt-srow-state.vt-wgrail-ring[data-state="working"]');
  assert.ok(ring, '상태 링은 행 직속');
  assert.ok(ring.querySelector('svg.vt-ring'));
  assert.strictEqual(ring.getAttribute('role'), 'img');
  assert.strictEqual(ring.getAttribute('aria-label'), '작업 중', '상태 이름은 지금과 같다');
  assert.ok(root.querySelector('.vt-wgrail-row > .vt-wgrail-agent-compact svg'), '에이전트 마크는 칸 가운데(행 직속)');
  assert.strictEqual(root.querySelector('.vt-state-icon'), null, '접힘은 상태 아이콘 모양이 아니라 링이다');
  assert.strictEqual(root.querySelector('.vt-srow-main'), null, '이름·줄은 접힘에서 그리지 않는다');
  assert.strictEqual(root.querySelector('.vt-srow-menu'), null);
  root.querySelector('.vt-wgrail-row').dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(calls, ['contextmenu']);
});

test('compact — 에이전트를 모르거나 셸이어도 마크가 있다(셸 프롬프트), 읽지 않음은 링이 아니라 점', async () => {
  const sh = await mount({ status: 'idle', agent: null }, { compact: true });
  assert.ok(sh.root.querySelector('.vt-wgrail-agent-compact svg'));
  assert.strictEqual(sh.root.querySelector('.vt-wgrail-unseen'), null);
  const un = await mount({ status: 'done', agent: 'codex', unseen: true }, { compact: true });
  assert.ok(un.root.querySelector('.vt-wgrail-unseen'), 'unseen 점');
  assert.strictEqual(un.root.querySelector('.vt-wgrail-ring').getAttribute('aria-label'), '완료 · 아직 확인 안 함');
  const idleUnseen = await mount({ status: 'idle', agent: 'claude', unseen: true }, { compact: true });
  assert.strictEqual(idleUnseen.root.querySelector('.vt-wgrail-unseen'), null, 'idle은 unseen을 안 그린다(펼침과 같다)');
});

test('compact — 작업 중 서브에이전트 개수 배지(S1): N · 0이면 없음 · 한도 도달은 속 빈 「+」 · 펼침에는 없다', async () => {
  const items = (...states) => ({ parent_id: 'p', items: states.map((state, i) => ({ id: 'a' + i, state, label: 'x' + i })) });
  const two = await mount({ status: 'waiting', agent: 'claude', subagents: items('working', 'working', 'idle') }, { compact: true });
  const b = two.root.querySelector('.vt-wgrail-subbadge');
  assert.strictEqual(b.textContent, '2');
  assert.strictEqual(b.getAttribute('aria-label'), '내부 에이전트 2 작업 중');
  assert.strictEqual(two.root.querySelector('button.vt-wgrail-subbadge'), null, '클릭 동작 없음(툴팁만)');
  const none = await mount({ status: 'working', agent: 'claude', subagents: items('idle', 'done') }, { compact: true });
  assert.strictEqual(none.root.querySelector('.vt-wgrail-subbadge'), null);
  const lim = await mount({ status: 'working', agent: 'claude', subagents: { ...items('working'), limited: true } }, { compact: true });
  assert.ok(lim.root.querySelector('.vt-wgrail-subbadge.limited'));
  const wide = await mount({ status: 'working', agent: 'claude', subagents: items('working') });
  assert.strictEqual(wide.root.querySelector('.vt-wgrail-subbadge'), null);
});

test('compact — 공용 data-tip 툴팁을 달지 않는다(호버 카드가 대신한다): 이름은 aria-label, 카드 열쇠는 data-card-key', async () => {
  const { root } = await mount({
    status: 'waiting', kind: 'approval', branch: 'dev', otherScreens: 2,
    share: { mode: 'readwrite', nick: 'mina' },
  }, { compact: true });
  const el = root.querySelector('.vt-wgrail-row');
  for (const a of ['data-tip', 'data-tip-sub', 'data-tip-side']) assert.strictEqual(el.getAttribute(a), null, a + ' 없음(이중 툴팁 방지)');
  assert.strictEqual(root.querySelector('[data-tip]'), null, '접힌 칸 안 어디에도 data-tip이 없다');
  assert.strictEqual(el.getAttribute('aria-label'), 'api', '칸의 이름');
  assert.strictEqual(el.getAttribute('data-card-key'), 'api', '카드 열쇠 = tmux 이름');
  assert.strictEqual(el.getAttribute('role'), 'button');
  assert.strictEqual(el.getAttribute('tabindex'), '0');
  const wide = await mount({ status: 'idle' });
  assert.strictEqual(wide.root.querySelector('.vt-rg-node').getAttribute('data-card-key'), null, '펼침에는 카드 열쇠가 없다');
});

test('answerSessionPrompt — 키 + Enter, esc는 ESC 바이트, promptId가 있으면 함께, 실패는 삼킨다', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const sent = [];
  const deps = { vtFetch: (path, opts) => { sent.push([path, JSON.parse(opts.body)]); return Promise.reject(new Error('409')); } };
  await env.answerSessionPrompt(deps, { sessionId: 's/1', promptId: 'p9' }, '1');
  await env.answerSessionPrompt(deps, { sessionId: 's2', promptId: null }, 'esc');
  await env.answerSessionPrompt(deps, { sessionId: '', promptId: null }, '1');
  assert.deepStrictEqual(sent, [
    ['/api/sessions/s%2F1/keys', { text: '1\r', prompt_id: 'p9' }],
    ['/api/sessions/s2/keys', { text: '\x1b' }],
  ], '세션 id가 없으면 보내지 않는다');
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


test('내부 에이전트는 기록 조회로, 위임 세션은 별도 레이블로 구분한다', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const root = env.document.createElement('div');
  env.document.body.append(root);
  const row = { ...base, status: 'working', subagents: { parent_id: 'parent', items: [
    { id: 'child', label: '리뷰', state: 'working', tool: 'Bash', started_at: 1 },
  ] } };
  env.mountNativeChildren(root, row);
  // 머리 줄 없이 행마다 「내부 · 작업 중」(시안 D02), 누르면 기록(툴팁이 알린다).
  assert.strictEqual(root.querySelector('.vt-child-heading'), null);
  assert.strictEqual(root.querySelector('.vt-native-kw').textContent, '내부 · 작업 중');
  assert.strictEqual(root.querySelector('.vt-native-kw').dataset.state, 'working');
  assert.ok(root.querySelector('.vt-native-state .vt-state-icon[data-state="working"]'));
  assert.strictEqual(root.querySelector('.vt-native-open').dataset.tip, '기록 보기');
  assert.strictEqual(root.querySelector('.vt-native-tool'), null, '도구 줄은 그리지 않는다 — 호출마다 생겼다 사라져 깜빡인다');
  assert.ok(!/Bash/.test(root.querySelector('.vt-native-row').textContent), '행에 도구 이름이 새지 않는다');
  env.mountSessionNode(root, { row, geometry: { lane: 1, lines: '' }, kidsCount: 1, progress: { reported: 0, open: 1 }, onToggleKids() {}, onOpen() {}, onContext() {} });
  assert.match(root.querySelector('.vt-srow-kids').textContent, /위임 보고 0\/1/);
  assert.match(root.querySelector('.vt-native-chip').textContent, /내부 1 작업 중/);
});

test('다른 맥에서 만든 칸 — 1줄 이름 옆에 아이콘(툴팁 「다른 맥에서 만듦」), 글은 없다', async () => {
  const { root } = await mount({ origin: 'peer' });
  const origin = root.querySelector('.vt-rg-a .vt-srow-origin');
  assert.strictEqual(origin.dataset.origin, 'peer');
  assert.strictEqual(origin.querySelector('.vt-srow-origin-text'), null);
  assert.match(origin.getAttribute('data-tip'), /다른 맥/);
});

test('묶음 머리 — 접기 캐럿이 있으면 aria-expanded로 말하고, 접으면 구성원 행을 그리지 않는다(머리·개수는 남는다)', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const { document } = env;
  const root = document.createElement('div');
  document.body.appendChild(root);
  let collapsed = false;
  let opened = 0;
  const render = () => {
    root.replaceChildren();
    env.mountViewGroup(root, { label: 'api · web', rows: ['a', 'b'], collapsed: () => collapsed, onToggle: () => { collapsed = !collapsed; render(); }, onOpen: () => { opened += 1; } });
  };
  render();
  const toggle = () => root.querySelector('.vt-view-group-toggle');
  assert.strictEqual(toggle().getAttribute('aria-expanded'), 'true');
  assert.strictEqual(toggle().getAttribute('aria-label'), 'api · web 묶음 접기');
  assert.strictEqual(root.querySelectorAll('.member').length, 2);
  toggle().click();
  assert.strictEqual(toggle().getAttribute('aria-expanded'), 'false');
  assert.ok(toggle().classList.contains('collapsed'));
  assert.strictEqual(root.querySelectorAll('.member').length, 0);
  assert.strictEqual(root.querySelector('.vt-view-group-title').textContent, 'api · web', '머리는 남는다');
  assert.strictEqual(opened, 0, '접기는 묶음 열기와 다른 단추다');
  root.querySelector('.vt-view-group-title').click();
  assert.strictEqual(opened, 1);
});

test('묶음 머리 — 접기를 넘기지 않으면(폰 Fleet) 캐럿이 없고 행은 늘 보인다', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  env.mountViewGroup(root, { label: 'g', rows: ['a', 'b'] });
  assert.strictEqual(root.querySelector('.vt-view-group-toggle'), null);
  assert.strictEqual(root.querySelectorAll('.member').length, 2);
});
