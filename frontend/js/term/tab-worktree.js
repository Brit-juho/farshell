// pane 헤더도 레일/카드와 같은 현재 작업 위치의 context를 사용한다.
// 생성 시 워크트리 소속으로 세션 이름을 자동 변경하던 옛 경로는 제거했다.
import { vtFetch } from '../core/api.js';
import { setBranchChipMap } from '../layout/panes.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';
import { sessionContext } from '../ui/session-context.js';

export function branchChipMap(sessions) {
  const map = new Map();
  for (const s of sessions || []) {
    const ctx = sessionContext(s);
    const branch = ctx.branch || (ctx.status === 'ready' || ctx.root ? '분리된 HEAD' : null);
    if (branch) map.set(s.name, branch);
  }
  return map;
}

let revision = 0;
export async function refreshTabWorktreeLabels() {
  const request = ++revision;
  try {
    const sessions = await vtFetch('/api/tmux/sessions');
    if (request === revision && Array.isArray(sessions)) setBranchChipMap(branchChipMap(sessions));
  } catch (_) { /* 마지막 성공 값을 유지한다 */ }
}

refreshTabWorktreeLabels();
setInterval(() => { if (!document.hidden) refreshTabWorktreeLabels(); }, 60000);
onWorkspaceEvent('tmux_sessions', (msg) => {
  if (!Array.isArray(msg.sessions)) return;
  revision += 1;
  setBranchChipMap(branchChipMap(msg.sessions));
});
