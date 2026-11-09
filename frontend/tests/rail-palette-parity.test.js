// ADR-8 패리티 가드 — 포인터 경로와 커맨드 팔레트(키보드 경로)는 "내용이 동일"해야 한다.
//
// 왜 이 파일이 생겼나: 2026-09-08에 프롬프트 스니펫이 팔레트에만 있고 rail에는
// 없는 상태로 2.0.0이 나갔다. 기존 테스트는 rail은 rail대로, 팔레트는 팔레트대로
// 각자만 검사해서 "둘 사이가 어긋났다"를 아무도 못 봤다. 그래서 두 경로를 교차로
// 비교하는 테스트를 따로 둔다.
//
// 리디자인 9단계(2026-11) — 포인터 경로가 index.html의 옛 아이콘 레일(#vt-rail .vt-rail-btn)에서
// 작업대 활동 막대(shell/activity-data.ts의 ACTIVITIES)로 바뀌었다. 옛 레일 DOM은 지웠으므로 비교 대상도
// 데이터로 옮긴다 — 활동 막대의 도구 활동(사이드바에 패널을 여는 것) ↔ 팔레트의 패널 명령(isPanel).
// 둘 다 같은 vite 테스트 빌드(helpers/solid-env)에서 진짜 값을 읽는다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

let _mod = null;
async function mod() {
  if (!_mod) {
    _mod = await loadSolidBuild();
    _doms.push(_mod);
  }
  return _mod;
}

// 활동 막대의 도구 활동이 여는 액션. 「세션」은 사이드바가 직접 그리는 보기라 액션이 없다.
async function activityActions() {
  const { TOOL_ACTIVITIES } = await mod();
  return new Set(TOOL_ACTIVITIES.map((a) => a.action).filter(Boolean));
}

async function palettePanelActions() {
  const { DEFAULT_COMMANDS, panelCommands } = await mod();
  return new Set(panelCommands(DEFAULT_COMMANDS).map((c) => c.action));
}

test('활동 막대의 도구 활동과 팔레트의 패널 명령이 정확히 일치한다 (ADR-8)', async () => {
  const acts = await activityActions();
  const palette = await palettePanelActions();

  const onlyInPalette = [...palette].filter((a) => !acts.has(a));
  const onlyInBar = [...acts].filter((a) => !palette.has(a));

  assert.deepStrictEqual(
    onlyInPalette, [],
    `팔레트에만 있고 활동 막대에 없는 명령: ${onlyInPalette.join(', ')} — ` +
    'shell/activity-data.ts의 ACTIVITIES에 같은 action의 도구 활동을 추가할 것(ADR-8)'
  );
  assert.deepStrictEqual(
    onlyInBar, [],
    `활동 막대에만 있고 팔레트에 없는 명령: ${onlyInBar.join(', ')} — ` +
    'shell/palette-data.ts의 DEFAULT_COMMANDS에 같은 action을 isPanel:true로 추가할 것(ADR-8)'
  );
});

test('스니펫이 양쪽 경로 모두에 존재한다', async () => {
  // 위 테스트가 이미 커버하지만, 이 회귀가 실제로 일어났던 항목이라
  // 이름을 박아 고정한다 — 일반 규칙이 느슨해져도 이건 남는다.
  assert.ok((await activityActions()).has('snippets.show'), '활동 막대에 스니펫이 있어야 한다');
  assert.ok((await palettePanelActions()).has('snippets.show'), '팔레트에 스니펫 명령이 있어야 한다');
});
