// 파일 탭(viewer/tree.js) 「경로 삽입」 — 터미널에 타이핑하는 문자열이 셸 인용을 거치는지 본다.
// 파일 이름은 열람 경계 안의 누구나 정할 수 있어서(클론한 저장소, 압축 해제) `$(…)`가 든 이름이 그대로 나가면
// 사용자가 Enter를 누르는 순간 실행된다. 결과를 진짜 sh 에 넣어 원문이 그대로 돌아오고 아무것도 실행되지 않는지 확인한다.
//   node --experimental-vm-modules --test frontend/tests/viewer-tree-insert.test.js
'use strict';
const { test, before, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const TREE_JS = path.join(__dirname, '../js/panels/viewer/tree.js');

let pathInsertText;
let dom;
before(async () => {
  const env = createDomEnv('<!doctype html><html><body><div id="app"></div></body></html>');
  dom = env.dom;
  const ns = await importFresh(TREE_JS, env.context, new Map());
  pathInsertText = ns.pathInsertText;
});
after(() => { try { dom.window.close(); } catch (_) {} });

test('평범한 경로는 그대로 + 뒤에 공백 하나', () => {
  assert.strictEqual(pathInsertText('/Users/u/GitHub/app/src/main.py'), '/Users/u/GitHub/app/src/main.py ');
  assert.strictEqual(pathInsertText('/Users/u/GitHub/앱/설명.md'), '/Users/u/GitHub/앱/설명.md ');
});

test('$(…)·백틱·; 가 든 이름은 인용되어 sh 에서 실행되지 않고 원문 그대로 돌아온다', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vt-tree-insert-'));
  try {
    const marker = path.join(dir, 'PWNED');
    for (const name of [
      `/x/a$(touch ${marker})b.txt`,
      `/x/a\`touch ${marker}\`b.txt`,
      `/x/a;touch ${marker};b.txt`,
      "/x/it's here.txt",
    ]) {
      const typed = pathInsertText(name);
      assert.notStrictEqual(typed, name + ' ', `인용되지 않았다: ${name}`);
      const out = execFileSync('sh', ['-c', `printf %s ${typed}`], { cwd: dir }).toString();
      assert.strictEqual(out, name);
      assert.ok(!fs.existsSync(marker), `실행됐다: ${name}`);
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
