// 코드 뷰어 파일 렌더 — F4에서 viewer.js에서 분리.
// 파일 본문은 CodeMirror 6(`editor` 지연 청크 — editor.js)로 그린다(파일 개편 단계 2). 편집·저장은 edit.js(단계 8).
// 받기는 `/api/fs/file` 2판의 구간·tail·rev·304·인코딩을 쓴다(file-load.js, 단계 3). 큰 파일은 앞 4MB까지 열고
// 「더 보기」, 「끝부분 보기」, 인코딩 고르기, 터미널 링크의 줄 이동이 여기 있다.
// 미리보기(단계 7, preview.js): 마크다운·SVG·CSV는 「렌더/원문」을 위 띠에서 바꾸고(기본 렌더, (호스트, 경로)별로
// 기억), 동영상·소리는 /api/fs/raw(Range)로 재생하고, PDF와 그 밖의 바이너리는 다운로드만 둔다(결정 D8).
import { vtUrl } from '../../core/api.js';
import {
  loadHead, loadMore, loadTail, loadProbe, growthFrom, canLoadMore, hostApiPath, mixedEol, ChangingError,
  CHUNK, OPEN_BUDGET, MAX_LOADED, TAIL_BYTES, PROBE_BYTES, INCREMENTAL_MIN,
} from './file-load.js';
import { _setMsg } from './state.js';
import { t } from '../../core/i18n.js';
import {
  previewKind, hasRenderedView, fitsPreview, viewLabels, mountCsvTable, mountPlayer, mountSvg, mountImageView, emptyState, downloadButton,
  CSV_MAX_COLS, CSV_MAX_ROWS,
} from './preview.js';
import { slugify } from './markdown.js';
import { downloadFsPath as _downloadFsPath, downloadBlockedReason } from './download.js';
import { decorateBar, syncConflict, editing, refreshEditing, stash, restore } from './edit.js';
// UMD 전역 VTDiffLex — 뷰어 전용이라 panels 청크에서 평가한다. ⚠ 마지막 import여야 한다:
// tests/helpers/vm-esm.js의 IMPORT_RE가 뒤에 `from '…'`이 더 있으면 부작용 import를 삼킨다.
import '../../lib/difflex.js';

// 파일 크기 표기 — 구 viewer/tree.js에서 이관(그 파일은 모달 뷰어와 함께 사라졌다).
export function _fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

// ── 파일 본문: CodeMirror 6 ─────────────────────────────────────────────────────────
// 편집기 청크는 파일을 열 때 요청과 겹쳐 받는다. 못 받으면(오프라인, 배포 직후 등) 줄 번호 없는 일반
// 텍스트로라도 보여 준다 — 빈 화면이나 오류만 남기지 않는다.
let _editorLoading = null;
export function loadEditor() {
  if (!_editorLoading) {
    _editorLoading = import('./editor.js').catch((e) => {
      _editorLoading = null;   // 다음 열기 때 다시 시도한다
      throw e;
    });
  }
  return _editorLoading;
}

// 붙여 둔 편집기들. CM은 창(window)과 스크롤 조상에 수신기를 걸어 두므로, 소비처가 호스트를 그냥 떼어 내면
// (다른 파일 열기, 페인 닫기) 그 편집기가 메모리에 남는다. 닫는 쪽(오버레이 닫기·파일 바꾸기, 페인 닫기·바꾸기,
// 새로고침의 호스트 교체)이 disposeViewer를 부르고, 놓친 것은 새 편집기를 붙일 때 DOM에서 빠진 것으로 거둔다.
const _editors = new Set();
function _destroyEditor(h) {
  _editors.delete(h);
  try { h.destroy(); } catch (_) { /* 이미 정리됨 */ }
}
function _sweepEditors() {
  for (const h of _editors) {
    if (!h.view.dom.isConnected) _destroyEditor(h);
  }
}

/**
 * root(칸 하나 또는 그것을 담은 상자) 안의 뷰어 칸을 거둔다 — 편집기를 destroy하고(창 수신기 해제) 진행 중인
 * 열기·다시 읽기가 더 그리지 않게 칸의 받기 상태를 끊는다. 닫거나 다른 것으로 바꾸는 쪽이 부른다.
 */
export function disposeViewer(root) {
  if (!root) return;
  const hosts = [];
  if (root.classList && root.classList.contains('vt-vw-code-pane')) hosts.push(root);
  if (root.querySelectorAll) hosts.push(...root.querySelectorAll('.vt-vw-code-pane'));
  for (const c of hosts) {
    try { stash(c); } catch (_) { /* 보관 실패는 닫기를 막지 않는다 */ }
    c._vtDoc = null;
    c._vtEdit = null;
    const h = c._vtEditor;
    c._vtEditor = null;
    if (h) _destroyEditor(h);
  }
}

// 사람이 고른 인코딩 — (호스트, 경로) 키(계획 3.5). 다시 읽기·「더 보기」·다른 표면으로 옮겨 열기에서 유지된다.
const _encChoice = new Map();
const _key = (host, path) => `${host || 'local'}\n${path}`;
const _encOf = (st) => _encChoice.get(_key(st.host, st.path)) || 'auto';
// 렌더/원문 고르기 — 같은 키. 기본은 렌더.
const _viewChoice = new Map();
const _viewOf = (st) => _viewChoice.get(_key(st.host, st.path)) || 'render';

/** 다운로드 — 진입점들(머리줄·페인·빈 상태)이 이 모듈에서 부른다(download.js). */
export const downloadFsPath = _downloadFsPath;

/** 스크롤 상자(.vt-vw-body·페인 본문) 안의 칸이 보이고 있는 판(rev). 다운로드가 「본 것과 같은 파일」을 받게 넘긴다. */
export function viewerRev(scrollEl) {
  const host = scrollEl && _currentHost(scrollEl);
  const st = host && host._vtDoc;
  return (st && st.meta && st.meta.rev) || null;
}

