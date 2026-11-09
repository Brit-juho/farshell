// 터미널 미리보기(팔레트 `@` · 빈 칸 세션 선택 카드)를 터미널 색으로 그린다 — 리디자인 6단계(시안 O01 · O03 · D03-A).
//
// 입력은 서버가 준 tmux `capture-pane -e` 원문(GET /api/tmux/preview/{name}?ansi=1)이다. 세션 안의 프로그램이
// 쓴 글자라 **신뢰하지 않는다**. 그래서 lib/ansilex.js(문자열 → innerHTML)와 다르게:
//   - 글자는 전부 textContent로 넣는다. HTML 문자열을 만들지 않는다.
//   - 색은 인라인 style이 아니라 **정해진 클래스 이름**(TOKEN_CLASSES)으로만 준다. 클래스는 CSS가
//     --color-term-* 토큰에 잇는다 — 스킨을 바꾸면 미리보기 색도 따라 바뀐다(시안: 「터미널 내용이 나오는 모든 곳은
//     이 토큰만 쓴다」). 256색·트루컬러는 가장 가까운 토큰 하나로 접는다.
//   - SGR 말고 다른 이스케이프(커서 이동·OSC 제목·하이퍼링크 등)와 제어 문자는 버린다.
//   - 줄 수·한 줄 길이에 상한을 둔다(큰 캡처가 와도 DOM이 커지지 않게).

/** 미리보기가 쓸 수 있는 클래스 전부. 이 밖의 값은 만들지 않는다(테스트가 본다). */
export const TOKEN_CLASSES = Object.freeze([
  'ta-dim', 'ta-red', 'ta-green', 'ta-amber', 'ta-blue', 'ta-mag', 'ta-cyan', 'ta-bright',
  'ta-bold', 'ta-rev', 'ta-bg-add', 'ta-bg-del', 'ta-bg-hl', 'ta-bg-sel',
]);

// 0–7(밝은 색 8–15도 같은 토큰): 검정은 바탕과 겹쳐 안 보이므로 흐린 글자로, 흰색은 기본 글자색(클래스 없음).
const BASE_FG = ['ta-dim', 'ta-red', 'ta-green', 'ta-amber', 'ta-blue', 'ta-mag', 'ta-cyan', ''];
const BRIGHT_FG = ['ta-dim', 'ta-red', 'ta-green', 'ta-amber', 'ta-blue', 'ta-mag', 'ta-cyan', 'ta-bright'];
// 배경은 뜻이 있는 넷만 살린다(diff 추가·삭제, 강조, 선택). 나머지 배경은 버린다 — 미리보기 칸에서 색 덩어리가 된다.
const BASE_BG = ['', 'ta-bg-del', 'ta-bg-add', 'ta-bg-hl', 'ta-bg-sel', '', '', ''];

// 256색 큐브·회색 띠 → RGB.
function xtermRgb(n) {
  if (n < 16) return null;
  if (n >= 232) { const v = 8 + (n - 232) * 10; return [v, v, v]; }
  const i = n - 16;
  const step = (k) => (k === 0 ? 0 : 55 + k * 40);
  return [step(Math.floor(i / 36)), step(Math.floor(i / 6) % 6), step(i % 6)];
}

/** RGB를 가장 가까운 토큰 하나로. 채도가 낮으면 밝기로 흐림/기본/밝음, 아니면 색상각으로 고른다. */
export function nearestToken(r, g, b) {
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 510;
  const sat = max === min ? 0 : (max - min) / (255 - Math.abs(max + min - 255));
  if (sat < 0.2 || max - min < 24) return l < 0.45 ? 'ta-dim' : l > 0.85 ? 'ta-bright' : '';
  let h;
  const d = max - min;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h = (h * 60 + 360) % 360;
  if (h < 20 || h >= 330) return 'ta-red';
  if (h < 70) return 'ta-amber';
  if (h < 160) return 'ta-green';
  if (h < 200) return 'ta-cyan';
  if (h < 260) return 'ta-blue';
  return 'ta-mag';
}

function indexedFg(n) {
  if (n < 8) return BASE_FG[n];
  if (n < 16) return BRIGHT_FG[n - 8];
  const rgb = xtermRgb(n);
  return rgb ? nearestToken(rgb[0], rgb[1], rgb[2]) : '';
}

