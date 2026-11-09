// 코드 뷰어 diff 렌더 + 줄 주석→프롬프트 큐 — F4에서 viewer.js에서 분리.
// git.js가 상태/커밋 목록에서 특정 파일을 눌렀을 때 renderFileDiff를 부른다.
//
// 리디자인 5단계(시안 P01-b): 줄 번호 열 하나(36) · 부호 열(14) · 본문. 줄 글자는 기본 글자색(term-fg)이고
// 초록·빨강은 +/− 부호와 **바뀐 낱말**에만 쓴다(DESIGN.md §2 「Terminal colors」 — 줄 전체를 초록·빨강
// 글자로 그리면 notepad·vscode에서 4.5:1을 못 맞춘다). 줄 바탕은 term-add-bg/term-del-bg.
//
// 파일 개편 단계 9: 줄 영역은 가상 목록이다(아래 「가상 목록」). 문맥 줄 하이라이트는 hljs 대신 편집기 청크의
// lezer 언어 팩(`editor.js` highlighterFor)으로 한다.
import { vtFetch, vtEsc } from '../../core/api.js';
import { isMac } from '../../core/env.js';
import { _setMsg } from './state.js';
import { t } from '../../core/i18n.js';
import { icon } from '../../ui/icons.js';
import { pIcon } from '../panel-icons.js';
import { loadEditor } from './file.js';
// UMD 전역 VTDiffLex — 뷰어 전용이라 panels 청크에서 평가한다(마지막 import일 것: file.js 참고).
import '../../lib/difflex.js';

/** 바뀐 줄 한 쌍(del → add)에서 앞뒤 공통 부분을 뺀 가운데를 바뀐 구간으로 본다. 낱말 경계까지 넓혀
 * 글자 반쪽이 칠해지지 않게 한다. 거의 다 바뀐 줄(바뀐 구간이 줄의 70% 넘게)은 표시하지 않는다 —
 * 줄 전체가 칠해지면 「어디가 바뀌었나」를 말해 주지 못한다. 반환: [del 구간, add 구간] 또는 null. */
