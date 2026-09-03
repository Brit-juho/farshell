// F1: sw.js의 NETWORK_FIRST 라우팅 정규식이 frontend/dist/(Vite 산출물)를 정확히
// 잡는지 고정한다.
//
// 실제로 잡을 뻔한 사고: frontend/css/app.css + js/bootstrap.js가 F1에서
// frontend/dist/app.{js,css}로 대체됐는데, sw.js의 NETWORK_FIRST 정규식은
// /static/(css|js)/ 만 매치했다. dist/를 빠뜨린 채 배포됐다면 새 앱 코드 요청이
// NETWORK_ONLY에도 NETWORK_FIRST에도 안 걸려 맨 아래 "vendor immutable
// (stale-while-revalidate)" 분기로 떨어지고, 이건 정확히 이 파일 헤더 주석이
// 막으려던 사고(브라우저가 옛 app.js를 캐시한 채 계속 서빙)를 새 경로에서
// 재현하는 것이었다.
//
// sw.js는 브라우저 전역(self/caches/fetch)에 의존하는 최상위 스크립트라 통째로
// 실행하지 않고, 정규식 리터럴만 소스에서 뽑아 직접 검증한다 — 손으로 베낀
// 사본이 아니라 실제 소스 문자열에서 추출하므로 소스가 바뀌면 이 테스트도
// 같이 갱신해야 함이 강제된다.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SW_JS = fs.readFileSync(path.join(__dirname, '../sw.js'), 'utf8');

function extractRegex(constName) {
  const m = SW_JS.match(new RegExp(`const ${constName} = (\\/.*\\/);`));
  assert.ok(m, `sw.js에서 ${constName} 정규식을 못 찾음 — 소스 형태가 바뀌었을 수 있음`);
  // 리터럴 텍스트(/.../) 를 그대로 eval해 진짜 RegExp 인스턴스로 되살린다.
  // eslint-disable-next-line no-eval
  return eval(m[1]);
}

test('NETWORK_FIRST가 dist/app.js·app.css를 잡는다 (F1 회귀)', () => {
  const NETWORK_FIRST = extractRegex('NETWORK_FIRST');
  assert.ok(NETWORK_FIRST.test('/static/dist/app.js'));
  assert.ok(NETWORK_FIRST.test('/static/dist/app.css'));
});

test('NETWORK_FIRST가 기존 js/·css/ 경로도 계속 잡는다 (구 경로 회귀 방지)', () => {
  const NETWORK_FIRST = extractRegex('NETWORK_FIRST');
  // F4: terminal.js → js/term/session.js, voice.js(최상위) → js/voice/index.js로
  // 쪼개졌다 — 둘 다 이미 `/static/js/` 규칙에 포함되므로 최상위 특례가 삭제됐다.
  assert.ok(NETWORK_FIRST.test('/static/js/term/session.js'));
  assert.ok(NETWORK_FIRST.test('/static/js/ui/toast.js'));
  assert.ok(NETWORK_FIRST.test('/static/js/voice/index.js'));
  assert.ok(NETWORK_FIRST.test('/'));
  assert.ok(NETWORK_FIRST.test('/manifest.json'));
});

test('vendor는 NETWORK_FIRST에 안 걸린다 (SWR 캐시 이득 유지)', () => {
  const NETWORK_FIRST = extractRegex('NETWORK_FIRST');
  assert.ok(!NETWORK_FIRST.test('/static/vendor/xterm.min.js'));
});

test('NETWORK_ONLY(api/ws/voice)는 그대로', () => {
  const NETWORK_ONLY = extractRegex('NETWORK_ONLY');
  assert.ok(NETWORK_ONLY.test('/api/sessions'));
  assert.ok(NETWORK_ONLY.test('/ws/abc'));
  assert.ok(NETWORK_ONLY.test('/voice/input'));
  assert.ok(!NETWORK_ONLY.test('/static/dist/app.js'));
});
