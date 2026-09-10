// ADR-26/N35 — 빌드 산출물 자체를 검사한다. vm-esm.js 하네스(다른 테스트
// 전부)는 import()마다 매번 새 모듈을 만들지 않고 하나의 cache Map을 공유해서
// 링크하므로, "정적 import를 되돌리면 core/store.js가 청크마다 복제된다"는
// 실제 버그를 재현하지 못한다(panels-viewer-lazy.test.js 작성 중 실측 확인 —
// 그 테스트를 일부러 옛 코드로 되돌려도 통과했다). Vite lib 모드가 entry를
// 항상 완전히 인라인하는 것 자체가 원인이라, **진짜 빌드 출력**을 열어보는
// 것 말고는 이 클래스의 회귀를 잡을 방법이 없다.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '../..');
const DIST = path.join(REPO_ROOT, 'frontend/dist');

// 이 파일 하나만 실제로 프로덕션 빌드를 돌린다(다른 테스트는 --mode test의
// 별도 산출물을 쓴다) — 무겁지만(약 0.5초) 산출물 검사엔 진짜 산출물이 필요하다.
execFileSync('npx', ['vite', 'build'], { cwd: REPO_ROOT, stdio: 'pipe' });

const appJs = fs.readFileSync(path.join(DIST, 'app.js'), 'utf8');
const panelsJs = fs.readFileSync(path.join(DIST, 'panels.js'), 'utf8');

test('app.js가 300KiB 상한(307200B) 이내다 — CI(release.yml)와 같은 기준', () => {
  const size = Buffer.byteLength(appJs, 'utf8');
  assert.ok(size <= 307200, `app.js가 ${size}B — 상한 307200B를 넘었다`);
});

test('panels.js(코드 뷰어 지연 청크)가 core/store.js의 세션 싱글톤을 복제하지 않는다', () => {
  // N35 실측 버그의 정확한 시그니처: 복제되면 core/store.js의 모듈 스코프
  // 변수 선언이 panels.js 안에 그대로 나타난다(`const sessions = {}` +
  // `let activeId = null`). 복제되면 panels/viewer/tree.js의 activeSessionId()가
  // 항상 빈 사본을 보게 되어 "터미널 위치에서 열기"가 조용히 죽는다.
  assert.doesNotMatch(panelsJs, /const sessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(panelsJs, /let activeId\s*=\s*null/, 'core/store.js의 activeId가 복제됐다');
});

test('panels.js가 core/dom.js의 액션 레지스트리를 복제하지 않는다', () => {
  // registerAction 자체는 하나뿐이라도(뷰어의 registerAction('viewer.show', …)는
  // 실행 시점엔 무해하다 — viewer-lazy.js의 래퍼가 getAction() 조회에 기대지
  // 않고 항상 로드된 모듈을 직접 부른다), Map을 새로 만드는 코드가 있다는 건
  // 다른 core 싱글톤도 같이 새겼다는 신호라 함께 확인해 둔다.
  assert.doesNotMatch(panelsJs, /const registry\s*=\s*new Map\(\)/, 'core/dom.js의 액션 레지스트리가 복제됐다');
});

test('app.js는 실제 세션 싱글톤을 그대로 갖고 있다(위 검사가 "아예 없어져서" 통과한 게 아님을 확인)', () => {
  assert.match(appJs, /const sessions\s*=\s*\{\}/);
  assert.match(appJs, /let activeId\s*=\s*null/);
});