export function changedSpans(a, b) {
  let p = 0;
  const max = Math.min(a.length, b.length);
  while (p < max && a[p] === b[p]) p++;
  let s = 0;
  while (s < max - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  const word = /[\p{L}\p{N}_]/u;
  while (p > 0 && word.test(a[p - 1]) && (word.test(a[p] || '') || word.test(b[p] || ''))) p--;
  while (s > 0 && word.test(a[a.length - s]) && (word.test(a[a.length - s - 1] || '') || word.test(b[b.length - s - 1] || ''))) s--;
  const da = [p, a.length - s];
  const db = [p, b.length - s];
  if (da[1] <= da[0] && db[1] <= db[0]) return null;
  const big = (r, len) => len > 0 && (r[1] - r[0]) / len > 0.7;
  if (big(da, a.length) && big(db, b.length)) return null;
  return [da, db];
}

function _markHTML(text, span) {
  if (!span || span[1] <= span[0]) return vtEsc(text);
  return vtEsc(text.slice(0, span[0])) + `<mark class="vt-vw-chg">${vtEsc(text.slice(span[0], span[1]))}</mark>`
    + vtEsc(text.slice(span[1]));
}

/** 한 덩어리(hunk) 안의 연속된 del 묶음과 바로 뒤 add 묶음을 순서대로 짝지어 바뀐 구간을 매긴다. */
function _pairSpans(lines) {
  const spans = new Map();
  for (let i = 0; i < lines.length;) {
    if (lines[i].type !== 'del') { i++; continue; }
    const dels = [];
    while (i < lines.length && lines[i].type === 'del') dels.push(i++);
    const adds = [];
    while (i < lines.length && lines[i].type === 'add') adds.push(i++);
    for (let k = 0; k < Math.min(dels.length, adds.length); k++) {
      const r = changedSpans(lines[dels[k]].text, lines[adds[k]].text);
      if (r) { spans.set(dels[k], r[0]); spans.set(adds[k], r[1]); }
    }
  }
  return spans;
}

// --- 가상 목록 ------------------------------------------------------------------------------
// 파일 개편 단계 9 — 줄마다 DOM 행을 다 만들던 렌더러(5,000줄 diff에서 노드 약 3만 개, 4배 감속에서 0.85초)를
// 보이는 줄만 그리는 목록으로 바꿨다. 행 높이가 고정(줄 20px · 덩어리 머리 30px)이라 위치는 누적 합으로 바로 나온다.
// 높이가 정해지지 않은 것은 열린 「이 줄에 지시…」 상자 하나뿐이고, 그 높이는 붙인 뒤 재서 넣는다.
// 파일 머리(`.vt-vw-dhead`, sticky)는 지금처럼 흐름 안에 두고 그 아래 줄 영역만 가상으로 그린다 — 여러 파일 diff
// (커밋 보기)에서도 머리가 그대로 붙어 있다. 스크롤은 바깥 상자(`.vt-vw-body`)가 한다.
//
// 작은 diff(행 SMALL_ITEMS개 이하)는 전부 그린다 — 브라우저 찾기(⌘F)와 끌어서 복사가 diff 전체에 걸린다.
// 큰 diff는 보이는 범위 ± OVERSCAN_PX만 DOM에 있어서 찾기·복사가 그 범위까지만 닿는다(편집기 본문과 같은 한계).
export const LINE_H = 20;            // 30-viewer.css `.vt-vw-dl` line-height
export const HUNK_H = 30;            // `.vt-vw-hunk` 높이 24 + 위 간격 6
export const SMALL_ITEMS = 400;
const OVERSCAN_PX = 600;
const HL_MAX_CHARS = 512 * 1024;     // 덩어리 하나가 이보다 크면 하이라이트하지 않는다(구문 분석 한 번이 길어진다)

/** 파일 하나의 행 목록: 덩어리 머리와 줄을 순서대로. */
export function diffItems(file) {
  const items = [];
  file.hunks.forEach((h, hi) => {
    items.push({ k: 'hunk', hi, text: h.header });
    h.lines.forEach((l, li) => items.push({ k: 'line', hi, li, l }));
  });
  return items;
}

/** 행마다 위쪽 끝 위치(px). `annot`({at, h})이면 at번 행 바로 뒤에 h를 끼운다. off[n]이 전체 높이다. */
export function diffOffsets(items, annot) {
  const off = new Float64Array(items.length + 1);
  let y = 0;
  for (let i = 0; i < items.length; i++) {
    off[i] = y;
    y += items[i].k === 'hunk' ? HUNK_H : LINE_H;
    if (annot && annot.at === i) y += annot.h;
  }
  off[items.length] = y;
  return off;
}

/** [y0, y1) 구간에 걸치는 행 번호 범위 [i0, i1). */
export function visibleRange(off, y0, y1) {
  const n = off.length - 1;
  let lo = 0;
  let hi = n;
  while (lo < hi) {             // 끝(off[i+1])이 y0보다 큰 첫 행
    const mid = (lo + hi) >> 1;
    if (off[mid + 1] > y0) hi = mid; else lo = mid + 1;
  }
  let end = lo;
  while (end < n && off[end] < y1) end++;
  return [lo, end];
}

// 고정폭 글꼴에서 두 칸을 차지하는 글자(한글·한자·가나·전각). 줄 영역의 가로 폭을 `ch`로 정할 때 센다.
const WIDE = /[ᄀ-ᅟ⺀-〾ぁ-㏿㐀-䶿一-鿿ꥠ-꥿가-힣豈-﫿︰-﹏＀-｠￠-￦]/g;
/** 가장 긴 줄의 칸 수. 가상 목록은 화면 밖 줄을 그리지 않으므로 가로 스크롤 폭을 미리 정해야 한다. */
export function maxColumns(items) {
  let best = 0;
  for (const it of items) {
    const s = it.k === 'hunk' ? it.text : it.l.text;
    if (s.length * 2 <= best) continue;
    const m = s.match(WIDE);
    const cols = s.length + (m ? m.length : 0);
    if (cols > best) best = cols;
  }
  return best;
}

/** 스크롤하는 상자 — 소비처가 넘기는 칸(`.vt-vw-body`) 자신이 그 상자인 경우가 흔해서 자기부터 본다. overflow만
 * auto이고 높이가 내용만큼 늘어나는 상자(페인의 `.vt-vw-code-pane`)는 건너뛰고 실제로 넘치는 상자를 고른다. 그런
 * 상자가 없으면(내용이 짧다) 처음 만난 auto 상자 — 짧은 diff는 어차피 전부 그린다. */
function _scrollParent(el) {
  let first = null;
  for (let cur = el; cur; cur = cur.parentElement) {
    const o = getComputedStyle(cur).overflowY;
    if (o !== 'auto' && o !== 'scroll') continue;
    if (cur.scrollHeight > cur.clientHeight) return cur;
    first ||= cur;
  }
  return first;
}

/** 파일 하나의 줄 영역. */
class FileLines {
  constructor(view, file) {
    this.view = view;
    this.file = file;
    this.path = file.newPath || file.oldPath;
    this.items = diffItems(file);
    this.off = diffOffsets(this.items, null);
    this.rows = new Map();           // 행 번호 → 그려 둔 엘리먼트
    this.spans = new Map();          // 덩어리 번호 → 바뀐 낱말 구간(Map 줄 번호 → [a, b])
    this.hl = new Map();             // 덩어리 번호 → 줄 번호별 하이라이트 HTML(문맥 줄만)
    this.hlFn = null;
    this.annot = null;               // { at, h, slot }
    this.el = document.createElement('div');
    this.el.className = 'vt-vw-dlines';
    this.el.style.height = `${this.off[this.items.length]}px`;
    // 번호 열 36 + 부호 열 14 + 본문 + 오른쪽 여백. ch는 이 영역의 글꼴(줄과 같은 고정폭) 기준이다.
    this.el.style.width = `calc(50px + ${maxColumns(this.items)}ch + var(--spacing) * 3)`;
    this.el.addEventListener('click', (ev) => this._onClick(ev));
  }

  _spansOf(hi) {
    let m = this.spans.get(hi);
    if (!m) { m = _pairSpans(this.file.hunks[hi].lines); this.spans.set(hi, m); }
    return m;
  }

  /** 문맥 줄 하이라이트. 덩어리의 새 쪽(문맥 + 추가 줄)을 한 번에 분석해야 여러 줄 문자열·주석이 맞게 칠해진다. */
  _hlOf(hi) {
    if (!this.hlFn) return null;
    if (this.hl.has(hi)) return this.hl.get(hi);
    const lines = this.file.hunks[hi].lines;
    const idx = [];
    const text = [];
    let size = 0;
    lines.forEach((l, li) => {
      if (l.type === 'ctx' || l.type === 'add') { idx.push(li); text.push(l.text); size += l.text.length + 1; }
    });
    let out = null;
    if (size <= HL_MAX_CHARS && idx.some((li) => lines[li].type === 'ctx')) {
      const html = this.hlFn(text.join('\n'));
      if (html && html.length === idx.length) {
        out = new Map();
        idx.forEach((li, k) => { if (lines[li].type === 'ctx') out.set(li, html[k]); });
      }
    }
    this.hl.set(hi, out);
    return out;
  }

  _txHTML(it) {
    const l = it.l;
    if (l.type === 'add' || l.type === 'del') return _markHTML(l.text, this._spansOf(it.hi).get(it.li));
    if (l.type === 'ctx') {
      const hl = this._hlOf(it.hi);
      const html = hl && hl.get(it.li);
      if (html != null) return html;   // classHighlighter의 `tok-*` span + 이스케이프한 글자만 들어 있다
    }
    return vtEsc(l.text);
  }

  _make(i) {
    const it = this.items[i];
    let row;
    if (it.k === 'hunk') {
      row = document.createElement('div');
      row.className = 'vt-vw-hunk';
      row.textContent = it.text;                       // textContent — XSS 방어
    } else {
      const l = it.l;
      const cls = l.type === 'add' ? 'add' : l.type === 'del' ? 'del' : l.type === 'meta' ? 'meta' : '';
      row = document.createElement('div');
      row.className = 'vt-vw-dl' + (cls ? ' ' + cls : '');
      row.dataset.hint = t('이 줄에 지시…');
      row.dataset.i = String(i);
      // 번호 열은 하나 — 지운 줄은 옛 번호, 나머지는 새 번호(시안 P01-b 「번호 열 36」).
      const no = document.createElement('span');
      no.className = 'vt-vw-no';
      const n = l.type === 'del' ? l.oldNo : (l.newNo ?? l.oldNo);
      no.textContent = n == null ? '' : n;
      const sign = document.createElement('span');
      sign.className = 'vt-vw-sign';
      sign.textContent = l.type === 'add' ? '+' : l.type === 'del' ? '−' : ' ';
      const tx = document.createElement('span');
      tx.className = 'vt-vw-tx';
      // 바뀐 줄은 하이라이트 대신 바뀐 낱말을 표시한다. 문맥 줄만 하이라이트한다.
      if (l.type === 'meta') tx.textContent = l.text;
      else tx.innerHTML = this._txHTML(it);
      row.append(no, sign, tx);
      if (this.annot && this.annot.at === i) row.classList.add('on');
    }
    row.style.top = `${this.off[i]}px`;
    return row;
  }

  /** 보이는 범위만 DOM에 둔다. vp: 이 영역 기준 [y0, y1). */
  update(y0, y1) {
    const n = this.items.length;
    const [i0, i1] = n <= SMALL_ITEMS ? [0, n] : visibleRange(this.off, y0 - OVERSCAN_PX, y1 + OVERSCAN_PX);
    for (const [i, el] of this.rows) {
      if (i < i0 || i >= i1) { el.remove(); this.rows.delete(i); }
    }
    let frag = null;
    for (let i = i0; i < i1; i++) {
      if (this.rows.has(i)) continue;
      const el = this._make(i);
      this.rows.set(i, el);
      (frag ||= document.createDocumentFragment()).appendChild(el);
    }
    if (frag) this.el.appendChild(frag);
  }

  /** 하이라이터가 도착했다 — 그려 둔 문맥 줄만 다시 칠한다. */
  repaintHighlight() {
    for (const [i, el] of this.rows) {
      const it = this.items[i];
      if (it.k !== 'line' || it.l.type !== 'ctx') continue;
      const tx = el.querySelector('.vt-vw-tx');
      if (tx) tx.innerHTML = this._txHTML(it);
    }
  }

  _relayout() {
    this.off = diffOffsets(this.items, this.annot && { at: this.annot.at, h: this.annot.h });
    this.el.style.height = `${this.off[this.items.length]}px`;
    for (const [i, el] of this.rows) el.style.top = `${this.off[i]}px`;
    if (this.annot) this.annot.slot.style.top = `${this.off[this.annot.at] + LINE_H}px`;
  }

  _onClick(ev) {
    const row = ev.target.closest && ev.target.closest('.vt-vw-dl');
    if (!row || !this.el.contains(row)) return;
    // 텍스트를 드래그로 복사-선택한 직후의 클릭이면 무시한다 — 안 그러면 diff를 긁어 복사할 때마다 주석 상자가
    // 열린다. 부호 열이 14px라 폰에서 누르기엔 좁아서, 줄 전체를 눌러도 열리게 한다.
    if (window.getSelection && String(window.getSelection())) return;
    this.view.toggleAnnotate(this, Number(row.dataset.i));
  }

  openAnnotate(i) {
    const it = this.items[i];
    const lineNo = it.l.newNo ?? it.l.oldNo ?? null;
    const slot = document.createElement('div');
    slot.className = 'vt-vw-annot-slot';
    this.annot = { at: i, h: 0, slot };
    const close = () => this.view.closeAnnotate();
    slot.appendChild(_annotateBox(this.path, lineNo, this.view.root, close));
    this.el.appendChild(slot);
    const measure = () => {
      if (!this.annot || this.annot.slot !== slot) return;
      const h = slot.offsetHeight;
      if (h !== this.annot.h) { this.annot.h = h; this._relayout(); this.view.schedule(); }
    };
    if (typeof ResizeObserver === 'function') {
      this.annot.ro = new ResizeObserver(measure);
      this.annot.ro.observe(slot);
    }
    this._relayout();
    measure();
    this.rows.get(i)?.classList.add('on');
  }

  closeAnnotate() {
    if (!this.annot) return;
    const { at, slot, ro } = this.annot;
    if (ro) ro.disconnect();
    slot.remove();
    this.rows.get(at)?.classList.remove('on');
    this.annot = null;
    this._relayout();
  }
}

/** diff 한 화면(파일 여러 개일 수 있다)의 가상 목록 묶음. container._vtDiff로 붙는다. */
class DiffView {
  constructor(root) {
    this.root = root;
    this.files = [];
    this.open = null;                // 주석 상자가 열린 FileLines
    this.sp = null;
    this._raf = 0;
    this._onScroll = () => this.schedule();
    this._ro = null;
    this.highlighted = Promise.resolve();
  }

  attach() {
    this.sp = _scrollParent(this.root);
    const target = this.sp || window;
    target.addEventListener('scroll', this._onScroll, { passive: true });
    if (!this.sp) window.addEventListener('resize', this._onScroll);
    if (typeof ResizeObserver === 'function') {
      // 칸 크기가 바뀌거나 숨어 있던 탭이 보이면 보이는 범위가 달라진다.
      this._ro = new ResizeObserver(this._onScroll);
      this._ro.observe(this.sp || this.root);
    }
    this.update();
  }

  destroy() {
    const target = this.sp || window;
    target.removeEventListener('scroll', this._onScroll);
    window.removeEventListener('resize', this._onScroll);
    if (this._ro) this._ro.disconnect();
    if (this._raf) cancelAnimationFrame(this._raf);
    for (const f of this.files) if (f.annot && f.annot.ro) f.annot.ro.disconnect();
    this._raf = 0;
  }

  schedule() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.update(); });
  }

  update() {
    // 소비처가 화면을 갈아 끼우면(innerHTML = '') 이 목록은 문서에서 떨어진다 — 다음 신호에서 스스로 정리한다.
    if (!this.files.length || !this.files[0].el.isConnected) { if (this.files.length) this.destroy(); return; }
    const vp = this.sp ? this.sp.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
    for (const f of this.files) {
      const r = f.el.getBoundingClientRect();
      f.update(vp.top - r.top, vp.bottom - r.top);
    }
  }

  toggleAnnotate(f, i) {
    const same = this.open === f && f.annot && f.annot.at === i;
    this.closeAnnotate();
    // 한 번에 하나만 — 다른 diff 화면(다른 칸)에 열려 있던 상자도 닫는다.
    for (const other of document.querySelectorAll('.vt-vw-annotate')) other.dispatchEvent(new CustomEvent('vt-annot-close'));
    if (same) return;
    f.openAnnotate(i);
    this.open = f;
    this.schedule();
  }

  closeAnnotate() {
    if (this.open) this.open.closeAnnotate();
    this.open = null;
    this.schedule();
  }
}

