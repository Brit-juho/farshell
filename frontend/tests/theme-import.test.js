// N14 — Ghostty/Warp 테마 가져오기. 파싱과 UI 토큰 추론이 전부 순수 함수라
// DOM 없이 검증한다.
//
// 이 테스트가 지키는 것 중 가장 중요한 것: **추론이 읽을 수 없는 화면을
// 만들지 않는다.** 임의의 테마 파일을 받는 이상 "노란 전경 + 흰 배경" 같은
// 조합이 실제로 들어오는데, 그대로 UI 토큰으로 쓰면 글자가 안 보인다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = path.join(__dirname, '../js/theme-import.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(JS, env.context, new Map());
}

const GHOSTTY = `
# Gruvbox Dark 비슷한 것
theme = gruvbox-dark
background = 1d2021
foreground = #ebdbb2
cursor-color = #fe8019
palette = 0=#282828
palette = 1=#cc241d
palette = 2=#98971a
palette = 3=#d79921
palette = 4=#458588
palette = 5=#b16286
palette = 6=#689d6a
palette = 7=#a89984
font-family = JetBrains Mono
window-padding-x = 8
`;

const WARP = `
name: Solarized Light
accent: '#268bd2'
background: '#fdf6e3'
foreground: '#586e75'
details: lighter
terminal_colors:
  normal:
    black: '#073642'
    red: '#dc322f'
    green: '#859900'
    yellow: '#b58900'
    blue: '#268bd2'
    magenta: '#d33682'
    cyan: '#2aa198'
    white: '#eee8d5'
  bright:
    black: '#002b36'
    red: '#cb4b16'
    green: '#586e75'
    yellow: '#657b83'
    blue: '#839496'
    magenta: '#6c71c4'
    cyan: '#93a1a1'
    white: '#fdf6e3'
`;

// ── 색 유틸 ────────────────────────────────────────────────────────────────

test('parseColor — #rgb · #rrggbb · 접두 없는 hex · rgb()', async () => {
  const T = await load();
  assert.strictEqual(T.toHex(T.parseColor('#fff')), '#ffffff');
  assert.strictEqual(T.toHex(T.parseColor('#1d2021')), '#1d2021');
  assert.strictEqual(T.toHex(T.parseColor('1d2021')), '#1d2021');
  assert.strictEqual(T.toHex(T.parseColor("'#1d2021'")), '#1d2021');
  assert.strictEqual(T.toHex(T.parseColor('rgb(29, 32, 33)')), '#1d2021');
  assert.strictEqual(T.parseColor('not a color'), null);
  assert.strictEqual(T.parseColor(null), null);
});

test('contrast — 흑백이 21:1', async () => {
  const T = await load();
  const w = T.parseColor('#ffffff');
  const b = T.parseColor('#000000');
  assert.ok(Math.abs(T.contrast(w, b) - 21) < 0.01);
  assert.ok(Math.abs(T.contrast(w, w) - 1) < 0.01);
});

test('ensureContrast — 목표에 닿을 때까지 배경 반대쪽으로 민다', async () => {
  const T = await load();
  const bg = T.parseColor('#1d2021');          // 어두운 배경
  const dim = T.parseColor('#2a2a2a');         // 거의 안 보이는 글자
  const fixed = T.ensureContrast(dim, bg, 4.5);
  assert.ok(T.contrast(fixed, bg) >= 4.5);
  // 이미 충분하면 그대로 둔다(색을 함부로 바꾸지 않는다).
  const ok = T.parseColor('#ebdbb2');
  assert.strictEqual(T.toHex(T.ensureContrast(ok, bg, 4.5)), '#ebdbb2');
});

// ── 파서 ──────────────────────────────────────────────────────────────────

test('parseGhostty — 색만 뽑고 나머지 설정은 조용히 무시한다', async () => {
  const T = await load();
  const p = T.parseGhostty(GHOSTTY);
  assert.strictEqual(p.name, 'gruvbox-dark');
  assert.strictEqual(T.toHex(p.background), '#1d2021');
  assert.strictEqual(T.toHex(p.foreground), '#ebdbb2');
  assert.strictEqual(T.toHex(p.cursor), '#fe8019');
  assert.strictEqual(T.toHex(p.palette[1]), '#cc241d');
  assert.strictEqual(T.toHex(p.palette[7]), '#a89984');
  assert.strictEqual(p.palette[8], undefined, '적지 않은 색은 없는 채로 둔다');
});

test('parseWarp — normal/bright 그룹이 ANSI 0-15로 간다', async () => {
  const T = await load();
  const p = T.parseWarp(WARP);
  assert.strictEqual(p.name, 'Solarized Light');
  assert.strictEqual(T.toHex(p.background), '#fdf6e3');
  assert.strictEqual(T.toHex(p.accent), '#268bd2');
  assert.strictEqual(T.toHex(p.palette[1]), '#dc322f');   // normal red
  assert.strictEqual(T.toHex(p.palette[9]), '#cb4b16');   // bright red
});

