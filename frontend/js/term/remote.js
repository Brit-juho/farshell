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
import { getSession } from '../core/store.js';
import { addSession, switchTo } from './session.js';
import { saveWorkspace } from './workspace.js';
import { showToast } from '../ui/toast.js';
import { vtFetch } from '../core/api.js';

export function remoteSessionId(hostId, tmuxName) {
  return `remote:${hostId}:${tmuxName}`;
}

/**
 * 원격 세션을 연다. 이미 열려 있으면 그 탭으로 전환만 한다.
 * @returns 세션 id, 실패면 null
 */
export async function attachRemoteSession(hostId, tmuxName) {
  if (!hostId || !tmuxName) return null;
  const id = remoteSessionId(hostId, tmuxName);
  if (getSession(id)) { switchTo(id); return id; }

  // 호스트가 살아 있는지 먼저 본다 — 죽은 호스트에 탭을 만들면 재연결 스톰의
  // 입구가 된다(ws.js 상단 주석의 그 사고와 같은 모양).
  try {
    const data = await vtFetch('/api/hosts');
    const host = (data?.hosts || []).find((h) => h.id === hostId);
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

window.attachRemoteSession = attachRemoteSession;
window.isRemoteSession = isRemoteSession;
