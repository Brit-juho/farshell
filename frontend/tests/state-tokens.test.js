// 상호작용 상태 토큰 계약 (2026-11, 화면 점검 6절).
//
// 값(대비)은 tokens-contrast.test.js가 본다. 이 파일은 **소비 규칙**을 본다 — 누가 소스에서
// 옛 체계(--menu-hover · 소비처별 outline · 제각각 transition)를 되살리면 여기서 걸린다.
// jsdom은 CSS를 로드하지 않으므로 파일을 직접 읽어 계산한다(tokens-contrast.test.js와 같은 방식).
'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const STYLES = path.join(ROOT, 'styles');

function cssFiles() {
  const out = [];
  for (const dir of ['layers', 'screens', 'theme']) {
    for (const f of fs.readdirSync(path.join(STYLES, dir))) {
      if (f.endsWith('.css')) out.push(path.join(STYLES, dir, f));
    }
  }
  return out;
}
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, ' ');
const read = (p) => strip(fs.readFileSync(p, 'utf8'));
const rel = (p) => path.relative(ROOT, p);

/** 규칙 단위로 자른다 — [선택자, 선언부] (중첩 @media 안의 규칙 포함, @-규칙 머리는 제외). */
function rules(css) {
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css))) {
    const sel = m[1].trim();
    if (!sel.startsWith('@')) out.push([sel, m[2]]);
  }
  return out;
}

test('옛 별칭(--menu-hover · --crust · --wrad · --trad)을 어디서도 쓰지 않는다', () => {
  const hits = [];
  const files = [...cssFiles()];
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p); else if (/\.(js|ts|tsx)$/.test(e.name)) files.push(p);
  });
  walk(path.join(ROOT, 'frontend', 'js'));
  for (const f of files) {
    const body = f.endsWith('.css') ? read(f) : fs.readFileSync(f, 'utf8');
    for (const name of ['--menu-hover', '--crust', '--wrad', '--trad']) {
      if (body.includes(name)) hits.push(`${rel(f)} ${name}`);
    }
  }
  assert.deepStrictEqual(hits, [], `옛 별칭이 남았다 — --color-state-* · --color-scrim · --radius-*로:\n  ${hits.join('\n  ')}`);
});

test('포커스 링 규칙은 00-tokens-reset.css의 :focus-visible 한 곳이고 토큰만 쓴다', () => {
  const ringRules = [];
  for (const f of cssFiles()) {
    for (const [sel, decl] of rules(read(f))) {
      if (!/outline\s*:/.test(decl) && !/outline-offset\s*:/.test(decl)) continue;
      if (/outline\s*:\s*none|outline\s*:\s*0\b|outline\s*:\s*1px solid transparent/.test(decl)) continue; // 끄기·dnd 표시
      if (/\.status-dot|\.vt-actcfg-list\.drop/.test(sel)) continue; // reduced-motion 대체 표시 · 끌어 놓기 대상
      ringRules.push([rel(f), sel, decl.trim()]);
    }
  }
  assert.strictEqual(ringRules.length, 1, `outline을 직접 쓰는 규칙은 포커스 링 본문(:focus-visible + :focus-within 묶음) 하나뿐이어야 한다:\n${JSON.stringify(ringRules, null, 1)}`);
  const [first] = ringRules;
  assert.strictEqual(first[0], 'styles/screens/00-tokens-reset.css');
  assert.match(first[2], /outline:\s*var\(--focus-ring-w\)\s+solid\s+var\(--color-focus-ring\)/);
  assert.match(first[2], /outline-offset:\s*var\(--focus-ring-offset\)/);
});

test('포커스 링 오프셋 숫자는 tokens.css의 한 쌍(+2 / −2 inset)이다', () => {
  const t = read(path.join(STYLES, 'theme', 'tokens.css'));
  assert.match(t, /--focus-ring-offset:\s*2px/);
  assert.match(t, /--focus-ring-inset:\s*-2px/);
  assert.match(t, /--focus-ring-w:\s*2px/);
  // 소비처가 :focus-visible에서 outline-offset을 숫자로 쓰지 않는다.
  const bad = [];
  for (const f of cssFiles()) {
    for (const [sel, decl] of rules(read(f))) {
      if (/:focus-visible/.test(sel) && /outline-offset\s*:\s*-?\d/.test(decl)) bad.push(`${rel(f)} ${sel}`);
    }
  }
  assert.deepStrictEqual(bad, []);
});

