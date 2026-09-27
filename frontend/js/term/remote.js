// N7/N39 3단계 — 원격 호스트의 tmux 세션을 이 화면의 탭/pane으로 연다.
//
// 로컬 attach(term/tmux-panel.js의 attachTmux)와의 차이는 **서버에 세션을 만들지
// 않는다**는 것 하나다. 원격 pane의 PTY는 상대 호스트(B)에 있고, 이 서버(A)는
// WS를 중계만 한다(routes/peer_proxy.py). 그래서 여기서 만드는 세션 id는 서버의
// session_store에 존재하지 않는 **클라이언트 쪽 식별자**다 —
// `remote:<host>:<tmux>` 형태라 로컬 세션 id(uuid)와 절대 겹치지 않고,
// layout/persist.js가 저장한 unreachable leaf의 키와도 같은 모양이다.
//
// 이 파일이 브라우저 전역(window.attachRemoteSession)에 함수를 하나 거는 이유:
// 지연 청크(shell/Rail.tsx)와 layout/panes.js의 「다시 시도」가 이 경로를 불러야
// 하는데, 그쪽은 상태 모듈을 정적 import 할 수 없다(ADR-26).
//
// 2026-09-28 — 원격 세션 **생성·종료·이름 바꾸기**와 「지금 어느 호스트를 보나」
// (host-view.js)가 여기 붙었다. 셋 다 이 서버의 `/api/hosts/{id}/sessions`를
// 거쳐 상대의 control 등급 엔드포인트로 간다(routes/hosts.py).
import { getSession, orderedSessionIds, setSessionTmuxName } from '../core/store.js';
import { addSession, revealSession, removeSession } from './session.js';
import { saveWorkspace } from './workspace.js';
import { showToast } from '../ui/toast.js';
import { vtFetch } from '../core/api.js';
import { confirmDialog } from '../core/dialog.js';
import { getTree, getActivePaneId, getActiveTabHostId, getTabHostSummaries,
  switchLayoutTab, openLayoutTab } from '../layout/store.js';
import { findNode } from '../layout/tree.js';
import { LOCAL_HOST, viewHost, firstSessionForHost, findRemoteSession, emptyTabForHost } from './host-view.js';

export function remoteSessionId(hostId, tmuxName) {
  return `remote:${hostId}:${tmuxName}`;
}

const _entries = () => orderedSessionIds().map((id) => [id, getSession(id)]);

// 호스트 id → 표시 이름. /api/hosts를 읽을 때마다 채운다 — 헤더 칩과 빈 원격
// 칸이 "work-mac-a1b2" 같은 id 대신 사람이 붙인 이름을 보여주려고.
const _hostLabels = new Map();

async function _fetchHosts() {
  const data = await vtFetch('/api/hosts');
  for (const h of data?.hosts || []) _hostLabels.set(h.id, h.label || h.id);
  return data?.hosts || [];
}

/** 호스트 표시 이름. 모르는 id면 목록을 한 번 읽어 본다(실패하면 id 그대로). */
export async function hostLabel(hostId) {
  if (!_hostLabels.has(hostId)) {
    try { await _fetchHosts(); } catch (_) { /* 이름을 못 얻어도 id로 보여준다 */ }
  }
  return _hostLabels.get(hostId) || hostId;
}

/** 캐시에 있는 이름만 동기로 — 렌더 경로(layout/panes.js)가 기다릴 수 없어서. */
export function hostLabelSync(hostId) {
  return _hostLabels.get(hostId) || hostId;
}

// 레일은 목록을 15초마다 읽는다. 방금 만들고·끄고·이름 바꾼 결과는 그 주기를
// 기다리지 않고 바로 보여야 한다 — 레일(지연 청크)이 이 이벤트를 듣고 즉시 다시 읽는다.
function _hostsChanged(hostId) {
  window.dispatchEvent(new CustomEvent('vt:hosts-changed', { detail: { hostId } }));
}

/**
 * 원격 세션을 연다. 이미 열려 있으면 그 탭으로 전환만 한다.
 * @returns 세션 id, 실패면 null
 */
