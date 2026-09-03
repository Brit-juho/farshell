// 탭 DOM 생성 + 드래그 정렬. F4에서 addSession(구 terminal.js :670-735)과
// makeTabDraggable(구 :1280-1325)에서 분리.
//
// 순환 import를 피하려고 switchTo/removeSession/renameSession(session.js 소유)을
// 직접 import하지 않는다 — 대신 handlers 콜백으로 주입받는다. session.js가 자기
// 함수를 넘겨 호출하는 쪽이라, tab-dom.js는 session.js를 몰라도 된다.
import { getSession } from '../core/store.js';
import { isMac } from '../core/env.js';
import { saveWorkspace } from './workspace.js';

// handlers: { onSwitch(id), onClose(id), onRename(id, newText, originalText) }
export function createTabElement(id, displayName, insertBeforeId, handlers) {
  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.sessionId = id;
  // 좌우 이동 단축키 안내(호버 툴팁)
  tab.title = `탭 이동: ${isMac ? 'Cmd' : 'Ctrl'} + Shift + ← / →`;
  const agentBadge = document.createElement('span');
  agentBadge.className = 'tab-agent';
  agentBadge.style.cssText = 'margin-right:4px;font-size:12px;';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'tab-name';
  nameSpan.textContent = displayName || id.slice(0, 8);
  const closeSpan = document.createElement('span');
  closeSpan.className = 'close';
  closeSpan.textContent = '×';
  // U2: tmux 세션은 탭을 닫아도 kill이 아니라 detach — 백그라운드에서 계속 돈다.
  // 호버 툴팁(데스크톱)과 닫은 직후 토스트(모바일 포함) 둘 다로 알린다.
  closeSpan.addEventListener('mouseenter', () => {
    const sess = getSession(id);
    const isTmux = sess && (sess.tmuxName || sess.tmux_name);
    closeSpan.title = isTmux ? '닫기 (tmux 세션은 백그라운드에서 계속 실행됨)' : '닫기 (세션 종료)';
  });
  closeSpan.onclick = (e) => {
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
  tab.appendChild(closeSpan);
  tab.onclick = () => handlers.onSwitch(id);
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

// 탭 드래그 정렬 (HTML5 DnD)
function makeTabDraggable(tab) {
  tab.draggable = true;
  tab.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/vt-tab-id', tab.dataset.sessionId);
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
    const draggedId = e.dataTransfer.getData('text/vt-tab-id');
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
}
