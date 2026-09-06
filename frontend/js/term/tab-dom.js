// 탭 DOM 생성 + 드래그 정렬. F4에서 addSession(구 terminal.js :670-735)과
// makeTabDraggable(구 :1280-1325)에서 분리.
//
// 순환 import를 피하려고 switchTo/removeSession/renameSession(session.js 소유)을
// 직접 import하지 않는다 — 대신 handlers 콜백으로 주입받는다. session.js가 자기
// 함수를 넘겨 호출하는 쪽이라, tab-dom.js는 session.js를 몰라도 된다.
import { getSession } from '../core/store.js';
import { isMac } from '../core/env.js';
import { saveWorkspace } from './workspace.js';
import { icon } from '../ui/icons.js';
import { SESSION_MIME, wireTouchDragSource } from '../layout/dnd.js';

// D7: #tabs를 진짜 탭 위젯으로 만든다 — 예전엔 <div>+onclick이라 키보드로
// 아예 접근이 안 됐다(포커스도, Enter로 전환도 불가능). role="tablist" +
// 롤링 탭인덱스(활성 탭만 0, 나머지 -1 — Tab 키는 탭 목록에 한 번만 멈추고
// 그 안에서는 방향키로 이동) 패턴은 WAI-ARIA Tabs 예제와 동일.
const tabsEl = document.getElementById('tabs');
if (tabsEl) {
  tabsEl.setAttribute('role', 'tablist');
  tabsEl.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = Array.from(tabsEl.querySelectorAll('.tab'));
    const i = tabs.indexOf(document.activeElement);
    if (i === -1) return;
    e.preventDefault();
    const next = e.key === 'ArrowLeft' ? tabs[(i - 1 + tabs.length) % tabs.length]
      : e.key === 'ArrowRight' ? tabs[(i + 1) % tabs.length]
      : e.key === 'Home' ? tabs[0]
      : tabs[tabs.length - 1];
    next.focus();
  });
}

// handlers: { onSwitch(id), onClose(id), onRename(id, newText, originalText) }
export function createTabElement(id, displayName, insertBeforeId, handlers) {
  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.sessionId = id;
  tab.id = `tab-${id}`;
  tab.setAttribute('role', 'tab');
  tab.setAttribute('aria-selected', 'false');
  tab.setAttribute('aria-controls', `term-${id}`);
  // 방향키로만 이동, Tab 키는 활성 탭 하나만 거친다(롤링 탭인덱스) — switchTo()가
  // 전환될 때마다 이전/이후 탭의 값을 뒤집는다.
  tab.tabIndex = -1;
  // 좌우 이동 단축키 안내(호버 툴팁)
  tab.title = `탭 이동: ${isMac ? 'Cmd' : 'Ctrl'} + Shift + ← / →`;
  const agentBadge = document.createElement('span');
  agentBadge.className = 'tab-agent';
  agentBadge.style.cssText = 'margin-right:4px;font-size:12px;';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'tab-name';
  nameSpan.textContent = displayName || id.slice(0, 8);
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'close';
  closeBtn.innerHTML = icon('x', 12);
  closeBtn.setAttribute('aria-label', '탭 닫기');
  // U2: tmux 세션은 탭을 닫아도 kill이 아니라 detach — 백그라운드에서 계속 돈다.
  // 호버 툴팁(데스크톱)과 닫은 직후 토스트(모바일 포함) 둘 다로 알린다.
  closeBtn.addEventListener('mouseenter', () => {
    const sess = getSession(id);
    const isTmux = sess && (sess.tmuxName || sess.tmux_name);
    closeBtn.title = isTmux ? '닫기 (tmux 세션은 백그라운드에서 계속 실행됨)' : '닫기 (세션 종료)';
  });
  closeBtn.onclick = (e) => {
    e.stopPropagation();
    const sess = getSession(id);
    const isTmux = sess && (sess.tmuxName || sess.tmux_name);
    handlers.onClose(id);
    if (isTmux && typeof showToast === 'function') {
      showToast('탭을 닫았습니다 — tmux 세션은 계속 실행 중', 'info');
    }
  };
  tab.appendChild(agentBadge);
  tab.appendChild(nameSpan);
  tab.appendChild(closeBtn);
  tab.onclick = () => handlers.onSwitch(id);
  tab.addEventListener('keydown', (e) => {
    // Space는 스크롤을 막아야 하므로 keydown에서 preventDefault, Enter는 기본 동작이 없다.
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    handlers.onSwitch(id);
  });
  // Phase 8 G7: 탭 드래그 정렬
  makeTabDraggable(tab);
  // 더블클릭으로 이름 편집
  nameSpan.ondblclick = (e) => {
    e.stopPropagation();
    const originalName = nameSpan.textContent;
    nameSpan.contentEditable = 'true';
    nameSpan.focus();
    const finishEdit = async () => {
      nameSpan.contentEditable = 'false';
      await handlers.onRename(id, nameSpan.textContent, originalName);
    };
    nameSpan.onblur = finishEdit;
    nameSpan.onkeydown = (ke) => { if (ke.key === 'Enter') { ke.preventDefault(); nameSpan.blur(); } };
  };
  // insertBeforeId가 주어지고 아직 DOM에 있으면 그 앞에 삽입 — 복원 시 원래
  // 탭 순서를 지키기 위함(없거나 이미 사라졌으면 기존처럼 끝에 append).
  const insertBeforeTab = insertBeforeId
    ? document.querySelector(`#tabs .tab[data-session-id="${insertBeforeId}"]`)
    : null;
  if (insertBeforeTab) {
    document.getElementById('tabs').insertBefore(tab, insertBeforeTab);
  } else {
    document.getElementById('tabs').appendChild(tab);
  }
  return tab;
}

