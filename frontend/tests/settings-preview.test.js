// 설정 화면 오른쪽 미리보기 칸의 순수 계산(panels/settings/preview-data.js).
// DOM을 그리는 preview.js는 core/settings.js·theme.js 싱글톤에 묶여 있어 여기서는 값만 검증하고,
// 그리는 쪽은 실제 브라우저로 확인한다(이 변경의 검증 기록 참고).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });
let _mod = null;
async function mod() { if (!_mod) { _mod = await loadSolidBuild(); _doms.push(_mod); } return _mod; }

test('estimateCols — 칸 폭 ÷ (글자 크기 × 0.6), 내림', async () => {
  const { estimateCols, MONO_ADVANCE } = await mod();
  assert.strictEqual(MONO_ADVANCE, 0.6);
  assert.strictEqual(estimateCols(336, 14), 40);   // 336 / 8.4
  assert.strictEqual(estimateCols(336, 28), 20);
  assert.strictEqual(estimateCols(100, 14), 11, '내림');
});

test('estimateCols — 폭이나 글자 크기가 0·음수·숫자가 아니면 0(그릴 근거가 없다)', async () => {
  const { estimateCols } = await mod();
  assert.strictEqual(estimateCols(0, 14), 0);
  assert.strictEqual(estimateCols(300, 0), 0);
  assert.strictEqual(estimateCols(-5, 14), 0);
  assert.strictEqual(estimateCols('x', 14), 0);
  assert.strictEqual(estimateCols(undefined, undefined), 0);
});

test('cursorClass — 스키마 값 셋만 통과, 그 밖은 block', async () => {
  const { cursorClass } = await mod();
  assert.strictEqual(cursorClass('block'), 'block');
  assert.strictEqual(cursorClass('underline'), 'underline');
  assert.strictEqual(cursorClass('bar'), 'bar');
  assert.strictEqual(cursorClass('beam'), 'block');
  assert.strictEqual(cursorClass(undefined), 'block');
});

test('resolveSkin — 목록 밖 스킨은 farshell로 되돌린다(theme.js와 같은 규칙)', async () => {
  const { resolveSkin } = await mod();
  const skins = ['farshell', 'macos', 'catppuccin'];
  assert.strictEqual(resolveSkin('macos', skins), 'macos');
  assert.strictEqual(resolveSkin('imported', skins), 'farshell');
  assert.strictEqual(resolveSkin('macos', undefined), 'farshell');
});

test('SAMPLE_LINES — 커서 토큰이 마지막 줄에 정확히 하나, 색 이름은 xterm 테마 키', async () => {
  const { SAMPLE_LINES } = await mod();
  const tokens = SAMPLE_LINES.flat();
  assert.strictEqual(tokens.filter((t) => t.cursor).length, 1);
  assert.ok(SAMPLE_LINES.at(-1).some((t) => t.cursor), '커서는 마지막 줄');
  const keys = new Set(['red', 'green', 'yellow', 'brightBlack']);
  for (const t of tokens) if (t.color) assert.ok(keys.has(t.color), `알 수 없는 색 이름: ${t.color}`);
});