export async function attachRemoteSession(hostId, tmuxName) {
  if (!hostId || !tmuxName) return null;
  // 2026-09-21(사용자 지적) — Rail.tsx의 openRow와 같은 이유. 이미 열려 있는
  // 세션이 다른 세션과 화면을 공유 중이면 switchTo가 그 짝을 밀어냈다.
  // 레코드로 찾는다(id로 찾지 않는다) — host-view.js findRemoteSession 주석.
  const open = findRemoteSession(_entries(), hostId, tmuxName);
  if (open) { revealSession(open); return open; }
  const id = remoteSessionId(hostId, tmuxName);
  if (getSession(id)) {
    // 이 id를 쓰던 세션이 다른 이름으로 바뀐 뒤 옛 이름으로 새 세션이 생긴 경우 —
    // 같은 id를 두 번 등록할 수 없으니 뒤에 표시를 붙인다.
    return attachRemoteSessionAs(`${id}#${Date.now().toString(36)}`, hostId, tmuxName);
  }
  return attachRemoteSessionAs(id, hostId, tmuxName);
}

async function attachRemoteSessionAs(id, hostId, tmuxName) {
  // 호스트가 살아 있는지 먼저 본다 — 죽은 호스트에 탭을 만들면 재연결 스톰의
  // 입구가 된다(ws.js 상단 주석의 그 사고와 같은 모양).
  try {
    const hosts = await _fetchHosts();
    const host = hosts.find((h) => h.id === hostId);
    if (!host) { showToast(`등록되지 않은 호스트: ${hostId}`, 'error'); return null; }
    if (!host.online) { showToast(`${host.label} — 연결할 수 없습니다`, 'error'); return null; }
  } catch (_) { /* 목록 조회 실패는 막지 않는다 — 소켓이 곧 진실을 말해준다 */ }

  // remote는 **소켓이 열리기 전에** 레코드에 있어야 한다(ws.js가 이걸 보고
  // 프록시 경로를 고른다) — addSession의 opts가 그 자리다.
  // 화면 토큰 — 상대 호스트(B)가 이 탭의 attach PTY를 `peer-<내 id>-<screen>`으로
  // 이름 붙이고, 「연결된 화면」이 그걸로 "이게 나"를 판정한다. 없으면 원격
  // 「이 화면만 남기기」가 자기 자신을 끊을 수 있어서 그 기능을 켤 수 없다.
  const screen = (crypto.randomUUID?.() || String(Math.random()).slice(2)).replace(/-/g, '');
  addSession(id, `${tmuxName} · ${hostId}`, undefined, {
    tmuxName,
    host: hostId,
    remote: { host: hostId, tmux: tmuxName, screen },
  });
  saveWorkspace();
  return id;
}

/** 이 세션이 원격인가 — "로컬 전용 기능"을 끄는 쪽에서 쓴다. */
export function isRemoteSession(id) {
  return !!(getSession(id) && getSession(id).remote);
}

// ── 지금 보는 호스트 · 호스트로 이동 ─────────────────────────────────────────

/** 활성 칸이 보여주는 호스트(host-view.js의 기준). 헤더 칩과 레일 스위처가
 * 이 값을 따라간다 — 세 곳이 서로 다른 호스트를 말하지 않게. */
export function activeViewHost() {
  const node = findNode(getTree(), getActivePaneId());
  const s = node && node.t === 'leaf' && node.session ? getSession(node.session) : null;
  return viewHost(s, getActiveTabHostId());
}

/** 레일 스위처에서 호스트를 고르면 화면도 그 호스트로 간다. 그 호스트의 열린
 * 세션이 있으면 그 칸으로(트리는 안 건드린다 — revealSession), 없으면 그 호스트
 * 전용 빈 탭으로(이미 있으면 재사용). 빈 원격 칸은 layout/panes.js가 "이
 * 호스트에 새 세션" 자리표시자로 그린다. */
export async function focusHost(hostId) {
  if (!hostId || activeViewHost() === hostId) return;
  const sid = firstSessionForHost(_entries(), hostId);
  if (sid) { revealSession(sid); return; }
  // 빈 칸 자리표시자가 id 대신 이름을 그리도록 먼저 이름을 받아 둔다(그리는
  // 쪽은 동기라 기다릴 수 없다).
  if (hostId !== LOCAL_HOST) await hostLabel(hostId);
  if (activeViewHost() === hostId) return;   // 기다리는 사이 사용자가 이미 옮겼다
  const tab = emptyTabForHost(getTabHostSummaries(), hostId);
  if (tab) { switchLayoutTab(tab); return; }
  openLayoutTab({ hostId });
}

