// shell/side-prefs.js — 작업대 사이드바의 폭·접힘·활동 규칙(순수 함수).
// 지키는 것: 폭은 활동 전체가 하나(새 키 → ui.dock.width → ui.rail.width → 360), 240~480,
// 그리는 폭은 40vw·regular 260으로 한 번 더 누르되 저장값은 안 바꾼다, 접힘 폴백·티어 기본값,
// 활동 재클릭 = 접기.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const SIDE = pathToFileURL(path.join(__dirname, '../js/shell/side-prefs.js')).href;
const load = () => import(SIDE);

// core/settings.js의 get/has 모양. has는 "사용자가 정한 값이 있는가".
function store(values) {
  return {
    get: (k) => values[k],
    has: (k) => Object.prototype.hasOwnProperty.call(values, k),
  };
}

test('폭 — 아무것도 저장 안 했으면 기본 360', async () => {
  const S = await load();
  const s = store({});
  assert.strictEqual(S.readSideWidth(s.get, s.has), 360);
  assert.strictEqual(S.SIDE_DEFAULT, 360);
});

test('폭 — 새 키(ui.side.width)가 옛 키를 이긴다', async () => {
  const S = await load();
  const s = store({ 'ui.side.width': 300, 'ui.dock.width': 420, 'ui.rail.width': 250 });
  assert.strictEqual(S.readSideWidth(s.get, s.has), 300);
});

test('폭 폴백 — 새 키가 없으면 ui.dock.width, 그것도 없으면 ui.rail.width', async () => {
  const S = await load();
  let s = store({ 'ui.dock.width': 420, 'ui.rail.width': 250 });
  assert.strictEqual(S.readSideWidth(s.get, s.has), 420);
  s = store({ 'ui.rail.width': 250 });
  assert.strictEqual(S.readSideWidth(s.get, s.has), 250);
});

test('폭 폴백 — 옛 dock 폭(320~560)이 사이드바 범위 밖이면 240~480으로 누른다', async () => {
  const S = await load();
  const s = store({ 'ui.dock.width': 560 });
  assert.strictEqual(S.readSideWidth(s.get, s.has), 480);
});

test('폭 범위 — 240 미만·480 초과·숫자가 아닌 값', async () => {
  const S = await load();
  assert.strictEqual(S.clampSideWidth(100), 240);
  assert.strictEqual(S.clampSideWidth(9999), 480);
  assert.strictEqual(S.clampSideWidth(301.6), 302);
  assert.strictEqual(S.clampSideWidth('abc'), 360);
  assert.strictEqual(S.clampSideWidth(undefined), 360);
  assert.strictEqual(S.dragSideWidth(360, -500), 240);
  assert.strictEqual(S.dragSideWidth(360, 40), 400);
});

test('그리는 폭 — wide는 min(저장 폭, 40vw)', async () => {
  const S = await load();
  assert.strictEqual(S.renderedSideWidth('open', 360, 1440), 360);
  assert.strictEqual(S.renderedSideWidth('open', 480, 1300), 480);
  // 1280px(wide 시작)의 40vw = 512
  assert.strictEqual(S.renderedSideWidth('open', 480, 1280), 480);
});

test('그리는 폭 — 어느 창 폭에서도 40vw를 넘지 않는다(저장값과 무관)', async () => {
  const S = await load();
  // 지금 범위(240~480, regular 260)에서는 40vw가 먼저 걸리는 폭이 없다(720×0.4 = 288 > 260,
  // 1280×0.4 = 512 > 480) — 그래도 상한은 계약이라 전 구간을 훑어 지켜지는지 본다.
  for (let vw = 720; vw <= 2400; vw += 7) {
    for (const stored of [240, 300, 360, 420, 480]) {
      const w = S.renderedSideWidth('open', stored, vw);
      assert.ok(w <= vw * S.SIDE_VW_RATIO, `vw ${vw}, 저장 ${stored} → ${w}`);
      assert.ok(w <= stored, '저장 폭보다 넓게 그리지 않는다');
    }
  }
  assert.strictEqual(S.SIDE_VW_RATIO, 0.4);
});

test('그리는 폭 — regular(720~1279)는 min(저장 폭, 260, 40vw)', async () => {
  const S = await load();
  assert.strictEqual(S.renderedSideWidth('open', 360, 1024), 260);
  assert.strictEqual(S.renderedSideWidth('open', 240, 1024), 240);
  assert.strictEqual(S.renderedSideWidth('open', 480, 720), 260);  // 40vw = 288
  assert.strictEqual(S.renderedSideWidth('open', 480, 1279), 260);
});

test('그리는 폭 — 접힘(세션 48 띠 / 도구 0)과 compact(작업대 없음)', async () => {
  const S = await load();
  assert.strictEqual(S.renderedSideWidth('strip', 360, 1440), 48);
  assert.strictEqual(S.renderedSideWidth('closed', 360, 1440), 0);
  assert.strictEqual(S.renderedSideWidth('open', 360, 719), 0);
  assert.strictEqual(S.renderedSideWidth('strip', 360, 390), 0);
});

