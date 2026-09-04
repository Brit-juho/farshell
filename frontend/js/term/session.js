// 세션 생성 진입점 + 탭 전환/이름변경/좌우이동 단축키 + 세션 제거.
// F4에서 terminal.js에서 분리 — addSession(구 :567-1018, 약 350줄)이 가장
// 위험한 분할 대상이었다. 계획서는 tab-dom/xterm-setup/ws 3분할을 제안했지만,
// 그 세 부분이 로컬 변수(id·tab·term·wrapper 등)를 촘촘히 공유해 함수 자체를
// 안전하게 3조각으로 쪼개면 오히려 버그를 심기 쉽다고 판단 — 대신 각 관심사를
// 별도 모듈의 "조립 부품" 함수로 뽑고(tab-dom.createTabElement/
// xterm-setup.createXtermInstance/ws.startSessionSocket), addSession은 그
// 부품들을 순서대로 호출하는 오케스트레이터로 남겼다. 계획서 자신도 이 경로를
// 명시적으로 허용했다("깊은 수술 없이 안전한 3분할이 아니면 하나의 오케스트레이터로
// 남겨도 된다").
import { getSession, allSessions, registerSession, removeSessionRecord, activeSessionId, setActive } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { createTabElement } from './tab-dom.js';
import { createXtermInstance } from './xterm-setup.js';
import { startSessionSocket } from './ws.js';
import { fitAndResize } from './resize.js';
import { saveWorkspace } from './workspace.js';
import { showOnboarding } from './boot.js';
import { createTmuxSession } from './tmux-panel.js';
import { registerAction } from '../core/dom.js';
// F5: picker.js와 순환 import 관계 — picker.js 상단 주석 참고.
import { updateSessionPicker } from '../picker.js';

