// 2026-09-22 — ui/icons.js의 에이전트 마크 매핑. DESIGN.md §4가 agent-ssh·
// agent-shell을 정의해뒀는데 AGENT_MARK가 claude/codex만 연결해둬서 죽은
// 코드였다(server/agent_detector.py가 ssh/shell을 반환하도록 바뀐 것과 짝).
// 여기서 지키는 계약: 알려진 이름은 전용 마크로, 모르는 이름만 agent-shell로.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const ICONS_JS = path.join(__dirname, '../js/ui/icons.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(ICONS_JS, env.context, new Map());
}

test('claude/codex/aider/gemini/ssh/python/node/docker/shell이 서로 다른 마크로 간다', async () => {
  const icons = await load();
  const names = ['claude', 'codex', 'aider', 'gemini', 'ssh', 'python', 'node', 'docker', 'shell'];
  const marks = names.map((n) => icons.agentMarkFor(n));
  assert.deepStrictEqual(marks, [
    'agent-claude', 'agent-codex', 'agent-aider', 'agent-gemini', 'agent-ssh',
    'agent-python', 'agent-node', 'agent-docker', 'agent-shell',
  ]);
  assert.strictEqual(new Set(marks).size, marks.length, '아홉 이름이 서로 다른 마크를 가리켜야 한다');
});

test('모르는 이름은 agent-shell로 떨어진다', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentMarkFor('ollama'), 'agent-shell');
  assert.strictEqual(icons.agentMarkFor(undefined), 'agent-shell');
});

test('agentIcon이 실제로 svg 마크업을 낸다(등록 안 된 이름 경고 없이)', async () => {
  const icons = await load();
  for (const n of ['claude', 'codex', 'aider', 'gemini', 'ssh', 'python', 'node', 'docker', 'shell']) {
    const svg = icons.agentIcon(n);
    assert.ok(svg.includes('<svg'), `${n} → svg 마크업이어야 한다`);
  }
});

test('agentLabel — ssh/python/node/docker/shell도 사람이 읽을 라벨이 있다', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentLabel('ssh'), 'SSH');
  assert.strictEqual(icons.agentLabel('python'), 'Python');
  assert.strictEqual(icons.agentLabel('node'), 'Node');
  assert.strictEqual(icons.agentLabel('docker'), 'Docker');
  assert.strictEqual(icons.agentLabel('shell'), '셸');
  assert.strictEqual(icons.agentLabel(''), '');
});
