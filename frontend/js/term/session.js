import { operationId } from '../core/operation-id.js';
import { disposeKeyboardInput } from './keyboard-input.js';
import { cancelPasteTransactions } from './clipboard.js';
// 세션 생성 진입점 + 세션 전환/이름변경/좌우이동 단축키 + 세션 제거.
// N37 3단계 6/n에서 세션 탭 줄(#tabs)이 사라졌다 — 이 파일은 더 이상 탭 DOM을
// 만들지도, 읽지도 않는다. 화면에 세션을 보여주는 곳은 워크트리 탭(layout/
// 옛 뷰 탭 줄) · pane 헤더 · 레일 세션 목록 · 피커 넷이다.
// F4에서 terminal.js에서 분리 — addSession(구 :567-1018, 약 350줄)이 가장
// 위험한 분할 대상이었다. 계획서는 tab DOM/xterm-setup/ws 3분할을 제안했지만,
// 그 세 부분이 로컬 변수(id·tab·term·wrapper 등)를 촘촘히 공유해 함수 자체를
// 안전하게 3조각으로 쪼개면 오히려 버그를 심기 쉽다고 판단 — 대신 각 관심사를
// 별도 모듈의 "조립 부품" 함수로 뽑고(xterm-setup.createXtermInstance/
// ws.startSessionSocket), addSession은 그
// 부품들을 순서대로 호출하는 오케스트레이터로 남겼다. 계획서 자신도 이 경로를
// 명시적으로 허용했다("깊은 수술 없이 안전한 3분할이 아니면 하나의 오케스트레이터로
// 남겨도 된다").
import { getSession, registerSession, removeSessionRecord,
  sessionDisplayName, setSessionDisplayName, setSessionTmuxName } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { screenId } from '../core/screen-id.js';
import { createXtermInstance } from './xterm-setup.js';
import { applyMouseMode } from './mouse-mode.js';
import { startSessionSocket } from './ws.js';
import { registerAction } from '../core/dom.js';
import { get as setting } from '../core/settings.js';
// F5: picker.js와 순환 import 관계 — picker.js 상단 주석 참고.
import { updateSessionPicker } from '../picker.js';
import { setPaneSession, revealSessionPane, openLayoutTab, clearSessionFromLayout, replaceSessionId } from '../layout/store.js';
import { saveLayoutNow } from '../layout/persist.js';
import { t } from '../core/i18n.js';