/** @param {{head?: boolean}} [o] head:false면 파일 머리(경로·+/−)를 그리지 않는다 — 하위 머리가 이미 말한다. */
export function _renderDiffDOM(container, diffText, o = {}) {
  if (container._vtDiff) container._vtDiff.destroy();
  const files = VTDiffLex.parse(diffText);
  const view = new DiffView(container);
  container._vtDiff = view;
  const waits = [];
  files.forEach(f => {
    const fileEl = document.createElement('div');
    fileEl.className = 'vt-vw-dfile';

    if (o.head !== false) {
      const head = document.createElement('div');
      head.className = 'vt-vw-dhead';
      const pathEl = document.createElement('span');
      pathEl.className = 'vt-vw-dpath';
      pathEl.textContent = f.newPath || f.oldPath;
      head.appendChild(pathEl);
      head.appendChild(_statEl(VTDiffLex.stats(f)));
      fileEl.appendChild(head);
    }

    if (f.binary) {
      const note = document.createElement('div');
      note.className = 'vt-vw-note';
      note.textContent = t('바이너리 파일');
      fileEl.appendChild(note);
    } else if (f.hunks.length) {
      const lines = new FileLines(view, f);
      view.files.push(lines);
      fileEl.appendChild(lines.el);
      // 문맥 줄 하이라이트는 편집기 청크의 언어 팩(lezer)으로 한다 — hljs를 걷어 냈다(단계 9). 받는 동안은
      // 글자만 그리고, 오면 그려 둔 문맥 줄만 다시 칠한다. 못 받으면 글자만 남는다.
      const lang = VTDiffLex.langForPath(lines.path);
      if (lang) {
        waits.push(loadEditor()
          .then((m) => m.highlighterFor(lines.path, lang))
          .then((fn) => { if (fn) { lines.hlFn = fn; lines.hl.clear(); lines.repaintHighlight(); } })
          .catch(() => {}));
      }
    }
    container.appendChild(fileEl);
  });
  view.highlighted = Promise.all(waits).then(() => {});
  view.attach();
  return files;
}

