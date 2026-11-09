// 뷰어 편집 모드(파일 개편 단계 8, 시안 F05 · F05-B · F05-C · F05-D · F05-D2 · F05-F · M-F5 · M-F5-B).
//
// - 「편집」을 누르면 **같은 EditorState**의 읽기 전용만 푼다(스크롤·선택 유지, editor.js `setEditable`). 자동 저장은 없다.
//   ⌘S/Ctrl+S 또는 위 띠의 「저장」(화면에 하나뿐인 주 버튼). 띠는 편집 중 스크롤 상자 위에 붙어 있어 폰 화상 키보드가
//   올라와도 저장이 보인다(M-F5).
// - 편집할 수 있는 파일(`editability`): 이 컴퓨터의 파일(D7) · 앞부분 보기 · 파일 전체를 받음 · 2 MB 이하 · 인코딩 확신 ·
//   줄 끝이 한 가지(D9) · 원문 보기 · 편집기 청크가 있음. 아니면 「편집」은 흐리게 두고 이유를 적는다.
// - 저장: `PUT /api/fs/file`에 지금 판(`rev`)을 `expected_rev`로 보낸다. 다르면 서버가 409 → 충돌 배너. 승격이 없으면
//   비밀번호를 한 번 묻고 같은 save_id로 다시 보낸다. 시간 초과·503이면 같은 save_id로 한 번 더(서버가 첫 결과를 준다).
// - 바깥 변경: 다시 읽기(감시 알림·새로고침)가 편집 중이면 문서를 덮지 않고 여기로 온다(`refreshEditing`). 디스크 판이
//   내가 마지막으로 저장한 판이면 무시, 고친 내용이 없으면 조용히 따라가고, 있으면 충돌 배너(비교 · 다시 불러오기 ·
//   내 것으로 덮어쓰기).
// - 고친 채 칸이 사라지면(닫기 · 다른 파일 · 페인 제거 · 표면 이동) 고친 내용을 (호스트, 경로) 키로 보관하고, 같은
//   파일을 다시 열면 편집 모드로 이어 연다(`stash`·`restore`). 페이지를 떠날 때는 브라우저가 한 번 묻는다.
import { t } from '../../core/i18n.js';
import { icon } from '../../ui/icons.js';
import { confirmDialog } from '../../core/dialog.js';
import { isTimeoutError } from '../../core/api.js';
import { loadHead, mixedEol, OPEN_BUDGET } from './file-load.js';
import {
  writeFetch, withWriteElevation, newSaveId, writeErrorText, writeBlockedReason, renameDialog, trashDialog, SAVE_MAX_BYTES,
} from './fs-write.js';

const _key = (host, path) => `${host || 'local'}\n${path}`;
const _drafts = new Map();          // (호스트, 경로) → 보관한 편집
const SAVED_FLASH_MS = 900;
const DIRTY_DEBOUNCE_MS = 120;
const DIFF_MAX_LINES = 1500;        // 비교에서 앞뒤 공통 줄을 뺀 나머지가 이보다 많으면 줄 단위 비교를 하지 않는다

function _toast(msg, kind) {
  if (typeof window.showToast === 'function') window.showToast(msg, kind);
}

