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
