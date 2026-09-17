// T11(2026-09-08) — 색 토큰의 대비를 순수 계산으로 검증한다.
//
// ## 왜 필요한가
//
// jsdom은 app.css를 로드하지 않는다(keybar.test.js:108에 명시). 그래서 이 저장소의
// node 테스트는 CSS 규칙을 한 줄도 보지 않는다 — 실제로 `.vt-card.open-tab`의
// box-shadow가 6스킨 전부에서 죽어 있었는데 테스트 261개가 전부 초록이었다.
//
// 브라우저를 띄우는 건 느리고 준비할 게 많다(서버·로그인·playwright). 그런데 대비는
// **색 값만 있으면 계산되는 것**이라 브라우저가 필요 없다. tokens.css와 skins.css를
// 직접 파싱해서 계산하면 밀리초 단위로 끝나고 CI에 상주시켜도 아무도 안 느낀다.
//
// ## 이 파일이 못 보는 것
//
// 캐스케이드의 결과는 못 본다 — 배치가 깨졌는지, 어떤 규칙이 다른 규칙을 덮는지는
// 실제 렌더링이 필요하다(rail 세션 카드가 카드 밖으로 1008px 삐져나왔던 것 같은).
// 그건 docs/TODOS.md [E1]의 실브라우저 스모크 몫이다. 여기서는 "색 값 자체가
// 기준을 만족하는가"만 본다.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const TOKENS = path.join(ROOT, 'styles', 'theme', 'tokens.css');
const SKINS = path.join(ROOT, 'styles', 'theme', 'skins.css');

// ── 색 파싱 · 대비 계산 ───────────────────────────────────────────────────

function hexToRgb(hex) {
  let h = hex.replace('#', '').trim();
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

// WCAG 2.x 상대 휘도.
function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// 주석을 먼저 지운다 — 주석 본문의 예시 hex(스펙 초안 색 등)를 토큰으로 읽으면
// 있지도 않은 값을 검사하게 된다.
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, ' ');
}

// 값이 hex일 수도 있고 `var(--other)`일 수도 있다. T9에서 액센트가
// `--color-acc: var(--acc-macos)` 형태가 됐다 — 테마 칩이 "다른 스킨의" 액센트를
// 보여줘야 해서 스코프 밖 상수를 하나 더 뒀기 때문이다. hex만 찾으면 그 순간
// 토큰이 통째로 사라져 검사가 조용히 빠진다.
function readRaw(css) {
  const out = {};
  const re = /(--[\w-]+):\s*([^;]+);/g;
  let m;
  while ((m = re.exec(css))) out[m[1]] = m[2].trim();
  return out;
}

/** `var(--x)` 참조를 몇 단계 따라가 최종 hex를 얻는다. 못 풀면 undefined. */
function resolveHex(name, table, depth = 4) {
  let cur = table[name];
  for (let i = 0; i < depth; i += 1) {
    if (!cur) return undefined;
    if (cur.startsWith('#')) return cur;
    const ref = cur.match(/^var\(\s*(--[\w-]+)\s*\)$/);
    if (!ref) return undefined;
    cur = table[ref[1]];
  }
  return undefined;
}

/** 스킨별 최종 색 토큰. skins.css의 값이 tokens.css의 기본값을 덮는다. */
function loadSkins() {
  const tokens = stripComments(fs.readFileSync(TOKENS, 'utf8'));
  const skinsCss = stripComments(fs.readFileSync(SKINS, 'utf8'));
  const base = readRaw(tokens);
  const raw = { farshell: { ...base } };
  const re = /html\[data-skin="(\w+)"\]\s*\{([\s\S]*?)\n\s*\}/g;
  let m;
  while ((m = re.exec(skinsCss))) {
    raw[m[1]] = { ...base, ...readRaw(m[2]) };
  }
  const out = {};
  for (const [skin, table] of Object.entries(raw)) {
    out[skin] = {};
    for (const key of Object.keys(table)) {
      if (!key.startsWith('--color-')) continue;
      const hex = resolveHex(key, table);
      if (hex) out[skin][key] = hex;
    }
  }
  return out;
}

