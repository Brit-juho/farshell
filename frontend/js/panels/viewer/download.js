// 탐색 중인 실제 파일 내려받기 — `GET /api/fs/download?path&rev`(파일 개편 계획 4.7, 결정 D4·D7, 시안 F08).
// 진입점: 파일 트리 행 메뉴(files/FileTree.tsx → tree.js), 뷰어 머리줄(overlay.js — 폰 전체 화면 시트 포함),
// 페인 뷰어의 떠 있는 버튼(layout/panes.js), 바이너리·PDF 빈 상태(file.js).
//
// - **이 컴퓨터의 파일만**(D7). 지금 화면이 다른 호스트를 보고 있거나(호스트 전환) 파일이 다른 호스트의 것이면
//   요청하지 않고 이유를 알린다. 서버도 중계 허용 목록에 이 경로를 두지 않아 원격으로는 닿지 않는다.
// - 본 판(rev)을 같이 보낸다 — 그 사이 디스크가 바뀌었으면 서버가 409를 주고, 여기서 「바뀌어 받지 않았다」고 알린다.
// - 64MB 이하는 fetch로 받아 진행률을 토스트에 보이고(1MB 이상일 때), 다 받으면 파일로 저장시킨다. 실패(409·413·
//   403, 도중 끊김)는 그 자리에서 이유를 말한다. 더 큰 파일은 메모리에 담지 않는다 — 응답 머리만 보고(오류면
//   여기서 알림) 브라우저 다운로드로 넘긴다(진행률은 브라우저가 보인다).
import { vtUrl } from '../../core/api.js';
import { isRelayed } from '../../core/host-route.js';
import { isCrossHost } from './file-load.js';
import { t } from '../../core/i18n.js';

export const FETCH_MAX = 64 * 1024 * 1024;     // 이보다 크면 브라우저 다운로드로 넘긴다(메모리에 담지 않는다)
export const PROGRESS_MIN = 1024 * 1024;       // 이보다 작으면 진행 토스트 없이 끝만 알린다(시안 F08)
const PROGRESS_EVERY_MS = 200;

function _fmt(n) {
  if (n == null || !Number.isFinite(n)) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

function _toast(msg, kind, opts) {
  if (typeof window.showToast === 'function') window.showToast(msg, kind, opts);
}

/** 내려받을 수 없는 이유(원격). 받을 수 있으면 null. `host`는 파일이 있는 호스트(없으면 지금 화면의 호스트). */
export function downloadBlockedReason(host) {
  if (isRelayed() || isCrossHost(host)) return t('다운로드는 이 컴퓨터의 파일만 됩니다 — 원격 호스트 파일은 받을 수 없습니다');
  return null;
}

export function downloadUrl(path, rev) {
  const q = new URLSearchParams({ path });
  if (rev) q.set('rev', rev);
  return vtUrl(`/api/fs/download?${q.toString()}`);
}

function _baseName(path) {
  return String(path).split('/').filter(Boolean).pop() || 'download';
}

// 저장시키기 — 같은 출처 `<a download>`. blob 주소는 클릭 뒤 잠시 두었다 거둔다(바로 거두면 일부 브라우저가 못 받는다).
function _save(href, name, revoke) {
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  if (revoke) setTimeout(() => { try { URL.revokeObjectURL(href); } catch (_) { /* 이미 거둠 */ } }, 30000);
}

async function _errorText(res, name) {
  let body = null;
  try { body = await res.json(); } catch (_) { /* JSON이 아니다 */ }
  if (res.status === 409) return { kind: 'warn', msg: t('{name}: 파일이 바뀌어 받지 않았습니다 — 보고 있던 내용과 디스크가 다릅니다. 새로고침한 뒤 다시 받으세요.', { name }) };
  if (res.status === 413) return { kind: 'error', msg: t('{name}: 너무 커서 받을 수 없습니다 ({size}, 상한 {max})', { name, size: _fmt(body && body.size), max: _fmt(body && body.max) }) };
  const reason = (body && (body.reason || body.error)) || `HTTP ${res.status}`;
  return { kind: 'error', msg: t('{name} 다운로드 실패: {msg}', { name, msg: reason }) };
}

/**
 * 파일 하나를 내려받는다. opts: `host`(파일이 있는 호스트), `rev`(화면에 보이는 판 — 다르면 409), `size`(알면).
 * 돌려주는 값: 'blocked' | 'error' | 'handoff'(브라우저 다운로드로 넘김) | 'done'.
 */
export async function downloadFsPath(path, opts = {}) {
  if (!path) return 'error';
  const name = _baseName(path);
  const blocked = downloadBlockedReason(opts.host);
  if (blocked) { _toast(blocked, 'warn'); return 'blocked'; }
  const url = downloadUrl(path, opts.rev);
  const key = `fs-download:${path}`;
  const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
  const headers = {};
  if (window.VT_TOKEN) headers.Authorization = `Bearer ${window.VT_TOKEN}`;
  let res;
  try {
    res = await fetch(url, { credentials: 'same-origin', headers, signal: ctrl ? ctrl.signal : undefined });
  } catch (e) {
    _toast(t('{name} 다운로드 실패: {msg}', { name, msg: (e && e.message) || String(e) }), 'error', { key });
    return 'error';
  }
  if (!res.ok) {
    const { kind, msg } = await _errorText(res, name);
    _toast(msg, kind, { key });
    return 'error';
  }
  const total = Number(res.headers.get('content-length')) || Number(opts.size) || 0;
  const reader = res.body && typeof res.body.getReader === 'function' ? res.body.getReader() : null;
  if (total > FETCH_MAX || !reader) {
    // 큰 파일 — 머리만 확인하고 끊은 뒤 브라우저 다운로드로 넘긴다(같은 rev를 보내므로 그 사이 바뀌면 받지 않는다).
    if (ctrl) ctrl.abort();
    _save(url, name, false);
    _toast(t('{name} 받기를 브라우저에 넘겼습니다 · {size}', { name, size: _fmt(total) }), 'info', { key });
    return 'handoff';
  }
  const chunks = [];
  let done = 0;
  let last = 0;
  const showProgress = total >= PROGRESS_MIN;
  try {
    for (;;) {
      const { value, done: end } = await reader.read();
      if (end) break;
      chunks.push(value);
      done += value.byteLength;
      const now = Date.now();
      if (showProgress && now - last >= PROGRESS_EVERY_MS) {
        last = now;
        const pct = Math.min(100, Math.floor((done / total) * 100));
        _toast(t('{name} 받는 중 · {done} / {total} · {pct}%', { name, done: _fmt(done), total: _fmt(total), pct }),
          'info', { key, duration: 0 });
      }
    }
  } catch (e) {
    _toast(t('{name} 받기가 끊겼습니다 — 그 사이 파일이 바뀌었거나 연결이 끊겼습니다', { name }), 'error', { key });
    return 'error';
  }
  if (total && done !== total) {
    _toast(t('{name} 받기가 끊겼습니다 — 그 사이 파일이 바뀌었거나 연결이 끊겼습니다', { name }), 'error', { key });
    return 'error';
  }
  const blob = new Blob(chunks, { type: 'application/octet-stream' });
  _save(URL.createObjectURL(blob), name, true);
  _toast(t('{name} 받음 · {size}', { name, size: _fmt(done) }), 'success', { key });
  return 'done';
}
