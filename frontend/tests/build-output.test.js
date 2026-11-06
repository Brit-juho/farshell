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
const screensJs = fs.readFileSync(path.join(DIST, 'screens.js'), 'utf8');
const shareJs = fs.readFileSync(path.join(DIST, 'share.js'), 'utf8');

// 2.1.6 — 상한의 대상이 app.js 하나에서 **초기 페이로드 합계**로 바뀌었다.
// 청크 규칙을 뒤집으면서(vite.config.js) app.js는 부팅 배선만 남은 2KB가 되고
// 상시 코드는 전부 core.js로 갔다. 여기서 app.js만 재면 상한이 아무것도
// 막지 못한다 — 숫자는 그대로 두되 **재는 대상**을 뜻에 맞춘다.
// 2026-11-04 — 상한을 300KiB(307200)에서 350KiB(358400)로 올렸다. 존재 이유는 vendor(xterm 약 290KB)가
// 초기 번들로 새는 것을 잡는 안전장치다(docs/ref/session-view-measurements.md §4).
const EAGER_LIMIT = 358400;

test('초기 페이로드(app.js + core.js)가 350KiB 상한 이내다 — CI(release.yml)와 같은 기준', () => {
  const size = Buffer.byteLength(appJs, 'utf8') + Buffer.byteLength(coreJs, 'utf8');
  assert.ok(size <= EAGER_LIMIT, `app.js+core.js가 ${size}B — 상한 ${EAGER_LIMIT}B를 넘었다`);
});

