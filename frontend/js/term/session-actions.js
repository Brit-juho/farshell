// "닫기"의 세 가지 뜻을 하나로 모은다 — 2.1 D3("저장소 1급화" 3단계).
//
// 지금까지 같은 낱말 "닫기"가 화면마다 다른 일을 했다: 워크트리 탭의 ×는
// 배치에서 빼는 것(tmux는 안 건드림), 피커/레일 컨텍스트 메뉴의 "닫기"는 웹
// 세션만 놓는 것(tmux는 계속 삶), tmux 패널의 "완전 종료"만 진짜로 tmux를
// 죽였다. 셋 다 필요한 동작이라 없애지 않는다 — 대신 이름과 구현을 하나로
// 모아서, 다음에 화면을 하나 더 붙일 때 또 자기만의 "닫기"를 만들지 않게 한다.
//
//   배치에서 빼기  — 이 탭/자리에서 세션을 뗀다. tmux도 웹 세션도 안 죽는다. 확인 없음.
//   세션 놓기     — 이 브라우저에서 세션을 놓는다(웹 세션 삭제). tmux는 계속 산다. 확인 없음.
//   완전 종료     — tmux 세션 자체를 죽인다. 되돌릴 수 없다. **항상 확인**하고,
//                  그 세션이 waiting/working이면 확인 문구에 그 사실을 적는다.
//
// Rail.tsx는 이 파일을 정적 import하지 못한다(shell 지연 청크 제약, Rail.tsx
// 머리말과 같은 이유) — 그래서 맨 아래서 함수들을 window에 브리지한다.
import { closeLayoutTab } from '../layout/store.js';
import { saveLayoutNow } from '../layout/persist.js';
import { removeSession } from './session.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { getSession } from '../core/store.js';
import { getStatus } from '../agent/state.js';

/** 배치에서 빼기 — 탭을 닫는다. 세션은 죽지 않는다(tabbar.js의 옛 주석 그대로). */
export function removeFromLayout(tabId) {
  closeLayoutTab(tabId);
  saveLayoutNow();
}

/** 세션 놓기 — 이 브라우저의 웹 세션을 놓는다. tmux 세션은 그대로 산다. */
export async function detachSession(sessionId) {
  return removeSession(sessionId);
}

/** 완전 종료 확인 문구. 대상이 waiting/working이면 그 사실을 반드시 적는다 —
 * 개입을 기다리는 세션을 "그냥 닫기"인 줄 알고 지우는 사고를 막기 위해서다. */
export function killWarning(name, status) {
  const busyNote = status === 'waiting' ? ' 입력을 기다리고 있습니다.'
    : status === 'working' ? ' 지금 작업 중입니다.'
    : '';
  return `'${name}'을(를) 완전히 종료합니다.${busyNote} 되돌릴 수 없습니다.`;
}

/** 완전 종료 — tmux 세션 자체를 죽인다. 웹에 열린 탭이 있으면 먼저 놓는다
 * (서버 kill이 PTY까지 destroy하므로, 열린 탭을 그대로 두면 WS가 끊긴 뒤
 * 무한 재연결 루프에 빠진다 — 옛 tmux-panel.js killTmuxSession과 같은 순서). */
export async function killSession(name, webSessionId) {
  if (webSessionId && getSession(webSessionId)) {
    await detachSession(webSessionId);
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/kill/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) { showToast(`완전 종료 실패: ${name} (${res.status})`); return false; }
    showToast(`완전 종료됨: ${name}`);
    return true;
  } catch (_) {
    showToast(`완전 종료 오류: ${name}`);
    return false;
  }
}

/** Rail.tsx 컨텍스트 메뉴처럼 확인 UI를 직접 못 그리는 자리를 위한 얇은 조합:
 * `window.confirm`으로 한 번 확인받고 죽인다. tmux-panel.js는 이미 자기
 * 자리에 2단계 인라인 확인(누르면 "종료?"로 바뀌는 버튼)이 있으므로 이 함수를
 * 안 쓰고 killSession을 직접 부른다 — 확인 UI는 화면마다 다를 수 있고, 이
 * 파일이 강제하는 건 "죽이기 전엔 반드시 물어보고, 문구엔 상태를 적는다"는
 * 규칙이지 위젯 모양이 아니다. */
export async function confirmAndKillSession(name, webSessionId) {
  const status = getStatus(name);
  if (!window.confirm(killWarning(name, status))) return false;
  return killSession(name, webSessionId);
}

window.vtRemoveFromLayout = removeFromLayout;
window.vtDetachSession = detachSession;
window.vtKillSession = killSession;
window.vtKillWarning = killWarning;
window.vtConfirmAndKillSession = confirmAndKillSession;
