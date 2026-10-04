// 세션·뷰 동작의 단일 출처 — docs/ref/terminal.md 「Session and view rules」
// (2026-10-03). 재우기/깨우기는 은퇴했고 동사는 넷이다: 열기 · 닫기(칸은 빈
// 칸으로 남는다 — 레일 메뉴·모바일 시트·Fleet·칸 ×가 같은 closeSession) ·
// 뷰 닫기(묶음 구성 기억) · 종료(항상 확인). 지연 청크(Rail/Fleet)는 정적
// import를 못 해 맨 아래 window 브리지를 쓴다.
import { closeLayoutTab, getTabsWithTrees, getTree, getActiveTabId, countLeaves, closePane,
  isLayoutEmpty, rememberClosedView, findClosedViewByTmux, takeClosedView, forgetClosedMembers,
  openTabWithTree } from '../layout/store.js';
import { collectSessions, findNode, findPaneBySession } from '../layout/tree.js';
import { saveLayoutNow, serializeTree, deserializeTree } from '../layout/persist.js';
import { removeSession, attachTmux, revealSession } from './session.js';
import { showOnboarding } from './boot.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { getSession, allSessions } from '../core/store.js';
import { getStatus } from '../agent/state.js';
import { confirmDialog } from '../core/dialog.js';

/** 닫기 — 웹 attach만 놓는다(tmux는 산다). 칸은 빈 칸으로 남는다. */
export async function closeSession(sessionId) {
  if (!sessionId || !getSession(sessionId)) return;
  await removeSession(sessionId);
}

export const detachSession = closeSession;   // 옛 이름
export const collectTreeSessions = collectSessions;   // 옛 이름

function _lookupLive(sessionId) {
  const s = getSession(sessionId);
  return { id: sessionId, tmux: (s && (s.tmuxName || s.tmux_name)) || null, host: (s && s.host) || 'local' };
}

function _showEmptyIfNoViews() {
  if (isLayoutEmpty() && !document.getElementById('onboarding')) showOnboarding();
}

/** 뷰 닫기 — 안의 세션을 전부 닫고, 묶음이었으면 구성을 기억한다. 마지막
 * 뷰면 빈 화면(온보딩). 확인창 없음. */
export async function closeView(tabId) {
  const tab = getTabsWithTrees().find((t) => t.id === tabId);
  if (!tab) return;
  const ids = collectSessions(tab.tree);
  if (ids.length >= 2) {
    rememberClosedView({ id: tab.id, label: tab.label, worktreeId: tab.worktreeId, hostId: tab.hostId,
      tree: serializeTree(tab.tree, _lookupLive) });
  }
  closeLayoutTab(tabId);
  for (const sid of ids) await closeSession(sid);
  saveLayoutNow();
  _showEmptyIfNoViews();
}

/** 칸 × — 세션 칸이면 세션을 닫고(빈 칸으로 남음), 빈 칸이면 칸을 없앤다.
 * 뷰의 마지막 칸이면 뷰를 닫는다. */
export async function closePaneAction(paneId) {
  const node = findNode(getTree(), paneId);
  if (!node || node.t !== 'leaf') return;
  const isTerminal = (node.kind || 'terminal') === 'terminal';
  if (isTerminal && node.session && getSession(node.session)) {
    await closeSession(node.session);
    return;
  }
  if (countLeaves() > 1) {
    closePane(paneId);
    saveLayoutNow();
    return;
  }
  await closeView(getActiveTabId());
}

/** 닫힌 묶음 뷰를 통째로 다시 연다. 이미 다른 칸에 열린 구성원은 옮겨 오지
 * 않고 그 자리를 빈 칸으로 둔다. */
export async function reopenClosedView(viewId, focusTmux = null) {
  const view = takeClosedView(viewId);
  if (!view) return false;
  const byTmux = new Map();
  const openNow = new Set(Object.values(allSessions()).map((s) => s && (s.tmuxName || s.tmux_name)).filter(Boolean));
  (function walk(node) {
    if (!node) return;
    if (node.t === 'leaf') {
      const tmux = node.session && node.session.tmux;
      if (tmux && !openNow.has(tmux)) byTmux.set(tmux, (node.host || node.session.host || 'local'));
      return;
    }
    walk(node.a); walk(node.b);
  })(view.tree);
  const attached = new Map();
  for (const [tmux, host] of byTmux) {
    let id = null;
    if (host && host !== 'local') {
      id = typeof window.attachRemoteSession === 'function'
        ? await window.attachRemoteSession(host, tmux, { place: 'none' }) : null;
    } else {
      id = await attachTmux(tmux, { place: 'none' });
    }
    if (id) attached.set(tmux, id);
  }
  const tree = deserializeTree(view.tree, (info) => (info && info.tmux && attached.get(info.tmux)) || null);
  if (!tree) return false;
  const focusId = focusTmux ? attached.get(focusTmux) : null;
  openTabWithTree({ id: view.id, label: view.label, worktreeId: view.worktreeId, hostId: view.hostId,
    tree, focusPaneId: focusId ? findPaneBySession(tree, focusId) : null });
  document.getElementById('onboarding')?.remove();
  if (focusId && getSession(focusId)) revealSession(focusId);
  saveLayoutNow();
  return true;
}

/** 행 클릭 = 열기: 열려 있으면 그 칸으로, 기억된 묶음이 있으면 통째로, 아니면
 * 새 단독 뷰. */
export async function openSessionFromRail({ sessionId = null, tmuxName = null } = {}) {
  if (sessionId && getSession(sessionId)) { revealSession(sessionId); return sessionId; }
  if (!tmuxName) return null;
  const view = findClosedViewByTmux(tmuxName);
  if (view && await reopenClosedView(view.id, tmuxName)) return true;
  return attachTmux(tmuxName);
}

/** 종료 확인 문구 — waiting/working이면 반드시 적는다. */
export function killWarning(name, status) {
  const busyNote = status === 'waiting' ? ' 입력을 기다리고 있습니다.'
    : status === 'working' ? ' 지금 작업 중입니다.'
    : '';
  return `'${name}'을(를) 완전히 종료합니다.${busyNote} 되돌릴 수 없습니다.`;
}

/** 종료 — 열려 있으면 먼저 닫는다(재연결 루프 방지). 다른 브라우저 탭은 WS
 * 4004로 칸을 비운다(term/ws.js). */
export async function killSession(name, webSessionId) {
  if (webSessionId && getSession(webSessionId)) {
    await closeSession(webSessionId);
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/kill/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) { showToast(`완전 종료 실패: ${name} (${res.status})`); return false; }
    forgetClosedMembers([name]);
    saveLayoutNow();
    showToast(`완전 종료됨: ${name}`);
    return true;
  } catch (_) {
    showToast(`완전 종료 오류: ${name}`);
    return false;
  }
}

/** 확인받고 종료(Rail.tsx 메뉴 등). */
export async function confirmAndKillSession(name, webSessionId) {
  const status = getStatus(name);
  if (!await confirmDialog(killWarning(name, status), { title: '완전 종료', okLabel: '완전 종료', danger: true })) return false;
  return killSession(name, webSessionId);
}

window.vtCloseSession = closeSession;
window.vtDetachSession = closeSession;   // 옛 이름(외부 스크립트 호환)
window.vtCloseView = closeView;
window.vtClosePane = closePaneAction;
window.vtOpenSessionFromRail = openSessionFromRail;
window.vtKillSession = killSession;
window.vtKillWarning = killWarning;
window.vtConfirmAndKillSession = confirmAndKillSession;