function _hhmm(d = new Date()) {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** 파일 끝 종류 → 저장할 줄 끝 이름. 줄 끝이 없으면 lf. */
export function eolName(kinds) {
  if (!kinds) return 'lf';
  if (kinds.crlf) return 'crlf';
  if (kinds.cr) return 'cr';
  return 'lf';
}

/** 편집할 수 있는가 → `{ok, reason}`. reason은 흐린 「편집」 옆·툴팁에 적는다. */
export function editability(st, container) {
  const meta = st && st.meta;
  const no = (reason) => ({ ok: false, reason });
  if (!meta || meta.image || meta.binary) return no(t('텍스트 파일만 편집할 수 있습니다'));
  const remote = writeBlockedReason(st.host);
  if (remote) return no(remote);
  if (!container || !container._vtEditor || typeof container._vtEditor.setEditable !== 'function') {
    return no(t('편집기를 불러오지 못해 편집할 수 없습니다'));
  }
  if (st.mode !== 'head') return no(t('끝부분 보기에서는 편집할 수 없습니다 — 「처음부터 보기」로 여세요'));
  if (st.preview) return no(t('원문 보기에서 편집할 수 있습니다'));
  if ((meta.size || 0) > SAVE_MAX_BYTES) return no(t('2 MB가 넘는 파일은 편집할 수 없습니다'));
  if (!meta.eof) return no(t('파일 전체를 받아야 편집할 수 있습니다'));
  if (!meta.confident || !meta.encoding || meta.encoding === 'unknown') return no(t('인코딩을 확신할 수 없어 읽기 전용입니다'));
  if (meta.eol && mixedEol(meta.eol)) return no(t('줄 끝이 섞여 있어 읽기 전용입니다'));
  if (st.changing) return no(t('파일이 계속 바뀌는 중이라 편집할 수 없습니다'));
  return { ok: true, reason: null };
}

export function editing(container) {
  return !!(container && container._vtEdit && container._vtEdit.on);
}

export function isDirty(container) {
  const ed = container && container._vtEdit;
  if (!ed || !ed.on) return false;
  const h = container._vtEditor;
  return !!h && h.getText() !== ed.base;
}

/** 보관한 편집이 하나라도 있거나 지금 고친 칸이 있는가(페이지를 떠날 때 묻는다). */
export function hasUnsaved() {
  if (_drafts.size) return true;
  for (const c of document.querySelectorAll('.vt-vw-code-pane')) if (isDirty(c)) return true;
  return false;
}

let _unloadWired = false;
function _wireUnload() {
  if (_unloadWired || typeof window.addEventListener !== 'function') return;
  _unloadWired = true;
  window.addEventListener('beforeunload', (ev) => {
    if (!hasUnsaved()) return;
    ev.preventDefault();
    ev.returnValue = '';
  });
}

// --- 상태 --------------------------------------------------------------------------------

function _newEdit(st) {
  const meta = st.meta;
  return {
    on: false, base: '', rev: meta.rev, encoding: meta.encoding, eol: eolName(meta.eol),
    status: 'idle', savedAt: null, savedRev: null, saveId: null, conflict: null, compare: false, dirty: false,
    timer: null, sync: null,
  };
}

/** 편집 모드로. 이미 편집 중이면 아무것도 하지 않는다. `draft`가 있으면 그 글로 이어 연다. */
export function startEdit(container, st, sync, draft = null) {
  const h = container._vtEditor;
  if (!h || editing(container)) return false;
  const can = editability(st, container);
  if (!can.ok) { _toast(can.reason, 'warn'); return false; }
  const ed = container._vtEdit || _newEdit(st);
  container._vtEdit = ed;
  ed.on = true;
  ed.sync = sync;
  ed.stHost = st.host;
  ed.stPath = st.path;
  ed.base = h.getText();
  ed.rev = st.meta.rev;
  ed.encoding = st.meta.encoding;
  ed.eol = eolName(st.meta.eol);
  ed.status = 'idle';
  if (draft) {
    // 보관해 둔 편집을 잇는다. 그 사이 디스크가 바뀌었으면(보관할 때의 판과 다르고 내용도 다름) 충돌 배너.
    const diskText = ed.base;
    const diskRev = st.meta.rev;
    ed.base = draft.base;
    ed.rev = draft.rev;
    h.setText(draft.text);
    if (diskRev !== draft.rev && diskText !== draft.base) {
      ed.conflict = { rev: diskRev, text: diskText };
    } else {
      ed.rev = diskRev;
      ed.base = diskText;
    }
  }
  h.setEditable(true, { onSave: () => { saveEdit(container, st).catch(() => {}); } });
  h.onChange(() => _scheduleDirty(container));
  if (!container._vtSaveKey) {
    // 띠의 버튼에 초점이 있어도 ⌘S/Ctrl+S가 저장이다(편집기 안은 editor.js 키맵이 받는다).
    container._vtSaveKey = (ev) => {
      if (!editing(container)) return;
      if ((ev.metaKey || ev.ctrlKey) && !ev.altKey && !ev.shiftKey && (ev.key === 's' || ev.key === 'S')) {
        if (ev.target && ev.target.closest && ev.target.closest('.cm-content')) return;
        ev.preventDefault();
        saveEdit(container, st).catch(() => {});
      }
    };
    container.addEventListener('keydown', container._vtSaveKey);
  }
  _wireUnload();
  _recompute(container);
  _sync(container);
  h.focus();
  return true;
}

/** 편집 끝내기. 고친 내용이 있으면 버릴지 묻는다(기본 초점은 「취소」). 끝냈으면 true. */
export async function stopEdit(container, { discard = false } = {}) {
  const ed = container._vtEdit;
  const h = container._vtEditor;
  if (!ed || !ed.on || !h) return true;
  if (!discard && isDirty(container)) {
    const ok = await confirmDialog(t('고친 내용이 아직 저장되지 않았습니다. 버리면 되돌릴 수 없습니다.'),
      { title: t('편집을 버릴까요?'), okLabel: t('버리기'), danger: true });
    if (!ok) return false;
  }
  if (isDirty(container) || ed.conflict) h.setText(ed.conflict ? ed.conflict.text : ed.base);
  if (ed.conflict) { ed.rev = ed.conflict.rev; ed.base = ed.conflict.text; }
  ed.on = false;
  ed.conflict = null;
  ed.compare = false;
  ed.status = 'idle';
  h.setEditable(false);
  h.onChange(null);
  _drafts.delete(_key(ed.stHost, ed.stPath));
  _sync(container);
  return true;
}

function _scheduleDirty(container) {
  const ed = container._vtEdit;
  if (!ed) return;
  clearTimeout(ed.timer);
  ed.timer = setTimeout(() => { _recompute(container); _sync(container); }, DIRTY_DEBOUNCE_MS);
}

function _recompute(container) {
  const ed = container._vtEdit;
  if (!ed) return;
  const was = ed.dirty;
  ed.dirty = isDirty(container);
  if (ed.dirty && ed.status === 'saved') ed.status = 'idle';
  if (was !== ed.dirty) container.classList.toggle('vt-vw-dirty', ed.dirty);
}

function _sync(container) {
  const ed = container._vtEdit;
  if (ed && typeof ed.sync === 'function') ed.sync();
}

// --- 저장 --------------------------------------------------------------------------------

/** 지금 디스크 내용(앞 4MB, 편집 대상은 2MB 이하라 전부)을 받는다 — 충돌 비교용. */
async function _diskText(st) {
  const parts = [];
  const r = await loadHead({
    path: st.path, host: st.host, encoding: st.meta.pinned || 'auto', target: OPEN_BUDGET,
    onFirst: (text) => { parts.length = 0; parts.push(text); },
    onAppend: (text) => { parts.push(text); },
    onRestart: () => { parts.length = 0; },
  });
  return { text: parts.join(''), meta: r.meta, etag: r.etag };
}

async function _putOnce(body) {
  try {
    return await writeFetch('/api/fs/file', 'PUT', body);
  } catch (e) {
    // 시간 초과·서버 쪽 503 — 같은 save_id로 한 번 더. 서버가 끝낸 저장이면 그 결과를 돌려준다(두 번 쓰지 않는다).
    if (!(isTimeoutError(e) || (e && e.status === 503))) throw e;
    await new Promise((r) => setTimeout(r, 1200));
    const out = await writeFetch('/api/fs/file', 'PUT', body);
    if (out && out.replayed) _toast(t('응답이 늦어 같은 저장을 다시 보냈습니다 — 두 번 쓰지 않고 첫 저장의 결과를 받았습니다'), 'warn');
    return out;
  }
}

/**
 * 저장. `opts.overwrite`면 충돌 배너의 「내 것으로 덮어쓰기」 — 디스크의 지금 판을 expected_rev로 보낸다.
 * 성공하면 true. 실패해도 편집 내용과 「저장 안 됨」은 그대로다.
 */
export async function saveEdit(container, st, opts = {}) {
  const ed = container._vtEdit;
  const h = container._vtEditor;
  if (!ed || !ed.on || !h || ed.status === 'saving') return false;
  if (ed.conflict && !opts.overwrite) {
    _toast(t('다른 곳에서 바뀐 파일입니다 — 위 배너에서 비교하거나 다시 불러오거나 덮어쓰세요'), 'warn');
    return false;
  }
  const text = h.getText();
  const expected = opts.overwrite && ed.conflict ? ed.conflict.rev : ed.rev;
  // 같은 저장(같은 글·같은 판)을 다시 보내는 경우에만 save_id를 재사용한다.
  const fp = `${expected}\n${text.length}\n${text}`;
  if (!ed.saveId || ed.saveFp !== fp) { ed.saveId = newSaveId(); ed.saveFp = fp; }
  const body = { path: st.path, content: text, encoding: ed.encoding, eol: ed.eol, expected_rev: expected, save_id: ed.saveId };
  ed.status = 'saving';
  _sync(container);
  let out;
  try {
    out = await withWriteElevation(() => _putOnce(body),
      t('파일 저장은 높은 권한 동작입니다. 웹 로그인 비밀번호를 확인하면 이 기기에서 15분 동안 다시 묻지 않습니다.'));
  } catch (e) {
    ed.status = 'failed';
    if (e && e.data && e.data.error === 'conflict') {
      ed.saveId = null;
      await _enterConflict(container, st, e.data.rev);
      _sync(container);
      return false;
    }
    if (e && e.data && e.data.error === 'unencodable') e.data.encoding = ed.encoding;
    if (e && e.data && e.data.error && e.data.error !== 'fs_unavailable') ed.saveId = null;   // 끝난 실패 — 다음 저장은 새 id
    _toast(writeErrorText(e), 'error');
    if (e && e.data && e.data.error === 'unencodable' && e.data.line) {
      h.jumpTo(e.data.line, e.data.col);
    }
    _sync(container);
    return false;
  }
  if (!out) { ed.status = 'idle'; _sync(container); return false; }   // 비밀번호를 취소했다
  ed.saveId = null;
  ed.base = text;
  ed.rev = out.rev;
  ed.savedRev = out.rev;
  ed.savedAt = new Date();
  ed.conflict = null;
  ed.compare = false;
  ed.status = 'saved';
  if (st.meta) { st.meta.rev = out.rev; st.meta.size = out.size; }
  st.etag = null;          // 다음 다시 읽기는 조건부가 아니라 새로 — 저장한 판과 같으면 refreshEditing이 무시한다
  st.probe = null;
  _recompute(container);
  if (out.durable === false) _toast(t('저장했지만 디스크 기록 확인이 끝나지 않았습니다 — 파일은 이미 바뀌었습니다'), 'warn');
  _sync(container);
  setTimeout(() => { if (ed.status === 'saved') _sync(container); }, SAVED_FLASH_MS);
  return true;
}

async function _enterConflict(container, st, rev) {
  const ed = container._vtEdit;
  try {
    const disk = await _diskText(st);
    ed.conflict = { rev: disk.meta.rev || rev || null, text: disk.text, at: new Date() };
  } catch (_) {
    ed.conflict = { rev: rev || null, text: null, at: new Date() };
  }
  ed.status = 'conflict';
  ed.compare = false;
}

/**
 * 편집 중 다시 읽기(감시 알림·새로고침). 문서를 덮지 않는다. 돌려주는 것은 file.js `_refreshDoc`와 같은 낱말.
 */
export async function refreshEditing(container, st) {
  const ed = container._vtEdit;
  let disk;
  try {
    disk = await _diskText(st);
  } catch (e) {
    if (e && e.status === 404) {
      ed.conflict = { rev: null, text: '', at: new Date(), gone: true };
      ed.status = 'conflict';
      _sync(container);
      return 'same';
    }
    throw e;
  }
  if (container._vtDoc !== st || !ed.on) return 'stale';
  const rev = disk.meta.rev;
  if (rev && (rev === ed.rev || rev === ed.savedRev)) return 'same';
  if (disk.text === ed.base && !disk.meta.binary) {
    ed.rev = rev;        // 시각만 바뀐 저장(내용 같음)
    if (st.meta) st.meta.rev = rev;
    return 'same';
  }
  if (!isDirty(container)) {
    // 고친 것이 없으면 조용히 따라간다.
    container._vtEditor.setText(disk.text);
    ed.base = disk.text;
    ed.rev = rev;
    st.meta = { ...st.meta, rev, size: disk.meta.size };
    _recompute(container);
    _sync(container);
    return 'ok';
  }
  ed.conflict = { rev, text: disk.text, at: new Date() };
  ed.status = 'conflict';
  _sync(container);
  return 'same';
}

// --- 보관 · 이어 열기 ------------------------------------------------------------------------

/** 칸이 사라지기 전에 고친 내용을 보관한다(disposeViewer가 부른다). */
export function stash(container) {
  const ed = container && container._vtEdit;
  const st = container && container._vtDoc;
  if (!ed || !ed.on || !st) return;
  if (!isDirty(container) && !ed.conflict) return;
  _drafts.set(_key(st.host, st.path), { text: container._vtEditor.getText(), base: ed.base, rev: ed.rev });
  _toast(t('저장하지 않은 편집을 보관했습니다 — 같은 파일을 다시 열면 이어서 고칩니다'));
}

/** 이 파일에 보관한 편집이 있으면 편집 모드로 이어 연다(file.js가 열기를 마친 뒤 부른다). */
export function restore(container, st, sync) {
  const k = _key(st.host, st.path);
  const draft = _drafts.get(k);
  if (!draft || st.mode !== 'head') return false;
  if (!editability(st, container).ok) return false;
  _drafts.delete(k);
  return startEdit(container, st, sync, draft);
}

/** 시험용. */
export function _draftCount() { return _drafts.size; }

// --- 비교(줄 단위) ---------------------------------------------------------------------------

/**
 * 줄 단위 비교. 앞뒤 공통 줄을 빼고, 가운데는 LCS. 가운데가 DIFF_MAX_LINES를 넘으면 null(너무 커서 비교하지 않는다).
 * 돌려주는 것: `[{kind: 'same'|'del'|'add', a: 줄 번호|null, b: 줄 번호|null, text}]` — a는 디스크, b는 내 편집.
 */
export function lineDiff(left, right) {
  const A = String(left).split('\n');
  const B = String(right).split('\n');
  let pre = 0;
  while (pre < A.length && pre < B.length && A[pre] === B[pre]) pre += 1;
  let suf = 0;
  while (suf < A.length - pre && suf < B.length - pre && A[A.length - 1 - suf] === B[B.length - 1 - suf]) suf += 1;
  const a = A.slice(pre, A.length - suf);
  const b = B.slice(pre, B.length - suf);
  if (a.length > DIFF_MAX_LINES || b.length > DIFF_MAX_LINES) return null;
  const n = a.length;
  const m = b.length;
  const W = m + 1;
  const L = new Uint32Array((n + 1) * W);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      L[i * W + j] = a[i] === b[j] ? L[(i + 1) * W + j + 1] + 1 : Math.max(L[(i + 1) * W + j], L[i * W + j + 1]);
    }
  }
  const out = [];
  for (let k = 0; k < pre; k += 1) out.push({ kind: 'same', a: k + 1, b: k + 1, text: A[k] });
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { out.push({ kind: 'same', a: pre + i + 1, b: pre + j + 1, text: a[i] }); i += 1; j += 1; }
    else if (i < n && (j >= m || L[(i + 1) * W + j] >= L[i * W + j + 1])) { out.push({ kind: 'del', a: pre + i + 1, b: null, text: a[i] }); i += 1; }
    else { out.push({ kind: 'add', a: null, b: pre + j + 1, text: b[j] }); j += 1; }
  }
  for (let k = 0; k < suf; k += 1) {
    out.push({ kind: 'same', a: A.length - suf + k + 1, b: B.length - suf + k + 1, text: A[A.length - suf + k] });
  }
  return out;
}

