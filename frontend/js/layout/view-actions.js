// 뷰(layout/store.js의 탭) 열기·이름 짓기의 window 브리지.
//
// 2026-11까지 이 파일은 헤더의 뷰 탭 줄(#vt-wtabs)을 그리는 `layout/tabbar.js`였다. 탭 줄은 없어졌다 —
// 세션 이동·묶음 이름·닫기는 레일이 이미 하고 있어 중복이었다. **뷰라는 개념은 그대로다**: 탭 하나가
// pane 트리 하나를 소유하고, 묶음(칸에 세션 2개 이상)·세션에 붙은 파일 뷰어 칸·닫은 묶음 기억이 전부
// 뷰 단위라서다(docs/ref/terminal.md 「Session and view rules」). 여기엔 그 뷰를 다루는 두 함수만 남았다.
import { openLayoutTab } from './store.js';
import { saveLayoutNow } from './persist.js';
import { renameView } from '../term/session-actions.js';

/** 세션 하나를 화면에 연다(레일·저장소 시트가 세션을 열기 전에 부른다).
 * 그 세션이 이미 어느 뷰에 떠 있으면 전환만 하고, 다른 워크트리를
 * 보던 중이었으면 `worktreeId`만 갱신한다. 2026-09-18 후속(그룹 재정의) —
 * `groupId` 대신 `sessionId`로 찾는다(store.js의 `openLayoutTab` 참고).
 * D4 시절엔 `openWorktreeTab`, ADR-29 D 시절엔 `openGroupTab({groupId})`
 * 였다 — 이름은 호출자 호환을 위해 그대로 뒀다. */
export function openGroupTab({ sessionId, worktreeId = null, hostId = 'local', label } = {}) {
  const id = openLayoutTab({ sessionId, worktreeId, hostId, label: label || null });
  saveLayoutNow();
  return id;
}

/** 뷰 이름 짓기 — 레일의 그룹 이름 인라인 편집이 부른다. 서버 왕복이 없다: 뷰 라벨은 워크스페이스
 * 스냅샷을 통해 이미 영속화되므로(layout/persist.js) 여기서 store만 바꾸고 바로 저장하면 끝이다. */
export function renameTab(tabId, label) {
  return renameView(tabId, label);
}

// 지연 청크(shell/Rail.tsx)가 정적 import 없이 부를 수 있게 — 이 저장소의 관행.
window.openGroupTab = openGroupTab;
window.vtRenameTab = renameTab;
