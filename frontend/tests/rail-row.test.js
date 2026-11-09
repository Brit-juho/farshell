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

test('행 해부 — 아이콘 열 + 1줄 이름·경과·⋯, 2줄 브랜치(main도)·변경량, 3줄 키워드·만든 곳', async () => {
  const { root } = await mount({
    name: 'api', status: 'waiting', kind: 'approval', unseen: true, since: Date.now() / 1000 - 120,
    branch: 'main', isMainWorktree: true, changed: { files: 2, add: 10, del: 1 },
    origin: 'external', agent: 'claude',
  });
  const rowEl = root.querySelector('.vt-wgrail-row');
  assert.ok(rowEl.classList.contains('is-waiting'), 'waiting 행은 틴트 클래스');
  assert.strictEqual(rowEl.dataset.state, 'waiting');
  // 상태 아이콘은 행의 첫 열(직속)이고 1줄 안에는 없다.
  assert.ok(root.querySelector('.vt-wgrail-row > .vt-srow-state .vt-state-icon[data-state="waiting"].unseen'), '읽지 않음은 아이콘 링');
  const top = root.querySelector('.vt-srow-top');
  assert.strictEqual(top.querySelector('.vt-state-icon'), null);
  assert.strictEqual(top.querySelector('.vt-srow-name').textContent, 'api');
  assert.match(top.querySelector('.vt-srow-elapsed').textContent, /2분/, '경과는 1줄 오른쪽');
  assert.ok(top.querySelector('.vt-srow-menu'), '⋯는 1줄 끝');
  const l2 = root.querySelector('.vt-srow-line2');
  assert.strictEqual(l2.querySelector('.vt-srow-branch').textContent, 'main');
  assert.ok(l2.querySelector('.vt-srow-branch svg'), '브랜치 앞 git-branch 아이콘');
  assert.strictEqual(l2.querySelector('.vt-srow-wt'), null, '워크트리 아이콘 대신 작업 위치 텍스트');
  assert.ok(text(l2).includes('+10 −1'));
  assert.strictEqual(l2.querySelector('.vt-diff-add').textContent, '+10');
  assert.strictEqual(l2.querySelector('.vt-diff-del').textContent, '−1');
  const l3 = root.querySelector('.vt-srow-line3');
  assert.strictEqual(l3.querySelector('.vt-srow-kw').textContent, '승인 요청');
  assert.strictEqual(l3.querySelector('.vt-srow-since'), null, '경과는 3줄에 다시 나오지 않는다');
  assert.strictEqual(l3.querySelector('.vt-srow-origin').dataset.origin, 'external');
  // 만든 곳은 아이콘 + 짧은 글(시안 D02 3줄). 글이 읽히고 아이콘은 장식, 툴팁은 긴 문장.
  const origin = l3.querySelector('.vt-srow-origin');
  assert.strictEqual(origin.querySelector('.vt-srow-origin-text').textContent, '직접 만듦');
  assert.ok(origin.querySelector('.vt-srow-origin-ico[aria-hidden="true"] svg'));
  assert.strictEqual(origin.dataset.tip, '직접 만듦 (CLI·tmux)');
  assert.strictEqual(origin.getAttribute('role'), null, '글이 있으니 img 역할로 감싸지 않는다');
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
  assert.strictEqual(work.root.querySelector('.vt-srow-tool').textContent, '도구: Edit');
});

test('compact — 상태 아이콘이 행 직속에 있고 ⋯ 버튼은 없다, 우클릭은 된다', async () => {
  const { root, window, calls } = await mount({ status: 'working', agent: 'claude' }, { compact: true });
  assert.ok(root.querySelector('.vt-wgrail-row > .vt-srow-state .vt-state-icon[data-state="working"]'));
  assert.ok(root.querySelector('.vt-wgrail-row > .vt-wgrail-agent-compact'), '에이전트 마크는 칸 모서리(행 직속)');
  assert.strictEqual(root.querySelector('.vt-srow-main'), null, '이름·줄은 접힘에서 그리지 않는다');
  assert.strictEqual(root.querySelector('.vt-srow-menu'), null);
  root.querySelector('.vt-wgrail-row').dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(calls, ['contextmenu']);
});

test('compact 툴팁 — 이름, 둘째 줄은 브랜치 · 상태 · 다른 화면 · 공유', async () => {
  const { root } = await mount({
    status: 'waiting', kind: 'approval', branch: 'dev', otherScreens: 2,
    share: { mode: 'readwrite', nick: 'mina' },
  }, { compact: true });
  const el = root.querySelector('.vt-wgrail-row');
  assert.strictEqual(el.dataset.tip, 'api');
  const sub = el.dataset.tipSub;
  assert.match(sub, /^dev · 승인 요청/);
  assert.match(sub, /다른 화면 2/);
  assert.match(sub, /mina/);
});