test('clipped 컨테이너의 inset 링 목록이 실제 CSS 클래스를 가리킨다', () => {
  const reset = read(path.join(STYLES, 'screens', '00-tokens-reset.css'));
  const m = reset.match(/input:not\([\s\S]*?\{\s*--focus-ring-offset:\s*var\(--focus-ring-inset\);/);
  assert.ok(m, 'inset 목록 블록이 없다');
  const all = cssFiles().map(read).join('\n');
  const dead = [];
  for (const cls of new Set(m[0].match(/\.vt-[\w-]+/g) || [])) {
    // 이 목록 밖의 어딘가에 그 클래스 규칙이 있어야 한다(오타·삭제 감지).
    const outside = all.replace(m[0], '');
    if (!outside.includes(cls) && !fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'shell', 'RailRow.tsx'), 'utf8').includes(cls.slice(1))) dead.push(cls);
  }
  assert.deepStrictEqual(dead, [], `정의가 없는 클래스가 inset 목록에 있다: ${dead.join(' ')}`);
});

test('hover 배경은 --color-state-hover 한 체계다 (--color-bg-3 호버 없음)', () => {
  const bad = [];
  for (const f of cssFiles()) {
    for (const [sel, decl] of rules(read(f))) {
      if (!/:hover/.test(sel)) continue;
      if (/background(-color)?\s*:\s*var\(--color-bg-3\)/.test(decl)) bad.push(`${rel(f)} ${sel}`);
    }
  }
  assert.deepStrictEqual(bad, [], `hover에 --color-bg-3을 쓰는 규칙:\n  ${bad.join('\n  ')}`);
});

test('눌림(:active) 규칙이 호버 규칙의 절반 이상이다 (이전: 16 / 137)', () => {
  let hover = 0;
  let press = 0;
  for (const f of cssFiles()) {
    for (const [sel, decl] of rules(read(f))) {
      if (/:hover/.test(sel) && /background/.test(decl)) hover += 1;
      if (/:active/.test(sel) && /(background|filter)/.test(decl)) press += 1;
    }
  }
  assert.ok(hover > 40, `hover 배경 규칙이 ${hover}개뿐이다 — 파서가 깨졌나`);
  assert.ok(press >= hover * 0.5, `눌림 규칙 ${press} < 호버 규칙 ${hover}의 절반`);
});

test('hover 배경을 가진 모든 단일 대상 규칙에 같은 대상의 :active 규칙이 있다', () => {
  const missing = [];
  const all = [];
  for (const f of cssFiles()) all.push(...rules(read(f)).map(([s, d]) => [rel(f), s, d]));
  const activeSelectors = new Set(all.filter(([, s]) => /:active/.test(s)).flatMap(([, s]) => s.split(',').map((x) => x.trim())));
  // 의도적으로 눌림이 없는 것: 배경을 지우는 규칙(background:none/transparent) · 목록 펼침의 hover 보조 요소
  for (const [file, sel, decl] of all) {
    if (!/background\s*:\s*var\(--color-state-hover\)/.test(decl)) continue;
    if (/:focus-within/.test(sel)) continue; // 안에 버튼을 가진 행(스니펫·네이티브) — 행 자체는 눌리지 않는다
    for (const part of sel.split(',').map((x) => x.trim())) {
      if (!/:hover(:not\([^)]*\))?$/.test(part)) continue;
      if (/:focus-within|\.pinned|\.foc/.test(part)) continue;
      const act = part.replace(/:hover(?=(:not\([^)]*\))?$)/, ':active');
      if (!activeSelectors.has(act)) missing.push(`${file}: ${part}`);
    }
  }
  assert.deepStrictEqual(missing, [], `:active 규칙이 없는 hover 규칙:\n  ${missing.join('\n  ')}`);
});

