// dock 버튼과 빠른 열기가 지연 import로 부르는 실제 MCP 팝업
// 열기 로직 — vite.config.js가 이 파일을 'settings' 청크로 못박는다
// (panels/mcp.js와 같은 청크). 두 파일이 어차피 같은 청크에 묶이므로
// mcp-open.js가 mcp.js를 dynamic import 해도 별도 네트워크 요청이 생기지
// 않는다. core(상시 초기 페이로드, 300KiB 상한)에 안 두는 이유: 처음엔
// core에서 registerAction('mcp.show', …)로 만들었는데 몇백 바이트 차이로
// 그 상한을 그대로 넘겼다(실측, 2026-09-28) — 진입점에서 곧바로 지연
// import하는 지금 구조로 바꿨다. mcp.js 자체는 layout/store.js를 직접
// import하지 않는다(상태 싱글톤이 청크에 복제될 수 있다) — 워크트리
// id는 여기서 구해 인자로 넘긴다.
import { openPanel, closePanel } from './panel.js';
import { getTabs, getActiveTabId, getTree, getActivePaneId, onLayoutChange } from '../layout/store.js';
import { findNode } from '../layout/tree.js';

/** MCP 화면의 기준 — 포커스된 칸의 tmux 세션(`session:<이름>`). 경로는 서버가
 *  tmux에 직접 묻는다(server/mcp_scan.py SESSION_PREFIX). 원격 호스트 칸은 이
 *  서버의 tmux에 없으므로 기준으로 쓰지 않는다. 세션이 없으면 탭의 워크트리. */
export function mcpContext() {
  const tab = getTabs().find((t) => t.id === getActiveTabId());
  const leaf = findNode(getTree(), getActivePaneId());
  const local = (!tab || (tab.hostId || 'local') === 'local')
    && (!leaf || (leaf.host || 'local') === 'local');
  if (!local) return null;
  if (local && leaf && leaf.t === 'leaf' && leaf.session) return `session:${leaf.session}`;
  return (tab && tab.worktreeId) || null;
}

export function showMcp() {
  // 설정 위에 팝업이 겹치지 않도록 먼저 닫는다.
  closePanel('vt-settings');
  // openPanel이 이미 토글이다 — 같은 id로 열려 있으면 여기서 닫고 null을
  // 반환한다(panel.js 158번 주석). 그 경우 아래로 내려가지 않는다.
  let unsub = null;
  const panel = openPanel({
    id: 'vt-mcp',
    ariaLabel: 'MCP와 스킬 관리',
    headHTML: '<div class="vt-vw-title">MCP · 스킬</div>',
    bodyId: 'vt-mcp-body',
    onClose: () => { if (unsub) unsub(); unsub = null; },
  });
  if (!panel) return;
  let ctx = mcpContext();
  import('./mcp.js')
    .then((m) => {
      m.mount(panel.body, ctx);
      // 팝업을 띄운 채 다른 칸을 누르면 그 칸 기준으로 다시 읽는다.
      unsub = onLayoutChange(() => {
        const next = mcpContext();
        if (next === ctx || !panel.body.isConnected) return;
        ctx = next;
        m.mount(panel.body, ctx);
      });
    })
    .catch((e) => { panel.body.textContent = '불러오지 못했습니다'; console.error('[FarShell mcp]', e); });
}