function _statEl(st) {
  const statEl = document.createElement('span');
  statEl.className = 'vt-vw-dstat';
  const addB = document.createElement('b'); addB.className = 'add'; addB.textContent = `+${st.add}`;
  const delB = document.createElement('b'); delB.className = 'del'; delB.textContent = `−${st.del}`;
  statEl.appendChild(addB);
  statEl.appendChild(document.createTextNode(' '));
  statEl.appendChild(delB);
  return statEl;
}

// --- diff 줄 주석 → 프롬프트 큐 --------------------------------------------
// 폰에서 diff를 보다가 그 줄에 바로 지시를 남기면 프롬프트 큐로 들어간다.
// 새 서버 엔드포인트 없이 기존 POST /api/queue를 그대로 쓴다.

// 대상 세션 목록 — 큐 패널의 대상 드롭다운과 같은 자료(로컬 tmux 세션). 실패하면 「자동」만 남긴다.
async function _fillTargets(sel) {
  try {
    const list = await vtFetch('/api/tmux/sessions');
    for (const s of list || []) {
      const o = document.createElement('option');
      o.value = s.name;
      o.textContent = s.name;
      sel.appendChild(o);
    }
  } catch (_) { /* 「자동」만 */ }
}

/** 「이 줄에 지시…」 상자. 큐에 넣으면 POST /api/queue(새 엔드포인트 없음). close()는 상자를 닫는다. */
function _annotateBox(filePath, lineNo, root, close) {
  const box = document.createElement('div');
  box.className = 'vt-vw-annotate';
  const cap = document.createElement('div');
  cap.className = 'vt-vw-annotate-cap';
  cap.textContent = t('{where} 에 대한 지시', { where: lineNo != null ? `${filePath}:${lineNo}` : filePath });
  const ta = document.createElement('textarea');
  ta.className = 'vt-input vt-vw-annotate-input';
  ta.rows = 2;
  ta.placeholder = t('이 줄에 지시… ({mod}+Enter로 추가)', { mod: isMac ? 'Cmd' : 'Ctrl' });
  const actions = document.createElement('div');
  actions.className = 'vt-vw-annotate-row';
  const tgtLab = document.createElement('span');
  tgtLab.className = 'vt-vw-annotate-lab';
  tgtLab.textContent = t('대상');
  const sel = document.createElement('select');
  sel.className = 'vt-input vt-vw-annotate-target';
  sel.setAttribute('aria-label', t('대상 세션'));
  sel.innerHTML = `<option value="">${vtEsc(t('자동'))}</option>`;
  _fillTargets(sel);
  const sp = document.createElement('span');
  sp.className = 'sp';
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'vt-btn sm quiet';
  cancelBtn.type = 'button';
  cancelBtn.textContent = t('취소');
  const addBtn = document.createElement('button');
  addBtn.className = 'vt-btn sm solid vt-vw-annotate-add';
  addBtn.type = 'button';
  addBtn.innerHTML = `<span>${vtEsc(t('큐에 추가'))}</span><span class="k">${isMac ? '⌘↵' : 'Ctrl+↵'}</span>`;
  actions.append(tgtLab, sel, sp, cancelBtn, addBtn);
  box.append(cap, ta, actions);

  box.addEventListener('vt-annot-close', close);
  cancelBtn.addEventListener('click', close);
  const submit = () => _submitDiffAnnotate(box, ta, sel, filePath, lineNo, root, close);
  addBtn.addEventListener('click', submit);
  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); submit(); }
    else if (ev.key === 'Escape') { ev.stopPropagation(); close(); }
  });
  // 클릭이 곧바로 버블링돼 document의 다른 리스너가 이 박스를 즉시 지우지
  // 않도록 막는다(현재 문서 전역에 그런 리스너는 없지만, 있었을 때를 대비).
  box.addEventListener('click', (ev) => ev.stopPropagation());
  requestAnimationFrame(() => ta.focus({ preventScroll: true }));
  return box;
}

