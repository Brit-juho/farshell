// ADR-8 패리티 가드 — rail(포인터 경로)과 커맨드 팔레트(키보드 경로)는
// "내용이 동일"해야 한다(docs/plan-2.0/30-layout-shell.md §3, ADR-8).
//
// 왜 이 파일이 생겼나: 2026-09-08에 프롬프트 스니펫이 팔레트에만 있고 rail에는
// 없는 상태로 2.0.0이 나갔다. 기존 테스트는 rail은 rail대로(rail.test.js),
// 팔레트는 팔레트대로(quickopen.test.js) 각자만 검사해서 "둘 사이가 어긋났다"를
// 아무도 못 봤다. 그래서 두 경로를 교차로 비교하는 테스트를 따로 둔다.
//
// N5/N40/N46 — quickopen.js(vanilla)가 shell/Palette.tsx(+palette-data.ts)로
// 옮겨가면서, 이전엔 quickopen.js 소스 텍스트를 정규식으로 긁어 비교했다.
// 이제 palette-data.ts가 DEFAULT_COMMANDS를 **데이터**(정규식 대상 텍스트가
// 아니라 실제 값)로 export하므로, 같은 vite 테스트 빌드(helpers/solid-env)를
// 통해 진짜 값을 비교한다 — 소스 텍스트 패턴이 바뀌어도 안 깨지는 더 튼튼한
// 검사다. rail 쪽은 여전히 index.html 마크업을 정적으로 읽는다(모듈 로드
// 순서·capability 게이트에 영향받지 않아야 패리티 자체를 볼 수 있다는 원래
// 이유가 그대로 적용된다).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { loadSolidBuild } = require('./helpers/solid-env');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

// rail 버튼 중 "패널/모달을 여는" 것들 — data-action을 단 버튼이 그것이다.
// 'session'/'settings'는 rail이 직접 그리는 임베디드 패널이라 data-action이
// 없고(팔레트에도 같은 형태로 없다) 이 비교 대상이 아니다.
function railPanelActions() {
  const doc = new JSDOM(INDEX_HTML).window.document;
  const btns = doc.querySelectorAll('#vt-rail .vt-rail-btn[data-action]');
  return new Set([...btns].map(b => b.dataset.action));
}

let _mod = null;
async function mod() {
  if (!_mod) {
    _mod = await loadSolidBuild();
    _doms.push(_mod);
  }
  return _mod;
}

async function palettePanelActions() {
  const { DEFAULT_COMMANDS, panelCommands } = await mod();
  return new Set(panelCommands(DEFAULT_COMMANDS).map((c) => c.action));
}

test('rail의 패널 버튼과 팔레트의 패널 명령이 정확히 일치한다 (ADR-8)', async () => {
  const rail = railPanelActions();
  const palette = await palettePanelActions();

  const onlyInPalette = [...palette].filter(a => !rail.has(a));
  const onlyInRail = [...rail].filter(a => !palette.has(a));

  assert.deepStrictEqual(
    onlyInPalette, [],
    `팔레트에만 있고 rail에 없는 명령: ${onlyInPalette.join(', ')} — ` +
    'index.html의 #vt-rail에 같은 data-action을 단 버튼을 추가할 것(ADR-8)'
  );
  assert.deepStrictEqual(
    onlyInRail, [],
    `rail에만 있고 팔레트에 없는 명령: ${onlyInRail.join(', ')} — ` +
    'shell/palette-data.ts의 DEFAULT_COMMANDS에 같은 action을 isPanel:true로 추가할 것(ADR-8)'
  );
});

test('스니펫이 양쪽 경로 모두에 존재한다', async () => {
  // 위 테스트가 이미 커버하지만, 이 회귀가 실제로 일어났던 항목이라
  // 이름을 박아 고정한다 — 일반 규칙이 느슨해져도 이건 남는다.
  assert.ok(railPanelActions().has('snippets.show'), 'rail에 스니펫 버튼이 있어야 한다');
  assert.ok((await palettePanelActions()).has('snippets.show'), '팔레트에 스니펫 명령이 있어야 한다');
});
