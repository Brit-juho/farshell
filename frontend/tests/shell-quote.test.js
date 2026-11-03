// shell-quote.js 단위 테스트 — 인용한 결과를 진짜 sh 에 넣어 원문이 그대로 돌아오고 아무것도 실행되지 않는지 본다.
//   node --test frontend/tests/shell-quote.test.js
const { test, before } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const vm = require('node:vm');
const { importFresh } = require('./helpers/vm-esm');

let shellQuotePath;
before(async () => {
  const ns = await importFresh(path.join(__dirname, '../js/lib/shell-quote.js'), vm.createContext({}), new Map());
  shellQuotePath = ns.shellQuotePath;
});

const CASES = [
  ['/Users/u/.vt/files/ab12__photo.png', '/Users/u/.vt/files/ab12__photo.png'],  // 일반 업로드는 그대로
  ['/Users/u/.vt/files/ab12__스크린샷 2026.png', null],                           // 공백 → 인용
  ['/Users/u/.vt/files/ab12__스크린샷.png', '/Users/u/.vt/files/ab12__스크린샷.png'], // 한글만이면 그대로
  ['/x/a$(touch PWNED)b.png', null],
  ['/x/a`touch PWNED`b.png', null],
  ['/x/a;touch PWNED;b.png', null],
  ['/x/a&&touch PWNED.png', null],
  ["/x/it's.png", null],
  ['/x/a"b.png', null],
  ['/x/a\\b.png', null],
  ['/x/a|b>c<d.png', null],
  ['/x/*?[a].png', null],
  ['/x/a#b!c{d}.png', null],
  ['/x/line1\nline2.png', null],
  ['/x/tab\there.png', null],
  ['=ls', null], ['%1', null], ['-rf', null], ['~root', null],
  ['', "''"],
];

test('인용 결과의 모양', () => {
  for (const [input, expected] of CASES) {
    const q = shellQuotePath(input);
    if (expected !== null) assert.strictEqual(q, expected, JSON.stringify(input));
    else assert.notStrictEqual(q, input, `인용돼야 한다: ${JSON.stringify(input)}`);
  }
});

test('진짜 sh 에서 원문 그대로 돌아오고 아무것도 실행되지 않는다', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'shq-'));
  try {
    for (const [input] of CASES) {
      const out = execFileSync('sh', ['-c', `printf '%s' ${shellQuotePath(input)}`], { cwd: dir, encoding: 'utf8' });
      assert.strictEqual(out, input, JSON.stringify(input));
    }
    assert.deepStrictEqual(fs.readdirSync(dir), [], '명령이 실행돼 파일이 생겼다');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('upload.js 는 붙여넣는 경로를 shellQuotePath 로 감싼다(배선이 빠지면 인용이 무의미하다)', () => {
  const src = fs.readFileSync(path.join(__dirname, '../js/term/upload.js'), 'utf8');
  assert.match(src, /import \{ shellQuotePath \} from '\.\.\/lib\/shell-quote\.js';/);
  assert.match(src, /sendPaste\(sessionId, done\.map\(\(d\) => shellQuotePath\(d\.path\)\)/);
});
