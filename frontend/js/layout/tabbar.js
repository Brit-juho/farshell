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
import { getTabs, getActiveTabId, switchLayoutTab, closeLayoutTab, openLayoutTab, onLayoutChange } from './store.js';
import { allSessions } from '../core/store.js';
import { saveLayoutNow } from './persist.js';
import { icon, agentIcon } from '../ui/icons.js';
import { getStatus, isUnseen, applyStatusDot, onStatusChange, URGENCY } from '../agent/state.js';

const HOST = 'vt-wtabs';

/** 이 탭에 속한 세션인가 — 탭의 워크트리에 속한 세션만. 워크트리가 없는
 * 탭(기타 작업 공간)은 **어디에도 안 속한 세션**을 받는다. 워크트리 정보가 아직
 * 없으면(목록 로딩 전) 전부 속한 것으로 본다 — 잠깐 전부 비는 것보다 낫다. */
export function sessionBelongsToTab(tmuxName, tabWorktreeId, worktreeSessions) {
  if (!worktreeSessions || worktreeSessions.size === 0) return true;
  const owner = tmuxName ? worktreeSessions.get(tmuxName) : undefined;
  if (tabWorktreeId) return owner === tabWorktreeId;
  return owner === undefined;      // 「기타」 탭
}

let _worktreeSessions = new Map();   // tmux 세션 이름 → worktreeId

/** term/tab-worktree.js가 워크트리 목록을 받을 때마다 여기에 알려준다 —
 * 같은 응답을 두 번 조회하지 않는다. */
export function setWorktreeSessionMap(map) {
  _worktreeSessions = map || new Map();
  paintTabs();
}

// ── 탭 하나의 상태 ─────────────────────────────────────────────────────────
// 10-shell-layout.md §4: 탭 구성은 `[에이전트 마크][이름][상태 dot][읽지 않음]`.
// 탭은 워크트리라 세션이 여럿일 수 있으므로 **가장 급한 것 하나**로 접는다
// (레일 워크트리 행과 같은 규칙 — 두 화면이 다른 말을 하면 안 된다).

/** 이 탭에 속한 tmux 세션 이름들. 필터(sessionBelongsToTab)와 같은 판정이다. */
function tabSessionNames(tab) {
  const out = [];
  for (const s of Object.values(allSessions())) {
    const tmux = s && (s.tmuxName || s.tmux_name);
    if (!tmux) continue;
    if (sessionBelongsToTab(tmux, tab.worktreeId, _worktreeSessions)) out.push(tmux);
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
    const tab = getTabs().find((t) => t.id === el.dataset.tabId);
    if (!tab) continue;
    const names = tabSessionNames(tab);
    const status = mostUrgent(names.map((n) => getStatus(n)));

    // 에이전트 마크 — 이 탭에서 돌고 있는 CLI. 여럿이면 첫 번째만(마크는
    // "무엇이 돌고 있나"의 힌트이지 목록이 아니다. 목록은 레일이 보여준다).
    const mark = el.querySelector('.vt-wtab-agent');
    const info = names.map((n) => _agents[n]).find((i) => i && i.agent);
    if (mark) {
      mark.innerHTML = info ? agentIcon(info.agent) : '';
      if (info && info.label) mark.title = info.label; else mark.removeAttribute('title');
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
  const tabs = getTabs();
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
    name.textContent = t.label;
    el.appendChild(name);

    // dot과 「읽지 않음」은 이름 뒤·닫기 앞이라는 순서가 고정돼야 한다 —
    // 상태가 바뀔 때마다 위치가 움직이면 닫기 버튼을 잘못 누르게 된다.
    const marks = document.createElement('span');
    marks.className = 'vt-wtab-marks';
    el.appendChild(marks);

    const unread = document.createElement('span');
    unread.className = 'vt-wtab-unread';
    unread.hidden = true;
    unread.title = '완료됐지만 아직 확인하지 않았습니다';
    unread.setAttribute('aria-label', '읽지 않음');
    el.appendChild(unread);

    if (tabs.length > 1) {
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'vt-icon-btn xs vt-wtab-close';
      x.innerHTML = icon('x', 11);
      x.setAttribute('aria-label', `${t.label} 탭 닫기`);
      // **세션은 죽이지 않는다** — 탭을 닫는 건 배치를 치우는 것이지 tmux를
      // 끄는 게 아니다(세션 탭 닫기가 detach인 것과 같은 규칙).
      x.addEventListener('click', (e) => {
        e.stopPropagation();
        closeLayoutTab(t.id);
        saveLayoutNow();
      });
      el.appendChild(x);
    }

    el.addEventListener('click', () => { switchLayoutTab(t.id); saveLayoutNow(); });
    host.appendChild(el);
  }
  paintTabs();
}

/** 워크트리를 탭으로 연다(레일·팔레트가 부른다). 이미 열려 있으면 전환만. */
export function openWorktreeTab(worktreeId, label) {
  const id = openLayoutTab({ worktreeId, label: label || '작업 공간' });
  saveLayoutNow();
  return id;
}

onLayoutChange(render);
// 상태가 바뀌면 탭 마크만 다시 칠한다(다시 그리지 않는다 — 렌더는 DOM을
// 통째로 갈아 끼우므로 클릭 중에 탭이 사라질 수 있다).
onStatusChange(paintTabs);
render();

// 지연 청크(shell/Rail.tsx)가 정적 import 없이 부를 수 있게 — 이 저장소의 관행.
window.openWorktreeTab = openWorktreeTab;
window.vtSetTabAgentInfo = setAgentInfo;
