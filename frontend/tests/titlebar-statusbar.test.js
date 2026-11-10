// 리디자인 2단계 — 제목 막대(TitleBar.tsx)와 상태 막대(Hud.tsx). 실제 index.html의 두 막대 마크업 위에 마운트한다.
// 1부: 제목 막대 — FarShell 메뉴(승인 대기 배지 자리 포함) · 호스트 · 경로 · 팔레트 입력 · 정적 자리 이동.
// 2부: 상태 막대 — 에이전트 칩 맨 왼쪽 고정, 토스트 메시지 칸과의 순서, 메뉴 버튼이 더는 상태 막대에 없다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const BODY = INDEX_HTML.slice(INDEX_HTML.indexOf('<header id="vt-titlebar"'), INDEX_HTML.indexOf('<header id="topbar">'));

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

const flush = (w) => new Promise((r) => w.setTimeout(r, 30));

async function fresh() {
  const env = await loadSolidBuild();
  _doms.push(env);
  env.document.body.innerHTML = BODY;
  return env;
}

function titleDeps(env, over = {}) {
  const calls = [];
  const actions = { 'quickopen.open': () => calls.push('palette'), 'settings.section': (id) => calls.push(`section:${id}`) };
  const fetched = [];
  return {
    calls, fetched,
    deps: {
      vtFetch: async (p) => {
        fetched.push(p);
        if (p === '/api/capabilities') return { hostname: 'sample-mac' };
        if (p === '/api/hosts') return { hosts: over.hosts || [{ id: 'local', label: 'sample-mac', online: true }] };
        return null;
      },
      getAction: (n) => actions[n],
      isMac: true,
      crumb: over.crumb || (() => ({ repo: 'sample-repo', branch: 'feat/x', session: 'dev-1' })),
      subscribeCrumb: over.subscribeCrumb,
    },
  };
}

// ── 1부: 제목 막대 ──────────────────────────────────────────────────────

test('정적 마크업: 제목 막대 안에 두 자리, 상태 막대는 따로 비어 있다', async () => {
  const { document } = await fresh();
  const tb = document.getElementById('vt-titlebar');
  assert.ok(tb.querySelector('#vt-mb-lead') && tb.querySelector('#vt-mb-tools'));
  const hud = document.getElementById('vt-hud');
  assert.strictEqual(hud.children.length, 0);
  assert.strictEqual(hud.getAttribute('role'), 'region', '막대 전체는 status가 아니다 — status는 메시지 칸에만');
});

test('마운트: 메뉴 · 호스트 · 경로 · 팔레트 입력, 정적 자리는 막대 양 끝으로 옮겨 간다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const lead = document.getElementById('vt-mb-lead');
  const tools = document.getElementById('vt-mb-tools');
  const { deps } = titleDeps(env);
  env.mountTitleBar(tb, deps);
  await flush(window);
  const left = tb.querySelector('.vt-tb-left');
  const right = tb.querySelector('.vt-tb-right');
  assert.strictEqual(left.firstElementChild.firstElementChild, lead, '신호등 자리는 왼쪽 맨 앞(같은 노드)');
  assert.ok(right.contains(tools) && right.lastElementChild.contains(tools), '도구 자리는 오른쪽 맨 끝');
  assert.ok(document.getElementById('vt-tb-focus-slot'), '집중 모드 버튼 자리(7단계)');
  const focusBtn = document.querySelector('#vt-tb-focus-slot > #vt-tb-focus');
  assert.ok(focusBtn, '7단계 — 자리 안에 집중 모드 버튼이 있다');
  assert.strictEqual(focusBtn.getAttribute('aria-pressed'), 'false');
  assert.strictEqual(document.querySelectorAll('#vt-mb-lead').length, 1);

  const menu = tb.querySelector('.vt-mb-menu');
  assert.ok(menu, 'FarShell 메뉴는 제목 막대에 있다(승인 대기 배지가 .vt-mb-menu를 찾는다)');
  assert.strictEqual(menu.getAttribute('aria-haspopup'), 'menu');
  assert.strictEqual(tb.querySelector('.vt-tb-host .vt-workspace-host').textContent, 'sample-mac');
  assert.strictEqual(tb.querySelector('button.vt-tb-host'), null, '맥이 하나면 누를 것이 없다');

  const parts = Array.from(tb.querySelectorAll('.vt-tb-crumb-part'), (e) => e.textContent);
  assert.deepStrictEqual(parts, ['sample-repo', 'feat/x', 'dev-1']);
  assert.ok(tb.querySelector('.vt-tb-crumb-part.session').classList.contains('cur'));
  assert.strictEqual(tb.querySelector('.vt-tb-crumb').getAttribute('data-tip'), 'sample-repo › feat/x › dev-1');

  const pal = tb.querySelector('#vt-tb-palette');
  assert.strictEqual(pal.tagName, 'BUTTON');
  assert.match(pal.textContent, /⌘K/);
  assert.strictEqual(pal.getAttribute('aria-label'), '빠른 열기');
});

test('팔레트 입력을 누르면 기존 팔레트(quickopen.open)가 열린다', async () => {
  const env = await fresh();
  const tb = env.document.getElementById('vt-titlebar');
  const { deps, calls } = titleDeps(env);
  env.mountTitleBar(tb, deps);
  tb.querySelector('#vt-tb-palette').click();
  assert.deepStrictEqual(calls, ['palette']);
});

