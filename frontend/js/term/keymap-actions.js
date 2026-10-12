// S3 — 레지스트리에 아직 주인이 없던 액션들을 실제 동작에 연결하고, 마지막에
// `wire()`로 document 리스너를 건다.
//
// 왜 이 파일이 따로 있나: 각 모듈이 자기 액션을 `register()`하는 게 원칙이지만
// (search.js가 'search'를, quickopen.js가 'palette'를 등록하듯), 분할·pane 닫기·
// rail 토글은 **소유 모듈이 키보드를 다루지 않는다**(layout/store.js는 순수
// 상태, rail.js는 클릭 배선). 그런 액션을 각 파일에 억지로 넣으면 레이아웃
// 모듈이 키보드를 알게 된다 — 대신 여기 한 곳에 모은다.
//
// document 리스너 자체는 core/keymap.js가 모듈 평가 시점에 건다 — 등록 순서와
// 무관하므로(핸들러는 키를 받은 순간 조회된다) 이 파일이 늦게 로드돼도 된다.
import { register as registerKey } from '../core/keymap.js';
import { getBinding, isNativePasteShortcut } from '../core/keymap.js';
import { splitActivePane, getActivePaneId, evenRatios } from '../layout/store.js';
import { closePaneAction } from './session-actions.js';
import { canSplit } from '../layout/panes.js';
import { activeSessionId } from '../core/store.js';
import { focusedTerminalSessionId } from './clipboard.js';
import { pasteFromClipboard, queueNativePasteFallback } from './clipboard.js';

registerKey('splitRight', () => { if (canSplit()) splitActivePane('row'); });
registerKey('splitDown', () => { if (canSplit()) splitActivePane('col'); });
// 칸 ×와 같은 동작(2026-10-03 세션·뷰 규칙 — 닫기 경로 통일).
registerKey('paneClose', () => closePaneAction(getActivePaneId()));
registerKey('paneEven', () => evenRatios());

// 기본 Ctrl+Shift+V는 브라우저 paste 이벤트를 먼저 기다린다. 이벤트가 없는
// 브라우저는 지연 Clipboard API 폴백을 쓴다. 팔레트 명령과 다른 사용자
// 재지정 조합은 바로 Clipboard API를 사용한다.
// 2026-09-30 — 대상은 "커서가 있는 터미널"이 먼저다. 예전엔 activeSessionId만
// 봤는데, 붙여넣기 이벤트는 포커스된 터미널이 받으므로 둘이 다르면 폴백이
// 다른 세션에 한 번 더 들어갈 수 있었다.
registerKey('paste', (e) => {
  const id = focusedTerminalSessionId() || activeSessionId();
  if (!id) return;
  if (e && isNativePasteShortcut(e, getBinding('paste'))) queueNativePasteFallback(id, e);
  else pasteFromClipboard(id);
});

// 레일 토글 — 현재 Rail.tsx의 접기/펴기 버튼과 같은 경로를 쓴다.
registerKey('railToggle', () => {
  const btn = document.querySelector('#vt-wgrail .vt-wgrail-collapse');
  if (btn) btn.click();
});

// 'settings' 액션의 주인은 panels/settings.js다(S4에서 생겼다) — 여기서
// 중복 등록하지 않는다. 같은 id에 두 번 register하면 나중 것이 이기므로
// 모듈 로드 순서에 따라 동작이 갈릴 수 있다.

// N44(30-worktree.md §3) — 'worktreeNew'의 실제 동작은 Rail.tsx(지연 청크)
// 안에 산다. rail.js의 rail 토글과 같은 이유로 여기서는 직접 구현하지 않고,
// Rail.tsx가 마운트되며 노출하는 window 브리지(vtOpenWorktreeDialog)를
// 누른다 — Rail이 아직 안 마운트됐으면(초기 로드 경합) 조용히 무시한다.
registerKey('worktreeNew', () => { window.vtOpenWorktreeDialog?.(); });
