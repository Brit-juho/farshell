const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { answerConfirm } = require('./helpers/dialog-stub');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
const flush = () => new Promise((r) => setImmediate(r));
const row = (over = {}) => ({ id: 'docs', kind: 'mcp', tool: 'codex', name: 'Docs',
  source: '/home/alice/.codex/config.toml', profile: '/home/alice/.codex', scope: 'global',
  level: 'user', enabled: true, writable: true, metadata: {}, origin: 'unknown', ...over });

async function setup({ initial = {}, items = [row()], reply } = {}) {
  const env = createDomEnv('<body></body>');
  doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  const calls = [];
  env.window.fetch = async (url, opts = {}) => {
    const route = url.split('?')[0];
    const body = opts.body ? JSON.parse(opts.body) : {};
    calls.push({ route, body });
    let data;
    if (reply) data = await reply(route, body);
    if (data === undefined) {
      if (route === '/api/hosts') data = { hosts: [{ id: 'local', label: 'Local', sessions: [{ name: 'sample' }] }, { id: 'other', label: 'Other', sessions: [] }] };
      else if (route.endsWith('/inventory')) data = { items, errors: [], presets: [], selected: initial.session ? { tool: 'codex', root: '/home/alice/.codex', project: '/home/alice/repo' } : null };
      else if (route.endsWith('/preview')) data = { token: 'review-token', changes: [{ name: 'Docs', kind: 'mcp', enabled: body.changes[0].enabled, source: '/home/alice/.codex/config.toml' }] };
      else if (route.endsWith('/apply')) data = { status: 'ok', results: [{ status: 'ok' }] };
      else if (route.endsWith('/request')) data = { status: 'pending' };
      else data = {};
    }
    return { ok: true, status: 200, json: async () => data };
  };
  const cache = new Map();
  await importFresh(path.join(__dirname, '../js/ui/toast.js'), env.context, cache);
  const module = await importFresh(path.join(__dirname, '../js/panels/agent-config.js'), env.context, cache);
  const host = env.document.createElement('div');
  env.document.body.appendChild(host);
  const manager = module.mount(host, initial);
  for (let i = 0; i < 6; i++) await flush();
  return { ...env, host, calls, manager };
}

test('전체·세션 보기와 원본 위치를 표시하고 읽기 전용도 분류에 선택한다', async () => {
  const { host, manager } = await setup({ items: [row({ kind: 'skill', writable: false, name: 'Review' })] });
  assert.match(host.textContent, /FarShell 전체/);
  assert.match(host.textContent, /선택한 세션/);
  assert.match(host.textContent, /\/home\/alice\/\.codex\/config.toml/);
  const check = host.querySelector('input[type="checkbox"]');
  assert.ok(check);
  check.click();
  const actions = Array.from(host.querySelectorAll('button'));
  assert.ok(actions.find((b) => b.textContent === '선택 켜기').disabled);
  assert.equal(actions.find((b) => b.textContent === '선택 항목 분류').disabled, false);
  manager.dispose();
});

test('토글은 검토 전 쓰지 않고 검토 토큰으로만 적용한다', async () => {
  const { host, calls, document, manager } = await setup();
  Array.from(host.querySelectorAll('button')).find((b) => b.textContent === '끄기').click();
  for (let i = 0; i < 4; i++) await flush();
  assert.ok(calls.some((c) => c.route.endsWith('/preview')));
  assert.equal(calls.some((c) => c.route.endsWith('/apply')), false);
  await answerConfirm(document);
  for (let i = 0; i < 8; i++) await flush();
  assert.deepEqual(calls.find((c) => c.route.endsWith('/apply')).body, { token: 'review-token' });
  manager.dispose();
});

test('원격 토글은 로컬 쓰기나 원격 직접 적용 대신 대상 승인 요청으로 보낸다', async () => {
  const { host, calls, document, manager } = await setup({ initial: { host: 'other' } });
  Array.from(host.querySelectorAll('button')).find((b) => b.textContent === '끄기').click();
  for (let i = 0; i < 4; i++) await flush();
  await answerConfirm(document);
  for (let i = 0; i < 8; i++) await flush();
  const request = calls.find((c) => c.route === '/api/hosts/other/agent-config/request');
  assert.deepEqual(request.body, { action: 'apply', payload: { token: 'review-token' } });
  assert.equal(calls.some((c) => c.route.endsWith('/apply')), false);
  assert.match(host.textContent, /대상 호스트 승인 대기/);
  manager.dispose();
});

test('폐기된 화면의 늦은 응답은 새 화면을 덮어쓰지 않는다', async () => {
  let resolve;
  const gate = new Promise((r) => { resolve = r; });
  const { host, manager } = await setup({ reply: async (route) => {
    if (route.endsWith('/inventory')) { await gate; return { items: [row({ name: 'Old session' })], errors: [], presets: [] }; }
  } });
  manager.dispose();
  host.textContent = 'New session';
  resolve();
  for (let i = 0; i < 8; i++) await flush();
  assert.equal(host.textContent, 'New session');
});