export const ENCODINGS = ['auto', 'utf-8', 'utf-16le', 'utf-16be', 'cp949', 'euc-kr', 'shift_jis', 'latin-1'];

function _encLabel(enc) {
  return enc === 'auto' ? t('자동') : String(enc).toUpperCase();
}

const CHANGING_MSG = () => t('파일이 계속 바뀌는 중이라 다시 읽기를 멈췄습니다. 잠시 뒤 새로고침하세요.');

function _errText(e) {
  return e && e.changing ? CHANGING_MSG() : String((e && e.message) || e);
}

function _plainText(container, text) {
  const pre = document.createElement('pre');
  pre.className = 'vt-vw-plain';
  pre.textContent = text;
  container.appendChild(pre);
  return pre;
}

// 한 뷰어 칸의 받기 상태. container._vtDoc에 둔다 — 다시 읽기·「더 보기」가 이어서 쓴다.
function _newDoc(path, host, mode) {
  return {
    path, host: host || null, mode: mode === 'tail' ? 'tail' : 'head', meta: null, etag: null, carry: '',
    changing: false, queue: Promise.resolve(), preview: false, previewBlocked: null,
  };
}

// 같은 칸의 작업(다시 읽기·더 보기·줄 이동)은 차례로 돈다 — 겹치면 옛 응답이 새것을 덮는다.
function _serial(st, fn) {
  const run = st.queue.then(fn, fn);
  st.queue = run.catch(() => {});
  return run;
}

function _reason(meta) {
  if (!meta) return '';
  const out = [];
  if (!meta.confident) {
    if (meta.reason === 'guessed_utf16') out.push(t('BOM 없는 UTF-16으로 추정했습니다'));
    else if (meta.reason === 'decode_errors') out.push(t('고른 인코딩으로 풀 수 없는 바이트가 있어 깨진 글자로 보입니다'));
    else out.push(t('UTF-8이 아닌 바이트가 있어 인코딩을 확신할 수 없습니다'));
  }
  if (meta.eol && mixedEol(meta.eol)) out.push(t('줄 끝(LF·CRLF)이 섞여 있습니다'));
  return out.join(' · ');
}

function _button(label, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'vt-vw-bar-btn';
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}

function _span(text, warn) {
  const s = document.createElement('span');
  if (warn) s.className = 'vt-vw-bar-warn';
  s.textContent = text;
  return s;
}

