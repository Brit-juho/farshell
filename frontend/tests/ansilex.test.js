// ansilex.js 순수 로직 단위 테스트. 의존성 없이 Node 내장 러너로 실행:
//   node --test frontend/tests/ansilex.test.js
//
// ansiToHtml은 그리드 카드 라이브 프리뷰(server의 tmux capture-pane 원문 →
// innerHTML)의 유일한 XSS 방어선이다. D13: 지금까지 회귀 감지 수단이 없었다.
const { test } = require('node:test');
const assert = require('node:assert');
const A = require('../js/ansilex.js');

// --- ansiToHtml: XSS 방어(HTML escape) ------------------------------------------

test('ansiToHtml: <script> 태그 등 HTML 특수문자를 이스케이프한다', () => {
  const out = A.ansiToHtml('<script>alert(1)</script>');
  assert.ok(!out.includes('<script>'), 'raw <script> 태그가 그대로 남으면 안 된다');
  assert.strictEqual(out, '&lt;script&gt;alert(1)&lt;/script&gt;');
});

test('ansiToHtml: & 도 이스케이프한다(이중 이스케이프 없이)', () => {
  assert.strictEqual(A.ansiToHtml('a & b'), 'a &amp; b');
});

test('ansiToHtml: 본문에 새 태그를 여는 문자(<,>)는 span 뒤 텍스트로만 남고 태그로 안 열린다', () => {
  // ANSI 코드 값 자체는 숫자만 파싱되므로(Number 캐스팅) style 속성 값에 임의
  // 문자열이 흘러들 경로가 없다. 본문 쪽 위협은 "새 태그를 여는 것"이므로,
  // <>가 이스케이프되어 있는지가 핵심이다(따옴표는 애초에 속성값이 아니라 텍스트
  // 노드로 렌더링되므로 이스케이프 대상이 아니다).
  const out = A.ansiToHtml('\x1b[31m<img src=x onerror=alert(1)>\x1b[0m');
  assert.strictEqual(out, `<span style="color:${A.ANSI_COLOR_MAP[31]}">&lt;img src=x onerror=alert(1)&gt;</span>`);
  assert.ok(!/<img/i.test(out), '새 <img> 태그가 실제로 열리면 안 된다');
});

// --- ansiToHtml: 색/스타일 변환 --------------------------------------------------

test('ansiToHtml: 전경색 코드가 color style로 변환된다', () => {
  const out = A.ansiToHtml('\x1b[31mred\x1b[0m');
  assert.strictEqual(out, `<span style="color:${A.ANSI_COLOR_MAP[31]}">red</span>`);
});

test('ansiToHtml: 배경색 코드(4x)가 background style로 변환된다', () => {
  const out = A.ansiToHtml('\x1b[41mred-bg\x1b[0m');
  assert.strictEqual(out, `<span style="background:${A.ANSI_COLOR_MAP[31]}">red-bg</span>`);
});

test('ansiToHtml: bold/italic/underline이 결합된다', () => {
  const out = A.ansiToHtml('\x1b[1;3;4mstyled\x1b[0m');
  assert.ok(out.includes('font-weight:bold'));
  assert.ok(out.includes('font-style:italic'));
  assert.ok(out.includes('text-decoration:underline'));
});

test('ansiToHtml: 알 수 없는 SGR 코드는 조용히 무시된다', () => {
  const out = A.ansiToHtml('\x1b[999mtext\x1b[0m');
  assert.strictEqual(out, '<span>text</span>');
});

// --- ansiToHtml: span 짝 맞추기 --------------------------------------------------

test('ansiToHtml: 안 닫힌 span은 끝에서 자동으로 닫힌다', () => {
  const out = A.ansiToHtml('\x1b[31munterminated');
  assert.strictEqual(out, `<span style="color:${A.ANSI_COLOR_MAP[31]}">unterminated</span>`);
});

test('ansiToHtml: 리셋 없이 색이 바뀌면 span이 중첩된다', () => {
  const out = A.ansiToHtml('\x1b[31ma\x1b[32mb');
  const openCount = (out.match(/<span/g) || []).length;
  const closeCount = (out.match(/<\/span>/g) || []).length;
  assert.strictEqual(openCount, closeCount);
  assert.strictEqual(openCount, 2);
});

// --- ansiToHtml: 색/스타일이 아닌 CSI/OSC는 제거 ---------------------------------

test('ansiToHtml: 커서 이동 등 비-색상 CSI 시퀀스는 제거된다', () => {
  assert.strictEqual(A.ansiToHtml('\x1b[2Jcleared\x1b[1;1H'), 'cleared');
});

test('ansiToHtml: OSC(타이틀 설정 등) 시퀀스는 제거된다', () => {
  const withBell = 'before\x1b]0;window title\x07after';
  assert.strictEqual(A.ansiToHtml(withBell), 'beforeafter');
});

test('ansiToHtml: 일반 텍스트는 그대로 통과한다', () => {
  assert.strictEqual(A.ansiToHtml('plain text'), 'plain text');
});

// --- trimBlankLines ---------------------------------------------------------------

test('trimBlankLines: 앞뒤 빈 줄을 제거한다', () => {
  assert.strictEqual(A.trimBlankLines('\n\ncontent\n\n'), 'content');
});

test('trimBlankLines: 중간의 연속된 빈 줄을 1개로 압축한다', () => {
  assert.strictEqual(A.trimBlankLines('a\n\n\n\nb'), 'a\n\nb');
});

test('trimBlankLines: ANSI escape만 있고 글자가 없는 줄도 빈 줄로 취급해 연속 압축 대상이 된다', () => {
  // isBlank 판정에만 쓰이고 실제로 쌓이는 건 원본 줄(첫 번째 빈 줄)이다 — 이 함수는
  // "빈 줄 내용을 지우는" 게 아니라 "연속된 빈 줄 중 첫 번째만 남기는" 함수다.
  assert.strictEqual(A.trimBlankLines('a\n\x1b[0m\n\x1b[2K\nb'), 'a\n\x1b[0m\nb');
});

test('trimBlankLines: 진짜 빈 줄과 ANSI 전용 줄이 섞인 연속 구간도 하나로 압축된다', () => {
  assert.strictEqual(A.trimBlankLines('a\n\x1b[0m\n\n   \nb'), 'a\n\x1b[0m\nb');
});

test('trimBlankLines: 빈 줄이 전혀 없으면 그대로 통과한다', () => {
  assert.strictEqual(A.trimBlankLines('a\nb\nc'), 'a\nb\nc');
});

test('trimBlankLines: 전부 빈 줄이면 빈 문자열이 된다', () => {
  assert.strictEqual(A.trimBlankLines('\n\n   \n\n'), '');
});

// --- collapseLeadingPad -----------------------------------------------------------

test('collapseLeadingPad: 24칸 이상의 선행 공백은 접힌다', () => {
  const padded = ' '.repeat(30) + 'right-aligned';
  assert.strictEqual(A.collapseLeadingPad(padded), 'right-aligned');
});

test('collapseLeadingPad: 24칸 미만(코드 들여쓰기)은 건드리지 않는다', () => {
  const indented = ' '.repeat(8) + 'def foo():';
  assert.strictEqual(A.collapseLeadingPad(indented), indented);
});

test('collapseLeadingPad: 선행 ANSI escape가 있어도 뒤의 긴 공백을 인식한다', () => {
  const line = '\x1b[0m' + ' '.repeat(30) + 'text';
  assert.strictEqual(A.collapseLeadingPad(line), 'text');
});
