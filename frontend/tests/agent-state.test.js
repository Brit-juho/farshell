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

test('URGENCY 순서가 서버(_URGENCY)와 같다 — 서버 파일을 직접 읽어 대조한다', async () => {
  const { S } = await load();
  const src = require('node:fs').readFileSync(path.join(__dirname, '../../server/agent_status.py'), 'utf8');
  const body = /^_URGENCY = \{(.*)\}/m.exec(src)[1];
  const server = {};
  for (const m of body.matchAll(/(\w+):\s*(\d+)/g)) server[m[1].toLowerCase()] = Number(m[2]);
  // error는 프런트 UI에서 뺐다 — 남은 4상태의 상대 순서만 같으면 된다.
  const order = (o) => Object.keys(S.URGENCY).sort((a, b) => o[a] - o[b]);
  assert.deepEqual(order(S.URGENCY), order(server));
  assert.ok(!('error' in S.URGENCY));
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

// ── L8 §8-b: 앱 아이콘 배지(Badging API) ──────────────────────────────────
// paint.js는 DOM/스토어 배선이 많아 통째로 로드하기 무거우므로, 배지 규칙만
// 같은 계산으로 검증한다: **waiting만 센다**(working은 기다리면 끝나고 done은
// 이미 푸시가 나간다 — 배지는 "지금 나를 부르고 있다"만 의미해야 한다).
test('배지 계산 — waiting 세션만 센다', async () => {
  const { S } = await load();
  S.applySnapshot({
    a: { status: 'waiting', tmux_session: 'q1' },
    b: { status: 'waiting', tmux_session: 'q2' },
    c: { status: 'working', tmux_session: 'w' },
    d: { status: 'done', tmux_session: 'd' },
  });
  const waiting = [...S.allStatuses().values()].filter((v) => v === 'waiting').length;
  assert.strictEqual(waiting, 2);
});

// ── N37 「읽지 않음」(unseen) ─────────────────────────────────────────────
// 정의(10-shell-layout.md §4): 상태가 done인데 **이 기기에서** 그 세션이 아직
// 눈에 띈 적이 없음. done은 서버가 정하고 unseen은 브라우저가 정한다 — 폰에서
// 확인한 완료가 맥에서도 읽음이 되면 안 되기 때문이다.

test('unseen — done으로 들어오면 읽지 않음이 된다', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  assert.strictEqual(S.isUnseen('dev'), true);
  assert.strictEqual(S.unseenCount(), 1);
});

test('unseen — 어떤 상태로든 바뀐 뒤 아직 안 봤으면 읽지 않음이다(작업 중·입력 대기 포함)', async () => {
  const { S } = await load();
  for (const status of ['working', 'waiting', 'done']) {
    S.markSeen('dev');
    S.applyEvent({ status, tmux_session: 'dev' });
    assert.strictEqual(S.isUnseen('dev'), true, `${status}로 바뀐 걸 안 봤다`);
  }
});

test('unseen — 작업 중 → 입력 대기 같은 후속 전이도 새로 읽지 않음을 만든다', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  S.markSeen('dev');
  assert.strictEqual(S.isUnseen('dev'), false);
  S.applyEvent({ status: 'waiting', tmux_session: 'dev' });
  assert.strictEqual(S.isUnseen('dev'), true);
});

test('unseen — 보고 있는 세션(probe)의 전이는 읽지 않음을 만들지 않는다, 비활성 세션만 만든다', async () => {
  const { S } = await load();
  S.setSeenProbe((name) => name === 'front');
  S.applyEvent({ status: 'working', tmux_session: 'front' });
  S.applyEvent({ status: 'waiting', tmux_session: 'front' });
  S.applyEvent({ status: 'working', tmux_session: 'back' });
  S.applyEvent({ status: 'waiting', tmux_session: 'back' });
  assert.strictEqual(S.isUnseen('front'), false);
  assert.strictEqual(S.isUnseen('back'), true);
  // 보고 있던 세션이 비활성이 된 뒤의 전이는 만든다.
  S.setSeenProbe(() => false);
  S.applyEvent({ status: 'done', tmux_session: 'front' });
  assert.strictEqual(S.isUnseen('front'), true);
});

