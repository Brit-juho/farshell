// 파일 쓰기 호출 — 저장 `PUT /api/fs/file`, 새 파일·새 폴더, 이름 바꾸기, 휴지통 삭제(파일 개편 단계 8, 폴더는 후속).
// 계약은 API.md. 폴더 요청은 `kind: 'dir'`를 밝힌다(서버는 밝히지 않은 폴더 요청을 거부한다).
//
// - **이 컴퓨터의 파일만**(결정 D7). 화면이 다른 호스트를 보고 있거나 파일이 다른 호스트의 것이면 요청하지 않고
//   이유를 돌려준다(`writeBlockedReason`). 서버도 중계 허용 목록에 쓰기를 두지 않는다.
// - 서버는 **브라우저 로그인 쿠키만** 사람으로 친다. `?token=`·Authorization이 실리면 403이라 vtFetch·apiFetch를 쓰지
//   않고 쿠키만 싣는 요청을 보낸다(share-api.js와 같은 이유). 마감·도달 판정은 timedFetch가 붙인다. 호스트 중계를 타지
//   않게 주소를 바꾸지 않는다.
// - 승격(웹 로그인 비밀번호 재확인, 15분, 결정 D1): 401 `elevation_required`면 비밀번호를 한 번 묻고 **같은 요청**을
//   다시 보낸다(저장은 같은 save_id — 두 번 쓰지 않는다).
import { timedFetch, isTimeoutError } from '../../core/api.js';
import { isRelayed } from '../../core/host-route.js';
import { confirmDialog, promptDialog } from '../../core/dialog.js';
import { elevate } from '../../authz/elevate.js';
import { needsElevation } from '../../authz/api.js';
import { isCrossHost } from './file-load.js';
import { t } from '../../core/i18n.js';

export const SAVE_MAX_BYTES = 2 * 1024 * 1024;   // 서버 fs_write.MAX_SAVE_BYTES와 같다(편집 저장 상한)

/** 쓸 수 없는 이유(원격). 쓸 수 있으면 null. `host`는 파일이 있는 호스트(없으면 지금 화면의 호스트). */
export function writeBlockedReason(host) {
  if (isRelayed() || isCrossHost(host)) return t('원격 호스트 파일은 읽기만 됩니다 — 편집 · 새 파일 · 이름 바꾸기 · 삭제는 이 컴퓨터의 파일에서만 됩니다');
  return null;
}

