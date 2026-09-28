// 「지금 어느 호스트를 보고 있는가」의 판정 — 순수 함수만 둔다(상태 없음).
//
// 2026-09-28(사용자 지적) — 화면 세 곳이 서로 다른 호스트를 말했다: 레일 스위처는
// "목록 필터"라 M5로 바뀌어도, 터미널 칸은 그대로 M1 세션이었고, 헤더 칩은 로그인한
// 서버(M1)를 부팅 때 한 번 읽어 고정했다. 셋이 같은 답을 내도록 판정을 여기 한
// 곳에 둔다 — 기준은 **활성 칸**이다: 칸에 세션이 있으면 그 세션의 호스트, 비어
// 있으면 그 칸이 속한 탭의 호스트(`layout/store.js` 탭의 `hostId`).

export const LOCAL_HOST = 'local';

/** 세션 레코드의 호스트. 원격 세션은 `remote.host`(term/remote.js가 소켓을 열기
 * 전에 싣는다), 그 밖은 로컬. 레코드가 없으면 null — "모른다"와 "로컬"을 구분한다. */
export function hostOfSession(s) {
  if (!s) return null;
  return (s.remote && s.remote.host) || s.host || LOCAL_HOST;
}

/** 활성 칸이 보여주는 호스트. */
export function viewHost(activeSession, tabHostId) {
  return hostOfSession(activeSession) || tabHostId || LOCAL_HOST;
}

/** 이미 열려 있는 원격 세션의 id. **id 문자열이 아니라 레코드로 찾는다** — 원격
 * 세션 이름을 바꾸면 레코드의 `remote.tmux`는 새 이름이 되지만 id(`remote:<host>:
 * <옛 이름>`)는 그대로라, id로 찾으면 같은 세션을 탭 두 개로 연다. */
export function findRemoteSession(entries, hostId, tmuxName) {
  for (const [id, s] of entries) {
    if (s && s.remote && s.remote.host === hostId && s.remote.tmux === tmuxName) return id;
  }
  return null;
}
