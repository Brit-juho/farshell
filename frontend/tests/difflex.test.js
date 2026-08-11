// difflex.js 순수 로직 단위 테스트. 의존성 없이 Node 내장 러너로 실행:
//   node --test frontend/tests/difflex.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const D = require('../js/difflex.js');

const SAMPLE = [
  'diff --git a/server/main.py b/server/main.py',
  'index 1111111..2222222 100644',
  '--- a/server/main.py',
  '+++ b/server/main.py',
  '@@ -10,6 +10,7 @@ import auth',
  ' import network_access',
  ' import tunnel',
  '+from routes.files import router as files_router',
  ' ',
  '-오래된 줄',
  ' 마지막',
].join('\n');

test('parse: 파일/헌크 기본 구조', () => {
  const files = D.parse(SAMPLE);
  assert.strictEqual(files.length, 1);
  assert.strictEqual(files[0].oldPath, 'server/main.py');
  assert.strictEqual(files[0].newPath, 'server/main.py');
  assert.strictEqual(files[0].hunks.length, 1);
});

test('parse: 줄 타입과 줄번호가 각각 증가', () => {
  const h = D.parse(SAMPLE)[0].hunks[0];
  const types = h.lines.map(l => l.type);
  assert.deepStrictEqual(types, ['ctx', 'ctx', 'add', 'ctx', 'del', 'ctx']);

  const add = h.lines.find(l => l.type === 'add');
  assert.strictEqual(add.oldNo, null);          // 추가 줄은 old 번호가 없다
  const del = h.lines.find(l => l.type === 'del');
  assert.strictEqual(del.newNo, null);          // 삭제 줄은 new 번호가 없다

  // 컨텍스트 첫 줄은 헤더의 시작 번호 그대로
  assert.strictEqual(h.lines[0].oldNo, 10);
  assert.strictEqual(h.lines[0].newNo, 10);
});

test('parseHunkHeader: count 생략 시 1로 해석', () => {
  assert.deepStrictEqual(D.parseHunkHeader('@@ -5 +7 @@'), {
    oldStart: 5, oldCount: 1, newStart: 7, newCount: 1, section: '',
  });
  const h = D.parseHunkHeader('@@ -1,2 +3,4 @@ def foo():');
  assert.strictEqual(h.oldStart, 1);
  assert.strictEqual(h.newCount, 4);
  assert.strictEqual(h.section, ' def foo():');
  assert.strictEqual(D.parseHunkHeader('not a hunk'), null);
});

test('stats: 추가/삭제 집계', () => {
  assert.deepStrictEqual(D.stats(D.parse(SAMPLE)[0]), { add: 1, del: 1 });
});

test('parse: 바이너리 파일 표시', () => {
  const bin = [
    'diff --git a/img.png b/img.png',
    'Binary files a/img.png and b/img.png differ',
  ].join('\n');
  const f = D.parse(bin)[0];
  assert.strictEqual(f.binary, true);
  assert.strictEqual(f.hunks.length, 0);
});

test('parse: 여러 파일', () => {
  const two = SAMPLE + '\n' + [
    'diff --git a/a.txt b/a.txt',
    '--- a/a.txt',
    '+++ b/a.txt',
    '@@ -1 +1 @@',
    '-old',
    '+new',
  ].join('\n');
  const files = D.parse(two);
  assert.strictEqual(files.length, 2);
  assert.strictEqual(files[1].newPath, 'a.txt');
  assert.deepStrictEqual(D.stats(files[1]), { add: 1, del: 1 });
});

test('parse: "\\ No newline" 은 meta 로 남고 줄번호를 먹지 않는다', () => {
  const t = [
    'diff --git a/x b/x', '--- a/x', '+++ b/x',
    '@@ -1 +1 @@', '-a', '\\ No newline at end of file', '+b',
  ].join('\n');
  const h = D.parse(t)[0].hunks[0];
  const meta = h.lines.find(l => l.type === 'meta');
  assert.ok(meta);
  assert.strictEqual(meta.oldNo, null);
  assert.strictEqual(meta.newNo, null);
  // meta 뒤의 추가 줄이 여전히 new 시작번호를 유지
  assert.strictEqual(h.lines.find(l => l.type === 'add').newNo, 1);
});

test('parse: 빈 입력/널 안전', () => {
  assert.deepStrictEqual(D.parse(''), []);
  assert.deepStrictEqual(D.parse(null), []);
  assert.deepStrictEqual(D.parse(undefined), []);
});

test('normalize: CRLF/CR 을 LF 로', () => {
  assert.strictEqual(D.normalize('a\r\nb\rc'), 'a\nb\nc');
});

test('expandTabs: 탭 정지점 4칸', () => {
  assert.strictEqual(D.expandTabs('\tx'), '    x');
  assert.strictEqual(D.expandTabs('ab\tx'), 'ab  x');   // 2칸 채워 4로
  assert.strictEqual(D.expandTabs('abcd\tx'), 'abcd    x');
  assert.strictEqual(D.expandTabs('a\tb', 8), 'a       b');
});

test('langForPath: 확장자 매핑', () => {
  assert.strictEqual(D.langForPath('server/main.py'), 'python');
  assert.strictEqual(D.langForPath('a/b/app.tsx'), 'typescript');
  assert.strictEqual(D.langForPath('run.sh'), 'bash');
  assert.strictEqual(D.langForPath('index.html'), 'xml');
  assert.strictEqual(D.langForPath('README.md'), 'markdown');
});

test('langForPath: 모르는 확장자/무확장자는 null (plaintext 폴백)', () => {
  assert.strictEqual(D.langForPath('VERSION'), null);
  assert.strictEqual(D.langForPath('a.unknownext'), null);
  assert.strictEqual(D.langForPath(''), null);
  assert.strictEqual(D.langForPath(null), null);
  // 프로토타입 오염 방어 — hasOwnProperty 로 조회하는지
  assert.strictEqual(D.langForPath('a.constructor'), null);
  assert.strictEqual(D.langForPath('a.toString'), null);
});