test('선택·waiting 행의 hover/눌림이 99-states.css에 있다 (틴트 위에서 hover가 사라지지 않는다)', () => {
  const st = read(path.join(STYLES, 'screens', '99-states.css'));
  for (const sel of ['.vt-srow.active:hover', '#vt-wgrail .vt-srow.active:hover', '.vt-ft-row.sel:hover',
    '.vt-srow.is-waiting:hover', '#vt-wgrail .vt-srow.is-waiting:hover', '.vt-segment[aria-selected="true"]:hover:not(:disabled)']) {
    assert.ok(st.includes(sel), `99-states.css에 ${sel} 가 없다`);
  }
  assert.match(st, /--color-state-selected-hover/);
  assert.match(st, /--color-state-waiting-hover/);
  // 선택 규칙의 특이도가 일반 행 hover보다 높아야 hover가 이긴다: #vt-wgrail .vt-srow:hover (1,2,0) < #vt-wgrail .vt-srow.active:hover (1,3,0)
  const rail = read(path.join(STYLES, 'screens', '70-worktree-rail.css'));
  assert.match(rail, /#vt-wgrail \.vt-srow:hover \{ background:var\(--color-state-hover\); \}/);
});

test('전환은 색·불투명도뿐이고 폭·padding·margin·위치는 없다 (상호작용 규칙)', () => {
  // 데이터 막대(게이지·진행률)의 width는 상태 전환이 아니라 값 표시라 허용한다.
  const ALLOW = /vt-usage-fill|vt-gauge|gauge-fill|vt-sys-fill|vt-gr-bar|vt-fl-gauge-fill/;
  const bad = [];
  for (const f of cssFiles()) {
    for (const [sel, decl] of rules(read(f))) {
      const m = decl.match(/transition(?:-property)?\s*:\s*([^;]+)/);
      if (!m) continue;
      if (/\b(width|height|padding|margin|left|right|top|bottom|inset|max-width|min-width)\b/.test(m[1]) && !ALLOW.test(sel)) {
        bad.push(`${rel(f)} ${sel}: ${m[1].trim()}`);
      }
    }
  }
  assert.deepStrictEqual(bad, [], bad.join('\n'));
});

test('상호작용 전환 기본선: --dur-fast · --ease-fast, 모션 감소에서는 0', () => {
  const s = read(path.join(STYLES, 'layers', 'states.css'));
  assert.match(s, /\.vt-act/, '활동 막대 항목이 전환 목록에 없다(이전: 호버 전환 0s)');
  assert.match(s, /transition:\s*background-color var\(--dur-fast\) var\(--ease-fast\)/);
  assert.match(s, /prefers-reduced-motion:\s*reduce\)\s*\{\s*:root\s*\{\s*--dur-fast:\s*0ms/);
  const t = read(path.join(STYLES, 'theme', 'tokens.css'));
  assert.match(t, /--dur-fast:\s*120ms/);
  assert.match(t, /--ease-fast:/);
});

test('커서·비활성 기본선이 있다 (pointer / not-allowed / opacity .45)', () => {
  const s = read(path.join(STYLES, 'layers', 'states.css'));
  assert.match(s, /cursor:\s*pointer/);
  assert.match(s, /cursor:\s*not-allowed/);
  assert.match(s, /opacity:\s*\.45/);
});

test('main.css가 states.css(components 레이어 앞)와 99-states.css(마지막)를 import한다', () => {
  const m = fs.readFileSync(path.join(STYLES, 'main.css'), 'utf8');
  assert.ok(m.indexOf('./layers/states.css') > 0 && m.indexOf('./layers/states.css') < m.indexOf('./layers/components.css'));
  const imports = [...m.matchAll(/@import "\.\/screens\/([^"]+)"/g)].map((x) => x[1]);
  assert.strictEqual(imports[imports.length - 1], '99-states.css');
});
