// `.vt-btn`은 display:inline-flex라 UA의 `[hidden]{display:none}`을 이긴다. 그래서
// index.html의 `<button class="vt-btn" hidden>`(#mic-retry-btn)이 숨지 않고 데스크톱/가로 폰
// 상태줄에 「다시 보내기」로 늘 떠 있었다(2026-10-06 가로 실측).
// jsdom은 app.css를 로드하지 않으므로 규칙의 존재를 소스에서 직접 본다.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const components = fs.readFileSync(path.join(root, 'styles/layers/components.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');

test('.vt-btn[hidden] 은 display:none 이다', () => {
  assert.match(components, /\.vt-btn\[hidden\]\s*\{\s*display:\s*none;?\s*\}/);
});

test('hidden 으로 시작하는 .vt-btn 이 index.html 에 있다 (이 규칙이 필요한 이유)', () => {
  assert.match(html, /<button[^>]*id="mic-retry-btn"[^>]*class="vt-btn[^"]*"[^>]*\bhidden\b/);
});

// 폰 실측(2026-10-06): 「연결된 화면」 시트의 터치 타깃이 24px이었다. 모바일 구간에서 44px로 키운다.
test('연결된 화면 시트는 모바일 구간에서 터치 타깃이 44px 이상이다', () => {
  const css = fs.readFileSync(path.join(root, 'styles/screens/60-mobile-hud.css'), 'utf8');
  const block = css.slice(css.indexOf('「연결된 화면」 시트의 닫기'));
  assert.match(block, /@media \(max-width:719px\)/);
  for (const sel of ['.vt-wtd-x', '.vt-clients-row', '.vt-clients-detach', '.vt-clients-head .vt-btn']) {
    assert.ok(block.includes(sel), sel);
  }
  assert.ok((block.match(/44px/g) || []).length >= 4);
});
