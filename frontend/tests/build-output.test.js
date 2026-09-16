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
//
// ⚠ **voice 빌드까지 이어서 돌려야 한다.** app 빌드는 emptyOutDir이라 dist를
// 통째로 비우는데(vite.config.js), 여기서 app만 돌리면 `frontend/dist/voice.js`가
// 지워진 채 남는다. 게이트 순서가 `npm run build && npm test`라 **테스트를 돌린
// 뒤의 트리는 항상 voice.js가 없는 상태**가 되고, 그 트리로 서버를 띄우면
// 브라우저가 매번 `/static/dist/voice.js` 404를 찍는다(실브라우저 스모크의
// "JS 에러 없이 뜬다"가 이것 때문에 실패했다 — 2026-09-13에 추적).
execFileSync('npx', ['vite', 'build'], { cwd: REPO_ROOT, stdio: 'pipe' });
execFileSync('npx', ['vite', 'build'], {
  cwd: REPO_ROOT, stdio: 'pipe', env: { ...process.env, VOICE_BUILD: '1' },
});

const appJs = fs.readFileSync(path.join(DIST, 'app.js'), 'utf8');
const coreJs = fs.readFileSync(path.join(DIST, 'core.js'), 'utf8');
const panelsJs = fs.readFileSync(path.join(DIST, 'panels.js'), 'utf8');
const settingsJs = fs.readFileSync(path.join(DIST, 'settings.js'), 'utf8');
const shellJs = fs.readFileSync(path.join(DIST, 'shell.js'), 'utf8');

// 2.1.6 — 상한의 대상이 app.js 하나에서 **초기 페이로드 합계**로 바뀌었다.
// 청크 규칙을 뒤집으면서(vite.config.js) app.js는 부팅 배선만 남은 2KB가 되고
// 상시 코드는 전부 core.js로 갔다. 여기서 app.js만 재면 상한이 아무것도
// 막지 못한다 — 숫자 307200은 그대로 두되 **재는 대상**을 뜻에 맞춘다.
const EAGER_LIMIT = 307200;

test('초기 페이로드(app.js + core.js)가 300KiB 상한 이내다 — CI(release.yml)와 같은 기준', () => {
  const size = Buffer.byteLength(appJs, 'utf8') + Buffer.byteLength(coreJs, 'utf8');
  assert.ok(size <= EAGER_LIMIT, `app.js+core.js가 ${size}B — 상한 ${EAGER_LIMIT}B를 넘었다`);
});

test('app.js가 지연 청크를 정적으로 import하지 않는다 — "지연이 지연이 아닌" 상태의 유일한 시그니처', () => {
  // 이 검사가 이 파일에서 가장 중요하다. 지연 청크가 공유 모듈을 끌어올리면
  // entry가 그 청크를 정적 import하게 되는데, **빌드는 성공하고 크기 게이트도
  // 통과한다**(app.js가 오히려 작아지니까). 실측한 사고: snippets.js 하나를
  // 지연시키자 app.js가 0.04KB 스텁이 되고 panels.js가 215KB로 부풀었다.
  const statics = [...appJs.matchAll(/^import\s[^;]*?from\s*["']\.\/([\w.-]+)["']/gm)].map((m) => m[1]);
  for (const dep of statics) {
    assert.strictEqual(dep, 'core.js',
      `app.js가 ./${dep}를 정적 import한다 — 지연 청크가 상시 로드로 승격됐다`);
  }
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

test('core.js는 실제 세션 싱글톤을 그대로 갖고 있다(위 검사가 "아예 없어져서" 통과한 게 아님을 확인)', () => {
  // 2.1.6 전에는 app.js가 상시 코드 전부였다. 지금은 core.js가 그 자리다.
  assert.match(coreJs, /const sessions\s*=\s*\{\}/);
  assert.match(coreJs, /let activeId\s*=\s*null/);
});

test('settings.js(설정·MCP·스니펫·사용량 지연 청크)도 core 싱글톤을 복제하지 않는다', () => {
  // panels.js와 같은 규칙. 이 청크는 dock 화면과 **일부러 분리**돼 있다 —
  // 「큐」탭 한 번에 설정 9개 섹션까지 받게 하지 않으려고(vite.config.js).
  assert.doesNotMatch(settingsJs, /const sessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(settingsJs, /let activeId\s*=\s*null/, 'core/store.js의 activeId가 복제됐다');
  assert.doesNotMatch(settingsJs, /const registry\s*=\s*new Map\(\)/, 'core/dom.js의 액션 레지스트리가 복제됐다');
});

test('shell.js(HUD·헤더·워크트리 레일 지연 청크)도 core 싱글톤을 복제하지 않는다', () => {
  // N36 — Rail.tsx도 panels.js와 같은 함정 대상이다. 여긴 window 브리지로만
  // core/store.js·layout/store.js·agent/state.js를 읽으므로(직접 import 금지),
  // 복제되면 셋 중 하나라도 이 시그니처가 나타난다.
  assert.doesNotMatch(shellJs, /const sessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(shellJs, /let activeId\s*=\s*null/, 'core/store.js의 activeId가 복제됐다');
  assert.doesNotMatch(shellJs, /const registry\s*=\s*new Map\(\)/, 'core/dom.js의 액션 레지스트리가 복제됐다');
  assert.doesNotMatch(shellJs, /const _byTmux\s*=\s*new Map\(\)/, 'agent/state.js의 상태 저장소가 복제됐다');
});

test('voice.js도 함께 남는다 — app 빌드가 dist를 비우고 끝나면 안 된다', () => {
  // sw.js PRECACHE가 이 이름을 그대로 참조하고, index.html도 같은 경로를
  // 불러온다. 이 파일이 app 빌드만 돌리던 시절엔 `npm test` 직후의 트리에서
  // 그 요청이 404였다(실브라우저 스모크가 잡아냈다).
  assert.ok(fs.existsSync(path.join(DIST, 'voice.js')), 'frontend/dist/voice.js가 없다');
});