/** 비교 결과에서 바뀐 곳 주변만(앞뒤 3줄) 남기고 사이는 `gap`으로. */
export function diffHunks(rows, context = 3) {
  const keep = new Uint8Array(rows.length);
  rows.forEach((r, i) => {
    if (r.kind === 'same') return;
    for (let k = Math.max(0, i - context); k <= Math.min(rows.length - 1, i + context); k += 1) keep[k] = 1;
  });
  const out = [];
  let gap = 0;
  rows.forEach((r, i) => {
    if (keep[i]) {
      if (gap) { out.push({ kind: 'gap', count: gap }); gap = 0; }
      out.push(r);
    } else gap += 1;
  });
  if (gap) out.push({ kind: 'gap', count: gap });
  return out;
}

// --- 그리기 ------------------------------------------------------------------------------------

function _btn(label, cls, onClick, ic) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `vt-vw-bar-btn${cls ? ` ${cls}` : ''}`;
  if (ic) b.insertAdjacentHTML('afterbegin', icon(ic, 14));
  b.append(document.createTextNode(label));
  b.addEventListener('click', onClick);
  return b;
}

function _chip(text, cls, ic) {
  const s = document.createElement('span');
  s.className = `vt-vw-ss ${cls}`;
  s.setAttribute('role', 'status');
  if (ic) s.insertAdjacentHTML('afterbegin', icon(ic, 14));
  s.append(document.createTextNode(text));
  return s;
}

