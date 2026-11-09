// 미리보기 — 파일 개편 단계 7(시안 F07). 마크다운(렌더/원문), SVG(그림/원문), CSV·TSV(표/원문), 동영상·소리,
// PDF(다운로드만, 결정 D8). 그리기 진입은 file.js(`_openRun`·`_paintMedia`)이고, 여기는 판정과 DOM 조립만 한다.
// 색은 토큰만(계약 9, styles/screens/30-viewer.css). 글은 전부 textContent — 파일 내용이 HTML로 해석되는 길은
// 정화된 마크다운(markdown.js) 하나뿐이다.
import { t } from '../../core/i18n.js';
import { icon } from '../../ui/icons.js';
import { MD_MAX_BYTES } from './markdown.js';

/** CSV를 표로 그리는 상한(바이트, 계획 7.4). 넘으면 원문으로. */
export const CSV_MAX_BYTES = 1024 * 1024;
export const CSV_ROW_H = 24;
const CSV_OVERSCAN = 20;
/** 표로 그리는 열·행 상한. 1MB 안에도 쉼표만 있으면 열이 50만 개다 — 머리 행과 보이는 행마다 그만큼 칸을 만들어
 * 탭이 멈춘다. 행은 가상 스크롤이지만 몸통 높이(행 × 24px)가 브라우저의 요소 높이 상한에 닿지 않게 둔다.
 * 넘으면 표 대신 원문으로 연다(file.js가 이유를 적는다). */
export const CSV_MAX_COLS = 512;
export const CSV_MAX_ROWS = 100000;

/** 경로의 미리보기 종류: 'md' | 'csv' | 'svg' | 'pdf' | null. */
export function previewKind(path) {
  const m = /\.([a-z0-9]+)$/i.exec(String(path || ''));
  const ext = m ? m[1].toLowerCase() : '';
  if (ext === 'md' || ext === 'markdown') return 'md';
  if (ext === 'csv' || ext === 'tsv') return 'csv';
  if (ext === 'svg') return 'svg';
  if (ext === 'pdf') return 'pdf';
  return null;
}

/** 렌더/원문 전환이 있는 종류. */
export function hasRenderedView(kind) {
  return kind === 'md' || kind === 'csv' || kind === 'svg';
}

/** 그 종류를 「렌더」로 그릴 수 있는 크기인가(SVG는 서버가 그림 상한으로 따로 판정한다). */
export function fitsPreview(kind, size) {
  if (kind === 'md') return size <= MD_MAX_BYTES;
  if (kind === 'csv') return size <= CSV_MAX_BYTES;
  return true;
}

/** 전환 단추의 이름 — [렌더 쪽, 원문 쪽]. */
export function viewLabels(kind) {
  if (kind === 'csv') return [t('표'), t('원문')];
  if (kind === 'svg') return [t('그림'), t('원문')];
  return [t('렌더'), t('원문')];
}

/**
 * RFC 4180에 가까운 CSV 파서. 따옴표 안의 구분자·줄바꿈·`""`를 다룬다. 줄 끝은 LF·CRLF 모두.
 * 돌려주는 것: 행 배열(각 행은 문자열 배열). 끝의 빈 줄은 버린다.
 */