// opts.place — 새 세션을 어디에 놓나(addSession 주석). 기본은 새 단독 뷰.
export async function createSession({ place = 'view' } = {}) {
  // "맥에서도 열기"가 켜져 있으면 tmux 세션으로 생성하고 서버에
  // 맥 터미널 창을 자동 오픈하도록 요청. E2: 체크박스 엘리먼트의 .checked가 아니라
  // 설정 스토어를 읽는다 — 그 엘리먼트는 rail 설정 패널 안에 있어서 값의 주인이
  // DOM이면 기기마다 다른 값이 되고, 패널 마크업이 바뀌면 조용히 undefined가 된다.
  const autoMac = setting('session.openOnMac');
  if (autoMac) {
    const res = await apiFetch(`${API_BASE}/api/tmux/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_open_on_mac: true, screen: screenId(),operation_id:operationId() }),
    });
    const data = await res.json();
    addSession(data.id, data.name || data.id, { place });
    // ⚠ tmuxName을 안 채우면 openSessionOnMac()의 tmuxName 가드가 항상 실패해
    // "이 세션은 tmux 세션이 아니라 맥에서 열 수 없습니다"를 잘못 띄운다 —
    // 실제로는 진짜 tmux 세션인데도(restoreWorkspace 경로는 이걸 항상 채워왔음).
    const s = getSession(data.id);
    if (s) { s.tmuxName = data.tmux_session; s.catalogSessionId = data.catalog_session_id; s.runtimeId = data.runtime_id; s.catalogHost = data.catalog_host || null; }
    return;
  }
  const res = await apiFetch(`${API_BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  // C5: 401(토큰)/500 시 JSON에 id가 없어 addSession(undefined) 방지.
  if (!res.ok) { showToast(t('세션 생성 실패 ({status})', { status: res.status })); return; }
  const { id } = await res.json();
  if (id) addSession(id, undefined, { place });
}

// + 버튼: 일반 터미널 / tmux 중 선택하는 드롭다운. (기존 createSession은
// 온보딩·auto-mac 호환을 위해 그대로 둔다.)
//
// 2026-09-18 후속(사용자 지적: "열리는 팝업은 누른 위치에 열리게") — 예전엔
// 항상 `#add-btn`(상단 탭 바의 + 버튼) 기준으로 위치를 계산했다. 그 버튼을
// 없애고 레일 하단 "+ 새 세션"으로 발자국을 합친 뒤에는, 그 하드코딩 때문에
// 메뉴가 클릭 위치와 무관하게 화면 구석(폴백 좌표)에 떴다 — 실제로 누른
// 요소(`el`)를 받아 그 기준으로 계산한다. `initActionDelegation`(core/
// dom.js)이 위임 클릭에서 이미 `el`(closest('[data-action]'))을 정확히
// 계산해 넘겨준다 — delegated listener의 `e.currentTarget`은 델리게이트
// 루트를 가리켜 못 쓴다는 게 이유였다. Rail.tsx의 레일 버튼처럼 델리게이션을
// 안 거치는 호출자는 자기 엘리먼트를 직접 넘긴다.
export function showAddMenu(el, e) {
  if (e) e.stopPropagation();
  // 토글: 이미 열려 있으면 닫기
  const existing = document.getElementById('add-menu');
  if (existing) { existing.remove(); return; }

  const menu = document.createElement('div');
  menu.id = 'add-menu';
  menu.className = 'vt-menu';
  // 누른 버튼 바로 아래에 정렬 (기본 .vt-menu는 우측 고정이라 left로 재배치)
  const r = el ? el.getBoundingClientRect() : { left: 8, bottom: 44 };
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
  menu.appendChild(mkItem(t('일반 터미널'), t('단발 셸 (tmux 아님)'), createPlainSession));
  menu.appendChild(mkItem(t('tmux 세션'), t('detach 유지 · 맥/모바일 공유'), createTmuxSession));
  document.body.appendChild(menu);

  // 레일 하단 버튼처럼 뷰포트 맨 아래에 붙은 경우, 아래로 펼치면 body의
  // overflow:hidden에 잘려 안 보인다 — 실제 높이를 측정해 필요하면 위로 뒤집는다.
  const menuHeight = menu.offsetHeight;
  if (r.bottom + 6 + menuHeight > window.innerHeight) {
    menu.style.top = `${Math.round(r.top - menuHeight - 6)}px`;
  }

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
  if (!res.ok) { showToast(t('세션 생성 실패 ({status})', { status: res.status })); return; }
  const { id } = await res.json();
  if (id) addSession(id);
}

// ADR-29 E — term/tmux-panel.js(목록 팝업 + 이 둘)에서 옮겨왔다. 팝업 자체는
// 레일(shell/Rail.tsx)·모바일 세션 시트(picker.js)가 각자 자기 목록에서
// 깨우기/완전 종료를 이미 제공하게 되면서 완전히 중복이 됐지만, 이 둘
// (attachTmux/createTmuxSession)은 그 목록들이 공통으로 부르는 진짜 동작이라
// 그대로 옮긴다 — createPlainSession의 tmux 버전이라 바로 옆이 제자리다.
// place는 addSession과 같다. 이미 열려 있으면 그 자리로 간다. 실패하면 null.
export async function attachTmux(tmuxName, { place = 'view', catalogSessionId = null, runtimeId = null, catalogHost = null } = {}) {
  const res = await apiFetch(`${API_BASE}/api/tmux/attach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: tmuxName, screen: screenId(), ...(catalogSessionId ? { catalog_session_id: catalogSessionId, runtime_id: runtimeId, ...(catalogHost ? {catalog_host: catalogHost} : {}) } : {}) }),
  });
  // 서버가 세션 없음(404) 등을 돌려주면 data.id가 없다. 그대로 addSession(undefined)하면
  // /ws/undefined로 무한 재연결하는 유령 탭이 생기므로 여기서 차단한다.
  if (!res.ok) { showToast(t('세션 열기 실패: {name} ({status})', { name: tmuxName, status: res.status })); return null; }
  const data = await res.json();
  if (!data.id) { showToast(t('세션 열기 실패: {name}', { name: tmuxName })); return null; }
  // 이미 웹에 열려 있으면 해당 탭으로 전환. 2026-09-21(사용자 지적) —
  // switchTo는 "배정"이라 그 세션이 다른 세션과 화면을 공유 중이면(그룹)
  // 짝을 밀어냈다 — revealSession이 트리를 안 건드리고 그 자리로만 이동한다.
  if (getSession(data.id)) {
    if (place !== 'none') revealSession(data.id);
  } else {
    addSession(data.id, data.name || data.id, { place, tmuxName: data.tmux_session || tmuxName });
    // ⚠ tmuxName 미설정 시 openSessionOnMac()이 "tmux 세션 아님"으로 오판한다.
    const s = getSession(data.id);
    if (s) { s.tmuxName = data.tmux_session || tmuxName; s.catalogSessionId = data.catalog_session_id || catalogSessionId; s.runtimeId = data.runtime_id || runtimeId; s.catalogHost = data.catalog_host || catalogHost; }
    if (place !== 'none') saveLayoutNow();
  }
  return data.id;
}

// export — showAddMenu의 "tmux 세션" 메뉴 항목이 부른다. 만든 세션 id를 돌려준다(사용량 재인증 등).
export async function createTmuxSession() {
  // "맥에서도 열기"가 켜져 있으면 서버가 메인 터미널(VT_TERMINAL) 창도 함께 연다.
  // E2: DOM이 아니라 설정 스토어가 이 값의 주인이다.
  const autoMac = setting('session.openOnMac');
  const res = await apiFetch(`${API_BASE}/api/tmux/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_open_on_mac: !!autoMac, screen: screenId(), operation_id: operationId() }),
  });
  if (!res.ok) { showToast(t('tmux 세션 생성 실패 ({status})', { status: res.status })); return; }
  const data = await res.json();
  if (!data.id) { showToast(t('tmux 세션 생성 실패')); return; }
  addSession(data.id, data.name || data.id, { tmuxName: data.tmux_session });
  // ⚠ tmuxName 미설정 시 openSessionOnMac()이 "tmux 세션 아님"으로 오판한다.
  const s = getSession(data.id);
  if (s) { s.tmuxName = data.tmux_session; s.catalogSessionId = data.catalog_session_id; s.runtimeId = data.runtime_id; s.catalogHost = data.catalog_host || null; }
  return data.id;
}