async function _submitDiffAnnotate(box, ta, sel, filePath, lineNo, root, close) {
  const comment = (ta.value || '').trim();
  if (!comment) return;
  const text = lineNo != null ? `${filePath}:${lineNo} — ${comment}` : `${filePath} — ${comment}`;
  const addBtn = box.querySelector('.vt-vw-annotate-add');
  if (addBtn) addBtn.disabled = true;
  const target = sel && sel.value ? { session: sel.value } : undefined;
  try {
    await vtFetch('/api/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, target }),
    });
    if (typeof showToast === 'function') showToast(t('큐에 추가됨'));
    close();
    _bumpQueued(root);
  } catch (e) {
    if (addBtn) addBtn.disabled = false;
    if (typeof showToast === 'function') showToast(t('추가 실패: {msg}', { msg: e.message }));
  }
}

/** diff 화면 바닥 줄 「이 diff에서 큐에 넣은 지시 N」 — 이 화면을 연 뒤로 넣은 것만 센다. */
function _bumpQueued(root) {
  const foot = root && root.querySelector('.vt-vw-dqueued');
  if (!foot) return;
  const n = (Number(foot.dataset.n) || 0) + 1;
  foot.dataset.n = String(n);
  foot.hidden = false;
  foot.querySelector('.n').textContent = String(n);
}

