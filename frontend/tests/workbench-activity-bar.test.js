// 리디자인 1단계 — 작업대(Workbench.tsx)와 활동 막대(ActivityBar.tsx).
// 1부: activity-data.ts(항목·게이팅·키 이동). 2부: 활동 막대 DOM(탭 목록 접근성, 활성 표시,
// 배지·툴팁, ↑↓ 이동, 끌기 배선). 3부: 작업대 마운트 — 레일과 dock 본문이 함께 마운트된 채
// hidden만 바뀌고, 활동을 바꿔도 사이드바 폭(data-vt-side·--vt-side-stored)이 그대로인지.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

async function fresh() {
  const env = await loadSolidBuild();
  _doms.push(env);
  return env;
}

// ── 1부: activity-data.ts ───────────────────────────────────────────────

test('활동 9개 — 세션 · 변경 · 파일 · 큐 · 첨부 · 스니펫 · 포트 · 사용량 · 시스템 순서', async () => {
  const { ACTIVITIES } = await fresh();
  assert.deepStrictEqual(
    Array.from(ACTIVITIES, (a) => a.id),
    ['sessions', 'scm', 'dir', 'queue', 'files', 'snippets', 'ports', 'usage', 'system'],
  );
  // 도구 활동의 패널 id는 옛 dock 탭과 같다(panels/panel.js·팔레트·키맵이 이 id를 부른다).
  const byId = Object.fromEntries(ACTIVITIES.map((a) => [a.id, a]));
  assert.strictEqual(byId.scm.panelId, 'vt-dock-scm');
  assert.strictEqual(byId.queue.action, 'queue.show');
  assert.strictEqual(byId.sessions.panelId, null);
});

test('설정 스키마의 ui.side.activity 값 목록이 활동 목록과 같다', async () => {
  const { ACTIVITIES, SETTINGS_SCHEMA } = await fresh();
  const spec = SETTINGS_SCHEMA['ui.side.activity'];
  assert.deepStrictEqual(Array.from(spec.values), Array.from(ACTIVITIES, (a) => a.id));
  assert.strictEqual(spec.def, 'sessions');
  assert.strictEqual(spec.scope, 'device');
  const w = SETTINGS_SCHEMA['ui.side.width'];
  assert.strictEqual(w.def, 360);
  assert.strictEqual(w.min, 240);
  assert.strictEqual(w.max, 480);
  assert.strictEqual(w.scope, 'device');
  assert.strictEqual(SETTINGS_SCHEMA['ui.side.collapsed'].scope, 'device');
});

test('설정 스키마의 ui.side.pinned 값 목록이 빈 값 + 도구 활동의 패널 id와 같다', async () => {
  const { TOOL_ACTIVITIES, SETTINGS_SCHEMA } = await fresh();
  const spec = SETTINGS_SCHEMA['ui.side.pinned'];
  assert.deepStrictEqual(Array.from(spec.values), ['', ...Array.from(TOOL_ACTIVITIES, (a) => a.panelId)]);
  assert.strictEqual(spec.def, '');
  assert.strictEqual(spec.scope, 'device');
});

test('capability 게이팅 — 응답 전에는 포트·사용량을 그리지 않고, 응답에 있으면 보인다', async () => {
  const { availableActivities } = await fresh();
  assert.ok(!availableActivities(null).some((a) => a.id === 'ports' || a.id === 'usage'));
  assert.ok(!availableActivities({}).some((a) => a.id === 'ports'));
  const ids = availableActivities({ ports: true, usage: true }).map((a) => a.id);
  assert.ok(ids.includes('ports') && ids.includes('usage'));
});

test('↑↓·Home·End는 처음/끝에서 돌아 나간다', async () => {
  const { stepActivityIndex } = await fresh();
  assert.strictEqual(stepActivityIndex(0, 'ArrowDown', 5), 1);
  assert.strictEqual(stepActivityIndex(4, 'ArrowDown', 5), 0);
  assert.strictEqual(stepActivityIndex(0, 'ArrowUp', 5), 4);
  assert.strictEqual(stepActivityIndex(3, 'Home', 5), 0);
  assert.strictEqual(stepActivityIndex(1, 'End', 5), 4);
  assert.strictEqual(stepActivityIndex(-1, 'ArrowDown', 5), 1, '초점이 막대 밖이면 첫 항목에서 출발');
  assert.strictEqual(stepActivityIndex(0, 'ArrowDown', 0), -1);
});

