'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const env = createDomEnv();
after(() => env.dom.window.close());
const load = () => importFresh(path.join(__dirname, '../js/ui/session-context.js'), env.context, new Map());
const session = (kind = 'linked') => ({ name: 'cli', cwd: '/trees/task/src', context: {
  cwd: '/trees/task/src', root: '/trees/task', status: 'ready', branch: 'feature',
  worktree: { kind, status: 'ready', name: 'task', relative: 'src', repositoryRoot: '/repos/project' },
} });

test('CLI와 무관하게 메인/별도 워크트리의 루트와 하위 폴더를 표시한다', async () => {
  const { contextLabel } = await load();
  for (const agent of ['codex', 'claude', null]) {
    assert.strictEqual(contextLabel({ ...session(), agent }), 'WT task/src · feature');
  }
  assert.strictEqual(contextLabel(session('main')), '메인 task/src · feature');
});

test('cwd가 바뀐 이전 스냅샷과 구버전 응답을 워크트리로 추정하지 않는다', async () => {
  const { contextLabel } = await load();
  assert.strictEqual(contextLabel({ ...session(), cwd: '/outside' }), 'outside · 정보 갱신 대기');
  assert.strictEqual(contextLabel({ cwd: '/trees/task/src' }), 'src');
});

test('종류만 미확인이면 정상 브랜치를 유지하고 갱신 대기를 표시한다', async () => {
  const { contextLabel } = await load();
  const s = session('unknown'); s.context.worktree.status = 'unavailable';
  assert.strictEqual(contextLabel(s), '미확인 task/src · feature · 정보 갱신 대기');
  s.context.branch = null;
  assert.ok(contextLabel(s).includes('분리된 HEAD'));
});

test('긴 상대 경로를 줄여도 작업 공간 루트와 현재 폴더를 남긴다', async () => {
  const { contextLabel } = await load();
  const s = session(); s.context.worktree.relative = 'packages/very-long-project/web/src';
  assert.strictEqual(contextLabel(s), 'WT task/…/src · feature');
});

test('작업 위치 상세는 임의 경로 문자를 HTML로 해석하지 않는다', async () => {
  const { showWorkspaceDetails } = await importFresh(path.join(__dirname, '../js/ui/workspace-details.js'), env.context, new Map());
  const s = session(); s.cwd = s.context.cwd = '/trees/<img src=x onerror=alert(1)>/src';
  showWorkspaceDetails(s);
  const details = env.window.document.querySelector('.vt-workspace-details');
  assert.ok(details.textContent.includes(s.cwd));
  assert.strictEqual(details.querySelector('img'), null);
  assert.ok(details.textContent.includes('별도 워크트리'));
});

const agentSession = () => ({
  name: 'cli', cwd: '/repo', contextCwd: '/trees/task/src', contextSource: 'agent',
  context: { cwd: '/trees/task/src', root: '/trees/task', status: 'ready', branch: 'feat/task',
    worktree: { kind: 'linked', status: 'ready', name: 'task', relative: 'src', repositoryRoot: '/repo' } },
});

test('에이전트 CLI 기준으로 읽은 브랜치를 보이고 터미널 위치는 툴팁에만 둔다', async () => {
  const { contextLabel, sessionContext, locationTip } = await load();
  assert.strictEqual(contextLabel(agentSession()), 'WT task/src · feat/task');
  const ctx = sessionContext(agentSession());
  assert.strictEqual(ctx.terminalCwd, '/repo');
  assert.strictEqual(locationTip(ctx), '/trees/task/src · 터미널: /repo');
});

test('터미널 기준이거나 위치가 같으면 툴팁에 터미널 위치를 덧붙이지 않는다', async () => {
  const { sessionContext, locationTip } = await load();
  assert.strictEqual(locationTip(sessionContext(session())), '/trees/task/src');
  const same = { ...agentSession(), cwd: '/trees/task/src' };
  assert.strictEqual(sessionContext(same).terminalCwd, null);
});

test('contextCwd와 스냅샷의 cwd가 어긋나면 에이전트 기준이어도 추정하지 않는다', async () => {
  const { contextLabel } = await load();
  const s = agentSession(); s.contextCwd = '/trees/other';
  assert.strictEqual(contextLabel(s), 'other · 정보 갱신 대기');
});
