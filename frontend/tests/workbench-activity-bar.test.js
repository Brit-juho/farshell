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
