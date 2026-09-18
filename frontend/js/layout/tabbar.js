// 10-shell-layout.md §4 2단계 — 워크트리 탭 바.
//
// **탭 하나가 pane 트리 하나를 소유한다**(layout/store.js의 탭 집합). 1단계에서는
// 탭 이름만 워크트리 단위였고 화면 전체에 트리가 하나뿐이었다 — 그래서 워크트리를
// 옮길 때마다 이전 배치가 사라졌다. 이제 탭을 바꾸면 그 워크트리에서 쓰던 분할이
// 그대로 돌아온다.
//
// 3단계(6/n)에서 아래쪽 세션 탭 줄(`#tabs`)은 사라졌다. 그 줄이 하던 일은
// 전부 옮겨졌다: 이름·순서는 세션 레코드로(core/store.js), 드래그 소스와
// 닫기·이름 변경은 레일 세션 행으로, 에이전트 마크·상태 dot·읽지 않음은
// 이 파일의 워크트리 탭으로.
import { getTabsWithTrees, getActiveTabId, switchLayoutTab, openLayoutTab, setTabLabel, tabDisplayLabel, onLayoutChange } from './store.js';
import { collectSessions } from './tree.js';
import { allSessions } from '../core/store.js';
import { saveLayoutNow } from './persist.js';
import { sleepTab } from '../term/session-actions.js';
import { icon, agentIcon } from '../ui/icons.js';
import { getStatus, isUnseen, applyStatusDot, onStatusChange, URGENCY } from '../agent/state.js';

const HOST = 'vt-wtabs';

// ── 탭 하나의 상태 ─────────────────────────────────────────────────────────
// 10-shell-layout.md §4: 탭 구성은 `[에이전트 마크][이름][상태 dot][읽지 않음]`.
// 탭은 워크트리라 세션이 여럿일 수 있으므로 **가장 급한 것 하나**로 접는다
// (레일 워크트리 행과 같은 규칙 — 두 화면이 다른 말을 하면 안 된다).

/** 이 탭(실제 pane 트리)에 떠 있는 세션들의 tmux 이름. 2026-09-18 후속
 * (그룹 재정의) — 태그(`@fsh_grp`)가 아니라 실제 pane 트리에서 뽑는다. */
function tabSessionNames(tab) {
  const all = allSessions();
  const out = [];
  for (const sid of collectSessions(tab.tree)) {
    const s = all[sid];
    const tmux = s && (s.tmuxName || s.tmux_name);
    if (tmux) out.push(tmux);
  }
  return out;
}

export function mostUrgent(statuses) {
  let best = 'idle';
  for (const st of statuses) {
    if ((URGENCY[st] ?? 9) < (URGENCY[best] ?? 9)) best = st;
  }
  return best;
}

let _agents = {};   // tmux 세션 이름 → { agent, label } (agent/status.js가 준다)

/** agent/badges.js가 스냅샷을 받을 때마다 알려준다 — 같은 응답을 두 번 조회하지 않는다. */
export function setAgentInfo(agents) {
  _agents = agents || {};
  paintTabs();
}

function paintTabs() {
  const host = document.getElementById(HOST);
  if (!host) return;
  for (const el of host.querySelectorAll('.vt-wtab')) {
    const tab = getTabsWithTrees().find((t) => t.id === el.dataset.tabId);
    if (!tab) continue;
    const names = tabSessionNames(tab);
    const status = mostUrgent(names.map((n) => getStatus(n)));

    // 에이전트 마크 — 이 탭에서 돌고 있는 CLI. 여럿이면 첫 번째만(마크는
    // "무엇이 돌고 있나"의 힌트이지 목록이 아니다. 목록은 레일이 보여준다).
    const mark = el.querySelector('.vt-wtab-agent');
    const info = names.map((n) => _agents[n]).find((i) => i && i.agent);
    if (mark) {
      mark.innerHTML = info ? agentIcon(info.agent) : '';
      if (info && info.label) mark.setAttribute('data-tip', info.label); else mark.removeAttribute('data-tip');
    }

    // 상태 dot — idle이면 안 그린다(세션 탭과 같은 규칙: "아무 일도 없음"은
    // 기본값이라 표시할 필요가 없다. 상시로 붙은 회색 점은 노이즈다).
    const dotHost = el.querySelector('.vt-wtab-marks');
    if (dotHost) {
      if (!status || status === 'idle') dotHost.querySelector(':scope > .status-dot')?.remove();
      else applyStatusDot(dotHost, status);
    }

    // 읽지 않음 — done인데 아직 이 기기에서 본 적 없는 세션이 하나라도 있으면.
    // dot(=지금 상태)과 뜻이 다르므로 둘 다 뜬다.
    const unread = el.querySelector('.vt-wtab-unread');
    if (unread) unread.hidden = !names.some((n) => isUnseen(n));
  }
}