const SKINS_ALL = loadSkins();
const SKIN_NAMES = Object.keys(SKINS_ALL);
const BACKGROUNDS = ['--color-bg-0', '--color-bg-1', '--color-bg-2', '--color-bg-3'];

// 역할별 기준. muted까지 4.5를 강요하면 sub와 구분이 안 돼 3단 램프가 무너진다 —
// WCAG가 실제로 나누는 대로(1.4.3 텍스트 / 1.4.11 UI 컴포넌트) 나눈다.
const TEXT_TOKENS = ['--color-txt', '--color-sub'];
// 2.1.6에서 램프에 4번째 단계(--color-faint)가 생겼다. 이건 비활성 라벨·
// 플레이스홀더처럼 **못 읽어도 일이 막히지 않는** 글자 전용이라 텍스트가 아니라
// UI 컴포넌트 기준(3:1)으로 본다. 다만 bg-3(hover·활성 행)은 제외한다 —
// 어두운 스킨에서 2.2~2.9:1로 떨어지고, 그걸 맞추려 밝히면 바로 위 muted와
// 붙어 4단 램프가 다시 3단이 된다. 대신 "본문에 쓰지 않는다"가 규칙이고
// scripts/check_contrast.py가 램프 간격(1.25배)으로 그 분리를 지킨다.
const FAINT_BACKGROUNDS = ['--color-bg-0', '--color-bg-1', '--color-bg-2'];
const GRAPHIC_TOKENS = [
  '--color-muted',
  '--color-st-idle',
  '--color-st-working',
  '--color-st-waiting',
  '--color-st-done',
  '--color-st-error',
];

// ── 전제: 파서가 실제로 뭔가를 읽었는가 ──────────────────────────────────
//
// 파서가 조용히 빈 객체를 돌려주면 아래 모든 테스트가 0회 반복으로 통과한다.
// 검사기가 "통과하는데 아무것도 안 보는" 상태가 되는 걸 먼저 막는다.

test('토큰 파서가 6스킨을 모두 읽는다', () => {
  assert.deepStrictEqual(
    SKIN_NAMES.sort(),
    ['catppuccin', 'farshell', 'macos', 'notepad', 'vscode', 'windows'],
  );
});

test('각 스킨이 검사에 필요한 토큰을 전부 갖는다', () => {
  for (const skin of SKIN_NAMES) {
    for (const t of [...BACKGROUNDS, ...TEXT_TOKENS, ...GRAPHIC_TOKENS, '--color-acc']) {
      assert.ok(SKINS_ALL[skin][t], `${skin}에 ${t}가 없다 — 파서나 스킨 정의가 깨졌다`);
    }
  }
});

test('대비 계산이 알려진 값과 맞는다', () => {
  // 검산: 흰 위 검정은 21:1, 같은 색끼리는 1:1.
  assert.strictEqual(Math.round(contrast([0, 0, 0], [255, 255, 255])), 21);
  assert.strictEqual(Math.round(contrast([18, 52, 86], [18, 52, 86])), 1);
});

// ── 대비 ─────────────────────────────────────────────────────────────────

test('텍스트 토큰이 배경 4단 전부에서 AA(4.5:1)를 넘는다', () => {
  const fails = [];
  for (const skin of SKIN_NAMES) {
    for (const fg of TEXT_TOKENS) {
      for (const bg of BACKGROUNDS) {
        const r = contrast(hexToRgb(SKINS_ALL[skin][fg]), hexToRgb(SKINS_ALL[skin][bg]));
        if (r < 4.5) fails.push(`${skin} ${fg}/${bg} = ${r.toFixed(2)}:1`);
      }
    }
  }
  assert.deepStrictEqual(fails, [], `AA 미달:\n  ${fails.join('\n  ')}`);
});

