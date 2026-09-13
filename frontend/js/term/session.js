// 세션 생성 진입점 + 세션 전환/이름변경/좌우이동 단축키 + 세션 제거.
// N37 3단계 6/n에서 세션 탭 줄(#tabs)이 사라졌다 — 이 파일은 더 이상 탭 DOM을
// 만들지도, 읽지도 않는다. 화면에 세션을 보여주는 곳은 워크트리 탭(layout/
// tabbar.js) · pane 헤더 · 레일 세션 목록 · 피커 넷이다.
// F4에서 terminal.js에서 분리 — addSession(구 :567-1018, 약 350줄)이 가장
// 위험한 분할 대상이었다. 계획서는 tab DOM/xterm-setup/ws 3분할을 제안했지만,
// 그 세 부분이 로컬 변수(id·tab·term·wrapper 등)를 촘촘히 공유해 함수 자체를
// 안전하게 3조각으로 쪼개면 오히려 버그를 심기 쉽다고 판단 — 대신 각 관심사를
// 별도 모듈의 "조립 부품" 함수로 뽑고(xterm-setup.createXtermInstance/
// ws.startSessionSocket), addSession은 그
// 부품들을 순서대로 호출하는 오케스트레이터로 남겼다. 계획서 자신도 이 경로를
// 명시적으로 허용했다("깊은 수술 없이 안전한 3분할이 아니면 하나의 오케스트레이터로
// 남겨도 된다").
import { register as registerKey } from '../core/keymap.js';
import { getSession, allSessions, registerSession, removeSessionRecord, activeSessionId, setActive,
  sessionDisplayName, setSessionDisplayName, orderedSessionIds, moveSessionByOffset } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { createXtermInstance } from './xterm-setup.js';
import { applyMouseMode } from './mouse-mode.js';
import { startSessionSocket } from './ws.js';
import { saveWorkspace } from './workspace.js';
import { showOnboarding } from './boot.js';
import { createTmuxSession } from './tmux-panel.js';
import { registerAction } from '../core/dom.js';
import { get as setting } from '../core/settings.js';
// F5: picker.js와 순환 import 관계 — picker.js 상단 주석 참고.
import { updateSessionPicker } from '../picker.js';
import { setPaneSession } from '../layout/store.js';