// ── 2부: 활동 막대 DOM ──────────────────────────────────────────────────

test('탭 목록 — role=tablist + aria-orientation=vertical, 활동마다 role=tab·aria-label·툴팁', async () => {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  env.mountActivityBar(root, { caps: { ports: true } });
  const list = root.querySelector('[role="tablist"]');
  assert.ok(list);
  assert.strictEqual(list.getAttribute('aria-orientation'), 'vertical');
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  assert.deepStrictEqual(tabs.map((t) => t.dataset.activity),
    ['sessions', 'scm', 'dir', 'queue', 'files', 'snippets', 'ports', 'system']);
  for (const t of tabs) {
    assert.ok(t.getAttribute('aria-label'), `${t.dataset.activity}에 aria-label`);
    assert.strictEqual(t.dataset.tip, t.getAttribute('aria-label'));
    assert.strictEqual(t.dataset.tipSide, 'right');
    assert.strictEqual(t.getAttribute('aria-controls'), 'vt-side');
    assert.ok(t.querySelector('svg'), '아이콘 칸');
  }
  // 아래 무리(MCP·마이크 자리·설정)는 탭이 아니다.
  assert.ok(root.querySelector('.vt-act-mcp:not([role="tab"])'));
  assert.ok(root.querySelector('#vt-activity-mic-home'));
  assert.ok(root.querySelector('.vt-act-settings:not([role="tab"])'));
});

test('활성 표시 — 지금 활동만 aria-selected·tabindex=0, 사이드바가 0이면 강조선(.active)만 끈다', async () => {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const h = env.mountActivityBar(root);
  const tab = (id) => root.querySelector(`[data-activity="${id}"]`);
  assert.strictEqual(tab('sessions').getAttribute('aria-selected'), 'true');
  assert.strictEqual(tab('sessions').getAttribute('tabindex'), '0');
  assert.strictEqual(tab('queue').getAttribute('aria-selected'), 'false');
  assert.strictEqual(tab('queue').getAttribute('tabindex'), '-1');
  assert.ok(tab('sessions').classList.contains('active'));
  h.setCurrent('queue');
  assert.ok(tab('queue').classList.contains('active'));
  assert.ok(!tab('sessions').classList.contains('active'));
  h.setShown(false);
  assert.ok(!tab('queue').classList.contains('active'), '도구를 접어 0이면 강조선이 없다');
  assert.strictEqual(tab('queue').getAttribute('aria-selected'), 'true');
});

test('툴팁 — 지금 활동은 ⌘B 사이드바 토글, 설정은 ⌘,, 배지는 수치와 단위', async () => {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const h = env.mountActivityBar(root);
  const tab = (id) => root.querySelector(`[data-activity="${id}"]`);
  assert.match(tab('sessions').dataset.tipSub || '', /⌘B/);
  assert.strictEqual(tab('queue').dataset.tipSub, undefined);
  assert.strictEqual(root.querySelector('.vt-act-settings').dataset.tipSub, '⌘,');
  h.setBadges({ queue: 3 });
  assert.strictEqual(tab('queue').querySelector('.vt-act-badge').textContent, '3');
  assert.match(tab('queue').dataset.tipSub, /3/);
  h.setBadges({ queue: 0 });
  assert.strictEqual(tab('queue').querySelector('.vt-act-badge'), null, '0이면 배지가 없다');
  assert.strictEqual(tab('queue').dataset.tipSub, undefined);
});

