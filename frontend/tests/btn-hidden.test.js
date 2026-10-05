// `.vt-btn`은 display:inline-flex라 UA의 `[hidden]{display:none}`을 이긴다. 그래서
// index.html의 `<button class="vt-btn" hidden>`(#mic-retry-btn)이 숨지 않고 데스크톱/가로 폰
// 상태줄에 「다시 보내기」로 늘 떠 있었다(2026-10-06 가로 실측).
//
// 왜 CSS 소스 문자열을 보는가(약한 검증임을 알고 둔다): jsdom은 `@layer` 캐스케이드와 app.css를
// 계산하지 못해 "실제로 숨는가"를 단위 테스트로 증명할 수 없다. 실제 동작은 실기기 실측으로
// 확인했고(docs/ref/session-view-measurements.md), 여기서는 그 수정을 누가 지우지 못하게 하는
// 회귀 감시용 규칙 존재 검사만 한다. 값이 아니라 선언의 존재만 본다.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const reset = fs.readFileSync(path.join(root, 'styles/layers/reset.css'), 'utf8');
const components = fs.readFileSync(path.join(root, 'styles/layers/components.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');

test('base 레이어가 [hidden]을 !important 로 숨긴다 (어느 컴포넌트 display도 못 이긴다)', () => {
  assert.match(reset, /\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}/);
});

test('.vt-btn[hidden] 은 display:none 이다 (위 규칙의 이중 안전장치)', () => {
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
