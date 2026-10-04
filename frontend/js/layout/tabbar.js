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
import { getTabsWithTrees, getActiveTabId, switchLayoutTab, openLayoutTab, setTabLabel, moveLayoutTab, tabDisplayLabel, onLayoutChange, isLayoutEmpty } from './store.js';
import { collectSessions } from './tree.js';
import { allSessions, activeSessionId, getSession } from '../core/store.js';
import { saveLayoutNow } from './persist.js';
import { closeView } from '../term/session-actions.js';
import { icon, setAgentMark } from '../ui/icons.js';
import { getStatus, isUnseen, applyStatusDot, onStatusChange } from '../agent/state.js';
import { stateLabel, representativeState } from '../design/state-classes.js';

const HOST = 'vt-wtabs';

// 2026-09-23 — 헤더 탭 순서 변경(데스크톱 전용). 레일의 세션 드래그(dnd.js
// SESSION_MIME)나 OS 파일 드롭과 섞이면 안 되므로 전용 mime을 쓴다. 모바일은
// 이 탭 바 자체가 숨겨져 있어(80-dock.css @media max-width:719px) 손댈 게 없다.
const TAB_MIME = 'application/x-fsh-tab';

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

let _agents = {};   // tmux 세션 이름 → { agent, label } (agent/status.js가 준다)

/** agent/badges.js가 스냅샷을 받을 때마다 알려준다 — 같은 응답을 두 번 조회하지 않는다. */
export function setAgentInfo(agents) {
  _agents = agents || {};
  paintTabs();
}

function paintTabs() {
  const host = document.getElementById(HOST);
  if (!host) return;
  const byId = new Map(getTabsWithTrees().map((t) => [t.id, t]));
  for (const el of host.querySelectorAll('.vt-wtab')) {
    const tab = byId.get(el.dataset.tabId);
    if (!tab) continue;
    const names = tabSessionNames(tab);
    // 대표 상태 — 레일·Fleet과 같은 규칙(design/state-classes.js representativeState).
    const rep = representativeState(names.map((n) => ({ status: getStatus(n), unseen: isUnseen(n) })));

    // 에이전트 마크 — 이 탭에서 돌고 있는 CLI. 여럿이면 첫 번째만(마크는
    // "무엇이 돌고 있나"의 힌트이지 목록이 아니다. 목록은 레일이 보여준다).
    const mark = el.querySelector('.vt-wtab-agent');
    const info = names.map((n) => _agents[n]).find((i) => i && i.agent);
    if (mark) {
      setAgentMark(mark, info && info.agent);
      if (info && info.label) mark.setAttribute('data-tip', info.label); else mark.removeAttribute('data-tip');
    }

    // 상태 dot — idle이면 안 그린다(세션 탭과 같은 규칙: "아무 일도 없음"은
    // 기본값이라 표시할 필요가 없다. 상시로 붙은 회색 점은 노이즈다).
    // 2026-09-30 — 「읽지 않음」을 이 dot 하나에 합쳤다. 예전엔 옆에 같은 색
    // (--info = --color-st-done) 점을 하나 더 그려서, 끝났는데 안 본 탭에는
    // 파란 점이 두 개 떴다가 보면 하나로 줄었다(사용자: "원이 2개씩, 오락가락").
    // 이제 안 본 변화는 dot 둘레의 링(.unseen)으로만 말한다 — 자리는 늘 하나다.
    // 읽지 않음이 어떤 상태로든 생길 수 있게 되면서(agent/state.js), 링만 있고
    // 상태는 idle인 탭도 회색 dot에 링으로 그린다.
    const dotHost = el.querySelector('.vt-wtab-marks');
    if (dotHost) {
      const { status, unseen } = rep;
      if (status === 'idle' && !unseen) {
        dotHost.querySelector(':scope > .status-dot')?.remove();
      } else {
        const dot = applyStatusDot(dotHost, status);
        dot.classList.toggle('unseen', unseen);
        const label = stateLabel(status) + (unseen ? ' · 아직 확인 안 함' : '');
        dot.setAttribute('data-tip', label);
        dot.setAttribute('aria-label', label);
      }
    }
  }
}

