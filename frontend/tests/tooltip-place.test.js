// ui/tooltip-place.js — 툴팁 자리 계산(순수 함수). 패널 경계 안에 머물고, 안 들어가면 반대편으로 뒤집는다.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(path.join(__dirname, '../js/ui/tooltip-place.js'), 'utf8');
// import가 없는 단일 모듈이라 export만 벗겨 평가한다.
const computePlacement = new Function(`${SRC.replace(/^export /gm, '')}\nreturn computePlacement;`)();

const rect = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h });
const base = { w: 120, h: 28, vw: 1440, vh: 900, gap: 8, edge: 8 };

test('들어가면 요청한 변에 둔다', () => {
  const r = computePlacement({ ...base, rect: rect(600, 400, 28, 28), side: 'top' });
  assert.strictEqual(r.side, 'top');
  assert.strictEqual(r.y, 400 - 8 - 28);
  assert.strictEqual(r.x, 600 + 14 - 60);
});

test('뷰포트 위쪽에 안 들어가면 반대편(아래)으로 뒤집는다', () => {
  const r = computePlacement({ ...base, rect: rect(600, 10, 28, 28), side: 'top' });
  assert.strictEqual(r.side, 'bottom');
  assert.ok(r.y >= 10 + 28 + 8);
});

test('가장자리로 밀리면 뷰포트 안으로 당긴다', () => {
  const r = computePlacement({ ...base, rect: rect(2, 400, 28, 28), side: 'top' });
  assert.strictEqual(r.x, 8);
  const r2 = computePlacement({ ...base, rect: rect(1420, 400, 16, 28), side: 'top' });
  assert.strictEqual(r2.x + base.w, 1440 - 8);
});

test('사이드바 안쪽 행의 right 툴팁은 패널 경계를 넘지 않는다 — 반대편(left)으로 뒤집는다', () => {
  // 사이드바 48..408, 트리거는 오른쪽 끝(380..404). right는 404+8+120=532 > 404 → 안 들어간다.
  const bounds = { left: 52, right: 404, top: 36, bottom: 876 };
  const r = computePlacement({ ...base, rect: rect(380, 200, 24, 24), side: 'right', bounds });
  assert.strictEqual(r.side, 'left');
  assert.ok(r.x + base.w <= 404, `툴팁이 패널 오른쪽 경계를 넘었다: ${r.x + base.w}`);
  assert.ok(r.x >= 52);
});

test('양옆이 다 안 들어가면 위·아래로 열되 가로를 패널 안으로 당긴다', () => {
  const bounds = { left: 52, right: 200, top: 36, bottom: 876 };  // 폭 148, 툴팁 120
  const r = computePlacement({ ...base, rect: rect(150, 400, 40, 24), side: 'right', bounds });
  assert.ok(['top', 'bottom'].includes(r.side));
  assert.ok(r.x >= 52 && r.x + base.w <= 200, `가로가 패널 밖: ${r.x}..${r.x + base.w}`);
});

test('툴팁이 패널보다 넓으면 패널 제한을 풀고 뷰포트 안에만 둔다', () => {
  const bounds = { left: 52, right: 100, top: 36, bottom: 876 };
  const r = computePlacement({ ...base, rect: rect(60, 400, 24, 24), side: 'right', bounds });
  assert.ok(r.x >= 8 && r.x + base.w <= 1440 - 8);
});

test('bounds가 없으면(활동 막대 · data-tip-free) 패널 제한 없이 right로 연다', () => {
  const r = computePlacement({ ...base, rect: rect(8, 200, 32, 44), side: 'right' });
  assert.strictEqual(r.side, 'right');
  assert.strictEqual(r.x, 8 + 32 + 8);
});

test('tooltip.js가 data-tip 지연 400ms·WARM 즉시 규칙과 Esc 닫기를 유지한다', () => {
  const src = fs.readFileSync(path.join(__dirname, '../js/ui/tooltip.js'), 'utf8');
  assert.match(src, /const OPEN_DELAY = 400;/);
  assert.match(src, /const WARM_MS = 300;/);
  assert.match(src, /e\.key === 'Escape'\) hideTip\(\)/);
  assert.match(src, /computePlacement\(/);
});

test('툴팁 CSS: 제목·부제가 위아래로 쌓이고 면이 메뉴와 같은 규격이다', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../styles/layers/components.css'), 'utf8');
  const m = css.match(/\.vt-tip \{[\s\S]*?\n  \}/);
  assert.ok(m, '.vt-tip 규칙이 없다');
  assert.match(m[0], /flex-direction:\s*column/);
  assert.match(m[0], /align-items:\s*flex-start/);
  assert.match(m[0], /border:\s*1px solid var\(--color-line-strong\)/);
  assert.match(m[0], /background:\s*var\(--color-bg-raised\)/);
  assert.match(m[0], /box-shadow:\s*var\(--shadow-e2\)/);
  assert.match(m[0], /max-width:\s*min\(280px/);
  // 화면 CSS가 이 규칙을 다시 덮지 않는다(99-overlays의 옛 덮어쓰기를 지웠다).
  const ov = fs.readFileSync(path.join(__dirname, '../../styles/screens/99-overlays.css'), 'utf8');
  assert.doesNotMatch(ov.replace(/\/\*[\s\S]*?\*\//g, ''), /\.vt-tip\s*\{/);
});