// ESC로 시작하는 시퀀스 하나: CSI(… 최종 바이트), OSC(… BEL 또는 ESC \), 그 밖의 2바이트 이스케이프.
// eslint-disable-next-line no-control-regex
const SEQ = /\x1b\[([0-9;:?<=>]*)([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]?/g;
// 탭·줄바꿈을 뺀 C0 제어 문자와 DEL.
// eslint-disable-next-line no-control-regex
const CTRL = /[\x00-\x08\x0b-\x1f\x7f]/g;

function applySgr(state, params) {
  const p = params === '' ? [0] : params.split(/[;:]/).map((x) => (x === '' ? 0 : Number(x)));
  for (let i = 0; i < p.length; i++) {
    const c = p[i];
    if (!Number.isFinite(c)) continue;
    if (c === 0) { state.fg = ''; state.bg = ''; state.bold = false; state.dim = false; state.rev = false; }
    else if (c === 1) state.bold = true;
    else if (c === 2) state.dim = true;
    else if (c === 22) { state.bold = false; state.dim = false; }
    else if (c === 7) state.rev = true;
    else if (c === 27) state.rev = false;
    else if (c >= 30 && c <= 37) state.fg = BASE_FG[c - 30];
    else if (c >= 90 && c <= 97) state.fg = BRIGHT_FG[c - 90];
    else if (c === 39) state.fg = '';
    else if (c >= 40 && c <= 47) state.bg = BASE_BG[c - 40];
    else if (c >= 100 && c <= 107) state.bg = BASE_BG[c - 100];
    else if (c === 49) state.bg = '';
    else if (c === 38 || c === 48) {
      // 38;5;n · 38;2;r;g;b — 인자를 소비한다. 배경 확장색은 버린다(위 BASE_BG 주석).
      let cls = '';
      if (p[i + 1] === 5) { cls = indexedFg(p[i + 2] | 0); i += 2; }
      else if (p[i + 1] === 2) { cls = nearestToken(p[i + 2] | 0, p[i + 3] | 0, p[i + 4] | 0); i += 4; }
      else break;
      if (c === 38) state.fg = cls;
    }
  }
}

function classesOf(state) {
  const out = [];
  if (state.dim && !state.fg) out.push('ta-dim');
  else if (state.fg) out.push(state.fg);
  if (state.bold) out.push('ta-bold');
  if (state.rev) out.push('ta-rev');
  if (state.bg) out.push(state.bg);
  return out.join(' ');
}

/**
 * ANSI 원문 → 줄 배열. 각 줄은 [{ text, cls }] 조각들. cls는 TOKEN_CLASSES 안의 이름을 공백으로 이은 것(없으면 '').
 * 빈 줄 뭉치는 하나로 접고, 앞뒤 빈 줄은 버리고, 끝에서 maxLines줄만 남긴다.
 */
export function parseAnsi(text, { maxLines = 40, maxCols = 300 } = {}) {
  const src = String(text == null ? '' : text).replace(/\r\n?/g, '\n');
  const state = { fg: '', bg: '', bold: false, dim: false, rev: false };
  const lines = [];
  for (const raw of src.split('\n')) {
    const segs = [];
    let cols = 0;
    let last = 0;
    const push = (chunk) => {
      if (!chunk || cols >= maxCols) return;
      const clean = chunk.replace(CTRL, '').slice(0, maxCols - cols);
      if (!clean) return;
      cols += clean.length;
      const cls = classesOf(state);
      const prev = segs[segs.length - 1];
      if (prev && prev.cls === cls) prev.text += clean; else segs.push({ text: clean, cls });
    };
    SEQ.lastIndex = 0;
    let m;
    while ((m = SEQ.exec(raw))) {
      push(raw.slice(last, m.index));
      last = SEQ.lastIndex;
      if (m[2] === 'm' && m[1] !== undefined && !/[?<=>]/.test(m[1])) applySgr(state, m[1]);
      if (m[0] === '') SEQ.lastIndex++;   // 빈 일치 방지
    }
    push(raw.slice(last));
    lines.push(segs);
  }
  // 빈 줄 정리(ansilex.trimBlankLines와 같은 규칙): 연속 빈 줄은 하나, 앞뒤 빈 줄은 버린다.
  const blank = (segs) => segs.every((s) => !s.text.trim());
  const out = [];
  let run = false;
  for (const segs of lines) {
    const b = blank(segs);
    if (b && run) continue;
    out.push(b ? [] : segs);
    run = b;
  }
  while (out.length && blank(out[0])) out.shift();
  while (out.length && blank(out[out.length - 1])) out.pop();
  return out.slice(-maxLines);
}

/** `host`를 비우고 미리보기 줄을 그린다. 글자는 textContent, 색은 클래스로만. 반환: 그린 줄 수. */
export function renderAnsi(host, text, opts) {
  const doc = host.ownerDocument;
  const frag = doc.createDocumentFragment();
  const lines = parseAnsi(text, opts);
  for (const segs of lines) {
    const line = doc.createElement('div');
    line.className = 'vt-ansi-line';
    for (const s of segs) {
      if (!s.cls) { line.appendChild(doc.createTextNode(s.text)); continue; }
      const span = doc.createElement('span');
      span.className = s.cls;
      span.textContent = s.text;
      line.appendChild(span);
    }
    if (!segs.length) line.appendChild(doc.createTextNode(' '));
    frag.appendChild(line);
  }
  host.replaceChildren(frag);
  host.classList.add('vt-ansi');
  return lines.length;
}

// 같은 세션 미리보기를 짧은 사이에 여러 번 묻지 않게(팔레트에서 위아래로 훑을 때) 2초 동안 기억한다.
// 서버도 1초 캐시를 두지만 왕복 자체를 줄인다. 키는 「호스트|세션|줄 수」.
const CACHE_MS = 2000;
const cache = new Map();

/**
 * 세션 미리보기 원문을 받는다. fetchJson은 호출자의 fetch(중계·인증이 이미 붙은 것 — 팔레트는 deps.vtFetch).
 * 실패·없음이면 null. 받는 쪽은 결과가 오기 전에 다른 세션을 골랐을 수 있으니 이름을 다시 확인해야 한다.
 */
export async function fetchPreview(fetchJson, name, { lines = 30, host = '' } = {}) {
  if (!name) return null;
  const key = `${host}|${name}|${lines}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.value;
  let value = null;
  try {
    const d = await fetchJson(`/api/tmux/preview/${encodeURIComponent(name)}?lines=${lines}&ansi=1`);
    value = d && d.available !== false && typeof d.content === 'string' ? d.content : null;
  } catch (_) { value = null; }
  cache.set(key, { at: Date.now(), value });
  if (cache.size > 64) cache.delete(cache.keys().next().value);
  return value;
}
