// 터치(pointer: coarse)에서 탭 ×·칸 헤더 ×의 **누르는 영역만** 44px(2026-10-06). jsdom 은 app.css 를
// 로드하지 않으므로 규칙의 존재·값을 소스에서 직접 본다(실제 박스 크기는 Playwright 실측 — 커밋 메시지 참고).
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', '..', p), 'utf8');
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '');

// `@media (pointer:coarse){ ... }` 블록 본문들(중첩 중괄호 1단계까지).
function coarseBlocks(css) {
  const out = [];
  const re = /@media\s*\(pointer:\s*coarse\)\s*\{/g;
  let m;
  while ((m = re.exec(css))) {
    let depth = 1; let i = re.lastIndex;
    while (i < css.length && depth > 0) { if (css[i] === '{') depth += 1; else if (css[i] === '}') depth -= 1; i += 1; }
    out.push(css.slice(re.lastIndex, i - 1));
  }
  return out;
}
function ruleIn(blocks, selector) {
  for (const b of blocks) {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = b.match(new RegExp(`(?:^|[}\\s])${esc}\\s*\\{([^}]*)\\}`));
    if (m) return m[1];
  }
  return null;
}

test('뷰 탭 ×: coarse 에서만 padding 13px(18+26=44), 배경은 content-box, 글리프 크기는 그대로', () => {
  const css = stripComments(read('styles/screens/80-dock.css'));
  const blocks = coarseBlocks(css);
  const x = ruleIn(blocks, '.vt-wtab-close');
  assert.ok(x, 'coarse 블록에 .vt-wtab-close 규칙이 있다');
  assert.match(x, /box-sizing:\s*content-box/);
  assert.match(x, /padding:\s*13px/);
  assert.match(x, /background-clip:\s*content-box/);
  assert.doesNotMatch(x, /(^|[;\s])(width|height)\s*:/, '보이는 상자 크기(18px)는 건드리지 않는다');
  // coarse 밖의 기본 규칙에는 padding 이 없다(데스크톱 그대로).
  const outside = css.replace(/@media\s*\(pointer:\s*coarse\)\s*\{[\s\S]*?\n\}/g, '');
  assert.doesNotMatch(outside, /\.vt-wtab-close\s*\{[^}]*padding/);
  // 탭 높이가 44px 상단 바를 넘지 않게 위아래 padding 을 푼다
  assert.match(ruleIn(blocks, '.vt-wtab') || '', /padding-block:\s*0/);
});

test('칸 헤더 ×: coarse 에서만 padding 13px, 헤더는 44px 로 커져 상자가 안에 들어간다(터미널과 안 겹침)', () => {
  const css = stripComments(read('styles/screens/10-shell.css'));
  const blocks = coarseBlocks(css);
  const x = ruleIn(blocks, '.vt-pane-head .vt-pane-close');
  assert.ok(x, 'coarse 블록에 .vt-pane-close 규칙이 있다');
  assert.match(x, /box-sizing:\s*content-box/);
  assert.match(x, /padding:\s*13px/);
  assert.match(x, /background-clip:\s*content-box/);
  const head = ruleIn(blocks, '.vt-pane-head') || '';
  assert.match(head, /height:\s*44px/);
  assert.match(head, /padding-right:\s*calc\(var\(--spacing\)\s*\*\s*2\.5\)/, '구분선 터치 히트 영역(8px)을 비켜 간다');
  // 기본(데스크톱) 헤더는 24px 그대로
  assert.match(css, /\.vt-pane-head\s*\{[^}]*height:\s*24px/);
});

test('마크업이 바뀌지 않았다 — 두 × 모두 .vt-icon-btn.xs(18px 상자)이고 글리프는 11/13px', () => {
  const tabbar = read('frontend/js/layout/tabbar.js');
  assert.match(tabbar, /className = 'vt-icon-btn xs vt-wtab-close'/);
  assert.match(tabbar, /icon\('x', 11\)/);
  const panes = read('frontend/js/layout/panes.js');
  assert.match(panes, /class="vt-icon-btn xs danger vt-pane-close"[^>]*>\$\{icon\('x', 13\)\}/);
  const comp = read('styles/layers/components.css');
  assert.match(comp, /\.vt-icon-btn\.xs\s*\{\s*--icon-btn:\s*18px/);
});