// 스크롤바를 숨겼으니(80-dock.css) 잘린 쪽을 알려줄 길이 마스크뿐이다 —
// 스크롤 위치를 볼 때마다 data-fade를 갱신한다. host는 index.html 정적
// 마크업이라 모듈 평가 시점에 이미 있다 — render()가 replaceChildren으로
// 자식만 갈아 끼우므로 이 리스너는 한 번만 걸면 된다.
function _updateFade(host) {
  const max = host.scrollWidth - host.clientWidth;
  if (max <= 1) { host.removeAttribute('data-fade'); return; }
  const left = host.scrollLeft > 1;
  const right = host.scrollLeft < max - 1;
  host.dataset.fade = left && right ? 'both' : left ? 'left' : right ? 'right' : '';
  if (!host.dataset.fade) host.removeAttribute('data-fade');
}

function _wireScroll(host) {
  if (host.dataset.vtScrollWired) return;
  host.dataset.vtScrollWired = '1';
  host.addEventListener('scroll', () => _updateFade(host));
  // 세로 휠만 있는 트랙패드/마우스에서도 넘친 탭에 닿을 수 있게 — 가로
  // 스크롤이 이미 필요한 상황(deltaX가 없는 세로 휠)에서만 가로로 돌린다.
  host.addEventListener('wheel', (e) => {
    if (e.deltaY === 0 || e.deltaX !== 0) return;
    if (host.scrollWidth <= host.clientWidth) return;
    e.preventDefault();
    host.scrollLeft += e.deltaY;
  }, { passive: false });
  new ResizeObserver(() => _updateFade(host)).observe(host);
}

// ── 드래그 재정렬(데스크톱) ──────────────────────────────────────────────
// 탭 안(가운데 지점 기준)을 기준으로 앞/뒤 중 가까운 쪽에 놓인다. dragover마다
// 매번 클래스를 다시 계산해도 탭 개수가 적어 비용이 미미하다.
function _clearDropMarks(host) {
  for (const el of host.querySelectorAll('.vt-wtab-drop-before, .vt-wtab-drop-after')) {
    el.classList.remove('vt-wtab-drop-before', 'vt-wtab-drop-after');
  }
}

