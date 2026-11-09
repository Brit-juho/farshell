// 리디자인 6단계(시안 O15) — 「작업 기록」 창: 세션 기록(왼쪽) + 내부 서브에이전트 · 위임 세션 기록(오른쪽) 한 창.
// history/session-record.js의 openSessionRecord / openWorkLog를 진짜 대화상자 뼈대(core/dialog.js · panels/panel.js) 위에서 연다.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

const NOW = Math.floor(Date.now() / 1000);
const PARENT = { id: 'S1', title: '<b>api-server</b>', revision: 3, archived: false, tmux_name: 'api', ended: null };
const CHILD = { id: 'S2', title: 'docs-link-check', revision: 1, archived: false, tmux_name: 'docs-link', ended: null };

async function setup({ subagents = null, relations = [], running = true, remote = false } = {}) {
  const env = createDomEnv('<!doctype html><body></body>');
  doms.push(env.dom);
  const w = env.window;
  const calls = [];
  w.agentDetails = () => (subagents ? { api: { status: 'working', subagents } } : {});
  w.fetch = async (url, options = {}) => {
    const u = String(url);
    calls.push({ url: u, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
    let data = {};
    if (u.startsWith('/api/session-catalog?')) data = { sessions: [PARENT, CHILD] };
    else if (u === '/api/session-catalog/sessions/S1') {
      data = { session: PARENT, runtimes: [{ id: 'r1', name: 'api', started: NOW - 600, ended: running ? null : NOW - 60, state: running ? 'running' : 'stopped' }],
        resumable: { ok: !running, reason: running ? 'running' : null },
        conversations: [{ id: 'c1', title: '리프레시 토큰', native_id: 'n1', provider: 'claude', updated: NOW - 30 }], relations };
    } else if (u === '/api/session-catalog/sessions/S2') {
      data = { session: CHILD, runtimes: [], resumable: { ok: false, reason: 'no_conversation' }, relations: [], conversations: [{ id: 'c2', title: 'links', native_id: 'n2', provider: 'claude', updated: NOW }] };
    } else if (u.startsWith('/api/session-history/conversations/c2')) {
      data = { messages: [{ role: 'user', body: 'docs/ 링크 확인' }, { role: 'tool_call', body: '<img src=x onerror=alert(1)>' }, { role: 'assistant', body: '깨진 링크 2개' }], next: null };
    }
    return { ok: true, status: 200, json: async () => data };
  };
  const mod = await importFresh(path.join(__dirname, '../js/history/session-record.js'), env.context, new Map());
  // 모듈 그래프(core/api.js 등)가 window.vtFetch·vtRelayHost를 제 것으로 건다 — 불러온 뒤에 스텁을 얹는다.
  w.vtRelayHost = () => (remote ? 'peer' : 'local');
  w.vtFetch = async (p) => {
    calls.push({ url: String(p), method: 'GET' });
    return { parent_id: 'P', state: 'working', next_cursor: null, label: 'test-writer', items: [
      { id: 'i1', kind: 'user', text: '만료 경로 테스트를 써 줘' },
      { id: 'i2', kind: 'tool_call', tool: 'Bash', text: '<b>npm test</b>' },
    ] };
  };
  return { w, doc: w.document, mod, calls };
}

const SUBS = { parent_id: 'P', items: [
  { id: 'old', label: 'callsite-scan', state: 'done', started_at: NOW - 500 },
  { id: 'new', label: 'test-writer', state: 'working', started_at: NOW - 100 },
] };

test('행 메뉴 「작업 기록」 — 왼쪽은 세션(상태·동작·연결된 대화·내부 서브에이전트·실행 이력), 오른쪽은 가장 최근 서브에이전트', async () => {
  const { doc, mod, calls } = await setup({ subagents: SUBS });
  mod.openSessionRecord('S1', () => {}, { tmuxName: 'api' });
  await flush();
  const root = doc.getElementById('vt-session-record');
  assert.ok(root.classList.contains('vt-worklog'));
  const left = root.querySelector('.vt-wl-left');
  assert.match(left.textContent, /연결된 대화/);
  assert.match(left.textContent, /내부 서브에이전트/);
  assert.match(left.textContent, /실행 이력/);
  assert.equal(root.querySelector('.vt-wl-head-name').textContent, '<b>api-server</b>');
  assert.equal(root.querySelector('.vt-viewer-head b b'), null, '세션 제목은 글자로만');
  // 실행 중: 「이어서 시작」은 비활성, 「실행 종료」는 위험 테두리
  const btn = (label) => [...left.querySelectorAll('.vt-wl-acts button')].find((b) => b.textContent.trim() === label);
  assert.equal(btn('이어서 시작').disabled, true);
  assert.ok(btn('실행 종료').classList.contains('danger'));
  assert.equal(btn('실행 종료').disabled, false);
  // 오른쪽: 가장 최근(started_at) 서브에이전트 test-writer가 골라져 기록이 붙는다
  assert.equal(root.querySelector('.vt-wl-rhead-name').textContent, 'test-writer');
  assert.ok(calls.some((c) => c.url === '/api/agents/api/subagents/new/transcript'));
  const entries = [...root.querySelectorAll('.vt-wl-list .vt-wl-entry')];
  assert.equal(entries.length, 2);
  assert.equal(root.querySelector('.vt-wl-list b'), null, '기록 본문은 마크업으로 해석되지 않는다');
  assert.equal(entries[1].querySelector('.vt-wl-term').textContent, '<b>npm test</b>');
  assert.equal(left.querySelector('[data-wl-key="sub:new"]').getAttribute('aria-pressed'), 'true');
  // 종류 거르기
  [...root.querySelectorAll('.vt-wl-seg-btn')].find((b) => b.dataset.filter === 'tools').click();
  assert.equal(root.querySelector('.vt-wl-list').dataset.filter, 'tools');
});

test('하위 행 「기록 보기」(openWorkLog) — tmux 이름으로 세션을 찾고 그 서브에이전트를 오른쪽에 고른다. 예전 서브에이전트 창은 없다', async () => {
  const { doc, mod, calls } = await setup({ subagents: SUBS });
  mod.openWorkLog({ tmuxName: 'api', select: { kind: 'subagent', id: 'old' } });
  await flush();
  assert.ok(calls.some((c) => c.url.startsWith('/api/session-catalog?')), '카탈로그 목록으로 tmux 이름 → 세션');
  assert.ok(calls.some((c) => c.url === '/api/session-catalog/sessions/S1'));
  assert.ok(calls.some((c) => c.url === '/api/agents/api/subagents/old/transcript'));
  assert.equal(doc.getElementById('vt-subagent'), null);
  assert.equal(doc.querySelector('#vt-session-record [data-wl-key="sub:old"]').classList.contains('on'), true);
});

test('「보고 보기」 — 부모 기록을 열고 오른쪽에 위임 세션 대화를 보이며 보고를 맨 위에 붙인다', async () => {
  const relations = [{ id: 'R', kind: 'delegation', parent_id: 'S1', child_id: 'S2', source_id: 'D1', detail: JSON.stringify({ state: 'reported', outcome: 'succeeded' }) }];
  const { doc, mod } = await setup({ relations });
  mod.openWorkLog({ tmuxName: 'api', select: { kind: 'delegate', tmuxName: 'docs-link', delegation: 'D1', name: 'docs-link-check', summary: '깨진 링크는 2개입니다' } });
  await flush();
  const root = doc.getElementById('vt-session-record');
  assert.equal(root.querySelector('.vt-wl-rhead-name').textContent, 'docs-link-check');
  assert.equal(root.querySelector('.vt-wl-chip').textContent, '보고 · 성공');
  const kinds = [...root.querySelectorAll('.vt-wl-entry')].map((e) => e.dataset.kind);
  assert.deepEqual(kinds, ['assistant', 'user', 'tool_call', 'assistant']);
  assert.match(root.querySelector('.vt-wl-entry').textContent, /깨진 링크는 2개입니다/);
  assert.equal(root.querySelector('.vt-wl-list img'), null);
  assert.match(root.querySelector('.vt-wl-left').textContent, /보고 1\/1/);
  assert.equal(root.querySelector('[data-wl-key="del:S2"]').classList.contains('on'), true);
});

test('종료된 세션은 「이어서 시작」이 채운 주 버튼이고 「실행 종료」는 비활성, 고를 기록이 없으면 안내만', async () => {
  const { doc, mod } = await setup({ running: false });
  mod.openSessionRecord('S1');
  await flush();
  const left = doc.querySelector('#vt-session-record .vt-wl-left');
  const btn = (label) => [...left.querySelectorAll('.vt-wl-acts button')].find((b) => b.textContent.trim() === label);
  assert.ok(btn('이어서 시작').classList.contains('solid'));
  assert.equal(btn('실행 종료').disabled, true);
  assert.equal(doc.querySelectorAll('#vt-session-record .vt-btn.solid').length, 1, '채운 버튼은 하나뿐');
  assert.match(doc.querySelector('#vt-session-record .vt-wl-empty').textContent, /왼쪽에서/);
});

test('다른 맥을 보는 중에는 카탈로그를 묻지 않고 내부 서브에이전트만 보인다', async () => {
  const { doc, mod, calls } = await setup({ remote: true });
  mod.openWorkLog({ tmuxName: 'api' });
  await flush();
  assert.equal(calls.filter((c) => c.url.startsWith('/api/session-catalog')).length, 0);
  assert.match(doc.querySelector('#vt-session-record .vt-wl-left').textContent, /이 맥을 직접 볼 때만/);
});