test('「세션」 배지 — waiting 세션 수, 호박색(data-state=waiting), 툴팁은 「개입 필요 N」 + ⌘B', async () => {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const h = env.mountActivityBar(root);
  const tab = (id) => root.querySelector(`[data-activity="${id}"]`);
  assert.strictEqual(tab('sessions').querySelector('.vt-act-badge'), null, '0이면 배지가 없다');
  h.setBadges({ waiting: 2, queue: 1 });
  const b = tab('sessions').querySelector('.vt-act-badge');
  assert.strictEqual(b.textContent, '2');
  assert.strictEqual(b.dataset.state, 'waiting');
  assert.strictEqual(tab('queue').querySelector('.vt-act-badge').dataset.state, undefined, '다른 배지는 중립');
  assert.strictEqual(tab('sessions').dataset.tipSub, '개입 필요 2 · ⌘B 사이드바 토글');
  h.setBadges({ waiting: 0 });
  assert.strictEqual(tab('sessions').querySelector('.vt-act-badge'), null);
});

test('badgeTip — 세션 활동의 waiting 배지 문구', async () => {
  const { ACTIVITIES, badgeTip } = await fresh();
  const sessions = ACTIVITIES.find((a) => a.id === 'sessions');
  assert.strictEqual(sessions.badge, 'waiting');
  assert.strictEqual(badgeTip(sessions, 3), '개입 필요 3');
  assert.strictEqual(badgeTip(sessions, 0), undefined);
});

