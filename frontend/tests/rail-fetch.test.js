// 2.1 D5 — 서버에는 있었지만 어느 화면도 안 부르던 `DELETE /api/worktrees/{id}`를
// 레일 컨텍스트 메뉴에 처음 연결한다(shell/rail-fetch.ts의 deleteWorktreeRow).
// 계약: 확인 없이는 절대 안 지운다. 서버가 409(dirty)로 거절하면 한 번 더
// 물어보고, 승낙하면 force로 재시도한다.
//
// 2026-09-22 — confirmDialog(core/dialog.js)가 window.confirm을 대체하면서
// 목(mock)을 걷어냈다. 이제 openPanel() 기반 실제 카드가 뜨므로
// helpers/dialog-stub.js로 열린 카드의 진짜 버튼을 눌러 응답한다 —
// deleteWorktreeRow(...) 호출과 응답을 병렬로 걸고(먼저 시작만 해두고),
// 카드가 뜬 뒤 answerConfirm()으로 누른 다음 결과를 기다린다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');
const { waitForDialog, answerConfirm } = require('./helpers/dialog-stub');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });

async function load() {
  const built = await loadSolidBuild();
  _envs.push(built);
  return built;
}

function fakeDeps({ onDelete } = {}) {
  const calls = [];
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
  const calls = [];
  const deps = { vtFetch: async (path, opts) => { calls.push(path); return {}; }, getAction: () => undefined };
  const resultPromise = env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  await answerConfirm(env.document, { accept: false });
  const result = await resultPromise;
  assert.strictEqual(result.ok, false);
  assert.strictEqual(calls.length, 0, '확인 안 했는데 삭제 요청이 나가면 안 된다');
});

test('정상 삭제 — force 없이 한 번만 호출한다', async () => {
  const env = await load();
  const deps = fakeDeps();
  const resultPromise = env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  await answerConfirm(env.document, { accept: true });
  const result = await resultPromise;
  assert.strictEqual(result.ok, true);
  assert.strictEqual(deps.calls.length, 1);
  assert.strictEqual(deps.calls[0].path, '/api/worktrees/wt-1');
  assert.strictEqual(deps.calls[0].body.force, undefined);
});

test('더러움(409)이면 다시 물어보고, 승낙하면 force로 재시도한다', async () => {
  const env = await load();
  let attempt = 0;
  const deps = fakeDeps({
    onDelete: () => {
      attempt += 1;
      if (attempt === 1) {
        const e = new Error('dirty'); e.status = 409; e.data = { error: '더러움', dirty: true };
        return { throw: e };
      }
      return { ok: true };
    },
  });
  const resultPromise = env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  await answerConfirm(env.document, { accept: true }); // 1차 확인
  await answerConfirm(env.document, { accept: true }); // 409 재확인
  const result = await resultPromise;
  assert.strictEqual(result.ok, true);
  assert.strictEqual(deps.calls.length, 2, '한 번 거절당하고 force로 다시 불러야 한다');
  assert.strictEqual(deps.calls[1].body.force, true);
});

test('409 재확인에서 취소하면 force 재시도를 안 한다', async () => {
  const env = await load();
  const deps = {
    calls: [],
    vtFetch: async function (path, opts) {
      this.calls.push(path);
      const e = new Error('dirty'); e.status = 409; e.data = { error: '더러움', dirty: true };
      throw e;
    },
    getAction: () => undefined,
  };
  const resultPromise = env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  await answerConfirm(env.document, { accept: true });  // 첫 확인은 승낙
  await answerConfirm(env.document, { accept: false }); // 409 재확인은 거절
  const result = await resultPromise;
  assert.strictEqual(result.ok, false);
  assert.strictEqual(deps.calls.length, 1, '재확인을 거절했으면 force 재시도가 없어야 한다');
});

test('메인 워크트리가 아니어도 다른 이유로 실패(예: 500)하면 에러 메시지를 돌려준다', async () => {
  const env = await load();
  const deps = {
    vtFetch: async () => { const e = new Error('boom'); e.status = 500; e.data = { error: '서버 오류' }; throw e; },
    getAction: () => undefined,
  };
  const resultPromise = env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', false);
  await answerConfirm(env.document, { accept: true });
  const result = await resultPromise;
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.error, '서버 오류');
});

test('세션이 열려 있으면 확인 문구가 그 사실을 언급한다', async () => {
  const env = await load();
  const deps = { vtFetch: async () => ({}), getAction: () => undefined };
  const resultPromise = env.deleteWorktreeRow(deps, 'wt-1', 'farshell/feat', true);
  const dialog = await waitForDialog(env.document);
  const seenMsg = dialog.querySelector('.vt-dialog-msg').textContent;
  dialog.querySelector('.vt-dialog-cancel').click();
  await resultPromise;
  assert.match(seenMsg, /세션/);
});