// 위 띠: 인코딩(확신 못 함·직접 고름)과 그 이유(D9 — 읽기 전용으로 여는 이유), 끝부분 보기 안내, 계속 바뀌는 중.
// 보일 것이 없으면 띠를 두지 않는다.
function _syncTopBar(container, st) {
  let bar = container.querySelector(':scope > .vt-vw-bar');
  const meta = st.meta;
  const choice = _encOf(st);
  const reason = _reason(meta);
  const showEnc = !!meta && (!meta.confident || choice !== 'auto');
  const tail = st.mode === 'tail';
  const kind = previewKind(st.path);
  const toggle = st.mode === 'head' && hasRenderedView(kind) && !!meta && !meta.binary && !meta.image;
  // 편집 조작(단계 8): 편집기로 그린 텍스트면 띠에 「편집」(못 하면 흐리게 + 이유)이나 편집 중 조작을 둔다.
  const edit = !!container._vtEditor && !!meta && !meta.binary && !meta.image && !st.preview;
  if (!reason && !showEnc && !tail && !st.changing && !toggle && !edit) { if (bar) bar.remove(); syncConflict(container, st); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'vt-vw-bar';
    container.prepend(bar);
  }
  bar.replaceChildren();
  if (toggle) _viewToggle(container, st, bar, kind);
  if (st.changing) bar.appendChild(_span(CHANGING_MSG(), true));
  if (tail) {
    bar.append(_span(meta && meta.lineTruncated
      ? t('끝부분만 보는 중 — 줄 시작을 찾지 못해 줄 중간부터 보입니다. 줄 번호는 끝에서 센 값입니다.')
      : t('끝부분만 보는 중 — 줄 번호는 끝에서 센 값입니다.')),
    _button(t('처음부터 보기'), () => { _reopen(container, 'head'); }));
  }
  if (reason) bar.appendChild(_span(`${t('읽기 전용')} · ${reason}`, true));
  if (showEnc) {
    const label = document.createElement('label');
    label.className = 'vt-vw-enc';
    label.append(document.createTextNode(t('인코딩')));
    const sel = document.createElement('select');
    for (const enc of ENCODINGS) {
      const o = document.createElement('option');
      o.value = enc;
      o.textContent = enc === 'auto' && meta.requested === 'auto' && meta.encoding
        ? `${_encLabel('auto')} (${meta.encoding === 'unknown' ? t('모름') : _encLabel(meta.encoding)})`
        : _encLabel(enc);
      if (enc === choice) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener('change', () => {
      if (sel.value === 'auto') _encChoice.delete(_key(st.host, st.path));
      else _encChoice.set(_key(st.host, st.path), sel.value);
      _reopen(container, st.mode);
    });
    label.appendChild(sel);
    bar.appendChild(label);
    if (meta.suggested && meta.suggested !== choice && meta.suggested !== meta.encoding) {
      const sug = meta.suggested;
      bar.appendChild(_button(t('{enc}로 보기', { enc: _encLabel(sug) }), () => {
        _encChoice.set(_key(st.host, st.path), sug);
        _reopen(container, st.mode);
      }));
    }
  }
  if (edit) decorateBar(container, st, bar);
  bar.classList.toggle('vt-vw-bar-editing', editing(container));
  syncConflict(container, st);
}

// 렌더/원문 전환(시안 F07 — 마크다운 렌더/원문, CSV 표/원문, SVG 그림/원문). 크기 상한을 넘어 원문으로 연 경우와
// SVG의 sandbox 안내도 여기 적는다.
function _viewToggle(container, st, bar, kind) {
  const [renderLabel, sourceLabel] = viewLabels(kind);
  const view = st.preview ? 'render' : 'source';
  const seg = document.createElement('span');
  seg.className = 'vt-vw-seg';
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', t('보기 전환'));
  const tooBig = st.previewBlocked === 'csv-shape';
  const fits = (!st.meta || fitsPreview(kind, st.meta.size)) && !tooBig;
  for (const [v, label] of [['render', renderLabel], ['source', sourceLabel]]) {
    const b = _button(label, () => {
      if (v === view) return;
      if (v === 'render') _viewChoice.delete(_key(st.host, st.path));
      else _viewChoice.set(_key(st.host, st.path), 'source');
      _reopen(container, 'head');
    });
    b.classList.add('vt-vw-seg-btn');
    b.setAttribute('aria-pressed', String(v === view));
    if (v === 'render' && !fits) b.disabled = true;
    seg.appendChild(b);
  }
  bar.appendChild(seg);
  if (tooBig) {
    bar.appendChild(_span(t('표가 너무 커서(최대 {cols}열 · {rows}행) 표 대신 원문으로 보여 줍니다.', {
      cols: CSV_MAX_COLS, rows: CSV_MAX_ROWS.toLocaleString(),
    })));
  } else if (!fits) {
    bar.appendChild(_span(kind === 'csv'
      ? t('1MB를 넘어 표 대신 원문으로 보여 줍니다.')
      : t('1MB를 넘어 렌더 대신 원문으로 보여 줍니다.')));
  } else if (kind === 'svg' && st.preview) {
    bar.appendChild(_span(t('그림으로만 보여 줍니다 — SVG 안의 스크립트와 외부 불러오기는 실행되지 않습니다(sandbox).')));
  } else if (kind === 'md' && st.preview) {
    bar.appendChild(_span(t('원문 HTML은 글자로 보이고, 링크는 새 탭으로 열립니다. 외부 이미지는 막습니다.')));
  }
}

function _rawUrl(path, host) {
  return vtUrl(hostApiPath(`/api/fs/raw?path=${encodeURIComponent(path)}`, host));
}

// 마크다운 렌더 — `preview` 청크(marked + DOMPurify)를 받아 정화된 조각을 붙인다. 못 받으면 false(원문으로).
let _mdLoading = null;
function _loadMarkdown() {
  if (!_mdLoading) {
    _mdLoading = import('./markdown-lib.js').catch((e) => { _mdLoading = null; throw e; });
  }
  return _mdLoading;
}

/** 시험용 — 마크다운 렌더 모듈을 바꿔 끼운다(이 하네스는 맨 이름 import를 풀지 못한다). */
export function _setMarkdownLoader(fn) { _mdLoading = fn ? Promise.resolve().then(fn) : null; }

async function _paintMarkdown(container, text, st) {
  let mod;
  try { mod = await _loadMarkdown(); } catch (e) {
    console.warn('[FarShell viewer] markdown preview unavailable, showing source', e);
    return false;
  }
  if (container._vtDoc !== st) return true;
  const doc = document.createElement('div');
  doc.className = 'vt-md';
  doc.appendChild(mod.renderMarkdown(text, { docPath: st.path, rawUrl: (abs) => _rawUrl(abs, st.host) }));
  // 문서 안 앵커(#…)는 이 칸에서 같은 이름의 제목으로 옮긴다(id를 허용하지 않으므로 제목 글에서 이름을 만든다).
  // 막은 외부 이미지의 「주소 복사」.
  doc.addEventListener('click', (ev) => {
    const copy = ev.target.closest && ev.target.closest('.vt-md-copy');
    if (copy) {
      ev.preventDefault();
      // term/clipboard.js는 부팅 쪽 모듈(env·토스트)을 끌고 온다 — 누를 때만 부른다(core에 이미 있어 새로 받지 않는다).
      import('../../term/clipboard.js').then((m) => m.copyToClipboard(copy.dataset.copy || '')).then((ok) => {
        if (ok && typeof window.showToast === 'function') window.showToast(t('주소를 복사했습니다'));
      }).catch(() => {});
      return;
    }
    const a = ev.target.closest && ev.target.closest('a[href^="#"]');
    if (!a) return;
    ev.preventDefault();
    let want = a.getAttribute('href').slice(1);
    try { want = decodeURIComponent(want); } catch (_) { /* 그대로 */ }
    want = want.toLowerCase();
    const h = [...doc.querySelectorAll('h1,h2,h3,h4,h5,h6')].find((x) => slugify(x.textContent) === want);
    if (h && h.scrollIntoView) h.scrollIntoView({ block: 'start' });
  });
  container.appendChild(doc);
  return true;
}

// 「렌더」 그리기. 그렸으면 true(원문 편집기를 붙이지 않는다).
async function _paintPreview(container, st, kind, text) {
  if (kind === 'md') return _paintMarkdown(container, text, st);
  if (kind === 'csv') {
    const r = mountCsvTable(container, text, st.path);
    if (r.tooBig) { st.previewBlocked = 'csv-shape'; return false; }   // 열·행 상한 — 원문으로, 띠에 이유
    return true;
  }
  if (kind === 'svg') {
    mountSvg(container, _rawUrl(st.path, st.host), st.path.split('/').pop() || 'svg', () => {
      _setMsg(container, 'vt-vw-empty', [t('SVG를 그림으로 불러오지 못했습니다.'), t('「원문」으로 보세요.')]);
    });
    return true;
  }
  return false;
}

// 아래 띠: 앞부분만 받았을 때. 찾기·줄 이동이 받은 범위에서만 된다는 것을 함께 적는다(계획 4.2).
function _syncBottomNote(container, st) {
  let note = container.querySelector(':scope > .vt-vw-trunc');
  const meta = st.meta;
  const partial = st.mode === 'head' && meta && !meta.eof && !meta.image && !meta.binary && !st.preview;
  if (!partial) { if (note) note.remove(); return; }
  if (!note) {
    note = document.createElement('div');
    note.className = 'vt-vw-note warn vt-vw-trunc';
  }
  container.appendChild(note);   // 늘 맨 끝(편집기 다음)
  note.replaceChildren();
  const loaded = meta.loaded || meta.nextOffset || 0;
  let msg = t('앞 {loaded}만 불러왔습니다 (전체 {size}). 찾기와 줄 이동은 불러온 범위 안에서만 됩니다.',
    { loaded: _fmtSize(loaded), size: _fmtSize(meta.size) });
  if (meta.lineTruncated) msg += ` ${t('마지막 줄이 길어 중간에서 끊겼습니다.')}`;
  note.appendChild(_span(msg));
  if (canLoadMore(meta)) {
    note.appendChild(_button(t('더 보기'), (ev) => {
      ev.currentTarget.disabled = true;
      more(container).catch((e) => {
        if (typeof window.showToast === 'function') window.showToast(_errText(e), 'error');
      });
    }));
  } else if (loaded >= MAX_LOADED) {
    note.appendChild(_span(t('한 번에 볼 수 있는 상한({cap})에 닿았습니다.', { cap: _fmtSize(MAX_LOADED) })));
  }
  note.appendChild(_button(t('끝부분 보기'), () => { _reopen(container, 'tail'); }));
}

function _syncBars(container, st) {
  container._vtEditSync = () => { if (container._vtDoc === st) _syncBars(container, st); };
  _syncTopBar(container, st);
  _syncBottomNote(container, st);
}

function _dlButton(container, path, host, d, label) {
  const blocked = downloadBlockedReason(host);
  return downloadButton(label, () => {
    if (blocked) { if (typeof window.showToast === 'function') window.showToast(blocked, 'warn'); return; }
    const st = container._vtDoc;
    _downloadFsPath(path, { host, rev: d.rev || (st && st.meta && st.meta.rev) || null, size: d.size });
  }, blocked);
}

// 이미지·동영상·소리·바이너리 — 편집기를 쓰지 않는다.
function _paintMedia(container, d, path, host) {
  // [T3] 이미지는 그려 준다 — 스크린샷·다이어그램은 원격에서 코드만큼 자주
  // 열어본다. 바이트는 /api/fs/raw가 주고, 그 엔드포인트는 허용 타입만
  // 내보낸다(HTML·PDF는 같은 오리진에서 열면 XSS라 제외 — server/fsguard.py).
  container.replaceChildren();
  if (d.image) {
    if (d.tooLarge) {
      emptyState(container, t('이미지가 너무 큽니다 ({size})', { size: _fmtSize(d.size) }),
        t('미리보기 상한을 넘어 표시하지 않습니다.'), [_dlButton(container, path, host, d, t('다운로드'))]);
      return;
    }
    // 맞춤 / 100% 전환과 확대(시안 F07-C)는 preview.js `mountImageView`가 맡는다.
    mountImageView(container, _rawUrl(path, host), path.split('/').pop() || t('이미지'), {
      note: `${d.mime} · ${_fmtSize(d.size)}`,
      // 못 불러오면 빈 칸을 남기지 않는다 — 깨진 아이콘만 뜨면 "왜 안 되는지"를 모른다.
      onError: () => { _setMsg(container, 'vt-vw-empty', [t('이미지를 불러오지 못했습니다.')]); },
    });
    return;
  }
  if (d.media === 'video' || d.media === 'audio') {
    mountPlayer(container, d.media, _rawUrl(path, host), `${d.mime} · ${_fmtSize(d.size)}`, () => {
      emptyState(container, d.media === 'audio' ? t('소리를 재생하지 못했습니다.') : t('동영상을 재생하지 못했습니다.'),
        t('이 브라우저가 재생하지 못하는 형식일 수 있습니다. 내려받아 여세요.'), [_dlButton(container, path, host, d, t('다운로드'))]);
    });
    return;
  }
  if (previewKind(path) === 'pdf') {
    emptyState(container, t('PDF는 미리 보지 않습니다'),
      t('브라우저의 PDF 보기는 문서 안 스크립트를 실행할 수 있어 막았습니다. 내려받아 여세요.'),
      [_dlButton(container, path, host, d, t('다운로드 {size}', { size: _fmtSize(d.size) }))]);
    return;
  }
  emptyState(container, t('바이너리 파일 ({size})', { size: _fmtSize(d.size) }), t('미리보기를 지원하지 않습니다.'),
    [_dlButton(container, path, host, d, t('다운로드'))]);
}

// 텍스트를 비어 있는 container에 그린다. 편집기 청크를 못 받으면 일반 텍스트로(_vtPlain).
// 붙이는 사이 칸이 거둬졌거나(disposeViewer) 다른 열기로 바뀌었으면 방금 만든 편집기를 바로 버린다.
async function _paintText(container, text, path, mode, st) {
  let handle = null;
  try {
    const mod = await loadEditor();
    handle = await mod.mountEditor(container, text, path, VTDiffLex.langForPath(path),
      mode === 'tail' ? { lineFrom: 'end' } : {});
  } catch (e) {
    console.warn('[FarShell viewer] editor unavailable, showing plain text', e);
  }
  if (handle) {
    _sweepEditors();
    _editors.add(handle);
    if (st && container._vtDoc !== st) { _destroyEditor(handle); return; }
    container._vtEditor = handle;
    return;
  }
  if (st && container._vtDoc !== st) return;
  container._vtPlain = _plainText(container, text);
}

function _setDocText(container, text) {
  const h = container._vtEditor;
  if (h) return h.setText(text);
  const pre = container._vtPlain;
  if (pre && pre.textContent !== text) { pre.textContent = text; return true; }
  return false;
}

function _appendText(container, text) {
  const h = container._vtEditor;
  if (h) h.append(text);
  else if (container._vtPlain) container._vtPlain.textContent += text;
}

// 끝의 n글자를 text로 바꾼다(커진 끝 이어 받기 — 줄 끝 없이 끝났던 마지막 조각을 새것으로).
function _replaceEnd(container, n, text) {
  const h = container._vtEditor;
  if (h) return h.replaceEnd(n, text);
  const pre = container._vtPlain;
  if (!pre) return false;
  const s = pre.textContent;
  pre.textContent = s.slice(0, s.length - n) + text;
  return true;
}

// 화면 글의 끝: 줄 끝 없이 끝난 마지막 조각(partial)과 그 앞 PROBE_BYTES 글자(before).
function _docEnd(container) {
  let len;
  let cut;
  let slice;
  const h = container._vtEditor;
  if (h) {
    const doc = h.view && h.view.state && h.view.state.doc;
    if (!doc) return null;
    len = doc.length;
    cut = doc.line(doc.lines).from;
    slice = (a, b) => doc.sliceString(a, b);
  } else if (container._vtPlain) {
    const s = container._vtPlain.textContent;
    len = s.length;
    cut = s.lastIndexOf('\n') + 1;
    slice = (a, b) => s.slice(a, b);
  } else {
    return null;
  }
  const from = Math.max(0, cut - PROBE_BYTES);
  return { partial: slice(cut, len), partialLen: len - cut, before: slice(from, cut), beforeAll: from === 0 };
}

// 칸을 처음부터 연다. opts: {host, mode('head'|'tail'), line, col}. 첫 구간이 오면 바로 그리고 나머지는 이어 붙인다.
// 열기 전체(이어 받기, 줄 이동의 더 받기까지)가 그 칸의 차례(_serial) 하나로 돈다 — 첫 구간을 그린 뒤 온 다시 읽기가
// 문서를 통째로 바꾼 다음 열기의 남은 구간이 그 뒤에 또 붙어 끝이 두 번 들어가던 경쟁을 막는다.
function _open(container, path, opts = {}) {
  const st = _newDoc(path, opts.host, opts.mode);
  container._vtDoc = st;
  container._vtEditor = null;
  container._vtPlain = null;
  container._vtEdit = null;
  return _serial(st, () => _openRun(container, st, path, opts));
}

async function _openRun(container, st, path, opts) {
  const mine = () => container._vtDoc === st;
  const encoding = _encOf(st);
  if (st.mode === 'tail') {
    const r = await loadTail({ path, host: st.host, encoding });
    if (!mine()) return;
    st.meta = r.meta;
    st.etag = r.etag;
    if (r.meta.image || r.meta.binary || previewKind(path) === 'pdf') { _paintMedia(container, r.meta, path, st.host); return; }
    container.replaceChildren();
    await _paintText(container, r.text, path, 'tail', st);
    if (!mine()) return;
    _syncBars(container, st);
    if (container._vtEditor) container._vtEditor.jumpToEnd();
    return;
  }
  let painted = false;
  let r;
  // 렌더 보기(마크다운·CSV·SVG): 사람이 원문을 고르지 않았고, 줄 이동(터미널 링크)이 아니고, 파일 전체가 첫 구간에
  // 들어올 때만(SVG는 그림을 서버가 따로 주므로 크기만 본다). 아니면 지금까지처럼 원문 편집기다.
  const kind = previewKind(path);
  const wantPreview = hasRenderedView(kind) && _viewOf(st) === 'render' && !opts.line;
  // PDF는 바이트가 글자처럼 보여도(NUL 없는 PDF) 원문으로 그리지 않는다 — 다운로드만(결정 D8). 첫 구간만 받아 크기·rev를 안다.
  const pdf = kind === 'pdf';
  try {
    r = await loadHead({
      path, host: st.host, encoding, target: pdf ? 1 : OPEN_BUDGET,
      onFirst: async (text, meta) => {
        if (!mine()) return;
        st.meta = meta;
        if (pdf) { st.preview = true; painted = true; return; }
        if (painted) { _setDocText(container, text); return; }
        container.replaceChildren();
        if (wantPreview && fitsPreview(kind, meta.size) && (kind === 'svg' || meta.eof)) {
          st.preview = true;
          painted = true;
          if (await _paintPreview(container, st, kind, text)) return;
          st.preview = false;
          if (!mine()) return;
          container.replaceChildren();
        }
        await _paintText(container, text, path, 'head', st);
        painted = true;
      },
      onAppend: (text) => { if (mine() && !st.preview) _appendText(container, text); },
    });
  } catch (e) {
    if (!mine()) return;
    if (!(e instanceof ChangingError) || !painted) throw e;
    st.changing = true;
    _syncBars(container, st);
    return;
  }
  if (!mine()) return;
  st.meta = r.meta;
  st.etag = r.etag;
  st.carry = r.carry || '';
  if (r.meta.image || r.meta.binary || pdf) { _paintMedia(container, r.meta, path, st.host); return; }
  _syncBars(container, st);
  // 고친 채 닫았던 파일이면 편집 모드로 이어 연다(edit.js 보관).
  restore(container, st, container._vtEditSync);
  // 줄 이동이 실패해도(더 받다가 끊김) 이미 그린 내용을 오류로 덮지 않는다.
  if (opts.line) await _jump(container, st, opts.line, opts.col).catch(() => {});
}

// 아직 받지 않은 줄이면 「더 보기」 한 번(OPEN_BUDGET)까지만 스스로 더 받는다 — 지금까지의 줄 평균 길이로 보아
// 그 안에 들 것 같을 때만. `foo.log:99999999` 같은 링크가 상한(64MB)까지 받아 버리지 않게. 그 밖이면 알리고
// 「더 보기」·「끝부분 보기」는 사람이 고른다.
function _jumpReach(st, lines, line) {
  const loaded = (st.meta && (st.meta.loaded || st.meta.nextOffset)) || 0;
  if (!canLoadMore(st.meta) || lines < 1 || loaded <= 0) return false;
  return (line / lines) * loaded <= loaded + OPEN_BUDGET;
}

async function _jump(container, st, line, col) {
  const h0 = container._vtEditor;
  if (h0 && h0.lines() < line && _jumpReach(st, h0.lines(), line)) {
    try { await _more(container, st); } catch (_) { /* 받은 데까지로 옮긴다 */ }
  }
  const h = container._vtEditor;
  if (!h || container._vtDoc !== st) return;
  if (!h.jumpTo(line, col) && typeof window.showToast === 'function') {
    window.showToast(canLoadMore(st.meta)
      ? t('{line}번째 줄은 아직 불러오지 않았습니다. 아래 「더 보기」나 「끝부분 보기」로 이어 보세요.', { line })
      : t('{line}번째 줄은 불러온 범위 밖입니다', { line }), 'warn');
  }
}

async function _more(container, st) {
  try {
    st.carry = await loadMore({
      path: st.path, host: st.host, meta: st.meta, carry: st.carry,
      onAppend: (text) => { if (container._vtDoc === st) _appendText(container, text); },
    });
  } catch (e) {
    if (!e || e.status !== 409) throw e;
    // 붙여 둔 앞부분이 이미 옛 판이다 — 받으려던 만큼 처음부터 다시 받고 바뀐 부분만 바꾼다.
    await _reloadHead(container, st, Math.min(MAX_LOADED, (st.meta.nextOffset || 0) + OPEN_BUDGET));
  } finally {
    if (container._vtDoc === st) _syncBars(container, st);
  }
}

/** 「더 보기」 — 받은 끝에서 4MB 더(상한 MAX_LOADED). */
export function more(container) {
  const st = container && container._vtDoc;
  if (!st || !canLoadMore(st.meta)) return Promise.resolve();
  return _serial(st, () => _more(container, st));
}

// 앞에서부터 target까지 다시 받아 바뀐 부분만 바꾼다(다시 읽기, 더 보기 중 409). etag가 있으면 첫 구간을 조건부로.
// 돌려주는 것: 'same'(304) · 'ok' · 'changing' · 'stale' · 'kind'(텍스트가 아니게 됨 — 호출부가 새로 그린다).
async function _reloadHead(container, st, target, etag = null) {
  let parts = [];
  let r;
  try {
    r = await loadHead({
      path: st.path, host: st.host, encoding: _encOf(st), target, etag,
      onFirst: (text) => { parts = [text]; },
      onAppend: (text) => { parts.push(text); },
      onRestart: () => { parts = []; },
    });
  } catch (e) {
    if (!(e instanceof ChangingError)) throw e;
    // 옛 내용은 그대로 두고 알린다. 다음 변경 알림이나 새로고침 때 다시 시도한다.
    if (container._vtDoc !== st) return 'stale';
    st.changing = true;
    _syncBars(container, st);
    return 'changing';
  }
  if (container._vtDoc !== st) return 'stale';
  if (r.notModified) {
    if (st.changing) { st.changing = false; _syncBars(container, st); }
    return 'same';
  }
  if (r.meta.image || r.meta.binary) return 'kind';
  st.changing = false;
  st.meta = r.meta;
  st.etag = r.etag;
  st.carry = r.carry || '';
  st.probe = null;
  _setDocText(container, parts.join(''));
  _syncBars(container, st);
  return 'ok';
}

async function _reloadTail(container, st, etag = st.etag) {
  const r = await loadTail({ path: st.path, host: st.host, encoding: _encOf(st), etag });
  if (container._vtDoc !== st) return 'stale';
  if (r.notModified) return 'same';
  if (r.meta.image || r.meta.binary) return 'kind';
  // tail -f처럼 — 끝에 있었으면 새 끝을 따라간다.
  const h = container._vtEditor;
  const follow = !!h && h.atBottom();
  st.meta = r.meta;
  st.etag = r.etag;
  st.probe = null;
  _setDocText(container, r.text);
  _syncBars(container, st);
  if (follow) h.jumpToEnd();
  return 'ok';
}

// 칸이 화면에 든 바이트 구간 [start, end) — 앞부분 보기는 [0, 받은 끝), 끝부분 보기는 [tail 시작, 크기).
function _byteSpan(st) {
  const m = st.meta;
  if (!m) return { start: 0, end: 0 };
  if (st.mode === 'tail') return { start: m.tailOffset || 0, end: m.size || 0 };
  return { start: 0, end: m.loaded != null ? m.loaded : (m.nextOffset || 0) };
}

// 커지기만 했으면 받은 끝 뒤만 이어 받는다(file-load.js growthFrom). 돌려주는 것: 'same'(그대로) · 'ok'(이어 붙임) ·
// 'stale' · 'full'(처음부터 다시 읽어야 한다 — 창이 화면과 다르다, 크기가 늘지 않았다, 다른 파일이 되었다, 그 사이 또
// 바뀌었다(409), 창을 물을 수 없다). 창은 같은 구간이면 조건부로 묻는다 — 그대로면 304 하나로 끝난다.
async function _grow(container, st) {
  const meta = st.meta;
  if (st.carry || !meta.pinned) return 'full';
  const { start, end } = _byteSpan(st);
  const prev = st.probe;
  const offset = Math.max(start, end - PROBE_BYTES);
  let p;
  try {
    p = await loadProbe({
      path: st.path, host: st.host, meta, start, end,
      etag: prev && prev.offset === offset && prev.end === end ? prev.etag : null,
    });
  } catch (e) {
    if (e && typeof e.status === 'number') return 'full';   // 400(홀수 UTF-16 등)·409 — 처음부터 다시 읽기로
    throw e;
  }
  if (container._vtDoc !== st) return 'stale';
  const same = () => {
    if (st.changing) { st.changing = false; _syncBars(container, st); }
    return 'same';
  };
  if (p.notModified) return same();
  st.probe = { offset: p.offset, end, etag: p.etag };
  const d = p.data;
  if (d.rev && d.rev === meta.rev) return same();
  const tail = _docEnd(container);
  if (!tail) return 'full';
  const from = growthFrom(d, {
    start, end, offset: p.offset, oldSize: meta.size, oldRev: meta.rev,
    before: tail.before, beforeAll: tail.beforeAll, partial: tail.partial, pinned: meta.pinned, confident: meta.confident,
  });
  if (from == null) return 'full';
  const next = { ...meta, eol: { ...meta.eol }, size: d.size, rev: d.rev };
  if (st.mode === 'head' && !meta.eof) {
    // 받은 끝 뒤가 늘었을 뿐 — 화면에 든 부분은 그대로다. 아래 띠의 전체 크기만 바뀐다.
    st.meta = next;
    st.changing = false;
    _syncBars(container, st);
    return 'ok';
  }
  // 끝부분 보기는 끝을 따라 붙인다. 붙인 양이 끝부분 보기 한 번의 양을 넘으면 새 끝부분으로 다시 연다(무한히 늘지 않게).
  if (st.mode === 'tail' && d.size - start > 2 * TAIL_BYTES) return 'full';
  const target = st.mode === 'tail' ? d.size : Math.max(OPEN_BUDGET, end);
  next.eof = false;
  next.nextOffset = from;
  next.lineTruncated = false;
  const pieces = [];
  let carry;
  try {
    // 줄 끝 없이 끝났던 마지막 조각은 다시 받아 바꾼다 — 받을 것이 목표 너머여도 한 구간은 받는다.
    carry = await loadMore({
      path: st.path, host: st.host, meta: next, carry: '', target: Math.max(target, from + 1),
      onAppend: (text) => { pieces.push(text); },
    });
  } catch (e) {
    if (e && e.status === 409) return 'full';
    throw e;
  }
  if (container._vtDoc !== st) return 'stale';
  if (st.mode === 'tail') next.lineTruncated = meta.lineTruncated;   // 끝부분 보기에서는 "줄 시작을 못 찾음"이라는 뜻
  const h = container._vtEditor;
  const follow = st.mode === 'tail' && !!h && h.atBottom();
  st.meta = next;
  st.carry = st.mode === 'tail' ? '' : (carry || '');
  st.changing = false;
  st.probe = null;
  _replaceEnd(container, tail.partialLen, pieces.join(''));
  _syncBars(container, st);
  if (follow) h.jumpToEnd();
  return 'ok';
}

// 다시 읽기 한 번(그 칸의 차례 안에서 돈다). 받아 둔 양이 크면 먼저 커지기만 했는지 보고, 아니면 처음부터.
async function _refreshDoc(container, st) {
  const big = _byteSpan(st).end - _byteSpan(st).start > INCREMENTAL_MIN;
  if (big) {
    const g = await _grow(container, st);
    if (g !== 'full') return g;
  }
  if (st.mode === 'tail') return _reloadTail(container, st, big ? null : st.etag);
  return _reloadHead(container, st, Math.max(OPEN_BUDGET, st.meta.loaded || 0), st.etag);
}

/** 자동 갱신의 최소 간격(ms). 1MB 넘게 받아 둔 칸은 1초 — 연달아 오는 변경 알림을 묶어 큰 다시 읽기가 쌓이지 않게. */
export const LARGE_REFRESH_GAP_MS = 1000;
export function refreshGap(scrollEl) {
  const host = scrollEl && _currentHost(scrollEl);
  const st = host && host._vtDoc;
  if (!st || !st.meta) return 0;
  const { start, end } = _byteSpan(st);
  return end - start > CHUNK ? LARGE_REFRESH_GAP_MS : 0;
}

// 칸을 그 자리에서 다시 연다(인코딩 바꾸기, 앞부분 ↔ 끝부분). 칸 요소는 그대로라 소비처의 참조가 유지된다.
function _reopen(container, mode) {
  const st = container._vtDoc;
  if (!st) return Promise.resolve();
  stash(container);   // 편집 중이었으면 고친 내용을 보관 — 앞부분으로 다시 열면 이어 연다
  container.innerHTML = `<div class="vt-vw-loading">${t('불러오는 중…')}</div>`;
  return _open(container, st.path, { host: st.host, mode })
    .catch((e) => { _setMsg(container, 'vt-vw-empty', [_errText(e)]); });
}

function _currentHost(scrollEl) {
  return Array.from(scrollEl.children).find((c) => c.classList.contains('vt-vw-code-pane')) || null;
}

function _swapHost(scrollEl, host) {
  const top = scrollEl.scrollTop;
  const left = scrollEl.scrollLeft;
  const old = _currentHost(scrollEl);
  if (old) { old.replaceWith(host); disposeViewer(old); } else scrollEl.appendChild(host);
  scrollEl.scrollTop = top;
  scrollEl.scrollLeft = left;
}

/**
 * 이미 열린 파일을 다시 그린다(새로고침 버튼·자동 갱신).
 * - 앞서 받은 판의 ETag로 첫 구간만 조건부로 묻는다. **304면 아무것도 하지 않는다.**
 * - 200이면 받았던 만큼 다시 받아 편집기의 **바뀐 부분만** 바꾼다. 스크롤·선택이 그대로 남고, 내용이 같으면(시각만
 *   바뀐 저장) 아무것도 그리지 않는다. 그 사이 계속 바뀌어 3번 연속 409면 옛 내용을 두고 「계속 바뀌는 중」을 띄운다.
 * - 그 밖(이미지와 텍스트가 바뀜, 앞선 실패 등)은 떨어진 새 호스트에 다 그린 뒤 한 번에 바꾼다 —
 *   깜빡이지 않고 스크롤 위치를 되돌린다.
 * `scrollEl`은 호스트(.vt-vw-code-pane)를 담은 스크롤 상자다. 호스트만 바꾸고 곁에 있는 것(페인 뷰어의
 * 새로고침 버튼)은 둔다. `isCurrent()`는 그 사이 다른 파일로 바뀌지 않았는지 확인한다.
 * `opts.host`는 파일이 있는 호스트(없으면 앞서 연 칸의 것).
 */
export async function refreshFile(scrollEl, path, isCurrent, opts = {}) {
  const seq = (scrollEl._vtRefreshSeq || 0) + 1;
  scrollEl._vtRefreshSeq = seq;
  // 늦게 도착한 이전 새로고침이 최신 것을 덮으면 안 된다.
  const stale = () => scrollEl._vtRefreshSeq !== seq || !isCurrent();
  const old = _currentHost(scrollEl);
  const st = old && old._vtDoc;
  const host = opts.host !== undefined ? opts.host : (st ? st.host : null);
  // 그 칸이 하던 일(열기·더 보기·줄 이동)이 끝난 뒤에 판단한다 — 열기가 아직 구간을 붙이는 중이면 기다린다.
  const live = () => old._vtDoc === st && !!st.meta && !st.meta.image && !st.meta.binary
    && (!!old._vtPlain || (!!old._vtEditor && old._vtEditor.view.dom.isConnected));
  if (st && st.path === path) {
    let out;
    try {
      out = await _serial(st, () => {
        if (old._vtDoc !== st) return 'stale';
        // 편집 중이면 문서를 덮지 않는다 — 바깥 변경은 edit.js가 따라가거나 충돌 배너로 알린다.
        if (editing(old)) return refreshEditing(old, st);
        return live() ? _refreshDoc(old, st) : 'reopen';
      });
    } catch (e) {
      if (stale()) return;
      // 실패도 화면에 보여 준다 — 옛 내용을 조용히 남기면 끊긴 것을 모른다.
      const fail = document.createElement('div');
      fail.className = 'vt-vw-code-pane';
      _setMsg(fail, 'vt-vw-empty', [_errText(e)]);
      _swapHost(scrollEl, fail);
      return;
    }
    if (out !== 'kind' && out !== 'reopen') return;
  }
  const fresh = document.createElement('div');
  fresh.className = 'vt-vw-code-pane';
  try {
    await _open(fresh, path, { host, mode: st ? st.mode : 'head' });
  } catch (e) {
    if (stale()) return;
    _setMsg(fresh, 'vt-vw-empty', [_errText(e)]);
  }
  if (stale()) return;
  _swapHost(scrollEl, fresh);
}

/**
 * 파일 하나를 container에 그린다. 소비처는 뷰어 페인(layout/panes.js)과 파일 오버레이(overlay.js)다.
 * opts: `host`(파일이 있는 호스트, 없으면 지금 화면의 호스트), `line`·`col`(그 줄로 이동 — 터미널 링크).
 */
export async function renderFile(container, path, opts = {}) {
  loadEditor().catch(() => {});   // fire-and-forget — 아래 요청과 겹쳐 받는다. 실패는 _paintText가 처리한다
  container.innerHTML = `<div class="vt-vw-loading">${t('불러오는 중…')}</div>`;
  const jump = opts.line ? { line: opts.line, col: opts.col } : _takeJump(path);
  try {
    await _open(container, path, { host: opts.host, line: jump && jump.line, col: jump && jump.col });
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [_errText(e)]);
  }
}