test('unseen — 첫 스냅샷의 작업 중·입력 대기는 "방금 바뀐 것"이 아니다(done만 예외)', async () => {
  const { S } = await load();
  S.applySnapshot({
    a: { status: 'working', tmux_session: 'w' },
    b: { status: 'waiting', tmux_session: 'q' },
    c: { status: 'done', tmux_session: 'd' },
  });
  assert.strictEqual(S.isUnseen('w'), false);
  assert.strictEqual(S.isUnseen('q'), false);
  assert.strictEqual(S.isUnseen('d'), true);
  // 이후의 전이는 평소대로.
  S.applySnapshot({ a: { status: 'waiting', tmux_session: 'w' }, b: { status: 'waiting', tmux_session: 'q' }, c: { status: 'done', tmux_session: 'd' } });
  assert.strictEqual(S.isUnseen('w'), true);
  assert.strictEqual(S.isUnseen('q'), false, '변하지 않은 세션은 그대로');
});

test('error·알 수 없는 상태 — idle로 접힌다(깨지지 않는다)', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'error', tmux_session: 'dev' });
  assert.strictEqual(S.getStatus('dev'), 'idle');
  S.applySnapshot({ a: { status: 'bogus', tmux_session: 'x' }, b: { tmux_session: 'y' } });
  assert.strictEqual(S.getStatus('x'), 'idle');
  assert.strictEqual(S.getStatus('y'), 'idle');
  // 같은 세션에서 알 수 없는 값은 진짜 상태를 이기지 못한다.
  S.applySnapshot({ a: { status: 'error', tmux_session: 'z' }, b: { status: 'working', tmux_session: 'z' } });
  assert.strictEqual(S.getStatus('z'), 'working');
});

test('unseen — markSeen하면 배지는 사라지지만 done 상태는 그대로다', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  S.markSeen('dev');
  assert.strictEqual(S.isUnseen('dev'), false);
  // "봤다"와 "완료를 치웠다"는 다른 사건이다 — 상태는 ackLocal만 내린다.
  assert.strictEqual(S.getStatus('dev'), 'done');
});

test('unseen — 이미 읽은 done에 같은 스냅샷이 다시 와도 되살아나지 않는다', async () => {
  const { S } = await load();
  S.applySnapshot({ h1: { status: 'done', tmux_session: 'dev' } });
  S.markSeen('dev');
  // 폴링으로 같은 스냅샷이 반복해서 도착하는 상황
  S.applySnapshot({ h1: { status: 'done', tmux_session: 'dev' } });
  S.applySnapshot({ h1: { status: 'done', tmux_session: 'dev' } });
  assert.strictEqual(S.isUnseen('dev'), false, '읽음 처리가 폴링에 덮여 되살아나면 안 된다');
});

test('unseen — done을 벗어나도 그 전이를 안 봤으면 읽지 않음은 남는다', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  S.markSeen('dev');
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  assert.strictEqual(S.isUnseen('dev'), true, '작업 중으로 바뀐 걸 아직 안 봤다');
  S.markSeen('dev');
  assert.strictEqual(S.isUnseen('dev'), false);
});

test('unseen — done → 다른 상태 → 다시 done이면 새 완료라 또 읽지 않음', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  S.markSeen('dev');
  S.applyEvent({ status: 'working', tmux_session: 'dev' });
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  assert.strictEqual(S.isUnseen('dev'), true, '다음 작업의 완료는 따로 알려야 한다');
});

test('unseen — 스냅샷에서 사라진 세션은 읽지 않음도 함께 사라진다', async () => {
  const { S } = await load();
  S.applySnapshot({ h1: { status: 'done', tmux_session: 'dev' } });
  assert.strictEqual(S.isUnseen('dev'), true);
  S.applySnapshot({});   // 서버 TTL 정리 또는 세션 닫힘
  assert.strictEqual(S.isUnseen('dev'), false);
  assert.strictEqual(S.unseenCount(), 0);
});

test('unseen — ackLocal은 읽지 않음도 함께 지운다', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  S.ackLocal('dev');
  assert.strictEqual(S.isUnseen('dev'), false);
  assert.strictEqual(S.getStatus('dev'), 'idle');
});