// 온보딩 화면(boot.js showOnboarding)의 버튼은 data-action 위임으로 받는다 — 인라인 onclick은
// CSP(script-src 'self')가 막는다. 아래 window 브리지는 다른 지연 청크가 쓴다.
window.createTmuxSession = createTmuxSession;
// N8/N44(30-worktree.md §4) — Rail.tsx(지연 청크)가 이 모듈을 정적 import
// 못 하므로(Rail.tsx 상단 주석과 같은 이유) 워크트리 행 클릭 시 tmux 세션을
// 여는 경로를 window로 노출한다. attachTmux 자체의 동작은 그대로(이미 웹에
// 열려 있으면 전환, 아니면 새 탭으로 붙인다).
window.attachTmux = attachTmux;

// opts: 세션 레코드에 **소켓이 열리기 전에** 얹어야 하는 필드(N7/N39 3단계의
// remote/host가 그렇다 — startSessionSocket이 그 값을 보고 WS 경로를 고른다).
// opts.place(레코드엔 안 들어간다, 2026-10-03): 'view' 새 단독 뷰(기본) ·
// 'pane' 활성 칸(호출자가 칸을 골라 둠) · 'none' 등록만(트리는 호출자가).
export function addSession(id, displayName, opts = null) {
  const { place = 'view', ...recordOpts } = opts || {};
  // 방어: id 없이 호출되면(서버 오류 응답 등) 유령 탭 + /ws/undefined 무한재연결이
  // 생기므로 무시한다.
  if (!id) { showToast(t('세션 생성 실패 (id 없음)')); return; }
  // 빈 상태 온보딩이 떠 있으면 제거 — 안 그러면 새 터미널이 온보딩 뒤에 가려져
  // 탭은 생겼는데 이동/조작이 안 되는 것처럼 보인다.
  document.getElementById('onboarding')?.remove();

  const { term, fitAddon, searchAddon, wrapper } = createXtermInstance(id);

  // sessions[id] 선 초기화 — wrapE2E의 동기 onReady 콜백이 참조할 수 있도록.
  // ws는 startSessionSocket()에서 채운다.
  // displayName의 출처는 레코드다(core/store.js의 sessionDisplayName).
  // 2026-09-18 후속(사용자 지적) — 예전엔 이름이 없으면 id를 8자로 잘라
  // 채웠다("의미 없는 문자열"이라는 지적의 실제 원인이 여기였다 — store.js의
  // sessionDisplayName이 폴백을 계산해도, 여기서 이미 displayName이 채워져
  // 있으면 그 폴백에 닿지도 못한다). null로 비워 두면 tmuxName이 있는
  // 세션은 그 이름을, 없는 일반 세션은 sessionDisplayName의 "터미널 N"
  // 폴백을 그대로 받는다.
  registerSession(id, { term, ws: null, displayName: displayName || null,
    fitAddon, searchAddon, wrapper, wsHandle: null, reconnTimer: null, ...recordOpts });
  // O1: 재연결 오버레이의 "다시 연결" 버튼이 이 세션의 connectTerminalWs를
  // 부를 수 있도록 참조를 걸어둔다 — startSessionSocket이 채운다.
  const onResize = startSessionSocket(id, term);
  getSession(id).onResize = onResize;
  // S4: 「앱에 마우스 이벤트 전달」이 off면 이 세션도 로컬 선택 모드로 만든다.
  // (설정이 나중에 바뀌면 mouse-mode.js가 구독으로 전체 세션에 반영한다 —
  // 여기서는 "새로 생긴 세션"만 챙긴다.)
  applyMouseMode(getSession(id));

  // 옛 "활성 칸이 비었으면 채운다" 추측은 없앴다 — 빈 칸이 흔해졌다.
  if (place === 'pane') switchTo(id);
  else if (place !== 'none') revealSession(id);
}