test('클릭과 키보드 — 클릭은 onSelect, ↑↓는 초점만 옮긴다(Enter·Space는 버튼 기본 동작)', async () => {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const h = env.mountActivityBar(root);
  const tab = (id) => root.querySelector(`[data-activity="${id}"]`);
  tab('scm').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual([...h.calls.select], ['scm']);
  tab('sessions').focus();
  const down = new env.window.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
  tab('sessions').dispatchEvent(down);
  assert.strictEqual(env.document.activeElement, tab('scm'));
  assert.ok(down.defaultPrevented);
  tab('scm').dispatchEvent(new env.window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  assert.strictEqual(env.document.activeElement, tab('system'));
  assert.deepStrictEqual([...h.calls.select], ['scm'], '화살표는 활동을 바꾸지 않는다');
  root.querySelector('.vt-act-mcp').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  root.querySelector('.vt-act-settings').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.strictEqual(h.calls.mcp, 1);
  assert.strictEqual(h.calls.settings, 1);
});

test('끌기 — 도구 활동만 칸으로 끌 수 있게 배선된다(세션은 아니다)', async () => {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const h = env.mountActivityBar(root);
  assert.ok(!h.calls.drag.includes('sessions'));
  assert.ok(h.calls.drag.includes('scm') && h.calls.drag.includes('queue'));
});

// ── 3부: 작업대 마운트 ──────────────────────────────────────────────────

function settingsBridge(window, initial = {}) {
  const values = { ...initial };
  const writes = [];
  window.vtSettingsGet = (k) => values[k];
  window.vtSettingsHas = (k) => Object.prototype.hasOwnProperty.call(values, k);
  // core/settings.js의 set()처럼 같은 값이면 저장하지 않는다.
  window.vtSettingsSet = (k, v) => {
    if (Object.prototype.hasOwnProperty.call(values, k) && values[k] === v) return Promise.resolve(true);
    values[k] = v; writes.push([k, v]); return Promise.resolve(true);
  };
  window.vtSettingsSubscribe = () => () => {};
  return { values, writes };
}

async function mountWb(initial) {
  const env = await fresh();
  Object.defineProperty(env.window, 'innerWidth', { value: 1440, configurable: true });
  const s = settingsBridge(env.window, initial);
  const actions = [];
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  env.mountWorkbench(root, {
    vtFetch: async () => ({}),
    getAction: (name) => () => { actions.push(name); },
    keyHint: () => '',
  });
  return { env, root, s, actions };
}

test('작업대 — 레일과 dock 본문이 함께 마운트되고 활동에 따라 hidden만 바뀐다', async () => {
  const { env, root } = await mountWb({ 'ui.side.collapsed': false });
  const doc = env.document;
  const sessions = root.querySelector('.vt-side-sessions');
  const tools = root.querySelector('.vt-side-tools');
  assert.ok(doc.getElementById('vt-wgrail'), '레일');
  assert.ok(doc.querySelector('#vt-dock .vt-dock-body'), 'dock 본문');
  assert.strictEqual(typeof env.window.vtDockHost, 'function', 'vtDockHost 계약');
  assert.strictEqual(sessions.hidden, false);
  assert.strictEqual(tools.hidden, true);
  root.querySelector('[data-activity="queue"]').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.strictEqual(sessions.hidden, true);
  assert.strictEqual(tools.hidden, false);
  assert.ok(doc.getElementById('vt-wgrail'), '세션 보기를 떠나도 레일은 DOM에 남는다');
});

test('작업대 — 활동을 바꿔도 사이드바 폭 상태가 그대로다(공유 폭)', async () => {
  const { env, root, s } = await mountWb({ 'ui.side.collapsed': false, 'ui.side.width': 400 });
  const html = env.document.documentElement;
  const snap = () => [html.dataset.vtSide, html.style.getPropertyValue('--vt-side-stored')];
  assert.deepStrictEqual(snap(), ['open', '400px']);
  for (const id of ['scm', 'queue', 'sessions', 'files']) {
    root.querySelector(`[data-activity="${id}"]`).dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
    assert.deepStrictEqual(snap(), ['open', '400px'], `${id}로 바꾼 뒤`);
  }
  assert.ok(!s.writes.some(([k]) => k === 'ui.side.width'), '활동 전환은 폭을 저장하지 않는다');
  assert.deepStrictEqual(s.writes.filter(([k]) => k === 'ui.side.activity').map(([, v]) => v), ['scm', 'queue', 'sessions', 'files']);
});

test('작업대 — 지금 활동 재클릭과 ⌘B(vtSideToggle)는 접는다: 세션은 띠, 도구는 0', async () => {
  const { env, root, s } = await mountWb({ 'ui.side.collapsed': false });
  const html = env.document.documentElement;
  const click = (id) => root.querySelector(`[data-activity="${id}"]`).dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  click('sessions');
  assert.strictEqual(html.dataset.vtSide, 'strip');
  assert.ok(env.document.getElementById('vt-wgrail').classList.contains('collapsed'), '레일의 48px 접힘 모양');
  env.window.vtSideToggle();
  assert.strictEqual(html.dataset.vtSide, 'open');
  click('queue');
  assert.strictEqual(html.dataset.vtSide, 'open');
  click('queue');
  assert.strictEqual(html.dataset.vtSide, 'closed');
  assert.strictEqual(root.querySelector('#vt-side').hidden, true);
  assert.deepStrictEqual(s.writes.filter(([k]) => k === 'ui.side.collapsed').map(([, v]) => v), [true, false, true]);
});

test('작업대 — 펼친 도구를 칸으로 끌어내면(vt-panel-pane-drop) 사이드바를 접는다', async () => {
  const { env, root } = await mountWb({ 'ui.side.collapsed': false, 'ui.side.activity': 'queue' });
  const html = env.document.documentElement;
  assert.strictEqual(html.dataset.vtSide, 'open');
  env.window.dispatchEvent(new env.window.CustomEvent('vt-panel-pane-drop', { detail: { panel: 'scm', paneId: 'p1' } }));
  assert.strictEqual(html.dataset.vtSide, 'open', '안 보이던 도구는 할 일이 없다');
  env.window.dispatchEvent(new env.window.CustomEvent('vt-panel-pane-drop', { detail: { panel: 'queue', paneId: 'p1' } }));
  assert.strictEqual(html.dataset.vtSide, 'closed');
  assert.ok(root.querySelector('[data-activity="queue"]'));
});

test('작업대 — 저장된 값이 없으면 옛 키(ui.dock.width·ui.rail.collapsed)로 시작한다', async () => {
  const { env } = await mountWb({ 'ui.dock.width': 420, 'ui.rail.collapsed': true });
  const html = env.document.documentElement;
  assert.strictEqual(html.style.getPropertyValue('--vt-side-stored'), '420px');
  assert.strictEqual(html.dataset.vtSide, 'strip');
});

test('작업대 — 레일이 센 waiting 수가 활동 막대 「세션」 배지로 간다(개입 필요 구역을 숨겨도)', async () => {
  const env = await fresh();
  Object.defineProperty(env.window, 'innerWidth', { value: 1440, configurable: true });
  settingsBridge(env.window, { 'ui.side.collapsed': false, 'rail.attention.show': false });
  const status = { a: 'waiting', b: 'waiting', c: 'working' };
  env.window.getStatus = (name) => status[name] || 'idle';
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  env.mountWorkbench(root, {
    vtFetch: async (path) => (path === '/api/tmux/sessions' ? [{ name: 'a' }, { name: 'b' }, { name: 'c' }] : {}),
    getAction: () => () => {},
    keyHint: () => '',
  });
  const badge = () => root.querySelector('[data-activity="sessions"] .vt-act-badge');
  for (let i = 0; i < 40 && !badge(); i += 1) await new Promise((r) => setTimeout(r, 5));
  assert.ok(badge(), '세션 배지가 그려진다');
  assert.strictEqual(badge().textContent, '2');
  assert.strictEqual(badge().dataset.state, 'waiting');
});

// ── 4부: 오른쪽 고정 사이드바(리디자인 9단계) ─────────────────────────────

// 패널 렌더러 흉내 — 액션이 불리면 panels/panel.js처럼 vtDockHost에 자리를 묻고 머리 + 닫기 X를 가진 루트를 붙인다.
async function mountWbPanels(initial) {
  const env = await fresh();
  Object.defineProperty(env.window, 'innerWidth', { value: 1440, configurable: true });
  const s = settingsBridge(env.window, initial);
  const doc = env.document;
  const panelOf = { 'scm.show': 'vt-dock-scm', 'queue.show': 'vt-queue', 'files.show': 'vt-files' };
  const closes = [];
  env.window.closePanel = (id) => {
    const el = doc.getElementById(id);
    if (!el) return;
    el.remove(); closes.push(id);
    doc.dispatchEvent(new env.window.CustomEvent('vt:panel-close', { detail: { id } }));
  };
  const open = (name) => {
    const id = panelOf[name];
    if (!id) return;
    if (doc.getElementById(id)) { env.window.closePanel(id); return; }
    const host = env.window.vtDockHost(id);
    if (!host || host === 'stale') return;
    const el = doc.createElement('div');
    el.id = id; el.className = 'vt-dock-panel';
    el.innerHTML = '<div class="vt-viewer-card"><div class="vt-viewer-head"><b>t</b><button class="vt-icon-btn vt-vw-x"></button></div><div class="vt-vw-body"><button class="inner">x</button></div></div>';
    host.appendChild(el);
  };
  const root = doc.createElement('div');
  doc.body.appendChild(root);
  env.mountWorkbench(root, { vtFetch: async () => ({}), getAction: (name) => () => open(name), keyHint: () => '' });
  const flush = async () => { for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0)); };
  await flush();
  return { env, doc, root, s, open, closes, flush };
}