test('unseen — markSeen은 구독자에게 알린다(배지가 즉시 사라져야 하므로)', async () => {
  const { S } = await load();
  S.applyEvent({ status: 'done', tmux_session: 'dev' });
  let calls = 0;
  S.onStatusChange(() => { calls++; });
  S.markSeen('dev');
  assert.strictEqual(calls, 1);
  // 이미 읽은 걸 또 읽어도 헛 알림은 없다
  S.markSeen('dev');
  assert.strictEqual(calls, 1);
});

// 2026-09-30 — 레일·플릿이 4초 폴링 대신 이 저장소에서 세부·이름을 읽는다.
// vm 컨텍스트의 객체는 프로토타입이 달라 deepStrictEqual이 어긋난다 — 평범한 값으로 바꿔 비교.
const plain = (o) => JSON.parse(JSON.stringify(o));
test('세부 — 스냅샷의 도구·경과·질문을 세션 이름으로 접는다(더 급한 엔트리 기준)', async () => {
  const { S } = await load();
  S.applySnapshot({
    hook: { status: 'working', tmux_session: 'dev', tool: 'Bash', since: 10, host: 'local', session_id: 'hook' },
    pane: { status: 'waiting', tmux_session: 'dev', question: '진행할까요?', options: [{ key: 'y', label: '예' }],
      prompt_id: 'p1', since: 20, host: 'local', session_id: 'pane' },
  });
  assert.deepStrictEqual(plain(S.agentDetails().dev), {
    since: 20, tool: null, question: '진행할까요?', options: [{ key: 'y', label: '예' }], promptId: 'p1', kind: null, badge: null,
  });
});

test('세부 — 상태는 그대로여도 도구가 바뀌면 통지한다(상태 문장이 바뀌므로)', async () => {
  const { S } = await load();
  const base = { status: 'working', tmux_session: 'dev', host: 'local', session_id: 's1', since: 1 };
  S.applySnapshot({ s1: { ...base, tool: 'Read' } });
  let n = 0;
  S.onStatusChange(() => { n += 1; });
  S.applyEvent({ ...base, tool: 'Bash' });
  assert.strictEqual(n, 1);
  assert.strictEqual(S.agentDetails().dev.tool, 'Bash');
  S.applyEvent({ ...base, tool: 'Bash' });
  assert.strictEqual(n, 1, '같은 값이면 통지하지 않는다');
});

test('세부 — 이벤트는 스냅샷의 같은 엔트리를 갱신한다(키가 달라도 host/session_id로 맞춘다)', async () => {
  const { S } = await load();
  S.applySnapshot({ opaque: { status: 'working', tmux_session: 'dev', tool: 'Read', host: 'local', session_id: 's1' } });
  S.applyEvent({ status: 'working', tmux_session: 'dev', tool: 'Edit', host: 'local', session_id: 's1' });
  assert.strictEqual(S.agentDetails().dev.tool, 'Edit');
  S.applySnapshot({});
  assert.deepStrictEqual(plain(S.agentDetails()), {}, '스냅샷에서 사라지면 세부도 사라진다');
});

test('이름 — 바뀔 때만 통지하고, 순서가 달라도 같은 값이면 조용하다', async () => {
  const { S } = await load();
  let n = 0;
  S.onStatusChange(() => { n += 1; });
  S.applyAgentNames({ dev: { agent: 'claude', label: 'Claude' }, api: { agent: 'shell' }, x: null });
  assert.deepStrictEqual(plain(S.agentNames()), { dev: 'claude', api: 'shell' });
  S.applyAgentNames({ api: { agent: 'shell' }, dev: { agent: 'claude' } });
  assert.strictEqual(n, 1);
  S.applyAgentNames({ dev: { agent: 'codex' } });
  assert.strictEqual(n, 2);
  assert.deepStrictEqual(plain(S.agentNames()), { dev: 'codex' });
});

test('window 브리지 — 지연 청크(레일·플릿)가 읽고 쓰는 길', async () => {
  const { window, S } = await load();
  window.vtApplyAgentNames({ dev: { agent: 'claude' } });
  assert.deepStrictEqual(plain(window.agentNames()), { dev: 'claude' });
  window.vtApplyAgentSnapshot({ a: { status: 'done', tmux_session: 'dev', since: 5 } });
  assert.strictEqual(window.agentDetails().dev.since, 5);
  assert.strictEqual(S.getStatus('dev'), 'done');
});

