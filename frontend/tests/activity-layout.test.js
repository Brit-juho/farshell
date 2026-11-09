// 활동 막대 배치(shell/activity-layout.js) — 설정 › 상태 막대 · 활동 막대의 「활동 막대 순서」(시안 O05).
// 1부: 순수 규칙(파싱·저장·옮기기·숨김·막대 순서). 2부: 작업대가 그 배치를 따르는지(활동 막대 DOM).
// 설정 화면 쪽(편집기)은 settings-panel.test.js가 본다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });
async function fresh() { const env = await loadSolidBuild(); _doms.push(env); return env; }

const ALL = ['sessions', 'scm', 'dir', 'queue', 'files', 'snippets', 'ports', 'usage', 'system'];
const plain = (l) => ({ order: Array.from(l.order), hidden: Array.from(l.hidden), bottom: Array.from(l.bottom) });

test('활동 id 목록이 활동 막대(ACTIVITIES)·설정 스키마와 같고, 저장 키는 기기 설정이다', async () => {
  const { ACTIVITY_IDS, ACTIVITIES, SETTINGS_SCHEMA } = await fresh();
  assert.deepStrictEqual(Array.from(ACTIVITY_IDS), Array.from(ACTIVITIES, (a) => a.id));
  assert.deepStrictEqual(Array.from(ACTIVITY_IDS), Array.from(SETTINGS_SCHEMA['ui.side.activity'].values));
  const spec = SETTINGS_SCHEMA['ui.activity.layout'];
  assert.strictEqual(spec.type, 'str');
  assert.strictEqual(spec.def, '');
  assert.strictEqual(spec.scope, 'device', '화면 폭·쓰는 도구가 기기마다 달라 폰에서 바꾼 순서가 맥에 새면 안 된다');
});

test('파싱 — 비었거나 깨졌거나 모양이 틀리면 기본 배치(막대가 비지 않는다)', async () => {
  const { parseActivityLayout, BOTTOM_IDS } = await fresh();
  const d = { order: ALL, hidden: [], bottom: Array.from(BOTTOM_IDS) };
  for (const raw of ['', '   ', '{', 'null', '[]', '"x"', undefined, null, 42, { order: 'x' }]) {
    assert.deepStrictEqual(plain(parseActivityLayout(raw)), d, JSON.stringify(raw));
  }
});

test('파싱 — 세션은 늘 맨 위이고 숨길 수 없다, 모르는 id·중복은 버리고 빠진 활동은 기본 순서로 끝에 붙는다', async () => {
  const { parseActivityLayout } = await fresh();
  const l = parseActivityLayout(JSON.stringify({
    order: ['queue', 'sessions', 'nope', 'scm', 'queue'], hidden: ['sessions', 'usage', 'bogus', 'usage'], bottom: ['settings', 'x'],
  }));
  assert.deepStrictEqual(Array.from(l.order), ['sessions', 'queue', 'scm', 'dir', 'files', 'snippets', 'ports', 'usage', 'system']);
  assert.deepStrictEqual(Array.from(l.hidden), ['usage']);
  assert.deepStrictEqual(Array.from(l.bottom), ['settings', 'mcp', 'mic']);
});

test('저장 — 기본과 같으면 빈 문자열(기본을 따른다), 다르면 세대 표시가 붙은 JSON이고 다시 읽으면 같다', async () => {
  const { parseActivityLayout, serializeActivityLayout, setActivityHidden, isDefaultActivityLayout } = await fresh();
  const d = parseActivityLayout('');
  assert.ok(isDefaultActivityLayout(d));
  assert.strictEqual(serializeActivityLayout(d), '');
  const h = setActivityHidden(d, 'ports', true);
  const raw = serializeActivityLayout(h);
  assert.strictEqual(JSON.parse(raw).v, 1);
  assert.deepStrictEqual(plain(parseActivityLayout(raw)), plain(h));
  assert.strictEqual(serializeActivityLayout(setActivityHidden(h, 'ports', false)), '', '되돌리면 다시 기본');
});

test('옮기기 — ↑↓는 같은 무리 안에서 한 칸, 세션 자리(0)는 넘보지 못하고 끝에서는 그대로다', async () => {
  const { parseActivityLayout, stepActivity, moveActivity } = await fresh();
  const d = parseActivityLayout('');
  assert.strictEqual(stepActivity(d, 'scm', -1), d, '세션 바로 아래는 더 못 올라간다');
  assert.strictEqual(stepActivity(d, 'system', 1), d, '맨 끝은 더 못 내려간다');
  assert.strictEqual(stepActivity(d, 'sessions', 1), d, '세션은 옮기지 않는다');
  assert.strictEqual(stepActivity(d, 'nope', 1), d);
  const s1 = stepActivity(d, 'queue', -1);
  assert.deepStrictEqual(Array.from(s1.order).slice(0, 4), ['sessions', 'scm', 'queue', 'dir']);
  assert.deepStrictEqual(Array.from(d.order), ALL, '입력은 바꾸지 않는다');
  // 끌어 놓기 — 0(세션 자리)으로 놓아도 1로 들어간다, 범위를 넘으면 끝.
  assert.deepStrictEqual(Array.from(moveActivity(d, 'system', 0).order).slice(0, 2), ['sessions', 'system']);
  assert.strictEqual(Array.from(moveActivity(d, 'scm', 99).order).at(-1), 'scm');
  // 아래 무리는 아래 무리 안에서만.
  const b = stepActivity(d, 'settings', -1);
  assert.deepStrictEqual(Array.from(b.bottom), ['mcp', 'settings', 'mic']);
  assert.deepStrictEqual(Array.from(b.order), ALL);
  assert.strictEqual(stepActivity(d, 'mcp', -1), d);
});

