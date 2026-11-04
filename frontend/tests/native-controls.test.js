// 기본 브라우저 컨트롤을 되살리지 않게 지키는 검사.
//  - 숫자 입력은 ui/number-spinner.js만 만든다(기본 type=number는 OS 스핀 버튼·모양이 스킨과 따로 논다).
//  - <select>는 main.js가 watchSelects()로 모두 FarShell 드롭다운으로 바꾼다(끄려면 data-native).
//  - 체크박스는 components.css가 appearance를 끄고 토큰으로 그린다(끄려면 data-native).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '../js');
function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) walk(p, out); else if (/\.(js|ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}
const files = walk(root).filter(f => !f.includes(`${path.sep}locales${path.sep}`));
const NUMBER = /type\s*=\s*["']number["']|\.type\s*=\s*["']number["']|type:\s*["']number["']\s*[,}].*input/;

test('type=number 입력은 ui/number-spinner.js 밖에서 만들지 않는다', () => {
  const offenders = files.filter(f => !f.endsWith(`ui${path.sep}number-spinner.js`))
    .filter(f => NUMBER.test(fs.readFileSync(f, 'utf8'))).map(f => path.relative(root, f));
  assert.deepEqual(offenders, [], '숫자 입력은 numberSpinner()를 쓴다');
});

test('main.js가 select 자동 변환을 켜고, CSS가 네이티브 체크박스를 다시 그린다', () => {
  assert.match(fs.readFileSync(path.join(root, 'main.js'), 'utf8'), /watchSelects\(\)/);
  const css = fs.readFileSync(path.join(__dirname, '../../styles/layers/components.css'), 'utf8');
  assert.match(css, /input\[type="checkbox"\]:not\(\[data-native\]\)\s*\{[^}]*appearance:\s*none/);
});