test('고정 — 저장된 고정 패널은 오른쪽(#vt-side-right)에 열리고 활동 막대에 점이 붙는다', async () => {
  const { doc, root, flush } = await mountWbPanels({ 'ui.side.collapsed': false, 'ui.side.pinned': 'vt-dock-scm' });
  await flush();
  const right = doc.getElementById('vt-side-right');
  assert.ok(right && !right.hidden, '오른쪽 고정 사이드바');
  assert.strictEqual(doc.documentElement.dataset.vtPin, 'scm');
  assert.ok(right.querySelector('#vt-dock-scm'), '변경 패널이 오른쪽 본문에 붙는다');
  assert.ok(root.querySelector('[data-activity="scm"]').classList.contains('pinned'));
  assert.strictEqual(doc.documentElement.dataset.vtSide, 'open', '왼쪽은 세션 그대로');
  assert.ok(!root.querySelector('.vt-side-sessions').hidden);
});

test('고정 — 고정한 도구의 활동 버튼은 왼쪽 활동을 바꾸지 않는다', async () => {
  const { env, root, s, flush } = await mountWbPanels({ 'ui.side.collapsed': false, 'ui.side.pinned': 'vt-dock-scm' });
  root.querySelector('[data-activity="scm"]').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  await flush();
  assert.ok(!s.writes.some(([k]) => k === 'ui.side.activity' || k === 'ui.side.collapsed'));
  assert.ok(!root.querySelector('.vt-side-sessions').hidden);
});