test('개입 필요 행 — 답 버튼(선택지 + 터미널로), 누르면 onAnswer(key)이고 행은 안 열린다', async () => {
  const answered = [];
  const { root, window, calls } = await mount(
    { status: 'waiting', kind: 'approval', options: [{ key: '1', label: '예' }, { key: '3', label: '아니오' }] },
    { onAnswer: (key) => { answered.push(key); return Promise.resolve(); } },
  );
  const bar = root.querySelector('.vt-srow-main > .vt-srow-answers');
  assert.ok(bar, '답 버튼 줄은 줄 묶음의 맨 아래');
  const buttons = [...bar.querySelectorAll('button')].map((b) => text(b));
  assert.deepStrictEqual(buttons, ['1 · 예', '3 · 아니오', '터미널로']);
  assert.ok(bar.querySelector('.vt-srow-to-term.quiet svg'), '터미널로는 조용한 버튼 + 아이콘');
  bar.querySelectorAll('button')[1].dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(answered, ['3']);
  assert.deepStrictEqual(calls, [], '답 버튼 클릭은 행 열기로 올라가지 않는다');
  bar.querySelector('.vt-srow-to-term').dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepStrictEqual(calls, ['open'], '터미널로 = 그 세션 열기');
});

test('답 버튼은 응답이 오기 전까지 다시 못 누른다(같은 화면의 이중 클릭)', async () => {
  let release;
  const answered = [];
  const { root, window } = await mount(
    { status: 'waiting', options: [{ key: 'y', label: 'Yes' }] },
    { onAnswer: (key) => { answered.push(key); return new Promise((r) => { release = r; }); } },
  );
  const btn = root.querySelector('.vt-srow-answer');
  btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await Promise.resolve();
  assert.strictEqual(btn.disabled, true);
  btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(answered, ['y']);
  release();
  await new Promise((r) => setTimeout(r, 0));
  assert.strictEqual(btn.disabled, false);
});

test('답 버튼이 없는 경우 — 선택지가 없으면 터미널로만, 열리지 않은 행·접힘·onAnswer 없음·waiting 아님은 줄 자체가 없다', async () => {
  const onAnswer = () => Promise.resolve();
  const none = await mount({ status: 'waiting', options: [] }, { onAnswer });
  assert.deepStrictEqual([...none.root.querySelectorAll('.vt-srow-answers button')].map((b) => text(b)), ['터미널로']);
  const closed = await mount({ status: 'waiting', open: false, sessionId: '', options: [{ key: '1', label: '예' }] }, { onAnswer });
  assert.strictEqual(closed.root.querySelector('.vt-srow-answers'), null, '키를 보낼 열린 칸이 없다');
  const compact = await mount({ status: 'waiting', options: [{ key: '1', label: '예' }] }, { onAnswer, compact: true });
  assert.strictEqual(compact.root.querySelector('.vt-srow-answers'), null);
  const noHandler = await mount({ status: 'waiting', options: [{ key: '1', label: '예' }] });
  assert.strictEqual(noHandler.root.querySelector('.vt-srow-answers'), null);
  const working = await mount({ status: 'working', options: [{ key: '1', label: '예' }] }, { onAnswer });
  assert.strictEqual(working.root.querySelector('.vt-srow-answers'), null);
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

test('위임 하위 행 — 2줄 없이 위임 상태 + 「별도 터미널」, 부모 행은 「위임 보고」 토글', async () => {
  const kid = await mount({ status: 'done', branch: 'feat/x', lineage: { delegation: 'd', parent: 'p', state: 'reported', outcome: 'success', createdAt: 1, task: '', summary: '' } }, { kid: true });
  assert.strictEqual(kid.root.querySelector('.vt-srow-line2'), null);
  assert.ok(kid.root.querySelector('.vt-srow-line3 .vt-srow-lineage'));
  assert.strictEqual(kid.root.querySelector('.vt-srow-kidnote').textContent, '별도 터미널');
  const toggled = [];
  const parent = await mount({}, { kidsCount: 2, progress: { reported: 1, open: 2 }, onToggleKids: () => toggled.push(1) });
  const btn = parent.root.querySelector('.vt-srow-top .vt-srow-kids');
  assert.match(text(btn), /위임 보고 1\/2/);
  assert.ok(btn.querySelector('svg'));
  btn.dispatchEvent(new parent.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(toggled, [1]);
  assert.deepStrictEqual(parent.calls, [], '토글은 행을 열지 않는다');
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
  env.mountRow(root, { row, kidsCount: 1, progress: { reported: 0, open: 1 }, onToggleKids() {}, onOpen() {}, onContext() {} });
  assert.match(root.querySelector('.vt-srow-kids').textContent, /위임 보고 0\/1/);
  assert.match(root.querySelector('.vt-native-chip').textContent, /내부 1 작업 중/);
});

test('다른 맥에서 만든 행 — 3줄에 노트북 아이콘 + 「다른 맥에서 만듦」', async () => {
  const { root } = await mount({ origin: 'peer' });
  const origin = root.querySelector('.vt-srow-line3 .vt-srow-origin');
  assert.strictEqual(origin.dataset.origin, 'peer');
  assert.strictEqual(origin.querySelector('.vt-srow-origin-text').textContent, '다른 맥에서 만듦');
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
