// 코드 뷰어 diff 렌더 + 줄 주석→프롬프트 큐 — F4에서 viewer.js에서 분리.
// git.js가 상태/커밋 목록에서 특정 파일을 눌렀을 때 renderFileDiff를 부른다.
//
// 리디자인 5단계(시안 P01-b): 줄 번호 열 하나(36) · 부호 열(14) · 본문. 줄 글자는 기본 글자색(term-fg)이고
// 초록·빨강은 +/− 부호와 **바뀐 낱말**에만 쓴다(DESIGN.md §2 「Terminal colors」 — 줄 전체를 초록·빨강
// 글자로 그리면 notepad·vscode에서 4.5:1을 못 맞춘다). 줄 바탕은 term-add-bg/term-del-bg.
import { vtFetch, vtEsc } from '../../core/api.js';
import { isMac } from '../../core/env.js';
import { _setMsg } from './state.js';
import { t } from '../../core/i18n.js';
import { icon } from '../../ui/icons.js';
import { pIcon } from '../panel-icons.js';
import { _hl, ensureHljs } from './file.js';
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

/** @param {{head?: boolean}} [o] head:false면 파일 머리(경로·+/−)를 그리지 않는다 — 하위 머리가 이미 말한다. */
export function _renderDiffDOM(container, diffText, o = {}) {
  const files = VTDiffLex.parse(diffText);
  files.forEach(f => {
    const st = VTDiffLex.stats(f);
    const lang = VTDiffLex.langForPath(f.newPath || f.oldPath);

    const fileEl = document.createElement('div');
    fileEl.className = 'vt-vw-dfile';

    if (o.head !== false) {
      const head = document.createElement('div');
      head.className = 'vt-vw-dhead';
      const pathEl = document.createElement('span');
      pathEl.className = 'vt-vw-dpath';
      pathEl.textContent = f.newPath || f.oldPath;
      head.appendChild(pathEl);
      head.appendChild(_statEl(st));
      fileEl.appendChild(head);
    }

    if (f.binary) {
      const note = document.createElement('div');
      note.className = 'vt-vw-note';
      note.textContent = t('바이너리 파일');
      fileEl.appendChild(note);
    } else {
      f.hunks.forEach(h => {
        const hunk = document.createElement('div');
        hunk.className = 'vt-vw-hunk';
        hunk.textContent = h.header;                    // textContent — XSS 방어
        fileEl.appendChild(hunk);

        const spans = _pairSpans(h.lines);
        h.lines.forEach((l, idx) => {
          const cls = l.type === 'add' ? 'add' : l.type === 'del' ? 'del' : l.type === 'meta' ? 'meta' : '';
          const row = document.createElement('div');
          row.className = 'vt-vw-dl' + (cls ? ' ' + cls : '');
          row.dataset.hint = t('이 줄에 지시…');

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
          if (l.type === 'meta') tx.textContent = l.text;
          // 바뀐 줄은 하이라이트 대신 바뀐 낱말을 표시한다(바뀐 줄 위에서는 원래도 하이라이트 색을 끈다).
          else if (l.type === 'add' || l.type === 'del') tx.innerHTML = _markHTML(l.text, spans.get(idx));
          else tx.innerHTML = _hl(l.text, lang);         // hljs 결과만 innerHTML 예외

          row.appendChild(no);
          row.appendChild(sign);
          row.appendChild(tx);
          _wireDiffLineAnnotate(row, f.newPath || f.oldPath, l.newNo ?? l.oldNo ?? null, container);
          fileEl.appendChild(row);
        });
      });
    }
    container.appendChild(fileEl);
  });
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

function _wireDiffLineAnnotate(row, filePath, lineNo, root) {
  row.addEventListener('click', () => {
    // 텍스트를 드래그로 복사-선택한 직후의 클릭이면 무시한다 — 안 그러면
    // diff를 긁어 복사할 때마다 주석 박스가 열린다. sign 컬럼이 14px라
    // 모바일 터치 타깃으로 쓰기엔 좁아서, 줄 전체를 눌러도 열리게 한다.
    if (window.getSelection && String(window.getSelection())) return;
    _toggleDiffAnnotate(row, filePath, lineNo, root);
  });
}

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

function _toggleDiffAnnotate(row, filePath, lineNo, root) {
  const existing = row.nextElementSibling;
  if (existing && existing.classList.contains('vt-vw-annotate')) { existing.remove(); row.classList.remove('on'); return; }
  // 한 번에 하나만 — 다른 줄에서 이미 열려 있던 박스는 닫는다.
  document.querySelectorAll('.vt-vw-annotate').forEach(el => el.remove());
  document.querySelectorAll('.vt-vw-dl.on').forEach(el => el.classList.remove('on'));
  row.classList.add('on');

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
  row.after(box);

  const close = () => { box.remove(); row.classList.remove('on'); };
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
  requestAnimationFrame(() => ta.focus());
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
  // 하이라이터는 diff가 직접 부른다(fire-and-forget, 아래 요청과 겹친다). 전에는 파일 보기가 불러 둔 것을
  // 같이 썼다. 파일 보기가 CodeMirror로 바뀐 뒤(단계 2)로는 여기서 부르지 않으면 diff가 계속 흑백이다.
  ensureHljs();
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