// 2026-10-13 — 이벤트가 엔트리 하나로 세션 상태를 덮어써 스냅샷과
// 깜빡이던 문제, 반납(released) 엔트리 제거.
test('applyEvent — 스냅샷과 같은 규칙으로 다시 계산한다(엔트리 하나로 덮어쓰지 않는다)', async () => {
  const { S } = await load();
  S.applySnapshot({
    a: { status: 'waiting', tmux_session: 'dev', session_id: 'a', host: 'local' },
    b: { status: 'working', tmux_session: 'dev', session_id: 'b', host: 'local' },
  });
  S.applyEvent({ status: 'working', tmux_session: 'dev', session_id: 'b', host: 'local' });
  assert.strictEqual(S.getStatus('dev'), 'waiting', '다른 엔트리의 대기가 남아 있으면 그대로');
});

test('applyEvent — released는 그 엔트리를 지우고 세션 상태를 다시 계산한다', async () => {
  const { S } = await load();
  S.applySnapshot({
    old: { status: 'done', tmux_session: 'dev', session_id: 'old', host: 'local' },
    cur: { status: 'working', tmux_session: 'dev', session_id: 'cur', host: 'local' },
  });
  assert.strictEqual(S.getStatus('dev'), 'done');
  S.applyEvent({ session_id: 'old', host: 'local', tmux_session: 'dev', status: 'idle', released: true });
  assert.strictEqual(S.getStatus('dev'), 'working');
  S.applyEvent({ session_id: 'cur', host: 'local', tmux_session: 'dev', status: 'idle', released: true });
  assert.strictEqual(S.getStatus('dev'), 'idle');
  assert.strictEqual(S.allStatuses().size, 0);
});

test('applyEvent — 모르는 엔트리의 released는 아무것도 하지 않는다', async () => {
  const { S } = await load();
  S.applySnapshot({ a: { status: 'working', tmux_session: 'dev', session_id: 'a', host: 'local' } });
  let calls = 0;
  S.onStatusChange(() => calls++);
  calls = 0;
  S.applyEvent({ session_id: 'zz', host: 'local', tmux_session: 'dev', status: 'idle', released: true });
  assert.strictEqual(calls, 0);
  assert.strictEqual(S.getStatus('dev'), 'working');
});

test('applyEvent — 엔트리가 다른 세션으로 옮겨 가면 옛 세션도 다시 계산된다', async () => {
  const { S } = await load();
  S.applySnapshot({ a: { status: 'working', tmux_session: 'one', session_id: 'a', host: 'local' } });
  S.applyEvent({ status: 'working', tmux_session: 'two', session_id: 'a', host: 'local' });
  assert.strictEqual(S.getStatus('one'), 'idle');
  assert.strictEqual(S.getStatus('two'), 'working');
});

test('피어 호스트의 같은 이름 엔트리는 로컬 세션 상태에 섞이지 않는다', async () => {
  const { S } = await load();
  S.applySnapshot({
    a: { status: 'working', tmux_session: 'server-2', session_id: 'a', host: 'local', tool: 'Bash' },
    'm1@peer:server-2': { status: 'done', tmux_session: 'server-2', session_id: 'peer:server-2', host: 'm1' },
    'm1@peer:only': { status: 'waiting', tmux_session: 'only', session_id: 'peer:only', host: 'm1' },
  });
  assert.strictEqual(S.getStatus('server-2'), 'working');
  assert.strictEqual(S.agentDetails()['server-2'].tool, 'Bash');
  assert.strictEqual(S.getStatus('only'), 'idle');
  // 로컬 엔트리가 반납되면 피어의 done이 남지 않고 idle(터미널)로 돌아간다.
  S.applyEvent({ session_id: 'a', host: 'local', tmux_session: 'server-2', status: 'idle', released: true });
  assert.strictEqual(S.getStatus('server-2'), 'idle');
  assert.strictEqual(S.agentDetails()['server-2'], undefined);
});
