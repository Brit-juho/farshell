// A5 — agent/state.js. 서버가 판정한 4상태를 프런트가 들고 있는 유일한 곳이라,
// "누가 이겨야 하는가"(같은 세션에 엔트리가 여럿일 때)와 "정렬 우선순위"가
// 서버(agent_status의 _URGENCY)와 어긋나지 않는지가 핵심이다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const STATE_JS = path.join(__dirname, '../js/agent/state.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const S = await importFresh(STATE_JS, env.context, new Map());
  return { window: env.window, document: env.window.document, S };
}

test('모르는 세션은 idle', async () => {
  const { S } = await load();
  assert.strictEqual(S.getStatus('nope'), 'idle');
  assert.strictEqual(S.getStatus(null), 'idle');
});

test('applySnapshot — tmux_session이 있는 엔트리만 색인한다', async () => {
  const { S } = await load();
  S.applySnapshot({
    h1: { status: 'working', tmux_session: 'dev' },
    h2: { status: 'done', tmux_session: 'api' },
    h3: { status: 'waiting' }, // 세션 특정 실패 — 무시돼야 한다
  });
  assert.strictEqual(S.getStatus('dev'), 'working');
  assert.strictEqual(S.getStatus('api'), 'done');
  assert.strictEqual(S.allStatuses().size, 2);
});

test('applySnapshot — 같은 세션에 엔트리가 여럿이면 더 급한 쪽이 이긴다', async () => {
  const { S } = await load();
  S.applySnapshot({
    hook: { status: 'working', tmux_session: 'dev' },
    pane: { status: 'waiting', tmux_session: 'dev' },
  });
  assert.strictEqual(S.getStatus('dev'), 'waiting');
});

test('applySnapshot — 스냅샷에 없어진 세션은 idle로 되돌아간다', async () => {
  const { S } = await load();
  S.applySnapshot({ h1: { status: 'working', tmux_session: 'dev' } });
  S.applySnapshot({});
  assert.strictEqual(S.getStatus('dev'), 'idle', '유령 상태가 남으면 안 된다');
});

test('applyEvent — 세션을 특정하지 못한 이벤트는 무시한다', async () => {
  const { S } = await load();
  let calls = 0;
  S.onStatusChange(() => calls++);
  S.applyEvent({ status: 'waiting' });          // tmux_session 없음
  S.applyEvent({ status: 'waiting', tmux_session: null });
  assert.strictEqual(calls, 0, '"모호하면 안 켠다" 규칙');
});

test('구독 — 값이 실제로 바뀔 때만 통지한다', async () => {
  const { S } = await load();
  let calls = 0;
  S.onStatusChange(() => calls++);
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  assert.strictEqual(calls, 1);
});

test('구독 해제', async () => {
  const { S } = await load();
  let calls = 0;
  const off = S.onStatusChange(() => calls++);
  off();
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  assert.strictEqual(calls, 0);
});

test('구독자 하나가 예외를 던져도 나머지는 갱신된다', async () => {
  const { S } = await load();
  let ok = 0;
  S.onStatusChange(() => { throw new Error('boom'); });
  S.onStatusChange(() => ok++);
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  assert.strictEqual(ok, 1);
});

test('ackLocal — done만 내린다(작업 중은 안 건드림)', async () => {
  const { S } = await load();
  S.applySnapshot({
    a: { status: 'done', tmux_session: 'dev' },
    b: { status: 'working', tmux_session: 'api' },
  });
  S.ackLocal('dev');
  S.ackLocal('api');
  assert.strictEqual(S.getStatus('dev'), 'idle');
  assert.strictEqual(S.getStatus('api'), 'working', '작업 중인 세션은 클릭해도 그대로');
});

test('sortByUrgency — waiting > done > working > idle', async () => {
  const { S } = await load();
  S.applySnapshot({
    a: { status: 'working', tmux_session: 'w' },
    b: { status: 'waiting', tmux_session: 'q' },
    c: { status: 'done', tmux_session: 'd' },
  });
  const names = ['w', 'i', 'q', 'd'];
  assert.deepEqual(S.sortByUrgency(names, (n) => n), ['q', 'd', 'w', 'i']);
});

test('URGENCY 순서가 서버(_URGENCY)와 같다', async () => {
  const { S } = await load();
  // 서버 agent_status._URGENCY = {waiting:0, done:1, working:2, error:3, idle:4}
  assert.deepEqual(S.URGENCY, { waiting: 0, done: 1, working: 2, error: 3, idle: 4 });
});

test('applyStatusDot — dot을 붙이고, 이후에는 재생성하지 않고 갱신만 한다', async () => {
  const { S, document } = await load();
  const row = document.createElement('div');
  const first = S.applyStatusDot(row, 'working');
  assert.strictEqual(row.querySelectorAll('.status-dot').length, 1);
  assert.strictEqual(first.dataset.state, 'working');

  const second = S.applyStatusDot(row, 'waiting');
  assert.strictEqual(second, first, 'dot을 새로 만들면 breathing 애니메이션이 매번 처음부터 다시 돈다');
  assert.strictEqual(first.dataset.state, 'waiting');
  assert.strictEqual(row.querySelectorAll('.status-dot').length, 1);
});

test('applyStatusDot — 접근성 라벨이 상태와 함께 바뀐다', async () => {
  const { S, document } = await load();
  const row = document.createElement('div');
  S.applyStatusDot(row, 'waiting');
  assert.strictEqual(row.querySelector('.status-dot').getAttribute('aria-label'), '입력 대기');
  S.applyStatusDot(row, 'done');
  assert.strictEqual(row.querySelector('.status-dot').getAttribute('aria-label'), '완료');
});