test('--color-faint가 bg-0~2에서 3:1을 넘고, muted와 한 단계 이상 벌어진다', () => {
  const fails = [];
  for (const skin of SKIN_NAMES) {
    const faint = SKINS_ALL[skin]['--color-faint'];
    assert.ok(faint, `${skin}에 --color-faint가 없다 — 4단 램프가 안 채워졌다`);
    for (const bg of FAINT_BACKGROUNDS) {
      const r = contrast(hexToRgb(faint), hexToRgb(SKINS_ALL[skin][bg]));
      if (r < 3) fails.push(`${skin} --color-faint/${bg} = ${r.toFixed(2)}:1`);
    }
    // 램프가 눈으로 네 단계여야 한다. 값이 가까워지면(notepad의 sub 5.62 vs
    // muted 5.03 = 1.12배처럼) 이름만 네 개고 보이는 건 두세 개다.
    const bg0 = hexToRgb(SKINS_ALL[skin]['--color-bg-0']);
    const rMuted = contrast(hexToRgb(SKINS_ALL[skin]['--color-muted']), bg0);
    const rFaint = contrast(hexToRgb(faint), bg0);
    if (rMuted / rFaint < 1.25) {
      fails.push(`${skin} muted(${rMuted.toFixed(2)}) → faint(${rFaint.toFixed(2)}) = ${(rMuted / rFaint).toFixed(2)}배`);
    }
  }
  assert.deepStrictEqual(fails, [], `faint 기준 미달:\n  ${fails.join('\n  ')}`);
});

test('그래픽·UI 토큰이 배경 4단 전부에서 3:1을 넘는다', () => {
  const fails = [];
  for (const skin of SKIN_NAMES) {
    for (const fg of GRAPHIC_TOKENS) {
      for (const bg of BACKGROUNDS) {
        const r = contrast(hexToRgb(SKINS_ALL[skin][fg]), hexToRgb(SKINS_ALL[skin][bg]));
        if (r < 3) fails.push(`${skin} ${fg}/${bg} = ${r.toFixed(2)}:1`);
      }
    }
  }
  assert.deepStrictEqual(fails, [], `3:1 미달:\n  ${fails.join('\n  ')}`);
});

// 채움 버튼의 바탕은 --color-acc가 아니라 --color-acc-surface다. 기본값은 액센트
// 그대로지만 macos만 acc-dim으로 재정의돼 있다 — 애플 시스템 블루 위의 흰 글자가
// 3.65:1이었기 때문이다. 검사는 "실제로 글자가 얹히는 면"을 봐야 의미가 있다.
test('액센트 위 텍스트(acc-ink)가 실제 버튼 바탕에서 AA를 넘는다', () => {
  const fails = [];
  for (const skin of SKIN_NAMES) {
    const surface = SKINS_ALL[skin]['--color-acc-surface'];
    assert.ok(surface, `${skin}에 --color-acc-surface가 없다`);
    const r = contrast(hexToRgb(SKINS_ALL[skin]['--color-acc-ink']), hexToRgb(surface));
    if (r < 4.5) fails.push(`${skin} acc-ink/acc-surface = ${r.toFixed(2)}:1`);
  }
  assert.deepStrictEqual(fails, [], `주 액션 버튼의 글자가 안 읽힌다:\n  ${fails.join('\n  ')}`);
});

// 2.1.6 — legacy.css(2927줄 한 파일)가 styles/screens/ 아홉 개로 쪼개졌다.
// 아래 검사들은 "어느 파일에 있는가"가 아니라 "그 규칙이 어딘가에 살아 있는가"를
// 보는 것이므로, 파일 하나를 읽는 대신 screens 전체를 이어 붙여 본다. 이렇게
// 두면 다음에 또 쪼개도 이 테스트는 안 깨지고, 반대로 규칙이 **사라지면**
// 확실히 깨진다(그게 이 테스트가 지키려는 것이다).
function readScreens() {
  const dir = path.join(ROOT, 'styles', 'screens');
  return fs.readdirSync(dir).sort()
    .filter((f) => f.endsWith('.css'))
    .map((f) => fs.readFileSync(path.join(dir, f), 'utf8'))
    .join('\n');
}