/** 위 띠에 편집 조작을 붙인다(file.js `_syncTopBar`). 붙였으면 true. */
export function decorateBar(container, st, bar) {
  const h = container._vtEditor;
  if (!h || typeof h.setEditable !== 'function' || !st.meta || st.meta.image || st.meta.binary) return false;
  const ed = container._vtEdit;
  const tools = document.createElement('span');
  tools.className = 'vt-vw-edit-tools';
  if (!ed || !ed.on) {
    const can = editability(st, container);
    const edit = _btn(t('편집'), '', () => {
      startEdit(container, st, container._vtEditSync);
    }, can.ok ? 'square-pen' : 'lock');
    edit.classList.add('vt-vw-edit-btn');
    if (!can.ok) {
      edit.setAttribute('aria-disabled', 'true');
      edit.title = can.reason;
      edit.dataset.tip = can.reason;
    }
    tools.appendChild(edit);
    if (!writeBlockedReason(st.host)) {
      tools.appendChild(_btn(t('이름 바꾸기'), 'ghost', async () => {
        const to = await renameDialog(st.path, st.host);
        if (to && typeof window.openFile === 'function') window.openFile(to);
      }, 'pencil'));
      tools.appendChild(_btn(t('휴지통으로 이동'), 'ghost danger', async () => {
        if (await trashDialog(st.path, st.host, st.meta && st.meta.rev)) {
          container.replaceChildren();
          const msg = document.createElement('div');
          msg.className = 'vt-vw-empty';
          msg.textContent = t('휴지통으로 옮겼습니다. 휴지통에서 되돌릴 수 있습니다.');
          container.appendChild(msg);
        }
      }, 'trash-2'));
    } else {
      const why = document.createElement('span');
      why.className = 'vt-vw-bar-warn';
      why.textContent = writeBlockedReason(st.host);
      tools.appendChild(why);
    }
    bar.appendChild(tools);
    return true;
  }
  // 편집 중: 상태 칩 · 저장(주 버튼) · 편집 끝내기
  if (ed.status === 'saving') tools.appendChild(_chip(t('저장 중…'), 'ing'));
  else if (ed.status === 'saved' && !ed.dirty) tools.appendChild(_chip(t('저장됨 {time}', { time: _hhmm(ed.savedAt || new Date()) }), 'ok', 'check'));
  else if (ed.status === 'conflict' || ed.conflict) tools.appendChild(_chip(t('충돌'), 'warn', 'alert-triangle'));
  else if (ed.status === 'failed') tools.appendChild(_chip(t('저장 실패'), 'bad', 'alert-triangle'));
  else if (ed.dirty) tools.appendChild(_chip(t('편집 중 · 저장 안 됨'), 'dirty'));
  else tools.appendChild(_chip(t('편집 중'), 'idle'));
  const mac = /Mac|iPhone|iPad/.test((navigator && navigator.platform) || '');
  const save = _btn(t('저장'), 'primary', () => { saveEdit(container, st).catch(() => {}); });
  save.appendChild(Object.assign(document.createElement('kbd'), { textContent: mac ? '⌘S' : 'Ctrl+S' }));
  save.classList.add('vt-vw-save');
  if (ed.status === 'saving' || !!ed.conflict) save.disabled = true;
  tools.appendChild(save);
  tools.appendChild(_btn(t('편집 끝내기'), 'ghost', () => { stopEdit(container).catch(() => {}); }, 'x'));
  bar.appendChild(tools);
  return true;
}

