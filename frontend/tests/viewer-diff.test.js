// 파일 개편 단계 9 — diff 가상 목록(viewer/diff.js).
//   1. 위치 계산: 행 높이(줄 20 · 덩어리 머리 30)의 누적 합, 열린 주석 상자 높이를 그 행 뒤에 끼운다.
//   2. 큰 diff는 보이는 범위 ± 여유만 DOM에 두고, 작은 diff는 전부 그린다(찾기·복사가 전체에 걸린다).
//   3. 줄 누름 → 「이 줄에 지시…」 상자(한 번에 하나, 다시 누르면 닫힘). 상자는 행이 화면 밖으로 나가도 남는다.
//   4. 덩어리 머리와 줄 글자는 글자로만 들어간다(XSS).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const DIFF_JS = path.join(__dirname, '../js/panels/viewer/diff.js');
// vm 안에서 만든 배열은 프로토타입이 달라 deepStrictEqual이 같은 값도 다르다고 본다.
const arr = (a) => Array.from(a);

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="scroll" style="overflow:auto"><div id="host"></div></div></body></html>');
  _doms.push(env.dom);
  const mod = await importFresh(DIFF_JS, env.context, new Map());
  return { mod, env, host: env.document.getElementById('host') };
}

/** 한 파일, `hunks`개 덩어리 × 덩어리마다 문맥 2 + 지움 1 + 추가 1 + 문맥 2. 확장자가 없어 하이라이트를 부르지 않는다. */
function genDiff(hunks, name = 'notes') {
  const out = [`diff --git a/${name} b/${name}`, `--- a/${name}`, `+++ b/${name}`];
  for (let h = 0; h < hunks; h++) {
    const s = h * 10 + 1;
    out.push(`@@ -${s},5 +${s},5 @@ section ${h}`);
    out.push(` ctx a ${h}`, ` ctx b ${h}`, `-old ${h}`, `+new ${h}`, ` ctx c ${h}`, ` ctx d ${h}`);
  }
  // 끝 줄바꿈은 붙이지 않는다 — difflex는 마지막 빈 줄을 빈 문맥 줄로 읽는다(옛 렌더러와 같은 동작).
  return out.join('\n');
}

test('위치 — 줄 20 · 덩어리 머리 30, 주석 상자 높이는 그 행 바로 뒤에 끼운다', async () => {
  const { mod } = await load();
  const items = [{ k: 'hunk', text: '@@' }, { k: 'line', l: { text: 'a' } }, { k: 'line', l: { text: 'b' } }];
  assert.deepStrictEqual(arr(mod.diffOffsets(items, null)), [0, 30, 50, 70]);
  assert.deepStrictEqual(arr(mod.diffOffsets(items, { at: 1, h: 100 })), [0, 30, 150, 170]);
  const off = mod.diffOffsets(items, null);
  assert.deepStrictEqual(arr(mod.visibleRange(off, 0, 1)), [0, 1]);
  assert.deepStrictEqual(arr(mod.visibleRange(off, 31, 49)), [1, 2]);
  assert.deepStrictEqual(arr(mod.visibleRange(off, 29, 51)), [0, 3]);
  assert.deepStrictEqual(arr(mod.visibleRange(off, -100, 1000)), [0, 3]);
  assert.deepStrictEqual(arr(mod.visibleRange(off, 500, 600)), [3, 3]);
});

test('가로 폭 — 한글·전각은 두 칸으로 센다', async () => {
  const { mod } = await load();
  const cols = mod.maxColumns([{ k: 'line', l: { text: 'abc' } }, { k: 'line', l: { text: '한글ab' } }, { k: 'hunk', text: '@@ x' }]);
  assert.strictEqual(cols, 6);
});