export function switchTo(id) {
  document.dispatchEvent(new CustomEvent('vt:session-switch', { detail: { id } }));
  // 활성 세션은 칸에서 파생된다(layout/store.js) — setActive를 안 부른다.
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
}

// 2026-09-21(사용자 지적) — 레일·팔레트·플릿의 "세션을 클릭한다"는 진입점이
// switchTo()를 직접 부르면, 그 세션이 이미 다른 세션과 화면(pane 트리)을
// 공유 중일 때(=그룹) 짝을 그 칸에서 밀어냈다. switchTo는 이름과 달리 "가서
// 보여줘"가 아니라 "지금 활성 pane에 이 세션을 배정해"라서다(활성 pane이
// 하필 짝이 있던 칸이면 그 칸을 뺏는다).
//
// 이 함수가 실제 "가서 보여줘"다: 그 세션이 어느 탭의 어느 pane에 이미
// 떠 있는지 찾아(store.js `revealSessionPane`) 그 탭·그 pane으로 이동할
// 뿐, 트리는 손대지 않는다 — 그룹 배치가 그대로 보존된다.
//
// 찾지 못하면(트리에 아직 없는, 방금 만들었거나 다른 세션에 자리를 내준
// 세션) **그냥 switchTo로 떨어지지 않는다** — 지금 활성 pane이 하필 다른
// 그룹의 칸일 수 있고, switchTo는 그 칸을 그대로 덮어써 버린다(위에서 막으려는
// 것과 같은 사고). 대신 이 세션만을 위한 새 탭을 연다 — `openGroupTab`이
// 원래 "아직 어디에도 없으면 새 화면" 몫으로 하던 일 그대로다(레일 openRow가
// 예전엔 이 함수와 switchTo를 나란히 불렀다).
export function revealSession(id) {
  if (!revealSessionPane(id)) openLayoutTab({ sessionId: id });
  document.dispatchEvent(new CustomEvent('vt:session-switch', { detail: { id } }));
  getSession(id).term.focus();
  updateSessionPicker();
  saveLayoutNow();
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
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      // 2026-09-21 설계 — tmux 세션 이름이 단일 진실이 되면서, 예전처럼
      // "웹 라벨만 조용히 바꾸는" 폴백이 없어졌다: 서버가 거절한 이유를
      // 그대로 보여준다.
      const msg = data?.error === 'tmux session name must be alphanumeric/dash/underscore only'
        ? t('세션 이름은 영숫자·dash·underscore만 가능합니다(tmux 제약)')
        : data?.error === 'tmux session name already exists'
        ? t('이미 있는 이름입니다')
        : t('세션 이름 변경 실패');
      if (typeof showToast === 'function') showToast(msg, 'error');
      return false;
    }
    // tmux 세션은 이름의 단일 진실이 tmux 세션 이름 자체다(core/store.js의
    // sessionDisplayName). 서버가 방금 rename-session한 실제 이름으로 로컬
    // 레코드를 맞춘다 — 5초 레일 폴링을 기다리지 않고 즉시 반영한다.
    if (data?.tmux_name) setSessionTmuxName(id, data.tmux_name);
    else setSessionDisplayName(id, newName);
    // 사용자가 직접 지은 이름은 그 뒤 어떤 자동 라벨링도 덮지 않는다
    // (term/tab-worktree.js의 워크트리 라벨이 첫 소비자다).
    s.renamed = true;
    updateSessionPicker();
    saveLayoutNow();   // 칸의 tmux 이름이 바뀌었다 — 복원이 새 이름으로 찾게
    return true;
  } catch (_) {
    // 편집 중 DOM에 남은 값을 되돌린다 — 실패했는데 새 이름이 남아 있으면
    // 사용자는 바뀐 줄 안다.
    setSessionDisplayName(id, previousName);
    if (typeof showToast === 'function') showToast(t('세션 이름 변경 실패'), 'error');
    return false;
  }
}