/** 쿠키만 싣는 쓰기 요청. 2xx가 아니면 `.status`·`.data`·`.code`가 붙은 Error를 던진다. */
export async function writeFetch(path, method, body, opts = {}) {
  const res = await timedFetch(`${window.API_BASE || ''}${path}`, {
    method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    timeout: opts.timeout,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (!res.ok) {
    const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
    e.status = res.status; e.data = data; e.code = data && data.error;
    throw e;
  }
  return data;
}

/** 승격이 필요하면 한 번 묻고 같은 호출을 다시 한다. 취소하면 null. */
export async function withWriteElevation(call, message) {
  try {
    return await call();
  } catch (e) {
    if (!needsElevation(e)) throw e;
    if (!(await elevate(message))) return null;
    return call();
  }
}

/** 클라이언트가 만드는 저장 id(서버는 8–64자 영숫자·-·_만 받는다). */
export function newSaveId() {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** 실패 응답을 사람이 읽을 한 줄로(실패 계약, 시안 F05-C). */
export function writeErrorText(e) {
  if (isTimeoutError(e)) return t('응답이 없습니다 — 저장됐는지 확인하려면 다시 저장하세요(같은 저장은 두 번 쓰지 않습니다)');
  const d = (e && e.data) || {};
  switch (d.error) {
    case 'conflict': return t('저장 전에 파일이 바뀌었습니다');
    case 'too_large': return t('2 MB가 넘어 저장하지 않았습니다 — 편집 저장 상한은 2 MB입니다');
    case 'no_space': return t('디스크가 가득 차 저장하지 못했습니다 — 원래 파일은 그대로입니다');
    case 'not_writable': return d.reason || t('이 위치에 쓸 수 없습니다 (읽기 전용이거나 권한 없음)');
    case 'unencodable': return t('{encoding}로 바꿀 수 없는 글자가 있습니다 — {line}줄 {col}열 「{ch}」', {
      encoding: String(d.encoding || '').toUpperCase() || t('이 인코딩'), line: d.line, col: d.col, ch: d.char || '',
    });
    case 'hardlinked': return t('하드 링크가 걸린 파일입니다 — 바꿔 쓰면 다른 이름의 링크가 끊겨 저장을 막았습니다');
    case 'not_editable': return d.reason || t('이 파일은 편집할 수 없습니다');
    case 'exists': return t('같은 이름이 이미 있습니다 (덮어쓰지 않음)');
    case 'trash_unavailable': return d.reason || t('휴지통으로 옮기지 못해 삭제하지 않았습니다');
    case 'into_itself': return t('폴더를 자기 안으로 옮길 수 없습니다');
    case 'is_symlink': return t('심링크는 이 작업을 할 수 없습니다');
    case 'mount_point': return t('다른 디스크가 연결된 폴더(마운트 지점)는 옮길 수 없습니다');
    case 'denied': return d.reason || t('이 위치에는 쓸 수 없습니다');
    case 'human_session_required': return t('브라우저에서 로그인한 사람만 파일을 쓸 수 있습니다');
    case 'password_required': return t('웹 로그인 비밀번호를 설정해야 파일을 쓸 수 있습니다');
    case 'fs_unavailable': return d.reason || t('파일 작업이 밀려 있습니다 — 잠시 뒤 다시 시도하세요');
    default: return d.reason || (e && e.message) || t('파일을 쓰지 못했습니다');
  }
}

/**
 * 저장 실패 칩 옆에 붙일 짧은 원인(폰 한 줄용). 서버가 주는 한국어 `reason`은 그대로 쓰지 않는다 — 영어 화면에서
 * 한국어가 섞이기 때문에 오류 코드(없으면 HTTP 상태)를 `t()` 문구로 옮긴다. 모르는 코드는 일반 문구에 코드를 붙인다.
 */
export function writeErrorShort(e) {
  if (isTimeoutError(e)) return t('응답이 없습니다');
  const d = (e && e.data) || {};
  const code = (e && e.code) || d.error || '';
  switch (code) {
    case 'human_session_required': return t('브라우저 로그인이 필요합니다');
    case 'password_required': return t('웹 로그인 비밀번호를 설정하세요');
    case 'elevation_required': return t('비밀번호 재확인이 필요합니다');
    case 'unauthorized': return t('로그인이 필요합니다');
    case 'forbidden': return t('접근이 허용되지 않습니다');
    case 'rate_limited': return t('요청이 너무 많습니다');
    case 'audit_unavailable': return t('감사 기록을 쓸 수 없습니다');
    case 'lease_conflict': return t('다른 세션이 쓰는 경로입니다');
    case 'lease_unavailable': return t('폴더 점유 저장소를 읽을 수 없습니다');
    case 'changed': return t('경로가 바뀌었습니다');
    case 'conflict': return t('저장 전에 파일이 바뀌었습니다');
    case 'save_id_reused': return t('저장 번호가 겹쳤습니다 — 다시 저장하세요');
    case 'busy':
    case 'in_progress': return t('다른 쓰기가 진행 중입니다');
    case 'fs_unavailable': return t('파일 작업이 밀려 있습니다');
    case 'too_large': return t('2 MB 초과');
    case 'no_space': return t('디스크가 가득 찼습니다');
    case 'not_writable': return t('쓸 수 없는 위치입니다');
    case 'denied': return t('쓰기가 금지된 위치입니다');
    case 'unencodable': return t('인코딩할 수 없는 글자가 있습니다');
    case 'hardlinked': return t('하드 링크 파일입니다');
    case 'not_editable': return t('편집할 수 없는 파일입니다');
    case 'not_found': return t('파일이 없습니다');
    case 'is_directory': return t('폴더입니다');
    case 'not_a_file': return t('일반 파일이 아닙니다');
    case 'is_symlink': return t('심링크입니다');
    case 'exists': return t('같은 이름이 있습니다');
    case 'write_failed': return t('파일을 쓰지 못했습니다');
    case 'bad_json':
    case 'bad_path':
    case 'bad_rev':
    case 'bad_save_id':
    case 'bad_content':
    case 'bad_encoding':
    case 'bad_eol':
    case 'bad_overwrite':
    case 'bad_kind':
    case 'bad_name': return t('잘못된 요청입니다 ({code})', { code });
    default: break;
  }
  const st = e && e.status;
  if (!code && st === 401) return t('로그인이 필요합니다');
  if (!code && st === 403) return t('접근이 허용되지 않습니다');
  if (!code && st === 413) return t('2 MB 초과');
  if (!code && !st) return t('서버에 닿지 않았습니다');
  return t('서버가 거절했습니다 ({code})', { code: code || (st ? `HTTP ${st}` : '?') });
}

function _toast(msg, kind) {
  if (typeof window.showToast === 'function') window.showToast(msg, kind);
}

const _dir = (p) => { const i = p.lastIndexOf('/'); return i > 0 ? p.slice(0, i) : '/'; };
const _base = (p) => p.slice(p.lastIndexOf('/') + 1);
const _join = (d, n) => (d.endsWith('/') ? d + n : `${d}/${n}`);

/** 이름 규칙(서버와 같다) — 틀리면 이유, 맞으면 null. */
export function nameProblem(name) {
  if (!name || !name.trim()) return t('이름이 비었습니다');
  if (name.includes('/') || name.includes('\\')) return t('이름에 「/」나 「\\」를 쓸 수 없습니다');
  if (name === '.' || name === '..') return t('「.」과 「..」은 이름으로 쓸 수 없습니다');
  if (/[\u0000-\u001f\u007f]/.test(name)) return t('이름에 제어 문자가 있습니다');
  return null;
}

/** 새 빈 파일(시안 F05-E). 만들었으면 그 경로, 아니면 null. `host`가 원격이면 이유만 알린다. */
export async function createFileDialog(dir, host) {
  const blocked = writeBlockedReason(host);
  if (blocked) { _toast(blocked, 'warn'); return null; }
  const name = await promptDialog(t('{dir}에 만들 파일 이름. 같은 이름이 있으면 만들지 않습니다(덮어쓰지 않음).', { dir: _join(dir, '') }),
    { title: t('새 파일'), okLabel: t('만들기') });
  if (name == null) return null;
  const bad = nameProblem(name);
  if (bad) { _toast(bad, 'warn'); return null; }
  try {
    const out = await withWriteElevation(() => writeFetch('/api/fs/new', 'POST', { dir, name }),
      t('파일을 만들려면 웹 로그인 비밀번호를 확인하세요. 확인하면 이 기기에서 15분 동안 다시 묻지 않습니다.'));
    if (!out) return null;
    _toast(t('{name}을(를) 만들었습니다', { name }));
    return out.path || _join(dir, name);
  } catch (e) {
    _toast(writeErrorText(e), 'error');
    return null;
  }
}

/** 새 빈 폴더. 만들었으면 그 경로, 아니면 null. `host`가 원격이면 이유만 알린다. */
export async function createFolderDialog(dir, host) {
  const blocked = writeBlockedReason(host);
  if (blocked) { _toast(blocked, 'warn'); return null; }
  const name = await promptDialog(t('{dir}에 만들 폴더 이름. 같은 이름이 있으면 만들지 않습니다.', { dir: _join(dir, '') }),
    { title: t('새 폴더'), okLabel: t('만들기') });
  if (name == null) return null;
  const bad = nameProblem(name);
  if (bad) { _toast(bad, 'warn'); return null; }
  try {
    const out = await withWriteElevation(() => writeFetch('/api/fs/mkdir', 'POST', { dir, name }),
      t('폴더를 만들려면 웹 로그인 비밀번호를 확인하세요. 확인하면 이 기기에서 15분 동안 다시 묻지 않습니다.'));
    if (!out) return null;
    _toast(t('{name} 폴더를 만들었습니다', { name }));
    return out.path || _join(dir, name);
  } catch (e) {
    _toast(writeErrorText(e), 'error');
    return null;
  }
}

/** 이름 바꾸기(같은 폴더 안). `opts.kind === 'dir'`면 폴더(덮어쓰지 않는다). 바꿨으면 새 경로, 아니면 null. */
export async function renameDialog(path, host, opts = {}) {
  const blocked = writeBlockedReason(host);
  if (blocked) { _toast(blocked, 'warn'); return null; }
  const dirKind = opts.kind === 'dir';
  const old = _base(path);
  const name = await promptDialog(t('새 이름. 같은 이름이 있으면 바꾸지 않습니다(덮어쓰지 않음).'),
    { title: dirKind ? t('폴더 이름 바꾸기') : t('이름 바꾸기'), okLabel: t('이름 바꾸기'), defaultValue: old });
  if (name == null || name === old) return null;
  const bad = nameProblem(name);
  if (bad) { _toast(bad, 'warn'); return null; }
  const to = _join(_dir(path), name);
  try {
    const body = dirKind ? { path, to, kind: 'dir' } : { path, to };
    const out = await withWriteElevation(() => writeFetch('/api/fs/rename', 'POST', body),
      t('이름을 바꾸려면 웹 로그인 비밀번호를 확인하세요. 확인하면 이 기기에서 15분 동안 다시 묻지 않습니다.'));
    if (!out) return null;
    _toast(t('이름을 바꿨습니다: {name}', { name }));
    return out.path || to;
  } catch (e) {
    _toast(writeErrorText(e), 'error');
    return null;
  }
}

/** 휴지통으로 이동(지우지 않는다, 결정 D2). `opts.kind === 'dir'`면 폴더를 안의 내용째. 옮겼으면 true. */
export async function trashDialog(path, host, rev, opts = {}) {
  const blocked = writeBlockedReason(host);
  if (blocked) { _toast(blocked, 'warn'); return false; }
  const dirKind = opts.kind === 'dir';
  const msg = dirKind
    ? t('{name} 폴더를 안의 파일과 함께 휴지통으로 옮깁니다. 휴지통에서 되돌릴 수 있습니다(macOS Finder 「되돌리기」).', { name: _base(path) })
    : t('{name}을(를) 휴지통으로 옮깁니다. 휴지통에서 되돌릴 수 있습니다(macOS Finder 「되돌리기」).', { name: _base(path) });
  const ok = await confirmDialog(msg, { title: t('휴지통으로 이동'), okLabel: t('휴지통으로 이동'), danger: true });
  if (!ok) return false;
  try {
    let body;
    if (dirKind) body = { path, kind: 'dir' };
    else body = rev ? { path, expected_rev: rev } : { path };
    const out = await withWriteElevation(() => writeFetch('/api/fs/delete', 'POST', body),
      t('휴지통으로 옮기려면 웹 로그인 비밀번호를 확인하세요. 확인하면 이 기기에서 15분 동안 다시 묻지 않습니다.'));
    if (!out) return false;
    _toast(t('휴지통으로 옮겼습니다: {name}', { name: _base(path) }));
    return true;
  } catch (e) {
    _toast(writeErrorText(e), 'error');
    return false;
  }
}
