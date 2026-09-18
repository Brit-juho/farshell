// 2.1 D5 — 서버에는 있었지만 어느 화면도 안 부르던 `DELETE /api/worktrees/{id}`를
// 레일 컨텍스트 메뉴에 처음 연결한다(shell/rail-fetch.ts의 deleteWorktreeRow).
// 계약: 확인 없이는 절대 안 지운다. 서버가 409(dirty)로 거절하면 한 번 더
// 물어보고, 승낙하면 force로 재시도한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });

async function load() {
  const built = await loadSolidBuild();
  _envs.push(built);
  return built;
}

function fakeDeps(env, { onDelete } = {}) {
  const calls = [];
  env.window.confirm = () => true;
  return {
    calls,
    vtFetch: async (path, opts) => {
      calls.push({ path, body: opts?.body ? JSON.parse(opts.body) : null });
      const result = onDelete ? onDelete(calls[calls.length - 1]) : { ok: true };
      if (result && result.throw) throw result.throw;
      return result;
    },
    getAction: () => undefined,
  };
}

test('확인을 취소하면 API를 아예 안 부른다', async () => {
  const env = await load();
  env.window.confirm = () => false;
  const calls = [];
  const deps = { vtFetch: async (path, opts) => { calls.push(path); return {}; }, getAction: () => undefined };
  const result = await env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(calls.length, 0, '확인 안 했는데 삭제 요청이 나가면 안 된다');
});

test('정상 삭제 — force 없이 한 번만 호출한다', async () => {
  const env = await load();
  const deps = fakeDeps(env);
  const result = await env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(deps.calls.length, 1);
  assert.strictEqual(deps.calls[0].path, '/api/worktrees/wt-1');
  assert.strictEqual(deps.calls[0].body.force, undefined);
});

test('더러움(409)이면 다시 물어보고, 승낙하면 force로 재시도한다', async () => {
  const env = await load();
  let attempt = 0;
  const deps = fakeDeps(env, {
    onDelete: () => {
      attempt += 1;
      if (attempt === 1) {
        const e = new Error('dirty'); e.status = 409; e.data = { error: '더러움', dirty: true };
        return { throw: e };
      }
      return { ok: true };
    },
  });
  const result = await env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(deps.calls.length, 2, '한 번 거절당하고 force로 다시 불러야 한다');
  assert.strictEqual(deps.calls[1].body.force, true);
});

test('409 재확인에서 취소하면 force 재시도를 안 한다', async () => {
  const env = await load();
  let confirmCount = 0;
  env.window.confirm = () => { confirmCount += 1; return confirmCount === 1; }; // 첫 확인만 승낙, 두 번째(더러움 경고)는 거절
  const deps = {
    calls: [],
    vtFetch: async function (path, opts) {
      this.calls.push(path);
      const e = new Error('dirty'); e.status = 409; e.data = { error: '더러움', dirty: true };
      throw e;
    },
    getAction: () => undefined,
  };
  const result = await env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(deps.calls.length, 1, '재확인을 거절했으면 force 재시도가 없어야 한다');
});

test('메인 워크트리가 아니어도 다른 이유로 실패(예: 500)하면 에러 메시지를 돌려준다', async () => {
  const env = await load();
  const deps = {
    vtFetch: async () => { const e = new Error('boom'); e.status = 500; e.data = { error: '서버 오류' }; throw e; },
    getAction: () => undefined,
  };
  env.window.confirm = () => true;
  const result = await env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.error, '서버 오류');
});

test('세션이 열려 있으면 확인 문구가 그 사실을 언급한다', async () => {
  const env = await load();
  let seenMsg = '';
  env.window.confirm = (msg) => { seenMsg = msg; return false; };
  const deps = { vtFetch: async () => ({}), getAction: () => undefined };
  await env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', true);
  assert.match(seenMsg, /세션/);
});

// ── ADR-29 E — 드래그로 그룹 재편성(setSessionGroup) ────────────────────────
// 확인 없이 바로 보낸다(D3/D4의 "되돌릴 수 없는 것만 확인받는다" 원칙과
// 같다 — 그룹은 다시 드래그하면 되돌릴 수 있는 조작이다).

test('setSessionGroup — 세션 이름과 그룹 id를 그대로 POST한다', async () => {
  const env = await load();
  const calls = [];
  const deps = { vtFetch: async (path, opts) => { calls.push({ path, opts }); return {}; }, getAction: () => undefined };
  const result = await env.setSessionGroup(deps, 'dev', 'repo-a-id');
  assert.strictEqual(result.ok, true);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].path, '/api/tmux/dev/group');
  assert.strictEqual(calls[0].opts.method, 'POST');
  assert.deepStrictEqual(JSON.parse(calls[0].opts.body), { groupId: 'repo-a-id' });
});

test('setSessionGroup — groupId null이면 그룹에서 뺀다(묶지 않음으로)', async () => {
  const env = await load();
  const deps = { vtFetch: async (path, opts) => JSON.parse(opts.body), getAction: () => undefined };
  const result = await env.setSessionGroup(deps, 'dev', null);
  assert.strictEqual(result.ok, true);
});

test('setSessionGroup — tmux 이름에 특수문자가 있어도 경로를 안전하게 이스케이프한다', async () => {
  const env = await load();
  const calls = [];
  const deps = { vtFetch: async (path) => { calls.push(path); return {}; }, getAction: () => undefined };
  await env.setSessionGroup(deps, 'my session/1', 'g1');
  assert.strictEqual(calls[0], '/api/tmux/my%20session%2F1/group');
});

test('setSessionGroup — 서버 실패는 에러 메시지로 떨어진다(호출부가 토스트로 보여준다)', async () => {
  const env = await load();
  const deps = {
    vtFetch: async () => { const e = new Error('boom'); e.data = { error: '그룹이 없습니다' }; throw e; },
    getAction: () => undefined,
  };
  const result = await env.setSessionGroup(deps, 'dev', 'g1');
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.error, '그룹이 없습니다');
});

// ── ADR-29 E — 그룹 이름 짓기(renameGroup) ──────────────────────────────────

test('renameGroup — 그룹 id로 PATCH하고 label을 그대로 보낸다', async () => {
  const env = await load();
  const calls = [];
  const deps = { vtFetch: async (path, opts) => { calls.push({ path, opts }); return { ok: true }; }, getAction: () => undefined };
  const result = await env.renameGroup(deps, 'repo-a-id', '내 프로젝트');
  assert.strictEqual(result.ok, true);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].path, '/api/groups/repo-a-id');
  assert.strictEqual(calls[0].opts.method, 'PATCH');
  assert.deepStrictEqual(JSON.parse(calls[0].opts.body), { label: '내 프로젝트' });
});

test('renameGroup — 서버 실패(예: 그룹 40개 상한)는 이유를 그대로 돌려준다', async () => {
  const env = await load();
  const deps = {
    vtFetch: async () => { const e = new Error('boom'); e.data = { reason: '그룹은 최대 200개까지 만들 수 있습니다' }; throw e; },
    getAction: () => undefined,
  };
  const result = await env.renameGroup(deps, 'repo-a-id', '새 이름');
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.error, '그룹은 최대 200개까지 만들 수 있습니다');
});