export async function removeSession(id) {
  const s = getSession(id);
  if (!s) return;
  // 대기 중인 재연결 타이머 취소 — 안 그러면 탭을 닫은 뒤에도 setTimeout이 살아남아
  // (id는 이미 delete되지만) 죽은 타이머가 지연 후 깨어난다.
  if (s.reconnTimer) { clearTimeout(s.reconnTimer); s.reconnTimer = null; }
  if (s.ws) { try { s.ws.close(); } catch (_) {} }
  disposeKeyboardInput(id);
  cancelPasteTransactions(id);
  s.term.dispose();
  s.wrapper.remove();
  window.removeEventListener('resize', s.onResize);
  if (window.visualViewport) {
    window.visualViewport.removeEventListener('resize', s.onResize);
  }
  const wasRemote = !!s.remote;
  removeSessionRecord(id);
  // 칸은 빈 칸으로 남긴다(2026-10-03). 다른 세션(remaining[0])을 끌어오던
  // 대체 로직은 없앴다.
  clearSessionFromLayout(id);
  // 원격 세션(N7/N39 3단계)의 PTY는 상대 호스트에 있다 — 여기 세션 id는 서버의
  // session_store에 아예 없으므로 DELETE를 보내면 404만 받는다. 프록시 소켓은
  // 위에서 닫았고, 그게 닫히면 상대가 자기 PTY를 정리한다.
  if (!wasRemote) await apiFetch(`${API_BASE}/api/sessions/${id}`, { method: 'DELETE' });
  updateSessionPicker();
  saveLayoutNow();
}