function _wireDrag(el, t, host) {
  el.draggable = true;
  el.addEventListener('dragstart', (e) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData(TAB_MIME, t.id);
  });
  el.addEventListener('dragover', (e) => {
    if (!e.dataTransfer.types.includes(TAB_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const before = (e.clientX - el.getBoundingClientRect().left) < el.offsetWidth / 2;
    _clearDropMarks(host);
    el.classList.add(before ? 'vt-wtab-drop-before' : 'vt-wtab-drop-after');
  });
  el.addEventListener('dragleave', () => el.classList.remove('vt-wtab-drop-before', 'vt-wtab-drop-after'));
  el.addEventListener('drop', (e) => {
    if (!e.dataTransfer.types.includes(TAB_MIME)) return;
    e.preventDefault();
    _clearDropMarks(host);
    const draggedId = e.dataTransfer.getData(TAB_MIME);
    if (!draggedId || draggedId === t.id) return;
    const before = (e.clientX - el.getBoundingClientRect().left) < el.offsetWidth / 2;
    const tabs = getTabsWithTrees();
    let toIndex = tabs.findIndex((x) => x.id === t.id);
    if (!before) toIndex += 1;
    const fromIndex = tabs.findIndex((x) => x.id === draggedId);
    if (fromIndex < toIndex) toIndex -= 1;   // 자기 자신이 빠지며 뒤 인덱스가 하나 당겨진다
    if (moveLayoutTab(draggedId, toIndex)) saveLayoutNow();
  });
}

// 마지막으로 그린 탭 줄의 모양. onLayoutChange는 pane 포커스·분할 같은 탭 줄과
// 무관한 변경에도 불리므로, 모양이 같으면 DOM을 갈아 끼우지 않고 마크만 칠한다.
let _renderedKey = '';

function render() {
  const host = document.getElementById(HOST);
  if (!host) return;
  const tabs = getTabsWithTrees();
  const activeId = getActiveTabId();
  const empty = isLayoutEmpty();
  const key = JSON.stringify([activeId, empty, tabs.map((t) => [t.id, tabDisplayLabel(t)])]);
  if (key === _renderedKey && host.childElementCount === (empty ? 0 : tabs.length)) { paintTabs(); return; }
  _renderedKey = key;
  // 2026-09-29(사용자 결정) — 탭이 하나여도 바를 그린다. 예전엔 하나뿐이면
  // 숨겼는데, 세션이 하나인 맥으로 전환하면 탭 줄이 통째로 사라져 고장처럼 보였다.
  // 뷰 없음(isLayoutEmpty)이면 그리지 않는다.
  host.hidden = tabs.length === 0 || empty;
  if (empty) { host.replaceChildren(); return; }
  host.replaceChildren();
  _wireScroll(host);

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

    // dot은 이름 뒤·닫기 앞이라는 순서가 고정돼야 한다 — 상태가 바뀔 때마다
    // 위치가 움직이면 닫기 버튼을 잘못 누르게 된다. 「읽지 않음」도 이 dot의
    // 링으로 말한다(아래 paintTabs).
    const marks = document.createElement('span');
    marks.className = 'vt-wtab-marks';
    el.appendChild(marks);

    // 뷰 × — 하나여도 그린다. tmux는 안 죽인다(2026-10-03).
    {
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'vt-icon-btn xs vt-wtab-close';
      x.innerHTML = icon('x', 11);
      x.setAttribute('aria-label', `${tabDisplayLabel(t)} 닫기`);
      x.setAttribute('data-tip', '뷰 닫기');
      x.setAttribute('data-tip-sub', collectSessions(t.tree).length >= 2
        ? '세션은 계속 돕니다 · 묶음 구성은 기억해 두고 레일에서 다시 열 수 있습니다'
        : '세션은 계속 돕니다 · 레일에서 다시 열 수 있습니다');
      x.setAttribute('data-tip-side', 'bottom');
      x.addEventListener('click', (e) => {
        e.stopPropagation();
        closeView(t.id);
      });
      el.appendChild(x);
    }

    // 전환 + 왼쪽 위 칸의 터미널에 포커스(store.js switchLayoutTab).
    el.addEventListener('click', () => {
      if (!switchLayoutTab(t.id)) return;
      saveLayoutNow();
      const s = getSession(activeSessionId());
      if (s && s.term) s.term.focus();
    });
    _wireDrag(el, t, host);
    host.appendChild(el);
    // 지금 활성 탭이면 넘친 목록 밖에 숨어 있을 수 있으니 보이는 자리로
    // 끌어온다(예: 키보드로 마지막 탭까지 이동한 경우).
    if (t.id === activeId) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  _updateFade(host);
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
// 브랜드 로고 청크 도착(ui/icons.js) — 마크만 다시 칠한다.
window.addEventListener('vt:brand-icons', paintTabs);
// 상태가 바뀌면 탭 마크만 다시 칠한다(다시 그리지 않는다 — 렌더는 DOM을
// 통째로 갈아 끼우므로 클릭 중에 탭이 사라질 수 있다).
onStatusChange(paintTabs);
render();

// 지연 청크(shell/Rail.tsx)가 정적 import 없이 부를 수 있게 — 이 저장소의 관행.
window.openGroupTab = openGroupTab;
window.vtRenameTab = renameTab;
window.vtSetTabAgentInfo = setAgentInfo;