test('app.js가 지연 청크를 정적으로 import하지 않는다 — "지연이 지연이 아닌" 상태의 유일한 시그니처', () => {
  // 이 검사가 이 파일에서 가장 중요하다. 지연 청크가 공유 모듈을 끌어올리면
  // entry가 그 청크를 정적 import하게 되는데, **빌드는 성공하고 크기 게이트도
  // 통과한다**(app.js가 오히려 작아지니까). 실측한 사고: snippets.js 하나를
  // 지연시키자 app.js가 0.04KB 스텁이 되고 panels.js가 215KB로 부풀었다.
  // 산출물은 공백이 걷혀 있다— 형식이 바뀔 수 있으니(vite 8 이전 전에는 공백이 걷혀 한 줄에 이어졌다) 줄 머리(^) 대신 문장 경계로 찾고, 하나도 못 찾으면 실패시킨다
  // (정규식이 산출 형식을 못 따라가 0건이 되면 이 검사가 아무것도 막지 못한 채 통과한다).
  const statics = [...appJs.matchAll(/(?:^|[;}\n])\s*import\s*[^;()]*?from\s*["']\.\/([\w.-]+)["']/g)].map((m) => m[1]);
  assert.ok(statics.includes('core.js'), 'app.js의 정적 import를 하나도 못 찾았다 — 검사 정규식이 산출 형식과 어긋났다');
  // rolldown-runtime.js: Rolldown이 모든 청크가 공유하도록 내는 0.5KB짜리 헬퍼(상태 없음, 고정 이름).
  // 이것은 지연 청크가 아니므로 허용한다. 다른 것이 끼면 여전히 실패한다.
  for (const dep of statics.filter((d) => d !== 'rolldown-runtime.js')) {
    assert.strictEqual(dep, 'core.js',
      `app.js가 ./${dep}를 정적 import한다 — 지연 청크가 상시 로드로 승격됐다`);
  }
});

test('panels.js(코드 뷰어 지연 청크)가 core/store.js의 세션 싱글톤을 복제하지 않는다', () => {
  // N35 실측 버그의 정확한 시그니처: 복제되면 core/store.js의 모듈 스코프
  // 변수 선언이 panels.js 안에 그대로 나타난다(`const sessions = {}` +
  // `let activeId = null`). 복제되면 panels/viewer/tree.js의 activeSessionId()가
  // 항상 빈 사본을 보게 되어 "터미널 위치에서 열기"가 조용히 죽는다.
  assert.doesNotMatch(panelsJs, /\bsessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(panelsJs, /\bactiveId\s*=\s*null/, 'core/store.js의 activeId가 복제됐다');
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
  assert.match(coreJs, /\bsessions\s*=\s*\{\}/);
  assert.match(coreJs, /\bactiveId\s*=\s*null/);
});

test('settings 청크(설정·MCP 팝업·스니펫·사용량)도 core 싱글톤을 복제하지 않는다', () => {
  // panels.js와 같은 규칙. 이 청크는 dock 화면과 **일부러 분리**돼 있다 —
  // 「큐」탭 한 번에 설정 7개 섹션까지 받게 하지 않으려고(vite.config.js).
  assert.doesNotMatch(settingsJs, /\bsessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(settingsJs, /\bactiveId\s*=\s*null/, 'core/store.js의 activeId가 복제됐다');
  assert.doesNotMatch(settingsJs, /const registry\s*=\s*new Map\(\)/, 'core/dom.js의 액션 레지스트리가 복제됐다');
});

test('shell.js(HUD·헤더·워크트리 레일 지연 청크)도 core 싱글톤을 복제하지 않는다', () => {
  // N36 — Rail.tsx도 panels.js와 같은 함정 대상이다. 여긴 window 브리지로만
  // core/store.js·layout/store.js·agent/state.js를 읽으므로(직접 import 금지),
  // 복제되면 셋 중 하나라도 이 시그니처가 나타난다.
  assert.doesNotMatch(shellJs, /\bsessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(shellJs, /\bactiveId\s*=\s*null/, 'core/store.js의 activeId가 복제됐다');
  assert.doesNotMatch(shellJs, /const registry\s*=\s*new Map\(\)/, 'core/dom.js의 액션 레지스트리가 복제됐다');
  assert.doesNotMatch(shellJs, /const _byTmux\s*=\s*new Map\(\)/, 'agent/state.js의 상태 저장소가 복제됐다');
});

test('voice.js도 함께 남는다 — app 빌드가 dist를 비우고 끝나면 안 된다', () => {
  // sw.js PRECACHE가 이 이름을 그대로 참조하고, index.html도 같은 경로를
  // 불러온다. 이 파일이 app 빌드만 돌리던 시절엔 `npm test` 직후의 트리에서
  // 그 요청이 404였다(실브라우저 스모크가 잡아냈다).
  assert.ok(fs.existsSync(path.join(DIST, 'voice.js')), 'frontend/dist/voice.js가 없다');
});

test('세션 탭의 뷰 편집기(layout/session-order.js)는 screens 청크에 있고 core에는 없다', () => {
  // vite.config.js manualChunks에 이름이 없으면 포괄 규칙이 core로 보내 상시 로드가 ~5KB 늘어난다
  // (빌드·테스트는 통과하고 상한만 조용히 줄어든다 — 이 파일을 옮길 때 실제로 겪었다).
  assert.match(screensJs, /function renderOrderEditor/);
  assert.doesNotMatch(coreJs, /function renderOrderEditor/);
  assert.doesNotMatch(coreJs, /vt-order-close/);
});

test('select 드롭다운 본체(ui/select.js)와 이번에 뗀 모듈들은 지연 청크에 있고 core에는 없다', () => {
  const selectJs = fs.readFileSync(path.join(DIST, 'select.js'), 'utf8');
  assert.match(selectJs, /function enhanceSelect/);
  assert.doesNotMatch(coreJs, /function enhanceSelect/);
  assert.match(coreJs, /function watchSelects/);   // 스텁은 core에 남는다
  assert.doesNotMatch(coreJs, /function sparkPoints|function parseInput/);   // system.js·agent-profiles.js
});

test('모달 뼈대(panels/panel.js)와 대화상자 본체는 panel 청크에 있고 core·shell은 정적으로 받지 않는다', () => {
  const panelJs = fs.readFileSync(path.join(DIST, 'panel.js'), 'utf8');
  assert.match(panelJs, /function openPanel\(/);
  assert.match(panelJs, /vt-dialog-msg/);
  assert.doesNotMatch(coreJs, /function openPanel\(|vt-dialog-msg/);
  assert.match(coreJs, /prefetchDialogs/);   // 스텁은 core에 남는다
  const shellJs = fs.readFileSync(path.join(DIST, 'shell.js'), 'utf8');
  assert.doesNotMatch(shellJs, /from\s*["']\.\/panel\.js["']/, 'shell이 panel 청크를 정적으로 받으면 부팅 때 같이 내려온다');
  assert.doesNotMatch(coreJs, /from\s*["']\.\/panel\.js["']/);
});

test('업로드 본체(term/upload.js)와 사용 가이드 본문은 지연 청크에 있고 core에는 스텁만 남는다', () => {
  const uploadJs = fs.readFileSync(path.join(DIST, 'upload.js'), 'utf8');
  assert.match(uploadJs, /\/api\/upload/);
  assert.doesNotMatch(coreJs, /\/api\/upload/);
  assert.match(coreJs, /vtUploadFiles/);          // 브리지 스텁
  assert.match(coreJs, /vtCancelUpload/);   // 취소 브리지 스텁도 core에 남는다(창 기본 동작 차단과 같은 블록)
  const panelJs = fs.readFileSync(path.join(DIST, 'panel.js'), 'utf8');
  assert.match(panelJs, /vt-guide-scroll/);
  assert.doesNotMatch(coreJs, /vt-guide-scroll/);
});

test('에이전트 공유의 대화상자와 통합 승인함(모달·권한·감사)은 share 청크에 있고 core 에는 없다(core 에는 이벤트 수신·배지·import() 만)', () => {
  // vite.config.js manualChunks 의 `/frontend/js/share/` 규칙이 빠지면 포괄 규칙이 core 로 보내
  // 상시 로드가 ~18KB 늘어난다(빌드·테스트는 통과하고 상한만 조용히 줄어든다).
  assert.match(shareJs, /function openShareDialog/);
  assert.match(shareJs, /function mountPermissions/);
  assert.match(shareJs, /function mountAudit/);
  assert.match(shareJs, /vt-authz-approval/);
  assert.doesNotMatch(coreJs, /function openShareDialog/);
  assert.doesNotMatch(coreJs, /function mountPermissions/);
  assert.doesNotMatch(coreJs, /function mountAudit/);
  assert.doesNotMatch(coreJs, /vt-authz-approval/);
  assert.doesNotMatch(coreJs, /에이전트 접근 요청/);
  // core 쪽 진입점은 있고, 청크는 동적 import 로만 받는다
  assert.match(coreJs, /agent_share_request/);
  assert.match(coreJs, /import\("\.\/share\.js"\)/);
  // share 청크도 core 싱글톤을 복제하지 않는다
  assert.doesNotMatch(shareJs, /\bsessions\s*=\s*\{\}/, 'core/store.js의 세션 저장소가 복제됐다');
  assert.doesNotMatch(shareJs, /const registry\s*=\s*new Map\(\)/, 'core/dom.js의 액션 레지스트리가 복제됐다');
});


test('코드 뷰어 본문(CodeMirror 6)은 editor 지연 청크에만 있고, panels는 import()로만 받는다', () => {
  // 파일 개편 단계 2. vite.config.js의 editor 규칙이 빠지면 CM(약 590KB)이 panels로 가거나 해시 붙은 청크가
  // 생긴다. 언어 팩은 언어마다 고정 이름 청크다(연 언어만 받는다).
  const editorJs = fs.readFileSync(path.join(DIST, 'editor.js'), 'utf8');
  assert.match(editorJs, /class EditorView\b/);
  assert.match(editorJs, /function mountEditor\(/);
  for (const [name, src] of [['core.js', coreJs], ['app.js', appJs], ['panels.js', panelsJs]]) {
    assert.doesNotMatch(src, /class EditorView\b/, `${name}에 CodeMirror가 들어갔다`);
  }
  assert.match(panelsJs, /import\("\.\/editor\.js"\)/);
  assert.doesNotMatch(panelsJs, /from\s*["']\.\/editor\.js["']/, 'panels.js가 editor.js를 정적으로 import한다');
  assert.doesNotMatch(coreJs, /["']\.\/editor(?:-[a-z]+)?\.js["']/, 'core.js가 편집기 청크를 참조한다');
  for (const lang of ['js', 'css', 'html', 'md', 'py', 'java', 'go', 'yaml', 'json', 'sql', 'legacy']) {
    assert.ok(fs.existsSync(path.join(DIST, `editor-${lang}.js`)), `editor-${lang}.js가 없다`);
    assert.doesNotMatch(editorJs, new RegExp(`from\\s*["']\\./editor-${lang}\\.js["']`),
      `editor.js가 editor-${lang}.js를 정적으로 import한다`);
  }
  // 해시 붙은 청크가 새로 생기지 않았다(ADR-26 고정 이름).
  const hashed = fs.readdirSync(DIST).filter((f) => /-[A-Za-z0-9_]{8}\.js$/.test(f));
  assert.deepStrictEqual(hashed, []);
});

test('core never statically imports the lazy shell or history chunks', () => {
  assert.doesNotMatch(coreJs, /from\s*["']\.\/(shell|history)\.js["']/,
    'a static back edge evaluates lazy listeners before core state initialization');
});
