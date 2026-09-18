// 2.1 D4 — 탭의 정체성이 워크트리에서 저장소로 바뀌면서 sessionBelongsToTab의
// 두 번째 인자도 worktreeId가 아니라 repoId가 됐다. 같은 저장소의 두
// 워크트리에 딸린 세션이 같은 탭 소속으로 판정돼야 한다(그래야 탭의 상태
// dot이 "이 저장소 전체 중 가장 급한 것"을 보여준다).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const TABBAR_JS = path.join(__dirname, '../js/layout/tabbar.js');
const LAYOUT_STORE_JS = path.join(__dirname, '../js/layout/store.js');
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const PERSIST_JS = path.join(__dirname, '../js/layout/persist.js');
const AGENT_STATE_JS = path.join(__dirname, '../js/agent/state.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="vt-wtabs"></div></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  const cache = new Map();
  await importFresh(CORE_STORE_JS, env.context, cache);
  await importFresh(AGENT_STATE_JS, env.context, cache);
  await importFresh(LAYOUT_STORE_JS, env.context, cache);
  await importFresh(PERSIST_JS, env.context, cache);
  const T = await importFresh(TABBAR_JS, env.context, cache);
  return T;
}

test('sessionBelongsToTab — 지도가 비어 있으면(로딩 전) 전부 소속으로 본다', async () => {
  const { sessionBelongsToTab } = await load();
  assert.strictEqual(sessionBelongsToTab('dev', 'repo-a', new Map()), true);
  assert.strictEqual(sessionBelongsToTab('dev', null, new Map()), true);
});

test('sessionBelongsToTab — 같은 저장소의 다른 워크트리 세션도 그 저장소 탭 소속이다', async () => {
  const { sessionBelongsToTab } = await load();
  const repoSessions = new Map([['wt-main', 'repo-a'], ['wt-feat', 'repo-a'], ['wt-other', 'repo-b']]);
  assert.strictEqual(sessionBelongsToTab('wt-main', 'repo-a', repoSessions), true);
  assert.strictEqual(sessionBelongsToTab('wt-feat', 'repo-a', repoSessions), true, '다른 워크트리라도 같은 저장소면 같은 탭');
  assert.strictEqual(sessionBelongsToTab('wt-other', 'repo-a', repoSessions), false);
});

test('sessionBelongsToTab — 「기타」 탭(repoId 없음)은 어떤 저장소에도 안 속한 세션만 받는다', async () => {
  const { sessionBelongsToTab } = await load();
  const repoSessions = new Map([['wt-main', 'repo-a']]);
  assert.strictEqual(sessionBelongsToTab('plain-shell', null, repoSessions), true);
  assert.strictEqual(sessionBelongsToTab('wt-main', null, repoSessions), false);
});
