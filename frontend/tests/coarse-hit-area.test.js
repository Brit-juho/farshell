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

test('마크업이 바뀌지 않았다 — 칸 헤더 ×는 .vt-icon-btn.xs(18px 상자)이고 글리프는 13px', () => {
  const panes = read('frontend/js/layout/panes.js');
  assert.match(panes, /class="vt-icon-btn xs danger vt-pane-close"[^>]*>\$\{icon\('x', 13\)\}/);
  const comp = read('styles/layers/components.css');
  assert.match(comp, /\.vt-icon-btn\.xs\s*\{\s*--icon-btn:\s*18px/);
});