export async function createSession() {
  // "맥에서도 열기"가 켜져 있으면 tmux 세션으로 생성하고 서버에 osascript로
  // iTerm 창을 자동 오픈하도록 요청. E2: 체크박스 엘리먼트의 .checked가 아니라
  // 설정 스토어를 읽는다 — 그 엘리먼트는 rail 설정 패널 안에 있어서 값의 주인이
  // DOM이면 기기마다 다른 값이 되고, 패널 마크업이 바뀌면 조용히 undefined가 된다.
  const autoMac = setting('session.openOnMac');
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

// opts: 세션 레코드에 **소켓이 열리기 전에** 얹어야 하는 필드(N7/N39 3단계의
// remote/host가 그렇다 — startSessionSocket이 그 값을 보고 WS 경로를 고른다).
export function addSession(id, displayName, insertBeforeId, opts = null) {
  // 방어: id 없이 호출되면(서버 오류 응답 등) 유령 탭 + /ws/undefined 무한재연결이
  // 생기므로 무시한다.
  if (!id) { showToast('세션 생성 실패 (id 없음)'); return; }
  // 빈 상태 온보딩이 떠 있으면 제거 — 안 그러면 새 터미널이 온보딩 뒤에 가려져
  // 탭은 생겼는데 이동/조작이 안 되는 것처럼 보인다.
  document.getElementById('onboarding')?.remove();

  const { term, fitAddon, searchAddon, wrapper } = createXtermInstance(id);

  // sessions[id] 선 초기화 — wrapE2E의 동기 onReady 콜백이 참조할 수 있도록.
  // ws는 startSessionSocket()에서 채운다.
  // displayName의 출처는 레코드다(core/store.js의 sessionDisplayName).
  registerSession(id, { term, ws: null, displayName: displayName || id.slice(0, 8),
    fitAddon, searchAddon, wrapper, wsHandle: null, reconnTimer: null, ...(opts || {}) },
    insertBeforeId);
  // O1: 재연결 오버레이의 "다시 연결" 버튼이 이 세션의 connectTerminalWs를
  // 부를 수 있도록 참조를 걸어둔다 — startSessionSocket이 채운다.
  const onResize = startSessionSocket(id, term);
  getSession(id).onResize = onResize;
  // S4: 「앱에 마우스 이벤트 전달」이 off면 이 세션도 로컬 선택 모드로 만든다.
  // (설정이 나중에 바뀌면 mouse-mode.js가 구독으로 전체 세션에 반영한다 —
  // 여기서는 "새로 생긴 세션"만 챙긴다.)
  applyMouseMode(getSession(id));

  switchTo(id);
}

export function switchTo(id) {
  setActive(id);
  const s = getSession(id);
  // L3 1단계: wrapper를 여기서 직접 보이기/숨기기 하지 않는다 — 활성 pane에
  // 이 세션을 배정한다고만 알리면 layout/panes.js의 renderLayout()이 실제
  // DOM 반영(wrapper 이동·표시, 배경 탭은 풀로, rAF fit+PTY 크기 통보까지)을
  // 전부 대신한다. setPaneSession은 동기적으로 renderLayout을 트리거하므로
  // 이 줄이 끝난 시점엔 이미 wrapper가 보이는 상태라 바로 focus()해도 된다.
  setPaneSession(id);
  s.term.focus();
  // notifyActiveSession은 어느 파일에도 정의된 적 없는 죽은 방어 코드였다
  // (전수 grep 확인) — F4에서 함께 정리했다. F5에서 picker.js를 ES 모듈로
  // 전환하며 updateSessionPicker를 진짜 import로 바꿔 로드 순서 문제 자체가
  // 사라졌다(옛 classic script 시절엔 picker.js가 이 파일보다 늦게 로드돼
  // 부팅 직후엔 이 함수가 미정의였다 — 이제는 정적 import라 항상 준비돼 있다).
  updateSessionPicker();
  saveWorkspace();
}

// 레일 세션 행과 모바일 세션 관리 시트가 같은 경로로 이름을 바꾼다.
// 성공한 경우에만 레코드·피커·워크스페이스를 함께 동기화한다.
export async function renameSession(id, rawName, previousNameOverride) {
  const s = getSession(id);
  const newName = String(rawName || '').trim();
  if (!s || !newName) return false;
  const previousName = previousNameOverride ?? sessionDisplayName(id);
  if (newName === previousName) return true;
  try {
    const res = await apiFetch(`${API_BASE}/api/sessions/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });
    if (!res.ok) throw new Error('rename failed');
    setSessionDisplayName(id, newName);
    // 사용자가 직접 지은 이름은 그 뒤 어떤 자동 라벨링도 덮지 않는다
    // (term/tab-worktree.js의 워크트리 라벨이 첫 소비자다).
    s.renamed = true;
    updateSessionPicker();
    saveWorkspace();
    return true;
  } catch (_) {
    // 편집 중 DOM에 남은 값을 되돌린다 — 실패했는데 새 이름이 남아 있으면
    // 사용자는 바뀐 줄 안다.
    setSessionDisplayName(id, previousName);
    if (typeof showToast === 'function') showToast('세션 이름 변경 실패', 'error');
    return false;
  }
}

// 세션 좌/우 이동. 순서의 출처는 **세션 레코드**다(core/store.js의 order) —
// 2.1.3까지는 `#tabs .tab`의 DOM 순서였는데, 그 DOM은 N37 3단계에서 사라진다.
// 끝에서 순환하는 동작은 그대로 유지한다.
export function switchTabByOffset(delta) {
  const ids = orderedSessionIds();
  if (ids.length < 2) return;
  const activeId = activeSessionId();
  let idx = ids.indexOf(activeId);
  if (idx === -1) idx = 0;
  const nid = ids[(idx + delta + ids.length) % ids.length];
  if (nid && nid !== activeId) switchTo(nid);
}

// 순서 자체를 바꾸는 경로. 탭 줄이 사라진 뒤에도 재정렬 수단이 남아야 한다 —
// 레일 목록은 긴급도로 정렬되므로(agent/state.js의 sortByUrgency) 거기서
// 드래그 재정렬은 의미가 없다. 그래서 키보드를 재정렬 UI로 둔다.
export function moveTabByOffset(delta) {
  const id = activeSessionId();
  if (!id) return;
  if (moveSessionByOffset(id, delta)) saveWorkspace();
}

// S3: 탭 좌우 이동도 키맵 레지스트리 경유. 레지스트리가 document capture 단계에서
// 한 번만 듣고 preventDefault/stopPropagation까지 처리하므로(PTY로 새지 않는다),
// 여기서는 "무엇을 할지"만 남는다.
// 검색창·탭 이름 편집 중에는 텍스트 선택(Shift+화살표)을 방해하지 않는다 —
// 이 예외는 이 액션 고유의 문맥이라 레지스트리가 아니라 여기 남긴다.
function _inTextField() {
  const ae = document.activeElement;
  return !!(ae && (ae.id === 'search-input' || ae.isContentEditable
    || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA'));
}
registerKey('tabPrev', () => { if (!_inTextField()) switchTabByOffset(-1); });
registerKey('tabNext', () => { if (!_inTextField()) switchTabByOffset(1); });
registerKey('tabMovePrev', () => { if (!_inTextField()) moveTabByOffset(-1); });
registerKey('tabMoveNext', () => { if (!_inTextField()) moveTabByOffset(1); });

export async function removeSession(id) {
  const s = getSession(id);
  if (!s) return;
  // 대기 중인 재연결 타이머 취소 — 안 그러면 탭을 닫은 뒤에도 setTimeout이 살아남아
  // (id는 이미 delete되지만) 죽은 타이머가 지연 후 깨어난다.
  if (s.reconnTimer) { clearTimeout(s.reconnTimer); s.reconnTimer = null; }
  if (s.ws) { try { s.ws.close(); } catch (_) {} }
  s.term.dispose();
  s.wrapper.remove();
  window.removeEventListener('resize', s.onResize);
  if (window.visualViewport) {
    window.visualViewport.removeEventListener('resize', s.onResize);
  }
  const wasActive = activeSessionId() === id;
  const wasRemote = !!s.remote;
  removeSessionRecord(id);
  // 원격 세션(N7/N39 3단계)의 PTY는 상대 호스트에 있다 — 여기 세션 id는 서버의
  // session_store에 아예 없으므로 DELETE를 보내면 404만 받는다. 프록시 소켓은
  // 위에서 닫았고, 그게 닫히면 상대가 자기 PTY를 정리한다.
  if (!wasRemote) await apiFetch(`${API_BASE}/api/sessions/${id}`, { method: 'DELETE' });
  if (wasActive) {
    const remaining = Object.keys(allSessions());
    if (remaining.length > 0) {
      switchTo(remaining[0]);
    } else {
      // 마지막 세션을 닫은 경우 — 빈 컨테이너만 남기지 않고 온보딩(빈 상태) 화면으로.
      setActive(null);
      // L3 1단계: 이 삭제된 세션이 여전히 트리(유일한 leaf)에 남아있으면
      // 죽은 참조가 된다 — setPaneSession(null)이 그 leaf를 비우고,
      // renderLayout()이 알아서 #terminal-container를 빈 상태로 다시 그린다
      // (수동 innerHTML 클리어보다 이 경로 하나로 일원화).
      setPaneSession(null);
      // N35 §6 — 뷰어 페인이 열려 있으면 화면이 비어 있는 게 아니다. 온보딩을
      // 띄우면 그 위를 덮어버린다(z-index:500).
      const hasViewer = document.querySelector('.vt-pane-viewer');
      if (!hasViewer && !document.getElementById('onboarding')) showOnboarding();
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
