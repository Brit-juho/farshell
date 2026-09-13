// N14 — Ghostty / Warp 테마 가져오기.
//
// 스킨은 두 층이다: **터미널 팔레트**(theme.js의 VT_XTERM_THEMES — 배경·전경·
// 커서·ANSI 16색)와 **UI 크롬**(styles/theme/skins.css의 --color-* 30개).
// Ghostty config와 Warp YAML은 첫 층에만 1:1로 매핑된다 — 두 형식 모두 UI
// 토큰이라는 개념 자체가 없다.
//
// 사용자 선택(2026-09-14): 배경·전경·ANSI에서 **UI 토큰까지 추론해 7번째
// 스킨을 만든다**. 그래서 이 파일의 절반은 추론이고, 그 추론이 읽을 수 없는
// 화면을 만들지 않도록 마지막에 대비 검사를 거친다 — 임의의 테마를 받는
// 이상 "노란 글자에 흰 배경" 같은 조합이 실제로 들어온다.
//
// 이 모듈은 **지연 로드 대상**이다(설정 화면에서만 쓴다). 부팅 경로는
// 저장된 토큰 맵을 CSS 변수로 쓰기만 하면 되므로 theme-custom.js가 따로 있다.

export const IMPORTED_SKIN = 'imported';

// ── 색 유틸 ────────────────────────────────────────────────────────────────

/** '#rgb' · '#rrggbb' · 'rrggbb' · 'rgb(r,g,b)' → {r,g,b} (0-255). 실패 시 null. */
export function parseColor(input) {
  if (typeof input !== 'string') return null;
  let s = input.trim().replace(/^['"]|['"]$/g, '');
  const rgb = s.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i);
  if (rgb) {
    return { r: _byte(rgb[1]), g: _byte(rgb[2]), b: _byte(rgb[3]) };
  }
  s = s.replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(s)) {
    return { r: parseInt(s[0] + s[0], 16), g: parseInt(s[1] + s[1], 16), b: parseInt(s[2] + s[2], 16) };
  }
  if (/^[0-9a-f]{6}$/i.test(s)) {
    return { r: parseInt(s.slice(0, 2), 16), g: parseInt(s.slice(2, 4), 16), b: parseInt(s.slice(4, 6), 16) };
  }
  return null;
}

function _byte(v) {
  const n = Math.round(Number(v));
  return Math.max(0, Math.min(255, Number.isFinite(n) ? n : 0));
}