test('큰 diff — 행을 전부 만들지 않고, 높이는 전체를 잡는다', async () => {
  const { mod, host } = await load();
  const hunks = 400;                                  // 행 400 × 7 = 2,800개
  mod._renderDiffDOM(host, genDiff(hunks));
  const lines = host.querySelector('.vt-vw-dlines');
  assert.ok(lines, '줄 영역이 없다');
  const total = hunks * (30 + 6 * 20);
  assert.strictEqual(lines.style.height, `${total}px`);
  const drawn = lines.querySelectorAll('.vt-vw-dl, .vt-vw-hunk').length;
  assert.ok(drawn > 0 && drawn < 200, `그린 행 ${drawn}개`);
  // 첫 행은 덩어리 머리, 위치는 0.
  const first = lines.firstElementChild;
  assert.ok(first.classList.contains('vt-vw-hunk'));
  assert.strictEqual(first.style.top, '0px');
  // 줄 번호 열 하나, 지운 줄은 옛 번호.
  const del = lines.querySelector('.vt-vw-dl.del');
  assert.strictEqual(del.querySelector('.vt-vw-no').textContent, '3');
  assert.strictEqual(del.querySelector('.vt-vw-sign').textContent, '−');
  assert.strictEqual(del.querySelectorAll('.vt-vw-no').length, 1);
});

test('작은 diff — 전부 그린다', async () => {
  const { mod, host } = await load();
  mod._renderDiffDOM(host, genDiff(20));
  assert.strictEqual(host.querySelectorAll('.vt-vw-dl').length, 20 * 6);
  assert.strictEqual(host.querySelectorAll('.vt-vw-hunk').length, 20);
});

test('바뀐 낱말 — 지움·추가 짝에서 가운데만 표시한다', async () => {
  const { mod, host } = await load();
  mod._renderDiffDOM(host, ['diff --git a/x b/x', '--- a/x', '+++ b/x', '@@ -1,1 +1,1 @@',
    '-rail width 252 rows', '+rail width 300 rows'].join('\n'));
  const marks = [...host.querySelectorAll('mark.vt-vw-chg')].map((m) => m.textContent);
  assert.deepStrictEqual(marks, ['252', '300']);
});

test('XSS — 덩어리 머리와 줄 글자는 글자로만 들어간다', async () => {
  const { mod, host } = await load();
  mod._renderDiffDOM(host, ['diff --git a/x b/x', '--- a/x', '+++ b/x', '@@ -1,2 +1,2 @@ <img src=x onerror=1>',
    ' <script>a()</script>', '-<b>old</b>', '+<i>new</i>'].join('\n'));
  assert.strictEqual(host.querySelector('.vt-vw-dlines').querySelector('img, script, b, i'), null);
  assert.ok(host.querySelector('.vt-vw-hunk').textContent.includes('<img'));
});

test('줄 누름 — 주석 상자가 그 줄 뒤에 열리고, 다시 누르면 닫힌다(한 번에 하나)', async () => {
  const { mod, host, env } = await load();
  mod._renderDiffDOM(host, genDiff(5, 'src/notes'));
  const rows = host.querySelectorAll('.vt-vw-dl');
  rows[0].dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  let boxes = host.querySelectorAll('.vt-vw-annotate');
  assert.strictEqual(boxes.length, 1);
  assert.ok(rows[0].classList.contains('on'));
  assert.ok(boxes[0].textContent.includes('src/notes:1'), boxes[0].textContent);
  // 다른 줄 → 앞 상자는 닫히고 새 상자 하나.
  rows[2].dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  boxes = host.querySelectorAll('.vt-vw-annotate');
  assert.strictEqual(boxes.length, 1);
  assert.ok(!rows[0].classList.contains('on'));
  assert.ok(boxes[0].textContent.includes('src/notes:3'), boxes[0].textContent);
  // 같은 줄 다시 → 닫힘.
  rows[2].dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.strictEqual(host.querySelectorAll('.vt-vw-annotate').length, 0);
  assert.ok(!rows[2].classList.contains('on'));
  // 취소 단추도 닫는다.
  rows[1].dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  host.querySelector('.vt-vw-annotate .vt-btn.quiet').click();
  assert.strictEqual(host.querySelectorAll('.vt-vw-annotate').length, 0);
});

test('다시 그리기 — 같은 칸에 새 diff를 그리면 앞 목록은 정리되고 행이 섞이지 않는다', async () => {
  const { mod, host } = await load();
  mod._renderDiffDOM(host, genDiff(3, 'a'));
  const firstView = host._vtDiff;
  host.innerHTML = '';
  mod._renderDiffDOM(host, genDiff(2, 'b'));
  assert.notStrictEqual(host._vtDiff, firstView);
  assert.strictEqual(host.querySelectorAll('.vt-vw-dlines').length, 1);
  assert.strictEqual(host.querySelectorAll('.vt-vw-dl').length, 2 * 6);
});
