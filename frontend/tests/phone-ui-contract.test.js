// 폰 실기기 지적(2026-11-10) 회귀 — 스타일시트는 jsdom 이 읽지 않으므로 CSS·소스 원문 계약으로 본다.
// 실제 겹침·폭은 tests/e2e/test_smoke.py 의 폰 e2e(elementFromPoint · getBoundingClientRect)가 맡는다.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '');
const css = (f) => strip(fs.readFileSync(path.join(ROOT, 'styles/screens', f), 'utf8'));
const src = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');

// `selector { ... }` 의 본문들을 모은다(@media 안쪽 포함 — 중첩 한 단계라 단순 정규식으로 충분).
function bodies(text, selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(?:^|[}\\s,])${esc}\\s*\\{([^}]*)\\}`, 'g');
  const out = []; let m;
  while ((m = re.exec(text))) out.push(m[1]);
  return out;
}

test('시트 막이 하단 내비(650)보다 위이고, 시트 바닥 padding 에 내비 높이를 더하지 않는다', () => {
  const hud = css('60-mobile-hud.css');
  const nav = Number(/#vt-mnav-bottom\s*\{[^}]*z-index:\s*(\d+)/.exec(hud)[1]);
  const back = bodies(hud, '.vt-session-backdrop').map((b) => /z-index:\s*(\d+)/.exec(b)).filter(Boolean).map((m) => Number(m[1]));
  assert.ok(back.length && Math.max(...back) > nav, `막 z-index ${back} 는 내비 ${nav} 보다 커야 한다`);
  // 편집 키 줄(660) · 재연결 오버레이(700)보다는 아래여야 한다.
  assert.ok(Math.max(...back) < 700);
  assert.ok(!/\.vt-session-sheet\s*\{[^}]*52px/.test(hud), '시트 padding 에 내비 52px 보정을 다시 넣지 않는다 — 막이 내비를 덮는다');
});

test('꾹 눌러 메뉴 시트는 body 로 Portal 된다(#vt-fleet-slot 쌓임 맥락 500 에 갇히면 z-index 가 소용없다)', () => {
  const tsx = src('frontend/js/shell/Fleet.tsx');
  assert.match(tsx, /import \{[^}]*\bPortal\b[^}]*\} from 'solid-js\/web'/);
  assert.match(tsx, /<Portal>\s*<div class="vt-session-backdrop vt-fleet-menu-backdrop"/);
  assert.match(tsx, /<\/Portal>/);
});

test('시트를 열 때 DOM 선택을 지우고(openMenu), 시트·행은 user-select:none 이며 이름 변경 입력만 선택 가능하다', () => {
  const tsx = src('frontend/js/shell/Fleet.tsx');
  assert.match(tsx, /const openMenu = [\s\S]{0,400}removeAllRanges\(\)[\s\S]{0,200}setMenu\(\{ row \}\)/);
  const m = css('98-mobile.css');
  const sheet = bodies(m, '.vt-session-sheet').join(' ');
  assert.match(sheet, /-webkit-touch-callout:\s*none/);
  assert.match(sheet, /user-select:\s*none/);
  assert.match(bodies(m, '.vt-session-sheet .vt-input').join(' '), /user-select:\s*text/);
  // 행은 이미 none — 터미널 본문(xterm)에는 적용되지 않는다.
  assert.match(bodies(css('60-mobile-hud.css'), '.vt-fleet-row').join(' '), /user-select:\s*none/);
  assert.ok(!/#terminal-container[^{]*\{[^}]*user-select:\s*none/.test(m), '터미널 본문의 선택 복사를 막지 않는다');
});

test('하단 탭 화면의 머리 줄은 좌우 거터 16 + safe-area 한 규칙이다(설정 저장 문구도 따로 밀지 않는다)', () => {
  const m = css('98-mobile.css');
  const head = bodies(m, '.vt-viewer-backdrop.vt-mobile-route.vt-tab-panel .vt-viewer-head').join(' ');
  assert.match(head, /padding-left:\s*calc\(16px \+ env\(safe-area-inset-left,\s*0px\)\)/);
  assert.match(head, /padding-right:\s*calc\(16px \+ env\(safe-area-inset-right,\s*0px\)\)/);
  assert.match(bodies(m, '.vt-viewer-backdrop.vt-mobile-route .vt-viewer-head > .vt-set-save').join(' '), /margin-right:\s*0/);
});

test('정보 및 진단 훅 행: 폰에서는 배경이 거터(16) 밖으로 내밀지 않는다(margin 0)', () => {
  const t = css('50-settings-usage.css');
  const at = t.indexOf('.vt-set-hkgrid[data-all-ok="1"]');   // 폰(@media max-width:719px) 블록 안의 규칙
  assert.ok(at > 0);
  const phoneBlock = t.slice(at - 900, at + 1800);
  for (const sel of ['.vt-set-hookrow', '.vt-set-hookrow[data-state="add"], .vt-set-hookrow[data-state="update"], .vt-set-hookrow[data-state="warn"]']) {
    const b = bodies(phoneBlock, sel).join(' ');
    assert.match(b, /margin-inline:\s*0/, `${sel}: 폰에서 margin-inline:0`);
    assert.match(b, /padding-inline:\s*8px/, `${sel}: 폰에서 padding-inline:8px`);
  }
  // 데스크톱의 -6px 내민 배경은 그대로(두 열 표) — 폰 블록에서만 되돌린다.
  assert.match(t, /\.vt-set-hookrow\[data-state="add"\][^{]*\{\s*margin:0 -6px/);
});

test('꾹 눌러 시트의 백드롭은 user-select:none 이다(롱프레스 단어 선택이 하단 탭 라벨로 새지 않게), 터미널·입력은 막지 않는다', () => {
  const m = css('98-mobile.css');
  const back = bodies(m, '.vt-fleet-menu-backdrop').join(' ');
  assert.match(back, /user-select:\s*none/);
  assert.match(back, /-webkit-touch-callout:\s*none/);
  // 전역(body·html·.vt-mnav)으로 막지 않는다 — 터미널 본문 복사·입력 선택은 그대로.
  for (const f of ['98-mobile.css', '60-mobile-hud.css']) {
    const t = css(f);
    assert.ok(!/(?:^|[}\s,])(?:body|html|#terminal-container|\.xterm)[^{]*\{[^}]*user-select:\s*none/.test(t), `${f}: 전역 user-select:none 금지`);
  }
  // Fleet.tsx 의 시트 백드롭이 이 클래스를 쓴다.
  assert.match(src('frontend/js/shell/Fleet.tsx'), /class="vt-session-backdrop vt-fleet-menu-backdrop"/);
});
