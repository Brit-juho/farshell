// 스니펫 진입점 — 넓은 화면은 dock 탭, 좁은 화면(dock 없음)은 상단바 버튼.
// 좁은 화면에는 레일도 dock도 없고 하단 내비에도 자리가 없어서, 이 버튼이 없으면 팔레트로만 열렸다.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (rel) => fs.readFileSync(path.join(__dirname, '..', '..', rel), 'utf8');

test('상단바에 스니펫 버튼이 있고 같은 액션(snippets.show)을 연다', () => {
  const html = read('frontend/index.html');
  const m = html.match(/<button id="snippets-open-btn"[^>]*>/);
  assert.ok(m, '#snippets-open-btn 이 index.html 에 있어야 한다');
  assert.match(m[0], /data-action="snippets\.show"/);
  assert.match(m[0], /aria-label="프롬프트 스니펫"/);
});

test('버튼은 좁은 화면의 터미널 탭에서만 보인다 — 넓은 화면은 dock 탭이 대신한다', () => {
  const css = read('styles/screens/10-shell.css');
  assert.match(css, /#snippets-open-btn \{ display:none; \}/, '기본은 숨김');
  const media = css.match(/@media \(max-width:719\.98px\) \{[^}]*#snippets-open-btn \{ display:inline-flex; \}/);
  assert.ok(media, 'COMPACT_MAX(720) 미만에서만 보인다');
  assert.match(css, /data-mobile-tab\]:not\(\[data-mobile-tab="terminal"\]\) #snippets-open-btn \{ display:none; \}/);
});

test('활동 막대 목록에 스니펫이 있다(옛 dock 탭과 같은 패널 id·액션)', () => {
  const dock = read('frontend/js/shell/activity-data.ts');
  assert.match(dock, /id: 'snippets'[^}]*panelId: 'vt-snippets'[^}]*action: 'snippets\.show'/);
});
