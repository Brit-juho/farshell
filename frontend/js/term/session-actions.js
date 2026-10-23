// 세션·뷰 동작의 단일 출처 — docs/ref/terminal.md 「Session and view rules」
// (2026-10-03). 재우기/깨우기는 은퇴했고 동사는 넷이다: 열기 · 닫기(칸은 빈
// 칸으로 남는다 — 레일 메뉴·모바일 시트·Fleet·칸 ×가 같은 closeSession) ·
// 뷰 닫기(묶음 구성 기억) · 종료(항상 확인). 지연 청크(Rail/Fleet)는 정적
// import를 못 해 맨 아래 window 브리지를 쓴다.
import { closeLayoutTab, getTabsWithTrees, getTree, getActiveTabId, countLeaves, closePane,
  isLayoutEmpty, rememberClosedView, findClosedViewByTmux, findClosedViewByCatalog, peekClosedView, takeClosedView, forgetClosedMembers,
  openTabWithTree, getActivePaneId, setTabLabel } from '../layout/store.js';
import { collectSessions, findNode, findPaneBySession } from '../layout/tree.js';
import { applyPaneDrop, canSplit } from '../layout/dnd.js';
import { COMPACT_MAX } from '../layout/breakpoints.js';
import { _isCoarsePointer } from '../core/env.js';
import { saveLayoutNow, serializeTree, deserializeTree } from '../layout/persist.js';
import { removeSession, attachTmux, revealSession } from './session.js';
import { showOnboarding } from './boot.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { getSession, allSessions } from '../core/store.js';
import { getStatus } from '../agent/state.js';
import { confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';

/** 닫기 — 웹 attach만 놓는다(tmux는 산다). 칸은 빈 칸으로 남는다. */
export async function closeSession(sessionId) {
  if (!sessionId || !getSession(sessionId)) return;
  await removeSession(sessionId);
}

export const detachSession = closeSession;   // 옛 이름
export const collectTreeSessions = collectSessions;   // 옛 이름

/** 열린/닫힌 묶음 제목 편집. 세션 이름·cwd·브랜치를 바꾸지 않고 스냅샷에 저장한다. */
export function renameView(viewId, label) {
  const ok = setTabLabel(viewId, label);
  if (ok) saveLayoutNow();
  return ok;
}

function _lookupLive(sessionId) {
  const s = getSession(sessionId);
  return { id: sessionId, tmux: (s && (s.tmuxName || s.tmux_name)) || null, host: (s && s.host) || 'local', ...(s?.catalogSessionId ? {catalogSessionId:s.catalogSessionId,runtimeId:s.runtimeId,catalogHost:s.catalogHost} : {}) };
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
 * 않고 그 자리를 빈 칸으로 둔다. **기억은 attach가 하나라도 성공한 뒤에야 지운다** —
 * 먼저 꺼내 놓고 전부 실패하면 묶음이 흔적 없이 사라졌다. 일부만 실패하면 그 칸은 빈 칸이
 * 되고 한 줄로 알린다. 전부 실패하면 기억을 그대로 두고 false. */
const _reopening = new Set();
export async function reopenClosedView(viewId, focusTmux = null) {
  const view = peekClosedView(viewId);
  if (!view || _reopening.has(viewId)) return false;
  _reopening.add(viewId);
  try {
    const byTmux = new Map();
    const refKey=(info,host='local') => info.catalogSessionId ? `${host}:${info.catalogSessionId}:${info.runtimeId}` : info.tmux;
    let hasDurable=false;
    const openNow = new Set(Object.values(allSessions()).map((s) => s && (s.tmuxName || s.tmux_name)).filter(Boolean));
    (function walk(node) {
      if (!node) return;
      if (node.t === 'leaf') {
        const tmux = node.session && node.session.tmux;
        const info=node.session;
        const host=node.host || info?.host || 'local';
        if (info?.catalogSessionId) hasDurable=true;
        const alreadyOpen=info?.catalogSessionId ? Object.values(allSessions()).some(s=>s?.catalogSessionId===info.catalogSessionId && s.runtimeId===info.runtimeId && (s.host || 'local')===host) : openNow.has(tmux);
        if (tmux && !alreadyOpen) byTmux.set(refKey(info,host), {...info,host});
        return;
      }
      walk(node.a); walk(node.b);
    })(view.tree);
    const attached = new Map();
    const failed = [];
    for (const [key, info] of byTmux) {
      const {tmux,host}=info;
      let id = null;
      if (host && host !== 'local') {
        id = typeof window.attachRemoteSession === 'function'
          ? await window.attachRemoteSession(host, tmux, { place: 'none', catalogSessionId:info.catalogSessionId,runtimeId:info.runtimeId,catalogHost:info.catalogHost }) : null;
      } else {
        id = await attachTmux(tmux, { place: 'none',catalogSessionId:info.catalogSessionId,runtimeId:info.runtimeId,catalogHost:info.catalogHost });
      }
      if (id) attached.set(key, id); else failed.push(tmux);
    }
    if (byTmux.size > 0 && attached.size === 0 && !hasDurable) {
      showToast(t('묶음을 열지 못했습니다 — {names} (기억은 그대로 둡니다)', { names: failed.join(', ') }));
      return false;
    }
    const tree = deserializeTree(view.tree, (info) => info ? attached.get(refKey(info,info.host || 'local')) || null : null);
    if (!tree) return false;
    takeClosedView(viewId);
    if (failed.length) showToast(t('묶음의 {n}개 세션을 열지 못해 빈 칸으로 뒀습니다 — {names}', { n: failed.length, names: failed.join(', ') }));
    const focusId = focusTmux ? attached.get(focusTmux) : null;
    openTabWithTree({ id: view.id, label: view.label, worktreeId: view.worktreeId, hostId: view.hostId,
      tree, focusPaneId: focusId ? findPaneBySession(tree, focusId) : null });
    document.getElementById('onboarding')?.remove();
    if (focusId && getSession(focusId)) revealSession(focusId);
    saveLayoutNow();
    return true;
  } finally { _reopening.delete(viewId); }
}

/** 행 클릭 = 열기: 열려 있으면 그 칸으로, 기억된 묶음이 있으면 통째로, 아니면
 * 새 단독 뷰. */
export async function openSessionFromRail({ sessionId = null, tmuxName = null, catalogSessionId = null, runtimeId = null } = {}) {
  if (sessionId && getSession(sessionId)) { revealSession(sessionId); return sessionId; }
  if (!tmuxName) return null;
  const view = catalogSessionId ? findClosedViewByCatalog(catalogSessionId) : findClosedViewByTmux(tmuxName);
  if (view && await reopenClosedView(view.id, tmuxName)) return true;
  return attachTmux(tmuxName, { catalogSessionId, runtimeId });
}

/** 「오른쪽에 나란히 열기」「아래에 열기」를 행 메뉴에 넣을지. **폭 720px 이상 + 터치(pointer: coarse)**
 * 일 때만이다 — 720 미만은 칸 상한 2의 폰이고, 터치 드래그는 만들지 않으며(롱프레스는 메뉴가 쓴다)
 * 마우스 기기는 드래그가 있다. 활성 칸이 이미 그 세션이면(제 자리에서 쪼개 봐야 빈 칸만 남는다)
 * 칸 상한에 걸렸을 때도 넣지 않는다. 「묶기는 드래그로만」 규칙의 터치 한정 완화 —
 * docs/ref/terminal.md Actions 표. */
export function touchSplitAvailable(sessionId = null) {
  if (window.innerWidth < COMPACT_MAX || !_isCoarsePointer()) return false;
  if (!canSplit()) return false;
  const active = findNode(getTree(), getActivePaneId());
  if (!active || active.t !== 'leaf') return false;
  if (sessionId && active.session === sessionId) return false;
  return true;
}

/** 활성 칸을 dir('right'|'bottom') 방향으로 분할해 그 세션을 놓는다 — 드롭의 가장자리 처리
 * (`applyPaneDrop`)를 그대로 쓴다: 다른 뷰에 있던 세션은 옮겨 오고, 그 뷰가 비면 뷰를 닫는다
 * (closeViewIfEmptied). 열려 있지 않으면 먼저 attach(자리는 안 정함)한 뒤 놓는다. 놓았으면 id. */
export async function splitOpenSession(dir, { sessionId = null, tmuxName = null } = {}) {
  if (dir !== 'right' && dir !== 'bottom') return null;
  if (!touchSplitAvailable(sessionId)) return null;
  let id = sessionId && getSession(sessionId) ? sessionId : null;
  if (!id && tmuxName) id = await attachTmux(tmuxName, { place: 'none' });
  if (!id || typeof id !== 'string') return null;
  const paneId = getActivePaneId();
  applyPaneDrop(paneId, dir, id);
  saveLayoutNow();
  revealSession(id);
  return id;
}

/** 종료 확인 문구 — waiting/working이면 반드시 적는다. */
export function killWarning(name, status) {
  if (status === 'waiting') return t("'{name}'을(를) 완전히 종료합니다. 입력을 기다리고 있습니다. 되돌릴 수 없습니다.", { name });
  if (status === 'working') return t("'{name}'을(를) 완전히 종료합니다. 지금 작업 중입니다. 되돌릴 수 없습니다.", { name });
  return t("'{name}'을(를) 완전히 종료합니다. 되돌릴 수 없습니다.", { name });
}

/** 종료 — 열려 있으면 먼저 닫는다(재연결 루프 방지). 다른 브라우저 탭은 WS
 * 4004로 칸을 비운다(term/ws.js). */
export async function killSession(name, webSessionId) {
  if (webSessionId && getSession(webSessionId)) {
    await closeSession(webSessionId);
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/kill/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) { showToast(t('완전 종료 실패: {name} ({status})', { name, status: res.status })); return false; }
    forgetClosedMembers([name]);
    saveLayoutNow();
    showToast(t('완전 종료됨: {name}', { name }));
    return true;
  } catch (_) {
    showToast(t('완전 종료 오류: {name}', { name }));
    return false;
  }
}

/** 확인받고 종료(Rail.tsx 메뉴 등). */
export async function confirmAndKillSession(name, webSessionId) {
  const status = getStatus(name);
  if (!await confirmDialog(killWarning(name, status), { title: t('완전 종료'), okLabel: t('완전 종료'), danger: true })) return false;
  return killSession(name, webSessionId);
}

window.vtCloseSession = closeSession;
window.vtDetachSession = closeSession;   // 옛 이름(외부 스크립트 호환)
window.vtCloseView = closeView;
window.vtRenameTab = renameView;
window.vtClosePane = closePaneAction;
window.vtOpenSessionFromRail = openSessionFromRail;
window.vtKillSession = killSession;
window.vtTouchSplitAvailable = touchSplitAvailable;
window.vtSplitOpenSession = splitOpenSession;
window.vtKillWarning = killWarning;
window.vtConfirmAndKillSession = confirmAndKillSession;
