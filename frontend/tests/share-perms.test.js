// 에이전트 세션 공유 — 권한 패널(share/perms.js): 목록·마지막 사용·철회 호출·최근 기록.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor } = require('./helpers/share-env');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });

const NOW = Math.floor(Date.now() / 1000);
const grant = (id, agent, nick, { read = true, write = false, mode = 'readwrite' } = {}) => ({
  id, read, write, effective: { read, write: write && mode === 'readwrite' }, granted: NOW - 9000, updated: NOW - 9000,
  agent: { name: agent, id: `$${agent}` }, target: { nick, mode, name: nick, id: '$t' },
});
const audit = [
  { ts: NOW - 120, action: 'read', result: 'ok', by: 'agent', agent: { name: 'ag1', id: '$ag1' }, nick: 'build' },
  { ts: NOW - 400, action: 'decide', result: 'allowed', by: 'user', nick: 'build' },
];

async function open(extra = {}, grants = [grant('g1', 'ag1', 'build', { write: true }), grant('g2', 'ag2', 'docs')]) {
  const t = await loadShare({
    'GET /api/agent/share/grants': () => ({ json: { grants } }),
    'GET /api/agent/share/audit': () => ({ json: { entries: audit } }),
    'GET /api/agent/share/requests': () => ({ json: { requests: [] } }),
    ...extra,
  });
  _wins.push(t.window);
  t.mod.openPermsPanel();
  const dlg = await waitFor(() => t.document.querySelector('.vt-dialog'));
  await waitFor(() => dlg.querySelector('.vt-share-grant'));
  return { ...t, dlg };
}

test('목록: 에이전트 이름·대상 닉네임·수준·마지막 사용이 한 행에 보인다', async () => {
  const t = await open();
  const rows = [...t.dlg.querySelectorAll('.vt-share-grant')];
  assert.strictEqual(rows.length, 2);
  assert.ok(rows[0].textContent.includes('ag1') && rows[0].textContent.includes('build'));
  assert.match(rows[0].querySelector('.vt-share-grant-meta').textContent, /읽기·쓰기 · 공유 읽기·쓰기 · 마지막 사용 2분 전/);
  assert.match(rows[1].querySelector('.vt-share-grant-meta').textContent, /^읽기 · .* 마지막 사용 없음/);
  assert.ok(!rows[1].querySelector('[data-scope="write"]'), '쓰기 권한이 없으면 「쓰기만 철회」도 없다');
  assert.ok(rows[0].querySelector('[data-scope="write"]') && rows[0].querySelector('[data-scope="all"]'));
});

test('최근 기록이 몇 줄 보이고 입력 원문은 남지 않는다는 안내가 있다', async () => {
  const t = await open();
  const lines = [...t.dlg.querySelectorAll('.vt-share-audit li')].map((l) => l.textContent);
  assert.strictEqual(lines.length, 2);
  assert.match(lines[0], /ag1 · 화면 읽음 · build · 성공/);
  assert.match(lines[1], /사용자 · 결정 · build · 허용/);
  assert.match(t.dlg.textContent, /입력한 글 자체는 남지 않습니다/);
});

test('철회: 전체는 scope=all, 쓰기만은 scope=write 로 DELETE 하고 목록을 다시 받는다', async () => {
  let n = 0;
  const t = await open({
    'DELETE /api/agent/share/grants/g1': () => ({ json: { ok: true, grant: {} } }),
    'GET /api/agent/share/grants': () => ({ json: { grants: n++ === 0 ? [grant('g1', 'ag1', 'build', { write: true })] : [grant('g1', 'ag1', 'build')] } }),
  });
  t.dlg.querySelector('[data-scope="write"]').click();
  await waitFor(() => t.calls.some((c) => c.method === 'DELETE'));
  const del = t.calls.find((c) => c.method === 'DELETE');
  assert.ok(del.url.includes('/api/agent/share/grants/g1?scope=write'), del.url);
  await waitFor(() => !t.dlg.querySelector('[data-scope="write"]'));
  assert.match(t.toasts[0][0], /쓰기 권한을 철회/);
  t.dlg.querySelector('[data-scope="all"]').click();
  await waitFor(() => t.calls.filter((c) => c.method === 'DELETE').length === 2);
  assert.ok(t.calls.filter((c) => c.method === 'DELETE')[1].url.includes('scope=all'));
});

test('공유가 읽기 전용으로 낮아져 쓰기가 멈춘 권한은 그렇게 말한다', async () => {
  const t = await open({}, [grant('g1', 'ag1', 'build', { write: true, mode: 'read' })]);
  assert.match(t.dlg.querySelector('.vt-share-grant-meta').textContent, /쓰기는 공유가 읽기 전용이라 멈춤/);
});

test('권한이 없으면 빈 안내, 대기 요청이 있으면 「승인 대기 N건」과 보기 버튼(모달로 연결)', async () => {
  const t = await loadShare({
    'GET /api/agent/share/grants': () => ({ json: { grants: [] } }),
    'GET /api/agent/share/audit': () => ({ json: { entries: [] } }),
    'GET /api/agent/share/requests': () => ({ json: { requests: [{ id: 'p', kind: 'read', state: 'pending', created: 1, agent: { name: 'a', id: '$a' }, target: { nick: 'n', mode: 'read', name: 'n', id: '$t' } }] } }),
  });
  _wins.push(t.window);
  t.mod.openPermsPanel();
  const dlg = await waitFor(() => t.document.querySelector('.vt-dialog'));
  await waitFor(() => dlg.querySelector('.vt-share-pending'));
  assert.match(dlg.textContent, /허용된 권한이 없습니다/);
  assert.match(dlg.querySelector('.vt-share-pending').textContent, /승인 대기 1건/);
  // 패널이 대기 목록을 모달 큐에도 맞춘다 — 처음 보는 요청이라 모달이 곧바로 뜬다
  await waitFor(() => t.document.querySelector('.vt-share-approval'));
});

test('서버 오류(403 등)는 이유를 보인다', async () => {
  const t = await loadShare({
    'GET /api/agent/share/grants': () => ({ status: 403, json: { error: 'human_session_required', reason: 'x' } }),
    'GET /api/agent/share/audit': () => ({ status: 403, json: { error: 'human_session_required' } }),
    'GET /api/agent/share/requests': () => ({ status: 403, json: { error: 'human_session_required' } }),
  });
  _wins.push(t.window);
  t.mod.openPermsPanel();
  const dlg = await waitFor(() => t.document.querySelector('.vt-dialog'));
  await waitFor(() => /로그인한 브라우저/.test(dlg.textContent));
});