export function toHex({ r, g, b }) {
  const h = (v) => _byte(v).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** WCAG 상대 휘도. */
export function luminance(c) {
  const ch = (v) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
}

/** WCAG 대비비(1~21). */
export function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** a를 b 쪽으로 t(0~1)만큼 섞는다. */
export function mix(a, b, t) {
  const k = Math.max(0, Math.min(1, t));
  return {
    r: a.r + (b.r - a.r) * k,
    g: a.g + (b.g - a.g) * k,
    b: a.b + (b.b - a.b) * k,
  };
}

const WHITE = { r: 255, g: 255, b: 255 };
const BLACK = { r: 0, g: 0, b: 0 };

/**
 * `color`를 배경에서 **멀어지는 방향으로** 밀어 목표 대비를 채운다.
 * 어두운 배경이면 밝게, 밝은 배경이면 어둡게 — 색상은 최대한 유지한다.
 * 흰색/검정까지 갔는데도 목표에 못 미치면 그 끝값을 돌려준다(더 갈 곳이 없다).
 */
export function ensureContrast(color, bg, target) {
  if (contrast(color, bg) >= target) return color;
  const toward = luminance(bg) < 0.5 ? WHITE : BLACK;
  let best = color;
  for (let i = 1; i <= 20; i++) {
    const c = mix(color, toward, i / 20);
    best = c;
    if (contrast(c, bg) >= target) return c;
  }
  return best;
}

function _rgbaTint(c, alpha) {
  return `rgb(${_byte(c.r)} ${_byte(c.g)} ${_byte(c.b)} / ${alpha})`;
}

// ── 파서 ──────────────────────────────────────────────────────────────────

// 16색 이름 순서(ANSI 0~15) — xterm 테마 키 이름과 1:1.
const ANSI_KEYS = [
  'black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
  'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue',
  'brightMagenta', 'brightCyan', 'brightWhite',
];

/**
 * Ghostty config(`key = value`, `#` 주석). 팔레트는 `palette = 0=#1d2021` 형태다.
 * 인식 못 한 키는 조용히 무시한다 — Ghostty config에는 색과 무관한 설정이
 * 훨씬 많고, 그걸 오류로 만들면 실제 설정 파일은 하나도 못 읽는다.
 */
export function parseGhostty(text) {
  const out = { name: '', background: null, foreground: null, cursor: null, selection: null, palette: {} };
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.replace(/^\s+/, '');
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim().toLowerCase();
    const value = line.slice(eq + 1).trim();
    if (key === 'palette') {
      const m = value.match(/^(\d+)\s*=\s*(.+)$/);
      if (m) {
        const idx = Number(m[1]);
        const c = parseColor(m[2]);
        if (c && idx >= 0 && idx < 16) out.palette[idx] = c;
      }
    } else if (key === 'background') out.background = parseColor(value);
    else if (key === 'foreground') out.foreground = parseColor(value);
    else if (key === 'cursor-color') out.cursor = parseColor(value);
    else if (key === 'selection-background') out.selection = parseColor(value);
    else if (key === 'theme') out.name = value.replace(/^['"]|['"]$/g, '');
  }
  return out;
}

/**
 * Warp 테마 YAML. 전용 파서를 쓰지 않는다 — 이 파일들은 색 목록이라 구조가
 * 얕고(2단계), YAML 라이브러리 하나가 번들에 들어가는 값이 없다. 우리가
 * 읽는 키 밖의 문법은 무시한다.
 */
export function parseWarp(text) {
  const out = { name: '', background: null, foreground: null, cursor: null, selection: null, accent: null, palette: {} };
  let group = null;     // 'normal' | 'bright' | null
  for (const raw of String(text || '').split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith('#')) continue;
    const indent = raw.length - raw.replace(/^\s*/, '').length;
    const line = raw.trim();
    const colon = line.indexOf(':');
    if (colon < 0) continue;
    const key = line.slice(0, colon).trim().toLowerCase();
    const value = line.slice(colon + 1).trim();

    if (indent === 0) {
      group = null;
      if (key === 'terminal_colors') continue;
      if (key === 'name') { out.name = value.replace(/^['"]|['"]$/g, ''); continue; }
      if (key === 'background') out.background = parseColor(value);
      else if (key === 'foreground') out.foreground = parseColor(value);
      else if (key === 'accent') out.accent = parseColor(value);
      else if (key === 'cursor') out.cursor = parseColor(value);
      continue;
    }
    if (key === 'normal' || key === 'bright') { group = key; continue; }
    if (!group) continue;
    const idx = ANSI_KEYS.indexOf(group === 'normal' ? key : 'bright' + key[0].toUpperCase() + key.slice(1));
    if (idx >= 0) {
      const c = parseColor(value);
      if (c) out.palette[idx] = c;
    }
  }
  return out;
}

/** 내용을 보고 형식을 고른다 — 사용자가 "어느 쪽이냐"를 고르게 하면 틀린다. */
export function parseTheme(text) {
  const s = String(text || '');
  if (/terminal_colors\s*:/.test(s) || /^\s*accent\s*:/m.test(s)) return { kind: 'warp', ...parseWarp(s) };
  return { kind: 'ghostty', ...parseGhostty(s) };
}

// ── 터미널 팔레트 ──────────────────────────────────────────────────────────

/** 파싱 결과 → xterm 테마. 비어 있는 자리는 배경/전경에서 만들어 채운다 —
 *  16색을 다 적지 않은 테마 파일이 흔하다. */
export function toXtermTheme(parsed) {
  const bg = parsed.background || { r: 16, g: 16, b: 18 };
  const fg = parsed.foreground || (luminance(bg) < 0.5 ? WHITE : BLACK);
  const cursor = parsed.cursor || parsed.accent || fg;
  const theme = {
    background: toHex(bg),
    foreground: toHex(fg),
    cursor: toHex(cursor),
    cursorAccent: toHex(bg),
    selectionBackground: _rgbaTint(parsed.selection || cursor, 0.3),
  };
  for (let i = 0; i < 16; i++) {
    const c = parsed.palette[i];
    if (c) { theme[ANSI_KEYS[i]] = toHex(c); continue; }
    // 없는 색은 밝기 단계만 맞춘 무채색으로 — 색을 지어내는 것보다 낫다.
    const t = i < 8 ? 0.35 : 0.7;
    theme[ANSI_KEYS[i]] = toHex(mix(bg, fg, t));
  }
  return theme;
}

// ── UI 토큰 추론 ──────────────────────────────────────────────────────────

// 대비 목표. 본문 글자는 AA(4.5), 보조 텍스트·상태 색은 큰 글자/도형 기준(3).
const TARGET_TXT = 4.5;
const TARGET_SUB = 4.5;
const TARGET_MUTED = 3;
const TARGET_ACC = 3;
const TARGET_INK = 4.5;

/**
 * 터미널 팔레트에서 UI 토큰 30개를 만든다. 반환값은 `{'--color-bg-0': '#...'}`
 * 형태의 평범한 맵이라 그대로 CSS 변수로 쓰면 된다(부팅 경로가 파서를 몰라도
 * 되는 이유).
 *
 * 추론 규칙은 하나로 요약된다: **면은 배경에서 조금씩 멀어지게, 글자와 상태
 * 색은 배경에서 충분히 멀어지게.** 밝은 테마와 어두운 테마에서 "멀어진다"의
 * 방향이 반대라 그 판정(isDark)만 한 번 하고 나머지는 같은 식을 쓴다.
 */
export function deriveTokens(parsed) {
  const bg = parsed.background || { r: 16, g: 16, b: 18 };
  const fg0 = parsed.foreground || (luminance(bg) < 0.5 ? WHITE : BLACK);
  const isDark = luminance(bg) < 0.5;
  const away = isDark ? WHITE : BLACK;

  const pal = (i, fallback) => parsed.palette[i] || fallback;
  const txt = ensureContrast(fg0, bg, TARGET_TXT);
  const sub = ensureContrast(mix(fg0, bg, 0.35), bg, TARGET_SUB);
  const muted = ensureContrast(mix(fg0, bg, 0.55), bg, TARGET_MUTED);

  // 액센트: 커서 색이 1순위다(테마 제작자가 "눈에 띄어야 하는 색"으로 고른
  // 값이라 의도가 가장 가깝다). 없으면 Warp의 accent, 그다음 ANSI blue.
  const accRaw = parsed.cursor || parsed.accent || pal(4, pal(12, fg0));
  const acc = ensureContrast(accRaw, bg, TARGET_ACC);

  // 채움 버튼: 바탕 위 글자가 4.5를 넘어야 한다. 흰 글자와 검은 글자 중
  // 더 잘 보이는 쪽을 고르고, 그래도 모자라면 바탕을 글자 반대쪽으로 민다.
  let accSurface = acc;
  let ink = contrast(WHITE, accSurface) >= contrast(BLACK, accSurface) ? WHITE : BLACK;
  if (contrast(ink, accSurface) < TARGET_INK) {
    accSurface = ensureContrast(accSurface, ink, TARGET_INK);
    ink = contrast(WHITE, accSurface) >= contrast(BLACK, accSurface) ? WHITE : BLACK;
  }

  const st = (idx, target) => toHex(ensureContrast(pal(idx, mix(bg, fg0, 0.6)), bg, target));

  const tokens = {
    '--color-bg-0': toHex(bg),
    '--color-bg-1': toHex(mix(bg, away, 0.05)),
    '--color-bg-2': toHex(mix(bg, away, 0.09)),
    '--color-bg-3': toHex(mix(bg, away, 0.15)),
    '--color-line': _rgbaTint(fg0, 0.09),
    '--color-line-strong': _rgbaTint(fg0, 0.18),
    '--color-txt': toHex(txt),
    '--color-sub': toHex(sub),
    '--color-muted': toHex(muted),
    '--color-acc': toHex(acc),
    '--color-acc-dim': toHex(mix(acc, bg, 0.25)),
    '--color-acc-surface': toHex(accSurface),
    '--color-acc-ink': toHex(ink),
    '--color-st-idle': toHex(muted),
    '--color-st-working': st(2, TARGET_ACC),   // green
    '--color-st-waiting': st(3, TARGET_ACC),   // yellow
    '--color-st-done': st(4, TARGET_ACC),      // blue
    '--color-st-error': st(1, TARGET_ACC),     // red
    '--color-term-bg': toHex(bg),
    '--color-term-fg': toHex(fg0),
    '--color-surface-active': _rgbaTint(fg0, 0.18),
  };

  // 색점 램프 8색 — 세션 색깔 구분용이라 서로만 구별되면 된다. 테마의 밝기에
  // 맞춘 고정 색상환(다크는 밝은 파스텔, 라이트는 진한 톤)으로 만든다.
  const hues = [30, 90, 140, 180, 240, 280, 310, 350];
  hues.forEach((h, i) => {
    tokens[`--color-hash-${i + 1}`] = _hsl(h, 45, isDark ? 68 : 38);
  });

  return tokens;
}

function _hsl(h, s, l) {
  // hsl()을 그대로 써도 되지만, 저장값이 hex면 어디서 읽어도 같은 색이라
  // 디버깅이 쉽다(대비 검사도 hex 하나로 끝난다).
  const c = (1 - Math.abs(2 * (l / 100) - 1)) * (s / 100);
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l / 100 - c / 2;
  const t = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return toHex({ r: (t[0] + m) * 255, g: (t[1] + m) * 255, b: (t[2] + m) * 255 });
}

/**
 * 추론 결과의 대비 검사 — 화면에 적용하기 전에 사용자에게 보여줄 근거다.
 * 실패해도 막지 않는다(사용자가 고른 테마다) — 대신 무엇이 모자라는지 말한다.
 */
export function auditTokens(tokens) {
  const bg = parseColor(tokens['--color-bg-0']);
  const checks = [
    ['본문 글자', '--color-txt', TARGET_TXT],
    ['보조 글자', '--color-sub', TARGET_SUB],
    ['흐린 글자', '--color-muted', TARGET_MUTED],
    ['강조색', '--color-acc', TARGET_ACC],
  ];
  const issues = [];
  for (const [label, key, target] of checks) {
    const c = parseColor(tokens[key]);
    if (!c || !bg) continue;
    const ratio = contrast(c, bg);
    if (ratio < target) issues.push({ label, key, ratio: Math.round(ratio * 100) / 100, target });
  }
  const surface = parseColor(tokens['--color-acc-surface']);
  const ink = parseColor(tokens['--color-acc-ink']);
  if (surface && ink) {
    const ratio = contrast(ink, surface);
    if (ratio < TARGET_INK) {
      issues.push({ label: '채움 버튼 글자', key: '--color-acc-ink', ratio: Math.round(ratio * 100) / 100, target: TARGET_INK });
    }
  }
  return issues;
}

/** 텍스트 한 덩이 → 저장 가능한 스킨. 실패하면 null(이유는 reason). */
export function buildImportedSkin(text, fallbackName = '가져온 테마') {
  const parsed = parseTheme(text);
  if (!parsed.background && !parsed.foreground && Object.keys(parsed.palette).length === 0) {
    return { ok: false, reason: '색을 하나도 찾지 못했습니다 — Ghostty config나 Warp 테마 YAML인지 확인해 주세요.' };
  }
  const tokens = deriveTokens(parsed);
  return {
    ok: true,
    skin: {
      name: parsed.name || fallbackName,
      kind: parsed.kind,
      xterm: toXtermTheme(parsed),
      tokens,
    },
    issues: auditTokens(tokens),
  };
}