export function parseCsv(text, delim = ',') {
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  const s = String(text || '');
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (quoted) {
      if (c === '"') {
        if (s[i + 1] === '"') { cell += '"'; i += 1; } else quoted = false;
      } else cell += c;
      continue;
    }
    if (c === '"' && cell === '') { quoted = true; continue; }
    if (c === delim) { row.push(cell); cell = ''; continue; }
    if (c === '\r' && s[i + 1] === '\n') continue;
    if (c === '\n' || c === '\r') { row.push(cell); rows.push(row); row = []; cell = ''; continue; }
    cell += c;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

const NUM_RE = /^[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?(?:[eE][-+]?\d+)?%?$/;
/** 숫자 칸인가(오른쪽 맞춤). 빈 칸은 아니다. */
export function isNumeric(cell) {
  const v = String(cell).trim();
  return v !== '' && /\d/.test(v) && NUM_RE.test(v);
}

// 보이는 행 구간 [시작, 끝) — 머리 행 아래로 내려간 거리(scrolled)와 보이는 높이로.
export function csvWindow(scrolled, viewport, count, rowH = CSV_ROW_H, overscan = CSV_OVERSCAN) {
  const first = Math.min(count, Math.max(0, Math.floor(Math.max(0, scrolled) / rowH) - overscan));
  const last = Math.min(count, Math.ceil((Math.max(0, scrolled) + Math.max(0, viewport)) / rowH) + overscan);
  return [first, Math.max(first, last)];
}

function _scrollAncestor(el) {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if (o === 'auto' || o === 'scroll') return p;
  }
  return null;
}

/**
 * CSV 표 — 머리 행은 고정, 몸통은 가상 스크롤(보이는 행 + 위아래 20행만 DOM에). 스크롤은 바깥 상자
 * (.vt-vw-body·페인 본문)가 하므로 그 상자의 스크롤로 구간을 정한다. 칸 내용은 textContent로만 넣는다.
 * 열이 `CSV_MAX_COLS`, 몸통 행이 `CSV_MAX_ROWS`를 넘으면 아무것도 붙이지 않고 `{ tooBig: true, rows, cols }`를
 * 돌려준다(호출자가 원문으로 연다).
 */
export function mountCsvTable(container, text, path) {
  const delim = /\.tsv$/i.test(path) ? '\t' : ',';
  const rows = parseCsv(text, delim);
  const head = rows.length ? rows[0] : [];
  const body = rows.slice(1);
  const cols = rows.reduce((n, r) => Math.max(n, r.length), 0);
  if (cols > CSV_MAX_COLS || body.length > CSV_MAX_ROWS) return { tooBig: true, rows: body.length, cols };
  const numeric = [];
  for (let c = 0; c < cols; c += 1) {
    let num = 0;
    let seen = 0;
    for (let r = 0; r < body.length && seen < 200; r += 1) {
      const v = body[r][c];
      if (v == null || String(v).trim() === '') continue;
      seen += 1;
      if (isNumeric(v)) num += 1;
    }
    numeric.push(seen > 0 && num === seen);
  }
  const wrap = document.createElement('div');
  wrap.className = 'vt-csv';
  const table = document.createElement('table');
  table.className = 'vt-csv-table';
  const thead = document.createElement('thead');
  const htr = document.createElement('tr');
  const ix = document.createElement('th');
  ix.className = 'ix';
  ix.textContent = '#';
  htr.appendChild(ix);
  for (let c = 0; c < cols; c += 1) {
    const th = document.createElement('th');
    th.textContent = head[c] == null ? '' : head[c];
    if (numeric[c]) th.className = 'n';
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  const tbody = document.createElement('tbody');
  table.append(thead, tbody);
  wrap.appendChild(table);
  const foot = document.createElement('div');
  foot.className = 'vt-vw-note vt-csv-foot';
  foot.textContent = t('{rows}행 · {cols}열 · 보이는 행만 그립니다 · 구분자 {delim} · 첫 줄은 머리', {
    rows: body.length.toLocaleString(), cols, delim: delim === '\t' ? 'TAB' : ',',
  });
  container.append(wrap, foot);

  let range = [-1, -1];
  let scroller = null;
  const topPad = document.createElement('tr');
  topPad.className = 'vt-csv-pad';
  const botPad = document.createElement('tr');
  botPad.className = 'vt-csv-pad';
  const paint = () => {
    if (!scroller || !scroller.contains(wrap)) scroller = _scrollAncestor(wrap);
    const viewport = scroller ? scroller.clientHeight : 800;
    // 몸통 행 r은 tbody 위에서 r × 행 높이에 있다(위 여백 행이 tbody 안에 있다).
    const scrolled = scroller ? scroller.getBoundingClientRect().top - tbody.getBoundingClientRect().top : 0;
    const next = csvWindow(scrolled, viewport, body.length);
    if (next[0] === range[0] && next[1] === range[1]) return;
    range = next;
    const frag = document.createDocumentFragment();
    topPad.style.height = `${next[0] * CSV_ROW_H}px`;
    botPad.style.height = `${(body.length - next[1]) * CSV_ROW_H}px`;
    frag.appendChild(topPad);
    for (let r = next[0]; r < next[1]; r += 1) {
      const tr = document.createElement('tr');
      const n = document.createElement('td');
      n.className = 'ix';
      n.textContent = String(r + 1);
      tr.appendChild(n);
      for (let c = 0; c < cols; c += 1) {
        const td = document.createElement('td');
        const v = body[r][c];
        td.textContent = v == null ? '' : v;
        if (numeric[c]) td.className = 'n';
        tr.appendChild(td);
      }
      frag.appendChild(tr);
    }
    frag.appendChild(botPad);
    tbody.replaceChildren(frag);
  };
  paint();
  // 붙은 뒤(레이아웃이 생긴 뒤) 스크롤 상자를 찾아 스크롤마다 구간을 다시 정한다. 칸이 거둬지면 수신기를 푼다.
  let raf = 0;
  const onScroll = (ev) => {
    if (!wrap.isConnected) { ev.currentTarget.removeEventListener('scroll', onScroll); return; }
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; paint(); });
  };
  requestAnimationFrame(() => {
    if (!wrap.isConnected) return;
    paint();
    if (scroller) scroller.addEventListener('scroll', onScroll, { passive: true });
  });
  return { rows: body.length, cols, paint };
}

