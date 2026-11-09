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
// - **동시에 3개까지**(시안 F08). 넘는 것은 줄을 세우고 「대기 중」 카드(취소 가능)를 띄운다. 앞의 것이 끝나면 다음을
//   시작한다. 진행 카드와 대기 카드에는 「취소」가 있다 — AbortController로 요청을 끊으면 서버는 연결이 끊긴 것을 보고
//   스트림을 멈추고 fd를 닫는다(server/fs_stream.py `FdStreamingResponse`). 받던 조각은 버리고 아무것도 저장하지 않는다.
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
  if (typeof window.showToast === 'function') return window.showToast(msg, kind, opts);
  return null;
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

export const MAX_ACTIVE = 3;                    // 동시에 받는 수(시안 F08). 넘으면 줄을 세운다

let _seq = 0;
let _active = 0;
const _queue = [];                              // 기다리는 일 — { run, job }

function _cancelAction(job) {
  return [{ label: t('취소'), onClick: () => job.cancel() }];
}

// 자리가 나면 줄의 앞에서부터 시작한다. 기다리다 취소된 일은 이미 줄에서 빠져 있다.
function _pump() {
  while (_active < MAX_ACTIVE && _queue.length) {
    const next = _queue.shift();
    next.run();
  }
}

/** 지금 받는 수와 기다리는 수(시험·진단용). */
export function downloadQueueState() {
  return { active: _active, queued: _queue.length };
}

/**
 * 파일 하나를 내려받는다. opts: `host`(파일이 있는 호스트), `rev`(화면에 보이는 판 — 다르면 409), `size`(알면).
 * 돌려주는 값: 'blocked' | 'error' | 'cancelled' | 'handoff'(브라우저 다운로드로 넘김) | 'done'.
 * 이미 3개를 받는 중이면 줄을 서고, 차례가 와서 끝났을 때 값을 돌려준다.
 */
export function downloadFsPath(path, opts = {}) {
  if (!path) return Promise.resolve('error');
  const blocked = downloadBlockedReason(opts.host);
  if (blocked) { _toast(blocked, 'warn'); return Promise.resolve('blocked'); }
  const name = _baseName(path);
  _seq += 1;
  const job = {
    key: `fs-download:${_seq}`,
    name,
    ctrl: typeof AbortController === 'function' ? new AbortController() : null,
    cancelled: false,
    queued: null,
    cancel() {
      if (job.cancelled) return;
      job.cancelled = true;
      if (job.queued) {
        // 아직 시작하지 않았다 — 줄에서 빼고 끝낸다(요청을 보낸 적이 없다).
        const i = _queue.indexOf(job.queued);
        if (i >= 0) _queue.splice(i, 1);
        const q = job.queued;
        job.queued = null;
        _toast(t('{name} 받기를 취소했습니다', { name }), 'info', { key: job.key });
        q.resolve('cancelled');
        return;
      }
      if (job.ctrl) job.ctrl.abort();
    },
  };
  return new Promise((resolve) => {
    const run = () => {
      job.queued = null;
      _active += 1;
      _download(path, opts, job)
        .catch(() => 'error')
        .then((out) => { _active -= 1; resolve(out); _pump(); });
    };
    if (_active < MAX_ACTIVE) { run(); return; }
    job.queued = { run, resolve, job };
    _queue.push(job.queued);
    _toast(t('{name} 대기 중', { name }), 'info', {
      key: job.key, duration: 0,
      detail: t('동시에 {n}개까지 받습니다 — 앞의 것이 끝나면 시작합니다', { n: MAX_ACTIVE }),
      actions: _cancelAction(job),
    });
  });
}

function _cancelledToast(job) {
  _toast(t('{name} 받기를 취소했습니다', { name: job.name }), 'info', { key: job.key });
  return 'cancelled';
}

async function _download(path, opts, job) {
  const { name, key, ctrl } = job;
  const url = downloadUrl(path, opts.rev);
  const headers = {};
  if (window.VT_TOKEN) headers.Authorization = `Bearer ${window.VT_TOKEN}`;
  let res;
  try {
    res = await fetch(url, { credentials: 'same-origin', headers, signal: ctrl ? ctrl.signal : undefined });
  } catch (e) {
    if (job.cancelled) return _cancelledToast(job);
    _toast(t('{name} 다운로드 실패: {msg}', { name, msg: (e && e.message) || String(e) }), 'error', { key });
    return 'error';
  }
  if (job.cancelled) { if (ctrl) ctrl.abort(); return _cancelledToast(job); }
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
  let card = null;
  const paint = () => {
    // 사용자가 진행 카드를 닫았으면(× · 카드 누름) 다시 띄우지 않는다 — 받기는 계속되고 끝맺음만 알린다.
    if (card && (card._vtLeaving || !card.isConnected)) return;
    const pct = Math.min(100, Math.floor((done / total) * 100));
    card = _toast(t('{name} 받는 중 · {done} / {total} · {pct}%', { name, done: _fmt(done), total: _fmt(total), pct }),
      'info', { key, duration: 0, actions: _cancelAction(job) });
  };
  // 취소 단추는 받기 시작한 순간부터 보인다(첫 조각이 늦게 오는 느린 연결에서도 끊을 수 있게).
  if (showProgress) { last = Date.now(); paint(); }
  try {
    for (;;) {
      const { value, done: end } = await reader.read();
      if (end) break;
      if (job.cancelled) break;
      chunks.push(value);
      done += value.byteLength;
      const now = Date.now();
      if (showProgress && now - last >= PROGRESS_EVERY_MS) {
        last = now;
        paint();
      }
    }
  } catch (e) {
    if (job.cancelled) return _cancelledToast(job);
    _toast(t('{name} 받기가 끊겼습니다 — 그 사이 파일이 바뀌었거나 연결이 끊겼습니다', { name }), 'error', { key });
    return 'error';
  }
  if (job.cancelled) {
    try { await reader.cancel(); } catch (_) { /* 이미 끊김 */ }
    return _cancelledToast(job);
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