/** 충돌 배너(F05-D)와 비교(F05-D2). 위 띠 바로 아래. 없으면 지운다. */
export function syncConflict(container, st) {
  let ban = container.querySelector(':scope > .vt-vw-conflict');
  let cmp = container.querySelector(':scope > .vt-vw-compare');
  const ed = container._vtEdit;
  if (!ed || !ed.on || !ed.conflict) { if (ban) ban.remove(); if (cmp) cmp.remove(); return; }
  if (!ban) {
    ban = document.createElement('div');
    ban.className = 'vt-vw-conflict';
    ban.setAttribute('role', 'alert');
    const bar = container.querySelector(':scope > .vt-vw-bar');
    if (bar) bar.after(ban); else container.prepend(ban);
  }
  ban.replaceChildren();
  const c = ed.conflict;
  const tx = document.createElement('span');
  tx.className = 'vt-vw-conflict-tx';
  const b = document.createElement('b');
  b.textContent = c.gone ? t('다른 곳에서 이 파일이 지워졌거나 옮겨졌습니다') : t('다른 곳에서 이 파일이 바뀌었습니다');
  const s = document.createElement('span');
  s.textContent = ` ${t('({time}, 디스크 rev …{rev}). 내가 고친 내용은 그대로 있습니다.', {
    time: _hhmm(c.at || new Date()), rev: String(c.rev || '-').slice(-4),
  })}`;
  tx.append(b, s);
  ban.appendChild(tx);
  const acts = document.createElement('span');
  acts.className = 'vt-vw-conflict-acts';
  const sync = () => _sync(container);
  if (c.text != null) {
    const cmpBtn = _btn(ed.compare ? t('비교 닫기') : t('비교'), '', () => { ed.compare = !ed.compare; sync(); }, 'columns-2');
    acts.appendChild(cmpBtn);
    acts.appendChild(_btn(t('다시 불러오기'), '', async () => {
      const ok = await confirmDialog(t('내가 고친 내용을 버리고 디스크의 새 내용을 불러옵니다.'),
        { title: t('다시 불러오기'), okLabel: t('버리고 불러오기'), danger: true });
      if (!ok) return;
      container._vtEditor.setText(c.text);
      ed.base = c.text;
      ed.rev = c.rev;
      if (st.meta) st.meta.rev = c.rev;
      ed.conflict = null;
      ed.compare = false;
      ed.status = 'idle';
      _recompute(container);
      sync();
    }));
  }
  acts.appendChild(_btn(t('내 것으로 덮어쓰기'), 'danger', async () => {
    const ok = await confirmDialog(t('디스크의 바뀐 내용을 지우고 내 편집으로 저장합니다.'),
      { title: t('내 것으로 덮어쓰기'), okLabel: t('덮어쓰기'), danger: true });
    if (!ok) return;
    if (c.gone) {
      // 사라진 파일 — 없을 때만 다시 만든다(expected_rev null, 서버가 O_EXCL로).
      ed.conflict = { ...c, rev: null };
    }
    await saveEdit(container, st, { overwrite: true });
  }));
  ban.appendChild(acts);
  if (!ed.compare || c.text == null) { if (cmp) cmp.remove(); return; }
  if (!cmp) {
    cmp = document.createElement('div');
    cmp.className = 'vt-vw-compare';
    ban.after(cmp);
  }
  _paintCompare(cmp, c.text, container._vtEditor.getText());
}