function render() {
  const host = document.getElementById(HOST);
  if (!host) return;
  const tabs = getTabsWithTrees();
  const activeId = getActiveTabId();
  // 탭이 하나뿐이면 바를 그리지 않는다 — 2.1.1까지의 화면과 같아야 하고,
  // 고를 게 없는 탭 한 줄은 세로 공간만 먹는다(레일 호스트 칩과 같은 판단).
  host.hidden = tabs.length < 2;
  host.replaceChildren();

  for (const t of tabs) {
    const el = document.createElement('div');
    el.className = 'vt-wtab' + (t.id === activeId ? ' active' : '');
    el.dataset.tabId = t.id;
    el.setAttribute('role', 'tab');
    el.setAttribute('aria-selected', String(t.id === activeId));
    el.tabIndex = t.id === activeId ? 0 : -1;

    const mark = document.createElement('span');
    mark.className = 'vt-wtab-agent';
    el.appendChild(mark);

    const name = document.createElement('span');
    name.className = 'vt-wtab-name';
    name.textContent = tabDisplayLabel(t);
    el.appendChild(name);

    // dot과 「읽지 않음」은 이름 뒤·닫기 앞이라는 순서가 고정돼야 한다 —
    // 상태가 바뀔 때마다 위치가 움직이면 닫기 버튼을 잘못 누르게 된다.
    const marks = document.createElement('span');
    marks.className = 'vt-wtab-marks';
    el.appendChild(marks);

    const unread = document.createElement('span');
    unread.className = 'vt-wtab-unread';
    unread.hidden = true;
    unread.setAttribute('data-tip', '완료됐지만 아직 확인하지 않았습니다');
    unread.setAttribute('data-tip-side', 'bottom');
    unread.setAttribute('aria-label', '읽지 않음');
    el.appendChild(unread);

    if (tabs.length > 1) {
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'vt-icon-btn xs vt-wtab-close';
      x.innerHTML = icon('x', 11);
      x.setAttribute('aria-label', `${tabDisplayLabel(t)} 재우기`);
      x.setAttribute('data-tip', '재우기');
      x.setAttribute('data-tip-sub', 'tmux는 계속 삽니다 · 레일에서 다시 깨울 수 있습니다');
      x.setAttribute('data-tip-side', 'bottom');
      // **tmux는 죽이지 않는다** — 이 탭에 있던 세션들의 웹 세션만 놓는다
      // (ADR-29 D: 탭 = 그룹이라, 탭을 닫는 것은 그 그룹 전체를 재우는
      // 것과 같다). 완전 종료는 별도 동작(레일 세션 메뉴)이다.
      x.addEventListener('click', (e) => {
        e.stopPropagation();
        sleepTab(t.id);
      });
      el.appendChild(x);
    }

    el.addEventListener('click', () => { switchLayoutTab(t.id); saveLayoutNow(); });
    host.appendChild(el);
  }
  paintTabs();
}

/** 세션 하나를 화면에 연다(레일·저장소 시트가 세션을 열기 전에 부른다).
 * 그 세션이 이미 어느 탭(화면)에 떠 있으면 전환만 하고, 다른 워크트리를
 * 보던 중이었으면 `worktreeId`만 갱신한다. 2026-09-18 후속(그룹 재정의) —
 * `groupId` 대신 `sessionId`로 찾는다(store.js의 `openLayoutTab` 참고).
 * D4 시절엔 `openWorktreeTab`, ADR-29 D 시절엔 `openGroupTab({groupId})`
 * 였다 — 이름은 호출자 호환을 위해 그대로 뒀다. */
export function openGroupTab({ sessionId, worktreeId = null, hostId = 'local', label } = {}) {
  const id = openLayoutTab({ sessionId, worktreeId, hostId, label: label || null });
  saveLayoutNow();
  return id;
}

/** 탭(=화면) 이름 짓기 — 레일의 그룹 이름 인라인 편집이 부른다. 서버
 * 왕복이 없다: 탭 라벨은 워크스페이스 스냅샷을 통해 이미 영속화되므로
 * (layout/persist.js) 여기서 store만 바꾸고 바로 저장하면 끝이다. */
export function renameTab(tabId, label) {
  const ok = setTabLabel(tabId, label);
  if (ok) saveLayoutNow();
  return ok;
}

onLayoutChange(render);
// 상태가 바뀌면 탭 마크만 다시 칠한다(다시 그리지 않는다 — 렌더는 DOM을
// 통째로 갈아 끼우므로 클릭 중에 탭이 사라질 수 있다).
onStatusChange(paintTabs);
render();

// 지연 청크(shell/Rail.tsx)가 정적 import 없이 부를 수 있게 — 이 저장소의 관행.
window.openGroupTab = openGroupTab;
window.vtRenameTab = renameTab;
window.vtSetTabAgentInfo = setAgentInfo;
