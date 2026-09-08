// ADR-8 패리티 가드 — rail(포인터 경로)과 커맨드 팔레트(키보드 경로)는
// "내용이 동일"해야 한다(docs/plan-2.0/30-layout-shell.md §3, ADR-8).
//
// 왜 이 파일이 생겼나: 2026-09-08에 프롬프트 스니펫이 팔레트에만 있고 rail에는
// 없는 상태로 2.0.0이 나갔다. `snippets.js`는 2.0 재구조화에서 ESM으로 잘
// 이식됐고 팔레트 항목도 받았지만, 폐지된 ⋯ 메뉴를 대신할 rail 버튼을
// 못 받았다. 기존 테스트는 rail은 rail대로(rail.test.js), 팔레트는 팔레트대로
// (quickopen.test.js) 각자만 검사해서 "둘 사이가 어긋났다"를 아무도 못 봤다.
// 그래서 두 경로를 교차로 비교하는 테스트를 따로 둔다.
//
// 소스(index.html 마크업 + quickopen.js 텍스트)를 직접 읽는 정적 검사다 —
// 모듈 로드 순서나 capability 게이트에 영향받지 않아야 패리티 자체를 볼 수 있다.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const QUICKOPEN_SRC = fs.readFileSync(path.join(__dirname, '../js/quickopen.js'), 'utf8');

// rail 버튼 중 "패널/모달을 여는" 것들 — data-action을 단 버튼이 그것이다.
// 'session'/'settings'는 rail이 직접 그리는 임베디드 패널이라 data-action이
// 없고(팔레트에도 같은 형태로 없다) 이 비교 대상이 아니다.
function railPanelActions() {
  const doc = new JSDOM(INDEX_HTML).window.document;
  const btns = doc.querySelectorAll('#vt-rail .vt-rail-btn[data-action]');
  return new Set([...btns].map(b => b.dataset.action));
}

// quickopen.js의 _quickOpenCommands() 배열에서 `action: '...'`를 걷어온다.
// 팔레트에는 rail에 없는 명령(검색·음성 전용·파일 업로드·새 세션)도 있으므로,
// 교집합이 아니라 "패널을 여는 명령"만 골라 비교한다 — 그 기준이 `.show`/`.open`
// 접미사다(viewer.show/queue.show/snippets.show/ports.show/usage.open).
function palettePanelActions() {
  const body = QUICKOPEN_SRC.slice(
    QUICKOPEN_SRC.indexOf('function _quickOpenCommands()'),
  );
  const end = body.indexOf('return cmds');
  assert.ok(end > 0, '_quickOpenCommands()의 형태가 바뀌었다 — 이 테스트를 갱신할 것');
  const found = [...body.slice(0, end).matchAll(/action:\s*'([\w.-]+)'/g)].map(m => m[1]);
  return new Set(found.filter(a => a.endsWith('.show') || a.endsWith('.open')));
}

test('rail의 패널 버튼과 팔레트의 패널 명령이 정확히 일치한다 (ADR-8)', () => {
  const rail = railPanelActions();
  const palette = palettePanelActions();

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
    'quickopen.js의 _quickOpenCommands()에 같은 action을 추가할 것(ADR-8)'
  );
});

test('스니펫이 양쪽 경로 모두에 존재한다', () => {
  // 위 테스트가 이미 커버하지만, 이 회귀가 실제로 일어났던 항목이라
  // 이름을 박아 고정한다 — 일반 규칙이 느슨해져도 이건 남는다.
  assert.ok(railPanelActions().has('snippets.show'), 'rail에 스니펫 버튼이 있어야 한다');
  assert.ok(palettePanelActions().has('snippets.show'), '팔레트에 스니펫 명령이 있어야 한다');
});