function _paintCompare(box, disk, mine) {
  box.replaceChildren();
  const head = document.createElement('div');
  head.className = 'vt-vw-compare-head';
  const l = document.createElement('span');
  l.textContent = t('디스크(새 버전)');
  const r = document.createElement('span');
  r.textContent = t('내 편집 · 저장 안 됨');
  head.append(l, r);
  box.appendChild(head);
  const rows = lineDiff(disk, mine);
  if (!rows) {
    const msg = document.createElement('div');
    msg.className = 'vt-vw-note';
    msg.textContent = t('바뀐 곳이 너무 많아 줄 단위로 비교하지 않습니다.');
    box.appendChild(msg);
    return;
  }
  const hunks = diffHunks(rows);
  if (!hunks.some((x) => x.kind === 'add' || x.kind === 'del')) {
    const msg = document.createElement('div');
    msg.className = 'vt-vw-note';
    msg.textContent = t('내용이 같습니다.');
    box.appendChild(msg);
    return;
  }
  const grid = document.createElement('div');
  grid.className = 'vt-vw-compare-grid';
  for (const x of hunks) {
    const row = document.createElement('div');
    row.className = `vt-vw-cmp-row ${x.kind}`;
    if (x.kind === 'gap') {
      row.textContent = t('… 같은 줄 {n}개', { n: x.count });
    } else {
      const ln = document.createElement('span');
      ln.className = 'ln';
      ln.textContent = x.kind === 'add' ? `${x.b}` : `${x.a}`;
      const sg = document.createElement('span');
      sg.className = 'sg';
      sg.textContent = x.kind === 'add' ? '+' : x.kind === 'del' ? '−' : '';
      const tx = document.createElement('span');
      tx.className = 'tx';
      tx.textContent = x.text;
      row.append(ln, sg, tx);
    }
    grid.appendChild(row);
  }
  box.appendChild(grid);
  const foot = document.createElement('div');
  foot.className = 'vt-vw-note';
  foot.textContent = t('− 는 디스크에만 있는 줄(덮어쓰면 사라짐), + 는 내 편집에만 있는 줄입니다.');
  box.appendChild(foot);
}
