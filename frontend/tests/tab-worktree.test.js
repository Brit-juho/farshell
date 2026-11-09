// 10-shell-layout.md §4 1단계 — 탭 이름을 워크트리 단위로(렌더만). 진짜 다중
// 세션 탭(2단계)은 /api/workspace 스키마까지 건드리는 작업이라 이월했다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MOD = path.join(__dirname, '../js/term/tab-worktree.js');
// 2.1 D4 — 이 모듈이 이제 layout/panes.js(브랜치 칩)를 끌어오는데, 그 파일이
// pane-picker.js를 거쳐 agent/preview.js까지 끌고 온다. 그 파일은 UMD 전역
// VTAnsiLex를 모듈 최상단에서 읽으므로(layout-panes-cap.test.js와 같은 이유)
// 먼저 심어 둬야 한다.
const ANSILEX_JS = path.join(__dirname, '../js/lib/ansilex.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

let _m = null;
async function load() {
  if (_m) return _m;
  const env = createDomEnv('<!doctype html><html><body><div id="tabs"></div></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ worktrees: [] }) });
  const cache = new Map();
  await importFresh(ANSILEX_JS, env.context, cache);
  _m = await importFresh(MOD, env.context, cache);
  return _m;
}

test('pane 헤더 브랜치는 생성 태그가 아니라 현재 cwd 스냅샷을 쓴다', async () => {
  const { branchChipMap } = await load();
  const map = branchChipMap([
    { name: 'a', cwd: '/new', wt_id: 'old', context: { cwd: '/new', status: 'ready', branch: 'feature' } },
    { name: 'b', cwd: '/nonrepo', context: { cwd: '/nonrepo', status: 'nonrepo', branch: null } },
    { name: 'c', cwd: '/repo', context: { cwd: '/repo', status: 'ready', branch: null } },
    { name: 'd', cwd: '/moved', context: { cwd: '/old', status: 'ready', branch: 'wrong' } },
  ]);
  assert.strictEqual(map.get('a'), 'feature');
  assert.strictEqual(map.has('b'), false);
  assert.strictEqual(map.get('c'), '분리된 HEAD');
  assert.strictEqual(map.has('d'), false);
});

test('제목 막대 경로의 저장소 이름은 링크된 워크트리라도 메인 저장소 폴더다', async () => {
  const { repoNameMap } = await load();
  const map = repoNameMap([
    { name: 'main', cwd: '/w/sample-repo/src', context: { cwd: '/w/sample-repo/src', status: 'ready', root: '/w/sample-repo', branch: 'dev', worktree: { kind: 'main', repositoryRoot: '/w/sample-repo', name: 'sample-repo' } } },
    { name: 'wt', cwd: '/w/sample-repo-wt/feat', context: { cwd: '/w/sample-repo-wt/feat', status: 'ready', root: '/w/sample-repo-wt/feat', branch: 'feat/x', worktree: { kind: 'linked', repositoryRoot: '/w/sample-repo', name: 'feat' } } },
    { name: 'old', cwd: '/w/acme', context: { cwd: '/w/acme', status: 'ready', root: '/w/acme', branch: 'main' } },
    { name: 'plain', cwd: '/tmp', context: { cwd: '/tmp', status: 'nonrepo' } },
    { name: 'moved', cwd: '/w/b', context: { cwd: '/w/a', status: 'ready', root: '/w/a' } },
  ]);
  assert.strictEqual(map.get('main'), 'sample-repo');
  assert.strictEqual(map.get('wt'), 'sample-repo');
  assert.strictEqual(map.get('old'), 'acme');
  assert.strictEqual(map.has('plain'), false);
  assert.strictEqual(map.has('moved'), false, 'cwd가 바뀐 낡은 context는 쓰지 않는다');
});