test('고정 — 패널 머리 버튼으로 왼쪽 도구를 오른쪽에 고정했다가 되돌린다(노드를 옮기고 다시 열지 않는다)', async () => {
  const { doc, root, s, closes, flush } = await mountWbPanels({ 'ui.side.collapsed': false, 'ui.side.activity': 'queue' });
  await flush();
  const panel = doc.getElementById('vt-queue');
  assert.ok(panel && root.querySelector('.vt-dock-body').contains(panel), '처음에는 왼쪽 도구 보기에 있다');
  const pinBtn = () => doc.querySelector('#vt-queue .vt-viewer-head > .vt-dock-pin');
  assert.ok(pinBtn(), '머리에 고정 버튼');
  assert.strictEqual(pinBtn().nextElementSibling.classList.contains('vt-vw-x'), true, '닫기 X 바로 앞');
  assert.strictEqual(pinBtn().getAttribute('aria-pressed'), 'false');
  pinBtn().click();
  await flush();
  const right = doc.getElementById('vt-side-right');
  assert.ok(right && right.contains(panel), '같은 노드가 오른쪽으로 옮겨 갔다');
  assert.strictEqual(doc.getElementById('vt-queue'), panel);
  assert.deepStrictEqual(closes, [], '닫았다 다시 열지 않는다');
  assert.ok(!root.querySelector('.vt-side-sessions').hidden, '왼쪽은 세션으로 물러난다');
  assert.strictEqual(pinBtn().getAttribute('aria-pressed'), 'true');
  assert.deepStrictEqual(s.writes.filter(([k]) => k === 'ui.side.pinned').map(([, v]) => v), ['vt-queue']);

  pinBtn().click();
  await flush();
  assert.strictEqual(doc.getElementById('vt-side-right').hidden, true, '고정을 풀면 오른쪽이 숨는다');
  assert.ok(root.querySelector('.vt-dock-body').contains(panel), '왼쪽 도구 보기로 돌아온다');
  assert.ok(root.querySelector('.vt-side-sessions').hidden);
  assert.deepStrictEqual(s.writes.filter(([k]) => k === 'ui.side.pinned').map(([, v]) => v), ['vt-queue', '']);
});

test('고정 — 고정한 패널을 닫으면(닫기 X · 팔레트 토글) 고정이 풀린다', async () => {
  const { env, doc, s, flush } = await mountWbPanels({ 'ui.side.collapsed': false, 'ui.side.pinned': 'vt-dock-scm' });
  await flush();
  assert.ok(doc.getElementById('vt-dock-scm'));
  env.window.closePanel('vt-dock-scm');
  await flush();
  assert.strictEqual(doc.getElementById('vt-side-right').hidden, true);
  assert.strictEqual(doc.documentElement.dataset.vtPin, undefined);
  assert.deepStrictEqual(s.writes.filter(([k]) => k === 'ui.side.pinned').map(([, v]) => v), ['']);
});

test('고정 — 고정 켜기·끄기는 refit 게이트(vtSurfaceDrag)로 감싼다', async () => {
  const { env, doc, flush } = await mountWbPanels({ 'ui.side.collapsed': false, 'ui.side.activity': 'queue' });
  const phases = [];
  env.window.vtSurfaceDrag = (p) => phases.push(p);
  await flush();
  doc.querySelector('#vt-queue .vt-dock-pin').click();
  for (let i = 0; i < 10 && phases.length < 2; i += 1) await new Promise((r) => setTimeout(r, 20));
  assert.deepStrictEqual(phases, ['begin', 'end']);
});
