// grid.js는 ansiToHtml/_trimBlankLines를 이제 ansilex.js(window.VTAnsiLex)에서 가져와
// 쓴다(D13 리팩터 — 순수 로직을 분리해 ansilex.test.js가 직접 단위 테스트할 수 있게 함).
//
// grid.js 전체를 vm으로 실행하는 건 시도해봤으나 WebSocket 재연결·setTimeout 등
// 이 파일의 관심사와 무관한 런타임 API까지 계속 스텁해야 해서 깨지기 쉬웠다.
// 대신 이 테스트는 "배선 두 줄"(ansiToHtml/_trimBlankLines를 VTAnsiLex에서 꺼내오는
// const 선언)만 소스에서 그대로 추출해 VTAnsiLex 로드 컨텍스트에서 실행한다 —
// grid.js의 나머지 부분(WS/DOM)을 건드리지 않고도 실제 프로덕션 소스 문자열로
// "이 두 식별자가 VTAnsiLex와 정확히 같은 함수를 가리키는지"를 검증할 수 있다.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const gridSrc = fs.readFileSync(path.join(__dirname, '../js/grid.js'), 'utf8');

function extractWiringLines(src) {
  const start = src.indexOf('const ansiToHtml = VTAnsiLex.ansiToHtml;');
  const end = src.indexOf('const _trimBlankLines = VTAnsiLex.trimBlankLines;');
  assert.ok(start >= 0 && end >= 0, 'grid.js에서 VTAnsiLex 배선 코드를 못 찾음 — 리팩터가 되돌려졌을 수 있음');
  const lineEnd = src.indexOf('\n', end);
  return src.slice(start, lineEnd);
}

function loadWiredContext() {
  const A = require('../js/ansilex.js');
  const sandbox = { VTAnsiLex: A, console };
  const ctx = vm.createContext(sandbox);
  // top-level const/let은 vm 컨텍스트 객체의 own property로 안 남는다(전역 렉시컬
  // 환경에만 묶임) — var로 다시 노출해야 테스트에서 꺼내볼 수 있다.
  const code = extractWiringLines(gridSrc) + '\nvar __wired = { ansiToHtml: ansiToHtml, _trimBlankLines: _trimBlankLines };';
  vm.runInContext(code, ctx, { filename: 'grid.js (wiring excerpt)' });
  return ctx.__wired;
}

test('grid.js는 VTAnsiLex.ansiToHtml/trimBlankLines를 그대로 재노출한다(스코프 안 깨짐)', () => {
  const wired = loadWiredContext();
  const A = require('../js/ansilex.js');
  assert.strictEqual(wired.ansiToHtml, A.ansiToHtml);
  assert.strictEqual(wired._trimBlankLines, A.trimBlankLines);
});

test('grid.js는 VTAnsiLex가 로드 안 된 상태로 실행되면 ReferenceError를 던진다', () => {
  const ctx = vm.createContext({ console });
  assert.throws(
    () => vm.runInContext(extractWiringLines(gridSrc), ctx, { filename: 'grid.js (wiring excerpt)' }),
    /VTAnsiLex is not defined/,
    'ansilex.js가 grid.js보다 먼저 로드돼야 하는 index.html 순서 의존성을 이 실패가 방증한다'
  );
});

test('grid.js 소스는 ansiToHtml/_trimBlankLines를 더 이상 직접 정의하지 않는다', () => {
  // 리팩터 전 잔재(중복 정의)가 남으면 VTAnsiLex 배선이 죽은 코드가 된다.
  assert.ok(!/function ansiToHtml\(/.test(gridSrc));
  assert.ok(!/function _trimBlankLines\(/.test(gridSrc));
});