test('채움 버튼이 --color-acc가 아니라 --color-acc-surface를 쓴다', () => {
  const legacy = readScreens();
  assert.doesNotMatch(legacy, /background:var\(--acc\);\s*color:var\(--acc-ink\)/,
    '액센트를 바탕으로 직접 쓰면 macos에서 흰 글자가 3.65:1로 떨어진다');
});

// ── 3단 램프가 실제로 3단인가 ────────────────────────────────────────────
//
// 대비만 보면 txt=sub=muted로 만들어도 전부 통과한다. 그러면 위계가 사라진다.

test('텍스트 3단이 실제로 구분되는 밝기 순서를 갖는다', () => {
  for (const skin of SKIN_NAMES) {
    const t = SKINS_ALL[skin];
    const bgDark = luminance(hexToRgb(t['--color-bg-0'])) < 0.35;
    const [txt, sub, muted] = ['--color-txt', '--color-sub', '--color-muted']
      .map((k) => luminance(hexToRgb(t[k])));
    // 다크 스킨이면 txt가 가장 밝고, 라이트 스킨이면 가장 어둡다.
    const ordered = bgDark ? txt > sub && sub > muted : txt < sub && sub < muted;
    assert.ok(ordered, `${skin}: 텍스트 3단의 밝기 순서가 무너졌다 (txt/sub/muted)`);
  }
});

// ── 상태를 색으로만 말하지 않는가 ────────────────────────────────────────

test('상태 dot에는 색 말고 모양 채널이 있다 (reduced-motion 대체 포함)', () => {
  const components = fs.readFileSync(
    path.join(ROOT, 'styles', 'layers', 'components.css'), 'utf8',
  );
  // waiting은 애니메이션으로 구분되고, 모션을 줄인 사용자에게는 outline으로 대체된다.
  assert.match(components, /prefers-reduced-motion/,
    'reduced-motion 대체가 없으면 waiting이 색 하나로만 남는다');
  assert.match(components, /outline:\s*2px solid var\(--color-st-waiting\)/,
    'reduced-motion에서 outline 대체가 사라졌다');
});

test('rail 활성 표시가 색 단독이 아니다 (막대가 있어야 한다)', () => {
  const legacy = readScreens();
  assert.match(legacy, /\.vt-rail-btn\.active::before\s*\{[^}]*background:var\(--color-acc\)/,
    'rail 활성 막대가 사라졌다 — 배경 단차만으로는 1.13~1.37:1이라 색 단독 신호가 된다');
});

test('rail 활성 배경이 hover와 다른 토큰을 쓴다', () => {
  const legacy = readScreens();
  const active = legacy.match(/\.vt-rail-btn\.active\s*\{([^}]*)\}/);
  assert.ok(active, '.vt-rail-btn.active 규칙이 없다');
  assert.match(active[1], /background:var\(--color-surface-active\)/);
  assert.doesNotMatch(active[1], /background:var\(--menu-hover\)/,
    'hover와 같은 배경이면 포인터 장치에서 활성 항목과 구분이 안 된다');
});

// ── 하드코딩 색이 다시 늘지 않는가 ───────────────────────────────────────

test('테마 칩의 점 색이 인라인 style로 돌아오지 않았다', () => {
  const html = fs.readFileSync(path.join(ROOT, 'frontend', 'index.html'), 'utf8');
  const chipSection = html.match(/theme-chip[\s\S]{0,2000}/);
  assert.ok(chipSection);
  assert.doesNotMatch(chipSection[0], /class="dot"[^>]*style=/,
    '칩 점 색이 인라인으로 돌아왔다 — tokens.css의 --acc-<skin> 상수를 쓴다');
});

test('theme.js가 스킨별 색 표를 다시 갖지 않는다', () => {
  const js = fs.readFileSync(path.join(ROOT, 'frontend', 'js', 'theme.js'), 'utf8');
  assert.doesNotMatch(js, /VT_BAR_COLOR/,
    'theme-color 메타 색은 CSS의 --bar를 읽는다 — JS에 표를 두면 두 곳이 어긋난다');
});
