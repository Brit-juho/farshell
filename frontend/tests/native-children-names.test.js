// NativeChildren — 같은 이름 서브에이전트 구분(순번·aria-label)과 끝난 구역 접힘 버튼 표기.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
const base = {
  sessionId: 's1', tmuxName: 'api', name: 'api', open: true, status: 'working', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, changed: null,
  agent: null, branch: null, isMainWorktree: false, origin: 'local', viewLabel: null, otherScreens: 0, otherClients: 0,
};

test('같은 이름 둘 — 순번은 지금 보이는 행끼리(접힌 끝난 행은 세지 않는다), id 앞 4자·aria-label', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const root = env.document.createElement('div');
  env.document.body.append(root);
  const row = { ...base, subagents: { parent_id: 'p', items: [
    { id: 'a1111111', label: 'general-purpose', state: 'done', started_at: 10 },
    { id: 'a2222222', label: 'general-purpose', state: 'working', started_at: 20 },
    { id: 'z9999999', label: 'explore', state: 'working', started_at: 30 },
  ] } };
  env.mountNativeChildren(root, row);
  const rows = [...root.querySelectorAll('.vt-native-row')];
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].querySelector('.vt-native-name').textContent, 'general-purpose', '보이는 같은 이름이 하나뿐이면 번호가 없다');
  assert.strictEqual(rows[1].querySelector('.vt-native-name').textContent, 'explore');
  const fold = root.querySelector('.vt-native-fold-ended');
  assert.ok(fold.querySelector('.vt-native-fold-icon'));
  assert.match(fold.textContent, /끝난 서브에이전트 1/);
  fold.click();
  assert.strictEqual(root.querySelectorAll('.vt-native-row').length, 3);
  const working = root.querySelector('.vt-native-row[data-state="working"] .vt-native-name');
  assert.strictEqual(working.textContent, 'general-purpose #2');
  assert.match(root.querySelector('.vt-native-row[data-state="working"] .vt-native-open').getAttribute('aria-label'), /^general-purpose #2, /);
  assert.match(root.querySelector('.vt-native-row[data-state="working"] .vt-native-kw').textContent, /a222/);
  assert.strictEqual(root.querySelector('.vt-native-row[data-state="done"] .vt-native-name').textContent, 'general-purpose #1');
});