/** 다운로드 단추(빈 상태·PDF용). `blocked`면 흐리게 두고 이유를 붙인다(숨기지 않는다 — 시안 F01-C). */
export function downloadButton(label, onClick, blocked) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'vt-btn solid vt-vw-dl-btn';
  b.innerHTML = icon('download', 14);
  b.appendChild(document.createTextNode(label));
  if (blocked) {
    b.setAttribute('aria-disabled', 'true');
    b.dataset.tip = blocked;
  }
  b.addEventListener('click', onClick);
  return b;
}

/** 빈 상태(제목 + 설명 + 단추들). */
export function emptyState(container, title, detail, buttons = []) {
  const box = document.createElement('div');
  box.className = 'vt-vw-empty vt-vw-preview-empty';
  const b = document.createElement('b');
  b.textContent = title;
  box.appendChild(b);
  if (detail) {
    const s = document.createElement('span');
    s.textContent = detail;
    box.appendChild(s);
  }
  if (buttons.length) {
    const row = document.createElement('div');
    row.className = 'vt-vw-preview-acts';
    row.append(...buttons);
    box.appendChild(row);
  }
  container.replaceChildren(box);
  return box;
}

/** 동영상·소리 — `<video>`/`<audio>` + 브라우저 기본 조작. 바이트는 /api/fs/raw가 Range로 준다(계획 4.6). */
export function mountPlayer(container, kind, src, note, onError) {
  const wrap = document.createElement('div');
  wrap.className = `vt-vw-media ${kind === 'audio' ? 'audio' : 'video'}`;
  const el = document.createElement(kind === 'audio' ? 'audio' : 'video');
  el.controls = true;
  el.preload = 'metadata';
  if (kind !== 'audio') el.setAttribute('playsinline', '');
  el.addEventListener('error', onError);
  el.src = src;
  wrap.appendChild(el);
  const n = document.createElement('div');
  n.className = 'vt-vw-note';
  n.textContent = note;
  container.replaceChildren(wrap, n);
  return el;
}

// ── 이미지 보기: 맞춤 / 100%(시안 F07-C) ──────────────────────────────────────────────────────────────
// 「맞춤」은 칸 폭에 맞춰 줄이고(원래보다 크게 늘리지는 않는다), 「100%」는 원래 크기로 그리고 칸이 스크롤한다.
// 100%에서는 ⌘/Ctrl + 휠(트랙패드 핀치도 이 이벤트로 온다)과 두 손가락 핀치로 10%–800% 사이를 확대·축소한다.
// 맞춤에서 확대하면 지금 보이는 배율에서 100% 쪽으로 넘어가 이어서 확대한다. 그림은 늘 `<img>` 하나다 — SVG도
// 같은 `<img>`라 sandbox 규칙(결정 D8, 스크립트·외부 불러오기 없음)이 그대로다.
export const ZOOM_MIN = 0.1;
export const ZOOM_MAX = 8;

/** 배율 한 단계 — 휠 deltaY(위로 굴리면 음수)만큼. 범위 밖은 자른다. */
export function zoomStep(scale, deltaY) {
  const next = scale * Math.exp(-Number(deltaY || 0) * 0.01);
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
}

/** 「맞춤」일 때 보이는 배율(0–1). 원래 폭을 모르면 null. */
export function fitScale(shownWidth, naturalWidth) {
  if (!(naturalWidth > 0) || !(shownWidth > 0)) return null;
  return Math.min(1, shownWidth / naturalWidth);
}

/**
 * 이미지 한 장을 맞춤/100% 전환과 함께 그린다. opts: `svg`(SVG면 true — 클래스만 다르다), `note`(아래 줄 글),
 * `onError`. 돌려주는 것: `{ img, setMode(mode), mode(), scale() }`(시험용).
 */