test('표시 상태 — 세션을 접으면 띠, 도구를 접으면 0', async () => {
  const S = await load();
  assert.strictEqual(S.sideMode(false, 'sessions'), 'open');
  assert.strictEqual(S.sideMode(false, 'queue'), 'open');
  assert.strictEqual(S.sideMode(true, 'sessions'), 'strip');
  assert.strictEqual(S.sideMode(true, 'queue'), 'closed');
});

test('접힘 — 새 키 → ui.rail.collapsed → 티어 기본(wide 미만이면 접힘)', async () => {
  const S = await load();
  let s = store({ 'ui.side.collapsed': false, 'ui.rail.collapsed': true });
  assert.strictEqual(S.readSideCollapsed(s.get, s.has, 1024), false);
  s = store({ 'ui.rail.collapsed': true });
  assert.strictEqual(S.readSideCollapsed(s.get, s.has, 1440), true);
  s = store({});
  assert.strictEqual(S.readSideCollapsed(s.get, s.has, 1024), true, 'regular는 저장값이 없으면 접힘으로 시작');
  assert.strictEqual(S.readSideCollapsed(s.get, s.has, 1279), true);
  assert.strictEqual(S.readSideCollapsed(s.get, s.has, 1280), false, 'wide는 펼침으로 시작');
  assert.strictEqual(S.readSideCollapsed(s.get, s.has, 1920), false);
});

test('활동 — 기본은 세션, 쓸 수 없는 활동이면 세션으로 물러난다(저장값은 그대로)', async () => {
  const S = await load();
  assert.strictEqual(S.readSideActivity(store({}).get, store({}).has), 'sessions');
  const s = store({ 'ui.side.activity': 'ports' });
  assert.strictEqual(S.readSideActivity(s.get, s.has), 'ports');
  assert.strictEqual(S.effectiveActivity('ports', ['sessions', 'scm', 'queue']), 'sessions');
  assert.strictEqual(S.effectiveActivity('ports', ['sessions', 'ports']), 'ports');
});

test('활동 클릭 — 펼친 상태의 지금 활동 = 접기, 그 밖은 그 활동으로 펼침', async () => {
  const S = await load();
  const same = S.nextOnActivityClick({ activity: 'queue', collapsed: false }, 'queue');
  assert.strictEqual(same.collapsed, true);
  assert.strictEqual(same.activity, 'queue');
  const other = S.nextOnActivityClick({ activity: 'queue', collapsed: false }, 'scm');
  assert.strictEqual(other.collapsed, false);
  assert.strictEqual(other.activity, 'scm');
  const reopen = S.nextOnActivityClick({ activity: 'sessions', collapsed: true }, 'sessions');
  assert.strictEqual(reopen.collapsed, false, '접힌 띠에서 세션을 누르면 펼친다');
  const fromClosed = S.nextOnActivityClick({ activity: 'queue', collapsed: true }, 'sessions');
  assert.strictEqual(fromClosed.collapsed, false);
  assert.strictEqual(fromClosed.activity, 'sessions');
});

// ── 오른쪽 고정 사이드바(리디자인 9단계) ──────────────────────────────────
test('고정 — 저장값은 패널 id, 없으면 빈 문자열', async () => {
  const S = await load();
  assert.strictEqual(S.SIDE_PINNED_KEY, 'ui.side.pinned');
  assert.strictEqual(S.PIN_W, 300);
  let s = store({});
  assert.strictEqual(S.readSidePinned(s.get, s.has), '');
  s = store({ 'ui.side.pinned': 'vt-dock-scm' });
  assert.strictEqual(S.readSidePinned(s.get, s.has), 'vt-dock-scm');
});

test('고정 — 지금 쓸 수 있는 도구 활동일 때만 그 활동 id, 아니면 null(저장값은 안 바꾼다)', async () => {
  const S = await load();
  const avail = [
    { id: 'sessions', panelId: null }, { id: 'scm', panelId: 'vt-dock-scm' }, { id: 'queue', panelId: 'vt-queue' },
  ];
  assert.strictEqual(S.effectivePinned('vt-dock-scm', avail), 'scm');
  assert.strictEqual(S.effectivePinned('', avail), null);
  assert.strictEqual(S.effectivePinned('vt-ports', avail), null, '게이팅된(목록에 없는) 도구');
  assert.strictEqual(S.effectivePinned('nonsense', avail), null);
});

test('고정 — 표시 방식: wide는 칸 옆, regular는 떠 있는 서랍, compact는 없음', async () => {
  const S = await load();
  assert.strictEqual(S.pinnedMode(1440), 'docked');
  assert.strictEqual(S.pinnedMode(1280), 'docked');
  assert.strictEqual(S.pinnedMode(1279), 'drawer');
  assert.strictEqual(S.pinnedMode(720), 'drawer');
  assert.strictEqual(S.pinnedMode(719), 'none');
});