function _queuedFootEl() {
  const foot = document.createElement('div');
  foot.className = 'vt-psec vt-vw-dqueued';
  foot.hidden = true;
  foot.innerHTML = `${icon('clipboard', 12)}<span>${vtEsc(t('이 diff에서 큐에 넣은 지시'))}</span><span class="n">0</span>`
    + `<span class="note">${vtEsc(t('작업이 끝나면 전송'))}</span>`;
  return foot;
}

// 파일 하나의 diff. status 목록에서 특정 파일을 눌렀을 때 쓴다.
// N35 §6 — "어디에 그릴지"를 인자로 받는다(dock 소스컨트롤 탭이 유일한 소비처).
// opts: onBack, onOpenFile(file), status(상태 글자), statusLabel
export async function renderFileDiff(container, repo, file, staged, opts = {}) {
  // 하이라이터(편집기 청크)를 diff 요청과 겹쳐 받는다(fire-and-forget). 실패는 _renderDiffDOM이 글자만 그려 넘긴다.
  loadEditor().catch(() => {});
  if (container._vtDiff) { container._vtDiff.destroy(); container._vtDiff = null; }
  container.innerHTML = `<div class="vt-vw-loading">${t('git diff 실행 중…')}</div>`;

  let d;
  try {
    const q = `repo=${encodeURIComponent(repo)}&file=${encodeURIComponent(file)}&staged=${staged ? 'true' : 'false'}`;
    d = await vtFetch(`/api/git/diff?${q}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }

  container.innerHTML = '';
  // 하위 머리 36(시안 P01-b): ← · [상태 글자] 파일 이름 · 낱말 … +N −M · 뷰어로 열기.
  const bar = document.createElement('div');
  bar.className = 'vt-vw-cbar vt-psub';
  if (opts.onBack) {
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'vt-icon-btn sm vt-vw-cback';
    back.setAttribute('aria-label', t('상태로'));
    back.dataset.tip = t('상태로');
    back.innerHTML = icon('arrow-left', 14);
    back.addEventListener('click', opts.onBack);
    bar.appendChild(back);
  }
  if (opts.status) {
    const stl = document.createElement('span');
    stl.className = 'vt-stl';
    stl.dataset.st = opts.status === '!' ? 'C' : opts.status;
    stl.textContent = opts.status;
    bar.appendChild(stl);
  }
  const name = document.createElement('span');
  name.className = 'vt-psub-file';
  name.textContent = file.split('/').pop();
  name.title = file;
  bar.appendChild(name);
  if (opts.statusLabel) {
    const lab = document.createElement('span');
    lab.className = 'vt-psub-lab';
    lab.textContent = staged ? `${opts.statusLabel} · ${t('스테이지됨')}` : opts.statusLabel;
    bar.appendChild(lab);
  }
  const sp = document.createElement('span');
  sp.className = 'sp';
  bar.appendChild(sp);
  container.appendChild(bar);

  if (!d.diff || !d.diff.trim()) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = t('변경된 내용이 없습니다.');
    container.appendChild(empty);
    return;
  }
  const files = _renderDiffDOM(container, d.diff, { head: false });
  if (files.length === 1) bar.appendChild(_statEl(VTDiffLex.stats(files[0])));
  if (typeof opts.onOpenFile === 'function') {
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'vt-icon-btn sm';
    open.setAttribute('aria-label', t('뷰어로 열기'));
    open.dataset.tip = t('뷰어로 열기');
    open.innerHTML = pIcon('external-link', 14);
    open.addEventListener('click', () => opts.onOpenFile(file));
    bar.appendChild(open);
  }
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = t('diff가 커서 일부만 표시했습니다.');
    container.appendChild(note);
  }
  container.appendChild(_queuedFootEl());
}
