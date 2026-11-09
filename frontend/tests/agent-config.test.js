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

test('관리 창 구획(시안 P09-b) — 위 막대 · 탐색(아이콘 · 개수, 접근 이름은 글자만) · 행 머리 두 줄 · 바닥 막대', async () => {
  const { host, manager } = await setup({ items: [row(), row({ id: 'off', name: 'Off', enabled: false })] });
  const top = host.querySelector('.vt-ac-top');
  assert.ok(top.querySelector('.vt-ac-mode [role="tab"][aria-selected="true"]'));
  assert.ok(top.querySelector('.vt-ac-target select[aria-label="호스트"]'), '호스트 선택이 위 막대에 있다');
  assert.ok(Array.from(top.querySelectorAll('button')).some((b) => b.textContent === '새로고침'));
  const nav = Array.from(host.querySelectorAll('.vt-ac-main > .vt-ac-nav .vt-ac-navitem'));
  assert.equal(nav.length, 6);
  assert.equal(nav[0].getAttribute('aria-current'), 'page');
  assert.ok(nav[0].querySelector('.vt-ac-navico[aria-hidden="true"] svg'));
  assert.equal(nav[0].querySelector('.vt-ac-navcount').textContent, '2', '목록 개수');
  assert.equal(nav[0].querySelector('.vt-ac-navcount').getAttribute('aria-hidden'), 'true');
  const head = host.querySelector('.vt-ac-pane .vt-ac-itemhead');
  assert.equal(head.querySelector('.vt-ac-line1 strong').textContent, 'Docs');
  assert.equal(head.querySelector('.vt-ac-tag').textContent, 'MCP');
  assert.equal(head.querySelector('.vt-ac-line2').textContent, '/home/alice/.codex/config.toml');
  assert.deepEqual(Array.from(host.querySelectorAll('.vt-ac-status'), (s) => s.dataset.state), ['on', 'off']);
  const footer = host.querySelector('.vt-agent-config > .vt-ac-footer');
  assert.match(footer.textContent, /설치된 원본을 한곳에서 관리합니다/);
  nav[2].click();
  assert.equal(host.querySelector('.vt-ac-navitem[aria-current="page"]').textContent, '프리셋');
  assert.equal(host.querySelector('.vt-ac-body').dataset.section, 'presets');
  manager.dispose();
  assert.ok(!host.classList.contains('vt-agent-config'), '옛 보관함이 같은 본문에 그려지므로 배치 클래스를 거둔다');
});

test('옛 보관함 버튼은 바닥 막대에 있고 누르면 이 화면을 정리한 뒤 넘긴다', async () => {
  const env = await setup();
  let opened = 0;
  env.manager.dispose();
  env.host.replaceChildren();
  const cache = new Map();
  await importFresh(path.join(__dirname, '../js/ui/toast.js'), env.context, cache);
  const module = await importFresh(path.join(__dirname, '../js/panels/agent-config.js'), env.context, cache);
  module.mount(env.host, {}, () => { opened += 1; });
  for (let i = 0; i < 6; i++) await flush();
  const legacy = Array.from(env.host.querySelectorAll('.vt-ac-footer button')).find((b) => b.textContent === '기존 MCP 보관함·상세 관리');
  assert.ok(legacy);
  legacy.click();
  assert.equal(opened, 1);
  assert.ok(!env.host.classList.contains('vt-agent-config'));
});