// 탭 드래그 정렬 (HTML5 DnD) + L5: 같은 탭을 pane 위로 드래그하면 그쪽으로
// 배정(분할/세션교체) — layout/panes.js의 wirePaneDropTarget이 받는 쪽이다.
// mime을 하나(SESSION_MIME)로 공유하므로 탭 재정렬 드롭(다른 .tab 위)과
// pane 드롭(.vt-pane 위)이 서로 다른 엘리먼트에 걸린 리스너로 자연히 갈린다.
function makeTabDraggable(tab) {
  tab.draggable = true;
  tab.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData(SESSION_MIME, tab.dataset.sessionId);
    tab.classList.add('dragging');
  });
  tab.addEventListener('dragend', () => {
    tab.classList.remove('dragging');
    // 모든 탭의 insertion indicator 제거
    document.querySelectorAll('#tabs .tab').forEach(t => {
      t.classList.remove('drag-over-left', 'drag-over-right');
    });
  });
  tab.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    // D4: insertion indicator — 커서 위치 기준 좌/우 border 표시
    const rect = tab.getBoundingClientRect();
    const after = e.clientX > rect.left + rect.width / 2;
    document.querySelectorAll('#tabs .tab').forEach(t => {
      t.classList.remove('drag-over-left', 'drag-over-right');
    });
    if (after) tab.classList.add('drag-over-right');
    else tab.classList.add('drag-over-left');
  });
  tab.addEventListener('dragleave', () => {
    tab.classList.remove('drag-over-left', 'drag-over-right');
  });
  tab.addEventListener('drop', (e) => {
    e.preventDefault();
    tab.classList.remove('drag-over-left', 'drag-over-right');
    const draggedId = e.dataTransfer.getData(SESSION_MIME);
    if (!draggedId || draggedId === tab.dataset.sessionId) return;
    const dragged = document.querySelector(`#tabs .tab[data-session-id="${CSS.escape(draggedId)}"]`);
    if (!dragged) return;
    const tabsContainer = document.getElementById('tabs');
    const rect = tab.getBoundingClientRect();
    const after = e.clientX > rect.left + rect.width / 2;
    if (after) {
      tabsContainer.insertBefore(dragged, tab.nextSibling);
    } else {
      tabsContainer.insertBefore(dragged, tab);
    }
    saveWorkspace();
  });
  // L5: 터치 기기는 위 네이티브 dragstart가 안 뜨므로(iOS Safari는 아예
  // 미지원) long-press로 흉내낸다. 탭 재정렬(다른 탭 위 드롭)까지는 다루지
  // 않고 — pane 위로 옮기는 배정만 지원한다(wireTouchDragSource는 .vt-pane만
  // 드롭 타겟으로 본다). 마우스는 위 dragstart 경로를 그대로 쓰므로 내부에서
  // pointerType으로 걸러 중복 처리하지 않는다.
  wireTouchDragSource(tab, () => tab.dataset.sessionId);
}