test('parseTheme — 형식을 내용으로 판별한다(사용자에게 묻지 않는다)', async () => {
  const T = await load();
  assert.strictEqual(T.parseTheme(GHOSTTY).kind, 'ghostty');
  assert.strictEqual(T.parseTheme(WARP).kind, 'warp');
});

test('toXtermTheme — 안 적힌 ANSI 색도 빈자리 없이 채운다', async () => {
  const T = await load();
  const theme = T.toXtermTheme(T.parseGhostty(GHOSTTY));
  assert.strictEqual(theme.background, '#1d2021');
  assert.strictEqual(theme.red, '#cc241d');
  for (const k of ['brightBlack', 'brightRed', 'brightWhite']) {
    assert.ok(/^#[0-9a-f]{6}$/.test(theme[k]), `${k}가 비어 있으면 xterm이 기본 팔레트로 떨어진다`);
  }
});

// ── UI 토큰 추론 ──────────────────────────────────────────────────────────

test('deriveTokens — 스킨이 쓰는 토큰을 빠짐없이 만든다', async () => {
  const T = await load();
  const tokens = T.deriveTokens(T.parseGhostty(GHOSTTY));
  const required = [
    '--color-bg-0', '--color-bg-1', '--color-bg-2', '--color-bg-3',
    '--color-line', '--color-line-strong', '--color-txt', '--color-sub', '--color-muted',
    '--color-acc', '--color-acc-dim', '--color-acc-surface', '--color-acc-ink',
    '--color-st-idle', '--color-st-working', '--color-st-waiting', '--color-st-done',
    '--color-st-error', '--color-term-bg', '--color-term-fg', '--color-surface-active',
    '--color-hash-1', '--color-hash-8',
  ];
  for (const k of required) assert.ok(tokens[k], `${k}가 비어 있다`);
  assert.strictEqual(tokens['--color-bg-0'], '#1d2021');
  assert.strictEqual(tokens['--color-term-fg'], '#ebdbb2');
});

test('어두운 테마는 면이 밝아지고, 밝은 테마는 면이 어두워진다', async () => {
  const T = await load();
  const dark = T.deriveTokens(T.parseGhostty(GHOSTTY));
  const light = T.deriveTokens(T.parseWarp(WARP));
  const lum = (hex) => T.luminance(T.parseColor(hex));
  assert.ok(lum(dark['--color-bg-3']) > lum(dark['--color-bg-0']));
  assert.ok(lum(light['--color-bg-3']) < lum(light['--color-bg-0']));
});

test('대비 검사 — 정상 테마는 지적 사항이 없다', async () => {
  const T = await load();
  for (const src of [GHOSTTY, WARP]) {
    const tokens = T.deriveTokens(T.parseTheme(src));
    assert.strictEqual(T.auditTokens(tokens).length, 0, JSON.stringify(T.auditTokens(tokens)));
  }
});

test('읽기 힘든 테마도 보정해서 기준을 넘긴다 — 이게 이 추론의 존재 이유다', async () => {
  const T = await load();
  // 흰 배경 + 노란 전경. 그대로 쓰면 본문 글자가 1.1:1 수준이라 안 보인다.
  const tokens = T.deriveTokens(T.parseGhostty('background = #ffffff\nforeground = #ffee00\n'));
  const bg = T.parseColor(tokens['--color-bg-0']);
  assert.ok(T.contrast(T.parseColor(tokens['--color-txt']), bg) >= 4.5);
  assert.strictEqual(T.auditTokens(tokens).length, 0, JSON.stringify(T.auditTokens(tokens)));
});

test('채움 버튼 글자는 그 바탕 위에서 항상 읽힌다', async () => {
  const T = await load();
  for (const src of [GHOSTTY, WARP, 'background = #808080\nforeground = #909090\n']) {
    const tokens = T.deriveTokens(T.parseTheme(src));
    const ratio = T.contrast(T.parseColor(tokens['--color-acc-ink']), T.parseColor(tokens['--color-acc-surface']));
    assert.ok(ratio >= 4.5, `채움 버튼 대비 ${ratio}`);
  }
});

test('buildImportedSkin — 팔레트·토큰·이름을 한 덩이로 돌려준다', async () => {
  const T = await load();
  const built = T.buildImportedSkin(GHOSTTY);
  assert.strictEqual(built.ok, true);
  assert.strictEqual(built.skin.name, 'gruvbox-dark');
  assert.strictEqual(built.skin.kind, 'ghostty');
  assert.strictEqual(built.skin.xterm.background, '#1d2021');
  assert.ok(built.skin.tokens['--color-txt']);
  assert.strictEqual(built.issues.length, 0);
});

test('색이 하나도 없는 입력은 실패로 알려준다(조용히 빈 테마를 만들지 않는다)', async () => {
  const T = await load();
  const built = T.buildImportedSkin('# 그냥 주석\nfont-family = Menlo\n');
  assert.strictEqual(built.ok, false);
  assert.ok(built.reason.includes('색'));
});