// ── 원격 세션 생성 · 종료 · 이름 바꾸기 ──────────────────────────────────────

const _json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

/** 원격 호스트에 tmux 세션을 만들고 바로 연다. 활성 칸이 비어 있으면(빈 원격
 * 탭의 「새 세션」) 그 자리에, 아니면 새 탭에 열린다(addSession 규칙). */
export async function createRemoteSession(hostId, name = '') {
  if (!hostId || hostId === LOCAL_HOST) return null;
  let data;
  try {
    data = await vtFetch(`/api/hosts/${encodeURIComponent(hostId)}/sessions`, _json('POST', { name }));
  } catch (e) {
    showToast(`새 세션 실패 — ${e.message}`, 'error');
    return null;
  }
  _hostsChanged(hostId);
  return attachRemoteSession(hostId, data.name);
}

/** 원격 세션을 완전히 끝낸다. 열린 탭을 **먼저** 닫는다 — 상대가 세션을 없애면
 * 프록시 소켓이 "세션 없음"으로 닫히는데, 탭이 남아 있으면 그 자리에 재연결 중단
 * 오버레이만 덩그러니 남는다(로컬 killSession과 같은 순서). */
export async function killRemoteSession(hostId, tmuxName) {
  if (!hostId || !tmuxName) return false;
  const label = await hostLabel(hostId);
  const ok = await confirmDialog(
    `${label}의 '${tmuxName}'을(를) 완전히 종료합니다. 되돌릴 수 없습니다.`,
    { title: '완전 종료', okLabel: '완전 종료', danger: true },
  );
  if (!ok) return false;
  const open = findRemoteSession(_entries(), hostId, tmuxName);
  if (open) await removeSession(open);
  try {
    await vtFetch(
      `/api/hosts/${encodeURIComponent(hostId)}/sessions/${encodeURIComponent(tmuxName)}`,
      _json('DELETE'),
    );
  } catch (e) {
    showToast(`완전 종료 실패 — ${e.message}`, 'error');
    _hostsChanged(hostId);
    return false;
  }
  showToast(`완전 종료됨: ${tmuxName} · ${label}`);
  _hostsChanged(hostId);
  return true;
}

/** 원격 세션 이름을 바꾼다. 열린 탭은 **다시 붙지 않는다** — 상대의 attach PTY는
 * tmux 클라이언트라 세션 이름이 바뀌어도 그대로 따라간다. 레코드의 이름만 맞춘다
 * (ws.js가 재연결할 때 레코드의 `remote.tmux`를 읽으므로 끊겨도 새 이름으로 붙는다). */
export async function renameRemoteSession(hostId, oldName, newName) {
  const next = String(newName || '').trim();
  if (!hostId || !oldName || !next || next === oldName) return false;
  try {
    await vtFetch(
      `/api/hosts/${encodeURIComponent(hostId)}/sessions/${encodeURIComponent(oldName)}`,
      _json('PATCH', { name: next }),
    );
  } catch (e) {
    showToast(`이름 변경 실패 — ${e.message}`, 'error');
    return false;
  }
  const open = findRemoteSession(_entries(), hostId, oldName);
  if (open) {
    const s = getSession(open);
    s.remote = { ...s.remote, tmux: next };
    setSessionTmuxName(open, next);
    saveWorkspace();
  }
  _hostsChanged(hostId);
  return true;
}

window.attachRemoteSession = attachRemoteSession;
window.isRemoteSession = isRemoteSession;
window.vtActiveViewHost = activeViewHost;
window.vtFocusHost = focusHost;
window.vtHostLabel = hostLabel;
window.vtHostLabelSync = hostLabelSync;
window.vtCreateRemoteSession = createRemoteSession;
window.vtKillRemoteSession = killRemoteSession;
window.vtRenameRemoteSession = renameRemoteSession;