test('숨김 — 위 무리만, 세션·아래 무리·모르는 id는 그대로', async () => {
  const { parseActivityLayout, setActivityHidden } = await fresh();
  const d = parseActivityLayout('');
  assert.strictEqual(setActivityHidden(d, 'sessions', true), d);
  assert.strictEqual(setActivityHidden(d, 'settings', true), d, '설정 버튼을 숨기면 되돌릴 화면이 사라진다');
  assert.strictEqual(setActivityHidden(d, 'nope', true), d);
  const h = setActivityHidden(setActivityHidden(d, 'queue', true), 'queue', true);
  assert.deepStrictEqual(Array.from(h.hidden), ['queue'], '두 번 숨겨도 한 번');
});

test('막대 순서 — 지금 쓸 수 있는 활동만 배치 순서로, 숨긴 것은 빼고, 아래 무리는 CSS order 값', async () => {
  const { parseActivityLayout, orderedActivities, bottomOrderOf, availableActivities } = await fresh();
  const l = parseActivityLayout(JSON.stringify({ order: ['system', 'scm'], hidden: ['queue', 'usage'], bottom: ['settings', 'mic', 'mcp'] }));
  const ids = Array.from(orderedActivities(availableActivities({ ports: true }), l), (a) => a.id);
  assert.deepStrictEqual(ids, ['sessions', 'system', 'scm', 'dir', 'files', 'snippets', 'ports']);
  assert.strictEqual(bottomOrderOf(l, 'settings'), 0);
  assert.strictEqual(bottomOrderOf(l, 'mcp'), 2);
  assert.strictEqual(bottomOrderOf(parseActivityLayout(''), 'mic'), 1);
});

// ── 2부: 작업대가 배치를 따른다 ───────────────────────────────────────────

function bridge(window, initial = {}) {
  const values = { ...initial };
  const subs = new Set();
  window.vtSettingsGet = (k) => values[k];
  window.vtSettingsHas = (k) => Object.prototype.hasOwnProperty.call(values, k);
  window.vtSettingsSet = (k, v) => { values[k] = v; for (const fn of subs) fn({ [k]: v }); return Promise.resolve(true); };
  window.vtSettingsSubscribe = (fn) => { subs.add(fn); return () => subs.delete(fn); };
  return { values, subs };
}

async function mountWb(initial) {
  const env = await fresh();
  Object.defineProperty(env.window, 'innerWidth', { value: 1440, configurable: true });
  const s = bridge(env.window, initial);
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const actions = [];
  const dispose = env.mountWorkbench(root, { vtFetch: async () => ({}), getAction: (n) => () => { actions.push(n); }, keyHint: () => '' });
  return { env, root, s, actions, dispose };
}
const barIds = (root) => [...root.querySelectorAll('.vt-act-list [role="tab"]')].map((b) => b.dataset.activity);

test('작업대 — 저장된 배치대로 활동 막대를 그리고, 바뀌면 다시 그린다(새로고침 없이)', async () => {
  const layout = JSON.stringify({ v: 1, order: ['sessions', 'files', 'scm'], hidden: ['queue', 'system'], bottom: ['settings', 'mcp', 'mic'] });
  const { env, root } = await mountWb({ 'ui.side.collapsed': false, 'ui.activity.layout': layout });
  assert.deepStrictEqual(barIds(root), ['sessions', 'files', 'scm', 'dir', 'snippets']);
  assert.strictEqual(root.querySelector('.vt-act-settings').style.order, '0');
  assert.strictEqual(root.querySelector('.vt-act-mcp').style.order, '1');
  assert.strictEqual(root.querySelector('#vt-activity-mic-home').style.order, '2');
  env.window.vtSettingsSet('ui.activity.layout', '');
  assert.deepStrictEqual(barIds(root), ['sessions', 'scm', 'dir', 'queue', 'files', 'snippets', 'system']);
  assert.strictEqual(root.querySelector('.vt-act-mcp').style.order, '0');
});

test('작업대 — 숨긴 활동도 저장된 지금 활동이면 사이드바에 그대로 열린다(팔레트로 연 경우)', async () => {
  const { root, env } = await mountWb({
    'ui.side.collapsed': false, 'ui.side.activity': 'queue',
    'ui.activity.layout': JSON.stringify({ v: 1, hidden: ['queue'] }),
  });
  assert.ok(!barIds(root).includes('queue'), '막대에는 없다');
  assert.strictEqual(root.querySelector('.vt-side-tools').hidden, false, '도구 보기가 열려 있다');
  assert.strictEqual(root.querySelector('.vt-side-sessions').hidden, true);
  assert.strictEqual(env.document.documentElement.dataset.vtSide, 'open');
});

test('작업대 — 사라질 때 설정 구독을 끊는다', async () => {
  const { s, dispose } = await mountWb({});
  const n = s.subs.size;
  assert.ok(n >= 1);
  dispose();
  assert.ok(s.subs.size < n);
});