test('FarShell 메뉴: 「상태 막대 설정」이 설정의 hud 섹션을 연다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const { deps, calls } = titleDeps(env);
  env.mountTitleBar(tb, deps);
  tb.querySelector('.vt-mb-menu').click();
  await flush(window);
  const items = Array.from(document.querySelectorAll('.vt-mb-pop .vt-mb-item'));
  const labels = items.map((b) => b.querySelector('.vt-mb-label').textContent);
  assert.deepStrictEqual(labels, ['새 세션', '빠른 열기', '집중 모드', '접속 주소 복사', '설정', '상태 막대 설정', '캐시 지우기', '로그아웃']);
  items[5].click();
  assert.deepStrictEqual(calls, ['section:hud']);
  assert.strictEqual(document.querySelector('.vt-mb-pop'), null, '실행하면 닫힌다');
});

test('FarShell 메뉴: 「캐시 지우기」「로그아웃」이 같은 이름의 액션을 부른다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const { deps, calls } = titleDeps(env);
  const base = deps.getAction;
  deps.getAction = (n) => (n === 'app.clear-cache' ? () => calls.push('clear-cache') : n === 'session.logout' ? () => calls.push('logout') : base(n));
  env.mountTitleBar(tb, deps);
  for (const [i, want] of [[6, 'clear-cache'], [7, 'logout']]) {
    tb.querySelector('.vt-mb-menu').click();
    await flush(window);
    document.querySelectorAll('.vt-mb-pop .vt-mb-item')[i].click();
    assert.strictEqual(calls[calls.length - 1], want);
  }
});

test('경로: 모르는 조각은 빼고, 비면 아무것도 그리지 않고, 구독 알림에 맞춰 바뀐다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  let cur = { repo: null, branch: null, session: 'scratch' };
  let notify = () => {};
  const { deps } = titleDeps(env, { crumb: () => cur, subscribeCrumb: (fn) => { notify = fn; return () => {}; } });
  env.mountTitleBar(tb, deps);
  assert.deepStrictEqual(Array.from(tb.querySelectorAll('.vt-tb-crumb-part'), (e) => e.textContent), ['scratch']);
  assert.strictEqual(tb.querySelector('.vt-tb-crumb .vt-tb-crumb-sep'), null);
  cur = {};
  notify();
  await flush(window);
  assert.strictEqual(tb.querySelector('.vt-tb-crumb'), null);
  cur = { repo: 'acme', branch: 'main', session: 's' };
  notify();
  await flush(window);
  assert.deepStrictEqual(Array.from(tb.querySelectorAll('.vt-tb-crumb-part'), (e) => e.textContent), ['acme', 'main', 's']);
});

test('호스트: 맥이 둘 이상이면 버튼이 되고, 누르면 호스트 목록 메뉴가 열린다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const tb = document.getElementById('vt-titlebar');
  const { deps } = titleDeps(env, {
    hosts: [{ id: 'local', label: 'sample-mac', online: true, sessions: [] }, { id: 'peer', label: 'studio', online: false, reason: 'timeout' }],
  });
  env.mountTitleBar(tb, deps);
  await flush(window);
  const btn = tb.querySelector('button.vt-tb-host');
  assert.ok(btn, '고를 게 있으면 버튼');
  assert.strictEqual(btn.getAttribute('aria-haspopup'), 'menu');
  btn.click();
  await flush(window);
  const rows = Array.from(document.querySelectorAll('.vt-menu .vt-menu-item-label'), (e) => e.textContent);
  assert.deepStrictEqual(rows, ['✓ sample-mac', 'studio']);
});

test('accessUrl — 터널이 돌면 https 공개 주소, 아니면 지금 origin', async () => {
  const { accessUrl, window } = await fresh();
  assert.strictEqual(accessUrl({ tunnel: { running: true, url: 'https://sample.example' } }), 'https://sample.example');
  assert.strictEqual(accessUrl({ tunnel: { running: false, url: 'https://sample.example' } }), window.location.origin);
  assert.strictEqual(accessUrl({ tunnel: { url: 'http://insecure' } }), window.location.origin);
  assert.strictEqual(accessUrl(null), window.location.origin);
});

// ── 2부: 상태 막대 ──────────────────────────────────────────────────────

test('상태 막대: 에이전트 칩이 맨 왼쪽, 메뉴 버튼은 더는 상태 막대에 없다', async () => {
  const env = await fresh();
  const { document, window } = env;
  const hud = document.getElementById('vt-hud');
  env.mountHud(hud, {
    vtFetch: async (p) => (p === '/api/capabilities' ? { version: '9.9.9' } : null),
    activeTmuxName: () => '',
    getAction: () => undefined,
    e2eEnabled: true,
    getLayout: () => JSON.stringify({ v: 3, left: ['e2e'], right: ['version', 'agents'] }),
    agentCounts: () => ({ waiting: 1, working: 2, done: 0 }),
  });
  await flush(window);
  const chips = Array.from(hud.querySelectorAll('.vt-hud-chip'), (c) => c.dataset.chip);
  assert.deepStrictEqual(chips, ['agents', 'e2e', 'version'], '배치에 agents가 적혀 있어도 맨 왼쪽 한 번');
  const agents = hud.querySelector('[data-chip="agents"]');
  assert.strictEqual(agents.getAttribute('role'), 'button', '개입이 있으면 누를 수 있는 칩');
  assert.strictEqual(hud.querySelector('.vt-mb-menu'), null);
  assert.strictEqual(hud.querySelector('[role="status"]'), null, '메시지가 없으면 status 영역이 없다');
});