export function mountImageView(container, src, alt, opts = {}) {
  const wrap = document.createElement('div');
  wrap.className = opts.svg ? 'vt-vw-image vt-vw-svg' : 'vt-vw-image';
  const bar = document.createElement('div');
  bar.className = 'vt-vw-zoom-bar';
  const seg = document.createElement('span');
  seg.className = 'vt-vw-seg';
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', t('이미지 크기'));
  const pct = document.createElement('span');
  pct.className = 'vt-vw-zoom-pct';
  const hint = document.createElement('span');
  hint.className = 'vt-vw-zoom-hint';
  hint.textContent = t('⌘/Ctrl + 휠 · 두 손가락으로 확대');
  bar.append(seg, pct, hint);
  const box = document.createElement('div');
  box.className = 'vt-vw-zoom-box';
  const img = document.createElement('img');
  img.alt = alt;
  img.decoding = 'async';
  img.referrerPolicy = 'no-referrer';
  if (opts.onError) img.addEventListener('error', opts.onError);
  box.appendChild(img);
  wrap.append(bar, box);
  if (opts.note) {
    const n = document.createElement('div');
    n.className = 'vt-vw-note';
    n.textContent = opts.note;
    wrap.appendChild(n);
  }

  let mode = 'fit';
  let scale = 1;
  const natural = () => img.naturalWidth || 0;
  const buttons = {};
  const paint = () => {
    wrap.dataset.zoom = mode;
    for (const [m, b] of Object.entries(buttons)) b.setAttribute('aria-pressed', String(m === mode));
    if (mode === 'fit') {
      img.style.width = '';
      const f = fitScale(img.clientWidth, natural());
      pct.textContent = f == null ? t('맞춤') : t('맞춤 · {pct}%', { pct: Math.round(f * 100) });
      return;
    }
    const w = natural();
    img.style.width = w > 0 ? `${Math.round(w * scale)}px` : '';
    pct.textContent = `${Math.round(scale * 100)}%`;
  };
  const setMode = (m, s = 1) => {
    mode = m === 'actual' ? 'actual' : 'fit';
    scale = mode === 'actual' ? Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, s)) : 1;
    paint();
  };
  for (const [m, label] of [['fit', t('맞춤')], ['actual', '100%']]) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-vw-bar-btn vt-vw-seg-btn';
    b.textContent = label;
    b.addEventListener('click', () => setMode(m));
    buttons[m] = b;
    seg.appendChild(b);
  }
  // 맞춤에서 확대를 시작하면 지금 보이는 배율에서 이어 간다(갑자기 100%로 튀지 않게).
  const zoomTo = (next) => {
    if (mode !== 'actual') { mode = 'actual'; }
    scale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
    paint();
  };
  const current = () => (mode === 'actual' ? scale : (fitScale(img.clientWidth, natural()) || 1));
  box.addEventListener('wheel', (ev) => {
    if (!(ev.ctrlKey || ev.metaKey)) return;     // 그냥 휠은 스크롤
    ev.preventDefault();
    zoomTo(zoomStep(current(), ev.deltaY));
  }, { passive: false });
  // 두 손가락 핀치 — 포인터 둘의 거리 비율. 한 손가락은 스크롤(브라우저 기본)에 맡긴다.
  const pts = new Map();
  let pinch = null;
  const dist = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y) || 1; };
  box.addEventListener('pointerdown', (ev) => {
    if (ev.pointerType !== 'touch') return;
    pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pts.size === 2) pinch = { d: dist(), s: current() };
  });
  box.addEventListener('pointermove', (ev) => {
    if (!pts.has(ev.pointerId)) return;
    pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pinch && pts.size === 2) zoomTo(pinch.s * (dist() / pinch.d));
  });
  const lift = (ev) => { pts.delete(ev.pointerId); if (pts.size < 2) pinch = null; };
  box.addEventListener('pointerup', lift);
  box.addEventListener('pointercancel', lift);
  img.addEventListener('load', paint);
  if (typeof ResizeObserver === 'function') {
    const ro = new ResizeObserver(() => { if (!wrap.isConnected) { ro.disconnect(); return; } if (mode === 'fit') paint(); });
    ro.observe(box);
  }
  img.src = src;
  paint();
  container.appendChild(wrap);
  return { img, setMode, mode: () => mode, scale: () => scale };
}

/** SVG — `<img>`로만 그린다(결정 D8). 응답에는 sandbox CSP가 붙어 있다(server/fsguard.py RAW_CSP). */
export function mountSvg(container, src, alt, onError) {
  return mountImageView(container, src, alt, { svg: true, onError }).img;
}