export async function createSession() {
  // "맥에서도 열기" 토글이 켜져 있으면 tmux 세션으로 생성하고
  // 서버에 osascript로 iTerm 창을 자동 오픈하도록 요청
  const autoMac = document.getElementById('auto-mac-checkbox')?.checked;
  if (autoMac) {
    const res = await apiFetch(`${API_BASE}/api/tmux/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_open_on_mac: true }),
    });
    const data = await res.json();
    addSession(data.id, data.name || data.id);
    // ⚠ tmuxName을 안 채우면 openSessionOnMac()의 tmuxName 가드가 항상 실패해
    // "이 세션은 tmux 세션이 아니라 맥에서 열 수 없습니다"를 잘못 띄운다 —
    // 실제로는 진짜 tmux 세션인데도(restoreWorkspace 경로는 이걸 항상 채워왔음).
    const s = getSession(data.id);
    if (s) s.tmuxName = data.tmux_session;
    return;
  }
  const res = await apiFetch(`${API_BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  // C5: 401(토큰)/500 시 JSON에 id가 없어 addSession(undefined) 방지.
  if (!res.ok) { showToast(`세션 생성 실패 (${res.status})`); return; }
  const { id } = await res.json();
  if (id) addSession(id);
}

// + 버튼: 일반 터미널 / tmux 중 선택하는 드롭다운. (기존 createSession은
// 온보딩·auto-mac 호환을 위해 그대로 둔다.)
export function showAddMenu(e) {
  if (e) e.stopPropagation();
  // 토글: 이미 열려 있으면 닫기
  const existing = document.getElementById('add-menu');
  if (existing) { existing.remove(); return; }

  const menu = document.createElement('div');
  menu.id = 'add-menu';
  menu.className = 'vt-menu';
  // + 버튼 바로 아래에 정렬 (기본 .vt-menu는 우측 고정이라 left로 재배치)
  const btn = document.getElementById('add-btn');
  const r = btn ? btn.getBoundingClientRect() : { left: 8, bottom: 44 };
  menu.style.right = 'auto';
  menu.style.left = `${Math.round(r.left)}px`;
  menu.style.top = `${Math.round(r.bottom + 6)}px`;
  menu.style.minWidth = '200px';

  const mkItem = (label, hint, onClick) => {
    const it = document.createElement('div');
    it.className = 'vt-menu-item';
    it.innerHTML = `<div>${label}</div><div style="opacity:.55;font-size:11px;margin-top:2px;">${hint}</div>`;
    it.onclick = () => { menu.remove(); onClick(); };
    return it;
  };
  menu.appendChild(mkItem('일반 터미널', '단발 셸 (tmux 아님)', createPlainSession));
  menu.appendChild(mkItem('tmux 세션', 'detach 유지 · 맥/모바일 공유', createTmuxSession));
  document.body.appendChild(menu);

  setTimeout(() => {
    document.addEventListener('click', function _close(ev) {
      if (!document.body.contains(menu)) { document.removeEventListener('click', _close); return; }
      if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', _close); }
    });
  }, 0);
}

// 일반(비 tmux) 터미널 세션 생성
async function createPlainSession() {
  const res = await apiFetch(`${API_BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (!res.ok) { showToast(`세션 생성 실패 (${res.status})`); return; }
  const { id } = await res.json();
  if (id) addSession(id);
}

export function addSession(id, displayName, insertBeforeId) {
  // 방어: id 없이 호출되면(서버 오류 응답 등) 유령 탭 + /ws/undefined 무한재연결이
  // 생기므로 무시한다.
  if (!id) { showToast('세션 생성 실패 (id 없음)'); return; }
  // 빈 상태 온보딩이 떠 있으면 제거 — 안 그러면 새 터미널이 온보딩 뒤에 가려져
  // 탭은 생겼는데 이동/조작이 안 되는 것처럼 보인다.
  document.getElementById('onboarding')?.remove();

  const tab = createTabElement(id, displayName, insertBeforeId, {
    onSwitch: switchTo,
    onClose: removeSession,
    onRename: renameSession,
  });

  const { term, fitAddon, searchAddon, wrapper } = createXtermInstance(id);

  // sessions[id] 선 초기화 — wrapE2E의 동기 onReady 콜백이 참조할 수 있도록.
  // ws는 startSessionSocket()에서 채운다.
  registerSession(id, { term, ws: null, tabEl: tab, fitAddon, searchAddon, wrapper, wsHandle: null, reconnTimer: null });
  // O1: 재연결 오버레이의 "다시 연결" 버튼이 이 세션의 connectTerminalWs를
  // 부를 수 있도록 참조를 걸어둔다 — startSessionSocket이 채운다.
  const onResize = startSessionSocket(id, term);
  getSession(id).onResize = onResize;

  switchTo(id);
}

export function switchTo(id) {
  const prevId = activeSessionId();
  if (prevId && getSession(prevId)) {
    const prev = getSession(prevId);
    prev.wrapper.style.display = 'none';
    prev.tabEl.classList.remove('active');
    prev.tabEl.setAttribute('aria-selected', 'false');
    prev.tabEl.tabIndex = -1;
  }
  setActive(id);
  const s = getSession(id);
  s.tabEl.classList.add('active');
  s.tabEl.setAttribute('aria-selected', 'true');
  s.tabEl.tabIndex = 0;
  // T6: 그리드 카드와 같은 규칙 — "완료" 표시는 확인했다는 뜻이니 탭으로
  // 전환하면 지운다.
  s.tabEl.classList.remove('done');
  s.wrapper.style.display = 'block';
  s.term.focus();
  // display:block 직후엔 레이아웃이 아직 안 잡혀 fit이 stale 크기를 잡는다.
  // rAF로 레이아웃 확정 후 fit + PTY 크기 통보 — 탭 전환 시 xterm↔PTY 칸 수를
  // 반드시 재동기화한다(안 하면 TUI 정렬이 깨진 채로 남는다).
  requestAnimationFrame(() => fitAndResize(id));
  // notifyActiveSession은 어느 파일에도 정의된 적 없는 죽은 방어 코드였다
  // (전수 grep 확인) — F4에서 함께 정리했다. F5에서 picker.js를 ES 모듈로
  // 전환하며 updateSessionPicker를 진짜 import로 바꿔 로드 순서 문제 자체가
  // 사라졌다(옛 classic script 시절엔 picker.js가 이 파일보다 늦게 로드돼
  // 부팅 직후엔 이 함수가 미정의였다 — 이제는 정적 import라 항상 준비돼 있다).
  updateSessionPicker();
  saveWorkspace();
}

// 탭 더블클릭과 모바일 세션 관리 시트가 같은 경로로 이름을 바꾼다.
// 성공한 경우에만 탭·피커·워크스페이스를 함께 동기화한다.
export async function renameSession(id, rawName, previousNameOverride) {
  const s = getSession(id);
  const newName = String(rawName || '').trim();
  if (!s || !newName) return false;
  const nameEl = s.tabEl?.querySelector('.tab-name');
  const previousName = previousNameOverride ?? (nameEl ? nameEl.textContent : '');
  if (newName === previousName) return true;
  try {
    const res = await apiFetch(`${API_BASE}/api/sessions/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });
    if (!res.ok) throw new Error('rename failed');
    if (nameEl) nameEl.textContent = newName;
    updateSessionPicker();
    saveWorkspace();
    return true;
  } catch (_) {
    if (nameEl) nameEl.textContent = previousName;
    if (typeof showToast === 'function') showToast('세션 이름 변경 실패', 'error');
    return false;
  }
}

// 탭(터미널 섹션) 좌/우 이동. DOM 순서(= 화면 순서) 기준으로 끝에서 순환한다.
function switchTabByOffset(delta) {
  const tabEls = Array.from(document.querySelectorAll('#tabs .tab'));
  if (tabEls.length < 2) return;
  const activeId = activeSessionId();
  let idx = tabEls.findIndex((t) => t.dataset.sessionId === activeId);
  if (idx === -1) idx = 0;
  const next = (idx + delta + tabEls.length) % tabEls.length;
  const nid = tabEls[next].dataset.sessionId;
  if (nid && nid !== activeId) switchTo(nid);
}

// 단축키로 터미널 섹션 좌우 이동: Ctrl/Cmd + Shift + ← / →.
// xterm이 키를 먹기 전에 잡아야 하므로 document의 capture 단계에서 처리하고,
// stopPropagation으로 PTY까지 흘러가지 않게 막는다(포커스 위치와 무관하게 동작).
document.addEventListener('keydown', (e) => {
  if (!e.shiftKey || !(e.ctrlKey || e.metaKey) || e.altKey) return;
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  // 검색창·탭 이름 편집 중에는 텍스트 선택(Shift+화살표)을 방해하지 않는다.
  const ae = document.activeElement;
  if (ae && (ae.id === 'search-input' || ae.isContentEditable)) return;
  e.preventDefault();
  e.stopPropagation();
  switchTabByOffset(e.key === 'ArrowLeft' ? -1 : 1);
}, true);

export async function removeSession(id) {
  const s = getSession(id);
  if (!s) return;
  // 대기 중인 재연결 타이머 취소 — 안 그러면 탭을 닫은 뒤에도 setTimeout이 살아남아
  // (id는 이미 delete되지만) 죽은 타이머가 지연 후 깨어난다.
  if (s.reconnTimer) { clearTimeout(s.reconnTimer); s.reconnTimer = null; }
  if (s.ws) { try { s.ws.close(); } catch (_) {} }
  s.term.dispose();
  s.wrapper.remove();
  s.tabEl.remove();
  window.removeEventListener('resize', s.onResize);
  if (window.visualViewport) {
    window.visualViewport.removeEventListener('resize', s.onResize);
  }
  const wasActive = activeSessionId() === id;
  removeSessionRecord(id);
  await apiFetch(`${API_BASE}/api/sessions/${id}`, { method: 'DELETE' });
  if (wasActive) {
    const remaining = Object.keys(allSessions());
    if (remaining.length > 0) {
      switchTo(remaining[0]);
    } else {
      // 마지막 세션을 닫은 경우 — 빈 컨테이너만 남기지 않고 온보딩(빈 상태) 화면으로.
      setActive(null);
      document.getElementById('terminal-container').innerHTML = '';
      if (!document.getElementById('onboarding')) showOnboarding();
    }
  }
  updateSessionPicker();
  saveWorkspace();
}

// F3(c): data-action 위임용 등록. session.tmux-list/guide.show는 그 함수를
// 소유한 tmux-panel.js/guide.js가 각자 등록한다.
registerAction('session.add-menu', (el, e) => showAddMenu(e));

// 외부(picker.js/quickopen.js/grid.js/snippets.js/viewer.js/moreMenu.js/voice.js)가
// bare identifier 또는 window.foo(e) 형태로 참조하므로 브리지 필요.
window.switchTo = switchTo;
window.addSession = addSession;
window.removeSession = removeSession;
window.renameSession = renameSession;
window.showAddMenu = showAddMenu;
// showOnboarding()의 onclick="...createSession()" 인라인 문자열이 모듈 경계와
// 무관하게 window를 거쳐야 하므로 함께 브리지.
window.createSession = createSession;
// switchTabByOffset은 프로덕션에서 이 파일 안의 keydown 리스너만 쓰지만(외부
// 소비처 없음, 전수 grep 확인), 예전엔 classic script 최상위 함수 선언이라
// "부수적으로" 항상 window에 걸려 있었다 — 테스트가 그 사실에 기대 키보드
// 이벤트 없이 직접 호출한다(frontend/tests/terminal-lifecycle.test.js). 동작
// 동등성을 위해 그대로 브리지.
window.switchTabByOffset = switchTabByOffset;