// 서버가 재시작되면 PTY(웹 세션 id)는 전부 사라지지만 tmux 세션은 산다. 열려 있던
// 페이지의 WS는 그 id로 다시 붙다가 4004("세션 없음")를 받는데, 이걸 "세션이
// 종료됨"으로만 읽어 칸을 비우면 레이아웃이 곧바로 tmux 이름 없이 저장돼(2026-10-06
// 폰 실측) 새로고침·새 탭도 빈 칸만 복원한다. 칸은 tmux 이름이 가리키고 attach는
// 구현 세부이므로: tmux 세션이 살아 있으면 같은 이름으로 다시 attach해 칸의 id만 바꾼다.
// 반환: true=복구됨(또는 그 사이 닫힘) · false=tmux 세션이 없다(종료됨 → 호출자가
// 칸을 비운다) · null=확인 못 함(서버 아직 준비 중 등 → 호출자가 백오프로 재시도).
const _recovering = new Set();
export async function recoverSession(id) {
  const s = getSession(id);
  const tmux = s && !s.remote && (s.tmuxName || s.tmux_name);
  if (!tmux) return false;
  if (_recovering.has(id)) return null;
  _recovering.add(id);
  try {
    let list;
    try {
      const r = await apiFetch(`${API_BASE}/api/tmux/sessions`);
      if (!r.ok) return null;
      list = await r.json();
    } catch (_) { return null; }
    if (!getSession(id)) return true;
    if (!Array.isArray(list) || !list.some((x) => x && x.name === tmux)) return false;
    let data;
    try {
      const res = await apiFetch(`${API_BASE}/api/tmux/attach`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: tmux, screen: screenId() }),
      });
      if (!res.ok) return res.status === 404 ? false : null;
      data = await res.json();
    } catch (_) { return null; }
    if (!data || !data.id) return null;
    if (!getSession(id)) {   // attach하는 사이 사용자가 닫았다 — 새 PTY는 놓는다
      await apiFetch(`${API_BASE}/api/sessions/${data.id}`, { method: 'DELETE' }).catch(() => {});
      return true;
    }
    if (!getSession(data.id)) {
      addSession(data.id, data.name || data.id, { place: 'none', tmuxName: data.tmux_session || tmux });
      const ns = getSession(data.id);
      if (ns) ns.tmuxName = data.tmux_session || tmux;
    }
    replaceSessionId(id, data.id);   // 칸이 새 id를 가리킨다 — 빈 칸이 되지 않는다
    await removeSession(id);          // 옛 xterm/소켓 정리(트리엔 이미 없어 칸은 안 건드린다)
    return true;
  } finally { _recovering.delete(id); }
}
window.vtRecoverSession = recoverSession;

// F3(c): data-action 위임용 등록. guide.show는 그 함수를 소유한 guide.js가
// 등록한다. session.tmux-list는 ADR-29 E에서 없앴다 — 그 목록 팝업이 하던
// 일(tmux 세션 목록 + 깨우기 + 완전 종료)은 이제 picker.js의 세션 관리
// 시트(session.manager)가 직접 한다.
registerAction('session.add-menu', (el, e) => showAddMenu(el, e));

// 외부(picker.js/quickopen.js/grid.js/snippets.js/viewer.js/moreMenu.js/voice.js)가
// bare identifier 또는 window.foo(e) 형태로 참조하므로 브리지 필요.
window.switchTo = switchTo;
window.revealSession = revealSession;
window.addSession = addSession;
window.removeSession = removeSession;
window.renameSession = renameSession;
window.showAddMenu = showAddMenu;
window.createSession = createSession;
registerAction('onboarding.tmux', () => { document.getElementById('onboarding')?.remove(); createTmuxSession(); });
registerAction('onboarding.plain', () => { document.getElementById('onboarding')?.remove(); createSession(); });
