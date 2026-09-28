// 2026-09-22 — ui/icons.js의 에이전트 마크 매핑. DESIGN.md §4가 agent-ssh·
// agent-shell을 정의해뒀는데 AGENT_MARK가 claude/codex만 연결해둬서 죽은
// 코드였다(server/agent_detector.py가 ssh/shell을 반환하도록 바뀐 것과 짝).
// 여기서 지키는 계약: 알려진 이름은 전용 마크로, 모르는 이름만 agent-shell로.
//
// 2026-09-23 — gemini는 agy(Antigravity, Gemini CLI 후계자)로 대체, qwen(qwen-code)
// 전용 마크 추가, ollama/lms/llamacpp는 agent-local 하나를 공유(라벨만 다름).
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

test('claude/codex/aider/agy/qwen/ssh/python/node/docker/shell이 서로 다른 마크로 간다', async () => {
  const icons = await load();
  const names = ['claude', 'codex', 'aider', 'agy', 'qwen', 'ssh', 'python', 'node', 'docker', 'shell'];
  const marks = names.map((n) => icons.agentMarkFor(n));
  assert.deepStrictEqual(marks, [
    'agent-claude', 'agent-codex', 'agent-aider', 'agent-agy', 'agent-qwen', 'agent-ssh',
    'agent-python', 'agent-node', 'agent-docker', 'agent-shell',
  ]);
  assert.strictEqual(new Set(marks).size, marks.length, '열 이름이 서로 다른 마크를 가리켜야 한다');
});

test('로컬 LLM 러너 3종(ollama/lms/llamacpp)은 agent-local 하나를 공유한다', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentMarkFor('ollama'), 'agent-local');
  assert.strictEqual(icons.agentMarkFor('lms'), 'agent-local');
  assert.strictEqual(icons.agentMarkFor('llamacpp'), 'agent-local');
});

test('gemini는 더 이상 전용 마크가 아니다(agy로 대체) — 모르는 이름 취급', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentMarkFor('gemini'), 'agent-shell');
});

test('모르는 이름은 agent-shell로 떨어진다', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentMarkFor('made-up-cli'), 'agent-shell');
  assert.strictEqual(icons.agentMarkFor(undefined), 'agent-shell');
});

test('agentIcon이 실제로 svg 마크업을 낸다(등록 안 된 이름 경고 없이)', async () => {
  const icons = await load();
  for (const n of [
    'claude', 'codex', 'aider', 'agy', 'qwen', 'ssh',
    'python', 'node', 'docker', 'ollama', 'lms', 'llamacpp', 'shell',
  ]) {
    const svg = icons.agentIcon(n);
    assert.ok(svg.includes('<svg'), `${n} → svg 마크업이어야 한다`);
  }
});

test('agentLabel — ssh/python/node/docker/local/shell도 사람이 읽을 라벨이 있다', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentLabel('ssh'), 'SSH');
  assert.strictEqual(icons.agentLabel('python'), 'Python');
  assert.strictEqual(icons.agentLabel('node'), 'Node');
  assert.strictEqual(icons.agentLabel('docker'), 'Docker');
  assert.strictEqual(icons.agentLabel('agy'), 'Antigravity');
  assert.strictEqual(icons.agentLabel('qwen'), 'Qwen');
  assert.strictEqual(icons.agentLabel('ollama'), 'Ollama');
  assert.strictEqual(icons.agentLabel('lms'), 'LM Studio');
  assert.strictEqual(icons.agentLabel('llamacpp'), 'llama.cpp');
  assert.strictEqual(icons.agentLabel('shell'), '셸');
  assert.strictEqual(icons.agentLabel(''), '');
});

// 2026-09-29 — 브랜드 로고(ui/brand-icons.js). 있으면 채움 로고, 없으면 선 기호.
test('브랜드 로고가 있는 이름은 채움 svg로, 없는 이름은 선 기호로 그린다', async () => {
  const icons = await load();
  await icons.brandIconsReady;
  for (const n of ['claude', 'codex', 'agy', 'qwen', 'ollama', 'lms', 'python', 'node', 'docker',
    'opencode', 'cursor-agent', 'copilot', 'amp', 'goose', 'kimi']) {
    const svg = icons.agentIcon(n);
    assert.match(svg, /fill="currentColor"/, n);
    assert.doesNotMatch(svg, /stroke=/, n);
  }
  for (const n of ['aider', 'llamacpp', 'ssh', 'shell', 'made-up-cli', 'toString', '__proto__']) {
    assert.match(icons.agentIcon(n), /stroke="currentColor"/, n);
  }
});

test('새 CLI들은 사람이 읽는 라벨을 가진다', async () => {
  const icons = await load();
  assert.strictEqual(icons.agentLabel('cursor-agent'), 'Cursor');
  assert.strictEqual(icons.agentLabel('copilot'), 'Copilot');
});

test('브랜드 로고 청크가 오기 전에도 선 기호로 그린다(빈 문자열이 아니다)', async () => {
  const icons = await load();
  assert.match(icons.agentIcon('claude'), /<svg/);
});

// 2026-09-29 — index.html 아이콘의 원본은 icons.js 하나다.
test('index.html의 svg는 data-icon 빈 자리이고, 이름이 전부 icons.js에 있다', async () => {
  const fs = require('node:fs');
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  const env = createDomEnv(html);
  _doms.push(env.dom);
  const icons = await importFresh(ICONS_JS, env.context, new Map());
  const slots = [...env.dom.window.document.querySelectorAll('svg[data-icon]')];
  assert.ok(slots.length > 20);
  for (const el of slots) assert.strictEqual(el.innerHTML, '', `${el.dataset.icon} 자리에 도형이 복사돼 있다`);
  icons.hydrateIcons(env.dom.window.document);
  for (const el of slots) assert.notStrictEqual(el.innerHTML, '', `${el.dataset.icon}이 icons.js에 없다`);
});

test('로그인 게이트의 인라인 svg(번들 전 표시용)는 icons.js와 같은 도형이다', async () => {
  const fs = require('node:fs');
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  const src = fs.readFileSync(ICONS_JS, 'utf8');
  const norm = (s) => s.replace(/\s+/g, ' ').replace(/ ?\/>/g, '/>').trim();
  const inline = [...html.matchAll(/<svg(?![^>]*data-icon)[^>]*>([\s\S]*?)<\/svg>/g)].map((m) => norm(m[1]));
  assert.strictEqual(inline.length, 2);
  for (const [name, body] of [['square-terminal', inline[0]], ['alert-triangle', inline[1]]]) {
    const m = src.match(new RegExp(`'?${name}'?: '(.*)',`));
    assert.strictEqual(body, norm(m[1]), name);
  }
});