// 페인으로 여는 줄 이동. 페인은 트리 상태에서 그려지므로(layout/panes.js) 줄을 넘길 자리가 없다 — 여기 맡겨 두면
// 그 파일을 그리는 renderFile이 가져간다. 오래 남은 것은 버린다(다음에 그 파일을 열 때 엉뚱하게 튀지 않게).
const _pendingJump = new Map();
const JUMP_TTL_MS = 10000;
function _takeJump(path) {
  const j = _pendingJump.get(path);
  _pendingJump.delete(path);
  return j && Date.now() - j.at < JUMP_TTL_MS ? j : null;
}

/** 페인 뷰어에 줄 이동을 맡긴다. 그 파일이 이미 페인에 그려져 있으면 바로 옮긴다. */
export function queueJump(path, line, col) {
  if (!path || !line) return;
  let done = false;
  for (const body of document.querySelectorAll('.vt-pane-viewer')) {
    if (body.dataset.viewerFile !== path) continue;
    const pane = body.querySelector(':scope > .vt-vw-code-pane');
    if (pane && pane._vtDoc) { jumpToLine(pane, line, col).catch(() => {}); done = true; }
  }
  if (!done) _pendingJump.set(path, { line, col, at: Date.now() });
}

/** 이미 그려 둔 칸에서 그 줄로 이동한다(같은 파일 링크를 다시 눌렀을 때). */
export function jumpToLine(container, line, col) {
  const st = container && container._vtDoc;
  if (!st || !line) return Promise.resolve();
  return _serial(st, () => _jump(container, st, line, col));
}

