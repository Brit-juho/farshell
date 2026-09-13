// 10-shell-layout.md §4 2단계 — 워크트리 탭 바.
//
// **탭 하나가 pane 트리 하나를 소유한다**(layout/store.js의 탭 집합). 1단계에서는
// 탭 이름만 워크트리 단위였고 화면 전체에 트리가 하나뿐이었다 — 그래서 워크트리를
// 옮길 때마다 이전 배치가 사라졌다. 이제 탭을 바꾸면 그 워크트리에서 쓰던 분할이
// 그대로 돌아온다.
//
// 아래쪽 `#tabs`(세션 탭)는 없애지 않았다. 세션 이름·에이전트 배지·드래그 소스가
// 전부 그 DOM을 읽고 있어서(`session.tabEl`을 보는 곳이 26군데다) 지우면 그 전부를
// 같이 옮겨야 한다. 대신 **활성 탭에 속한 세션만 보이게 거른다** — 화면상으로는
// "워크트리 탭 안의 세션 줄"이 된다.
import { getTabs, getActiveTabId, switchLayoutTab, closeLayoutTab, openLayoutTab, onLayoutChange } from './store.js';
import { allSessions } from '../core/store.js';
import { saveLayoutNow } from './persist.js';
import { icon } from '../ui/icons.js';

const HOST = 'vt-wtabs';

/** 이 탭에 보여야 하는 세션인가 — 탭의 워크트리에 속한 세션만. 워크트리가 없는
 * 탭(기타 작업 공간)은 **어디에도 안 속한 세션**을 받는다. 워크트리 정보가 아직
 * 없으면(목록 로딩 전) 거르지 않는다 — 잠깐 전부 숨는 것보다 낫다. */
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
  applySessionFilter();
}

export function applySessionFilter() {
  const activeId = getActiveTabId();
  const tab = getTabs().find((t) => t.id === activeId);
  if (!tab) return;
  for (const s of Object.values(allSessions())) {
    const el = s && s.tabEl;
    if (!el) continue;
    const tmux = s.tmuxName || s.tmux_name || null;
    el.classList.toggle('other-wtab', !sessionBelongsToTab(tmux, tab.worktreeId, _worktreeSessions));
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

    const name = document.createElement('span');
    name.className = 'vt-wtab-name';
    name.textContent = t.label;
    el.appendChild(name);

    if (tabs.length > 1) {
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'vt-wtab-close';
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
  applySessionFilter();
}

/** 워크트리를 탭으로 연다(레일·팔레트가 부른다). 이미 열려 있으면 전환만. */
export function openWorktreeTab(worktreeId, label) {
  const id = openLayoutTab({ worktreeId, label: label || '작업 공간' });
  saveLayoutNow();
  return id;
}

onLayoutChange(render);
render();

// 지연 청크(shell/Rail.tsx)가 정적 import 없이 부를 수 있게 — 이 저장소의 관행.
window.openWorktreeTab = openWorktreeTab;
window.vtApplySessionFilter = applySessionFilter;
