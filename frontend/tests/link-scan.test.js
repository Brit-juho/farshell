// term/link-scan.js — 전각 문자 줄에서 링크 범위가 칸 번호와 맞는지, 줄바꿈 링크, ~ 경로.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(path.join(__dirname, '../js/term/link-scan.js'), env.context, new Map());
}

// xterm 버퍼 흉내: 한 줄 = 글자 배열, 전각은 [글자, 빈 뒷칸].
function mkLine(str, cols, wrapped = false) {
  const cells = [];
  for (const ch of str) {
    const wide = /[ᄀ-ᇿ㄰-㆏가-힯]/.test(ch);
    cells.push({ ch, w: wide ? 2 : 1 });
    if (wide) cells.push({ ch: '', w: 0 });
  }
  while (cells.length < cols) cells.push({ ch: '', w: 1 });
  return {
    isWrapped: wrapped, length: cols,
    getCell: (x) => (cells[x] ? { getWidth: () => cells[x].w, getChars: () => cells[x].ch } : undefined),
  };
}
const buf = (lines) => ({ getLine: (i) => lines[i] });

test('한글 뒤의 경로 범위가 칸 번호로 계산된다', async () => {
  const S = await load();
  // 한글 2자(4칸) + ": "(2칸) = 6칸 뒤에 시작 → x=7
  const links = S.linksForRow(buf([mkLine('한글: src/a.py ok', 40)]), 1);
  assert.strictEqual(links.length, 1);
  assert.strictEqual(links[0].text, 'src/a.py');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(links[0].range)), { start: { x: 7, y: 1 }, end: { x: 14, y: 1 } });
});

test('~/ 경로를 잡고 슬래시 없는 파일명은 잡지 않는다', async () => {
  const S = await load();
  const links = S.linksForRow(buf([mkLine('~/.claude/CLAUDE.md, lessons.md, docs/*', 60)]), 1);
  assert.deepStrictEqual([...links.map((l) => l.text)], ['~/.claude/CLAUDE.md']);
  assert.strictEqual(links[0].range.start.x, 1);
});

test('줄바꿈으로 이어진 URL은 하나로 잡고 두 행 모두에서 돌려준다', async () => {
  const S = await load();
  const b = buf([mkLine('see https://exam', 16), mkLine('ple.com/x done', 16, true)]);
  const r1 = S.linksForRow(b, 1), r2 = S.linksForRow(b, 2);
  assert.strictEqual(r1.length, 1);
  assert.strictEqual(r1[0].text, 'https://example.com/x');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r1[0].range)), { start: { x: 5, y: 1 }, end: { x: 9, y: 2 } });
  assert.strictEqual(r2.length, 1);
});

test('다른 행의 링크는 돌려주지 않는다', async () => {
  const S = await load();
  const b = buf([mkLine('plain', 16), mkLine('src/b.js', 16)]);
  assert.strictEqual(S.linksForRow(b, 1).length, 0);
  assert.strictEqual(S.linksForRow(b, 2).length, 1);
});
