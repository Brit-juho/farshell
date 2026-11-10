// 메뉴 한 규격 (2026-11, 화면 점검 8절 4번) — 모든 메뉴가 components.css 「메뉴 한 규격」 하나를 쓴다.
// 값은 시안 spec.html의 menu(항목 28 · 글자 13 · 패딩 8 · 아이콘 14 · 반경 8 · bg-raised · e2)이고, 폰은 44.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ');
const components = strip(read('styles', 'layers', 'components.css'));

function ruleBody(css, selectorStart) {
  const i = css.indexOf(selectorStart);
  assert.ok(i >= 0, `${selectorStart} 규칙이 없다`);
  const j = css.indexOf('}', i);
  return css.slice(i, j);
}

test('메뉴 면 규격: bg-raised · 1px line-strong · 반경 lg(8) · 그림자 e2 · 패딩 4', () => {
  const b = ruleBody(components, '.vt-menu, .vt-mb-pop, .vt-ft-menu {');
  assert.match(b, /--menu-item-h:\s*28px/);
  assert.match(b, /background:\s*var\(--color-bg-raised\)/);
  assert.match(b, /border:\s*1px solid var\(--color-line-strong\)/);
  assert.match(b, /border-radius:\s*var\(--radius-lg\)/);
  assert.match(b, /box-shadow:\s*var\(--shadow-e2\)/);
  assert.match(b, /padding:\s*calc\(var\(--spacing\) \* 1\)/);
});

test('메뉴 항목 규격: 높이 28 · 글자 13(fs-sm) · 패딩 8 · 반경 xs(4)', () => {
  const b = ruleBody(components, '.vt-menu-item, .vt-mb-item, .vt-ft-mi, .vt-pt-menu-item {');
  assert.match(b, /min-height:\s*var\(--menu-item-h\)/);
  assert.match(b, /font:\s*var\(--weight-normal\) var\(--fs-sm\)/);
  assert.match(b, /padding:\s*0 calc\(var\(--spacing\) \* 2\)/);
  assert.match(b, /border-radius:\s*var\(--radius-xs\)/);
  const icon = ruleBody(components, '.vt-menu-item > svg, .vt-menu-item-ico svg');
  assert.match(icon, /width:\s*14px;\s*height:\s*14px/);
});

test('폰·터치는 항목 44', () => {
  assert.match(components, /@media \(max-width: 719\.98px\), \(pointer: coarse\)\s*\{[\s\S]*?--menu-item-h:\s*44px/);
});

test('화면 CSS가 메뉴 면·항목 규격을 다시 선언하지 않는다 (자리만 정한다)', () => {
  const bars = strip(read('styles', 'screens', '95-bars.css'));
  const pop = ruleBody(bars, '.vt-mb-pop {');
  assert.doesNotMatch(pop, /background|border|box-shadow|padding|font/);
  assert.doesNotMatch(bars, /\.vt-mb-item\s*\{/);
  const ov = strip(read('styles', 'screens', '20-overlay.css'));
  assert.doesNotMatch(ruleBody(ov, '.vt-menu {'), /background|border|padding|box-shadow|min-width/);
  const files = strip(read('styles', 'screens', '96-files-tree.css'));
  assert.doesNotMatch(files, /^\.vt-ft-mi\s*\{/m);
  assert.doesNotMatch(ruleBody(files, '.vt-ft-menu {'), /background|border|box-shadow|padding/);
});

test('메뉴를 그리는 코드가 규격 클래스를 쓴다 (열거)', () => {
  const rail = read('frontend', 'js', 'shell', 'RailRow.tsx');
  assert.match(rail, /class="vt-menu"/);
  assert.match(rail, /class="vt-menu-item"/);
  assert.match(read('frontend', 'js', 'shell', 'TitleBar.tsx'), /class="vt-mb-pop"/);
  assert.match(read('frontend', 'js', 'files', 'FileTree.tsx'), /class="vt-ft-mi"/);
  assert.match(read('frontend', 'js', 'ui', 'select.js'), /'vt-menu vt-dd-list'/);
  const sess = read('frontend', 'js', 'term', 'session.js');
  assert.match(sess, /menu\.className = 'vt-menu'/);
  assert.match(sess, /'vt-menu-item two'/);
  assert.match(read('frontend', 'js', 'panels', 'ports', 'ports.js'), /'vt-pt-menu-item'/);
  const accounts = read('frontend', 'js', 'term', 'accounts-ui.js');
  assert.match(accounts, /menu\.className = 'vt-menu'/);
  assert.match(accounts, /'vt-menu-item two'/);
  assert.doesNotMatch(accounts, /style="/);
});

test('「+」 메뉴: 아이콘이 있고 인라인 스타일이 없으며 머리 막대의 +는 사이드바 바깥으로 연다', () => {
  const sess = read('frontend', 'js', 'term', 'session.js');
  const fn = sess.slice(sess.indexOf('export function showAddMenu'), sess.indexOf('// 일반(비 tmux) 터미널 세션 생성'));
  assert.match(fn, /vt-menu-item-ico/);
  assert.doesNotMatch(fn, /style="/, '인라인 style 문자열이 남았다');
  assert.doesNotMatch(fn, /opacity:\.55/);
  assert.match(fn, /closest\('\.vt-wgrail-head'\)/);
  assert.match(fn, /panelRight \+ 6/);
});

test('「+」 메뉴: 계정은 「이번만 다른 계정으로」 한 줄뿐이다 — 기본 계정 바꾸기는 설정·상태 막대 칩의 일이다', () => {
  const sess = read('frontend', 'js', 'term', 'session.js');
  const fn = sess.slice(sess.indexOf('export function showAddMenu'), sess.indexOf('// 일반(비 tmux) 터미널 세션 생성'));
  assert.match(fn, /이번만 다른 계정으로/);
  assert.doesNotMatch(fn, /기본 계정 바꾸기/);
  assert.doesNotMatch(fn, /mode: 'default'/);
  // 선택기 본체는 core가 아니라 지연 청크(shell)에 있다 — 초기 페이로드 상한 때문이다.
  assert.match(fn, /import\('\.\/accounts-ui\.js'\)/);
  assert.doesNotMatch(sess, /from '\.\/accounts-ui\.js'/);
});

test('행 메뉴 항목에 아이콘이 있다 — 모든 RowMenuId가 ROW_MENU_ICON에 있다', () => {
  const data = read('frontend', 'js', 'shell', 'rail-data.ts');
  const ids = [...data.match(/export type RowMenuId =([\s\S]*?);/)[1].matchAll(/'(\w+)'/g)].map((m) => m[1]);
  const rail = read('frontend', 'js', 'shell', 'RailRow.tsx');
  const map = rail.match(/ROW_MENU_ICON[^=]*=\s*\{([\s\S]*?)\};/)[1];
  // shareSettings · unshare · agentPerms 는 행 메뉴가 아니라 공유 패널에서만 쓰인다.
  const missing = ids.filter((id) => !new RegExp(`\\b${id}:`).test(map) && !['shareSettings', 'unshare', 'agentPerms'].includes(id));
  assert.deepStrictEqual(missing, [], `아이콘이 없는 행 메뉴 id: ${missing.join(' ')}`);
});
