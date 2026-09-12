// 10-shell-layout.md §4 1단계 — 탭 이름을 워크트리 단위로(렌더만). 진짜 다중
// 세션 탭(2단계)은 /api/workspace 스키마까지 건드리는 작업이라 이월했다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MOD = path.join(__dirname, '../js/term/tab-worktree.js');

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
  _m = await importFresh(MOD, env.context, new Map());
  return _m;
}

test('worktreeLabelMap — main은 저장소명, 나머지는 repo/branch (레일과 같은 규칙)', async () => {
  const { worktreeLabelMap } = await load();
  const map = worktreeLabelMap([
    { repoName: 'farshell', branch: 'master', isMain: true, sessions: ['dev'] },
    { repoName: 'farshell', branch: 'feat/2.1', isMain: false, sessions: ['wt-21', 'wt-21-b'] },
  ]);
  assert.strictEqual(map.get('dev'), 'farshell');
  assert.strictEqual(map.get('wt-21'), 'farshell/feat/2.1');
  assert.strictEqual(map.get('wt-21-b'), 'farshell/feat/2.1');
});

test('worktreeLabelMap — 세션이 중복으로 실려 와도 먼저 온 라벨을 유지한다', async () => {
  const { worktreeLabelMap } = await load();
  const map = worktreeLabelMap([
    { repoName: 'a', branch: 'x', isMain: false, sessions: ['dup'] },
    { repoName: 'b', branch: 'y', isMain: false, sessions: ['dup'] },
  ]);
  assert.strictEqual(map.get('dup'), 'a/x');
});

test('worktreeLabelMap — 빈 입력·세션 없는 워크트리도 안전하다', async () => {
  const { worktreeLabelMap } = await load();
  assert.strictEqual(worktreeLabelMap(undefined).size, 0);
  assert.strictEqual(worktreeLabelMap([{ repoName: 'a', branch: 'b', isMain: false }]).size, 0);
});

test('canRelabel — 사용자가 직접 지은 이름은 자동 라벨이 덮지 않는다', async () => {
  const { canRelabel } = await load();
  assert.strictEqual(canRelabel({ tmuxName: 'dev' }), true);
  assert.strictEqual(canRelabel({ tmuxName: 'dev', renamed: true }), false);
  assert.strictEqual(canRelabel(null), false);
});
