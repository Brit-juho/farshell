// 세션 스토어 — F3(b)에서 신설. terminal.js의 전역 `sessions`/`activeId`(구 :1-8)를
// 이 모듈이 정식으로 소유한다. F2 때는 activeId를 읽기전용 getter로만 브리지했지만
// (panels/panel.js가 읽기만 했으므로 충분했다), 이제 재할당(=세션 전환/제거)도
// 이 모듈을 거치게 해 구독자에게 변경을 알릴 수 있게 한다.
//
// terminal.js는 F4(큰 파일 분할) 전까지 classic script로 남아 세션 객체(xterm
// 인스턴스·WS·DOM 참조를 담은 rich object)를 직접 구성한다 — 그 필드 단위 mutation
// (`sessions[id].name = x` 등)까지 이 모듈을 거치게 강제하면 F4가 어차피 다시 쪼갤
// 코드를 지금 통째로 다시 쓰는 것과 같아진다. 대신 "무엇이 세션 목록에 존재하는가"
// 라는 구조적 변경(생성/삭제/활성 전환)만 이 모듈의 책임으로 좁혔다 — 상태 변경
// 추적이 불가능해지는 지점은 정확히 그 구조적 변경이었다.

import { t } from './i18n.js';
const sessions = {};
let activeId = null;
const subscribers = new Set();

function notify() {
  for (const fn of subscribers) {
    try { fn(); } catch (e) { console.error('[store] subscriber error', e); }
  }
}

export function getSession(id) {
  return sessions[id];
}

export function activeSession() {
  return activeId ? sessions[activeId] : undefined;
}

export function activeSessionId() {
  return activeId;
}

// 세션 목록 전체를 순회해야 하는 소비처(피커·퀵오픈 등)용 — 반환된 객체는
// 스토어가 들고 있는 것과 같은 참조이므로 구조를 직접 바꾸지 않고 읽기에만 쓴다.
export function allSessions() {
  return sessions;
}

/**
 * 세션의 표시 이름 — **단일 출처**.
 *
 * 2026-09-21 설계 — tmux 세션의 이름은 **tmux 세션 이름 그 자체**다. 예전엔
 * 사용자가 지은 `displayName`이 tmux 진짜 이름보다 우선이었는데, 그러면 이
 * 세션을 rename한 뒤 tmux 밖(iTerm2, 다른 기기)에서 이름이 또 바뀌어도 이
 * 화면만 옛 이름을 계속 보여주는 두 번째 진실이 생겼다. `s.tmuxName`은
 * 살아있는 동안 계속 최신으로 동기화되므로(Rail.tsx의 폴링, renameSession의
 * 즉시 갱신) 그냥 그대로 읽으면 된다.
 *
 * 비 tmux(일반) 세션은 tmux 정체성이 없어 이 정책의 대상이 아니다 — 여전히
 * `displayName`(세션 생성 시 addSession, 사용자 변경 시 renameSession이 쓴다)
 * 을 쓴다.
 */
export function sessionDisplayName(id) {
  const s = sessions[id];
  if (!s) return String(id || '').slice(0, 8);
  const tmux = s.tmuxName || s.tmux_name;
  if (tmux) return tmux;
  if (s.displayName) return s.displayName;
  // 일반(비 tmux) 세션의 폴백 이름이 id를 8자로 잘라 붙인 의미 없는
  // 문자열이었다 — 열린 순서대로 "터미널 1", "터미널 2"…
  return t('터미널 {num}', { num: plainSessionOrdinal(id) });
}

/** id가 몇 번째로 열린 일반(비 tmux) 세션인지 — 등록 순서(객체 삽입 순서)로
 * tmux 세션은 건너뛰고 센다. 이동 순서와는 무관한 이름 번호일 뿐이다. 세션이 닫히면 뒤쪽 번호가 하나씩 당겨진다(고정
 * 배지가 아니라 "지금 열려 있는 것들 중 몇 번째"라는 뜻 — 터미널 앱들의
 * 기본 창 번호 매기기와 같은 방식). */
function plainSessionOrdinal(id) {
  let n = 0;
  for (const oid of Object.keys(sessions)) {
    const s = sessions[oid];
    if (!s || s.tmuxName || s.tmux_name) continue;
    n += 1;
    if (oid === id) return n;
  }
  return n || 1;
}

export function setSessionDisplayName(id, name) {
  const s = sessions[id];
  if (!s) return;
  s.displayName = name;
  notify();
}

/** tmux 세션 이름의 로컬 캐시를 최신으로 맞춘다(2026-09-21 설계 — tmux 세션
 * 이름이 단일 진실이라 이 필드가 sessionDisplayName의 실제 출처다). 이 UI로
 * rename한 직후(term/session.js)와, 레일의 5초 폴링이 살아있는 세션의 실시간
 * tmux 이름을 다시 확인할 때(shell/Rail.tsx) 둘 다 이 함수를 거친다 — 값이
 * 같으면 굳이 구독자를 깨우지 않는다(폴링마다 매번 재렌더되는 걸 막는다). */
export function setSessionTmuxName(id, tmuxName) {
  const s = sessions[id];
  if (!s || !tmuxName || s.tmuxName === tmuxName) return;
  s.tmuxName = tmuxName;
  notify();
}

export function setActive(id) {
  activeId = id;
  window.activeId = activeId;
  notify();
}

// 세션 생성 — terminal.js의 addSession()이 xterm/WS/DOM을 다 갖춘 rich object를
// 만든 뒤 등록한다. 이후 필드 mutation은 getSession(id)로 얻은 참조에 직접 한다
// (같은 객체이므로 스토어에도 즉시 반영된다).
export function registerSession(id, data) {
  sessions[id] = data;
  notify();
}

export function removeSessionRecord(id) {
  delete sessions[id];
  notify();
}

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

// 아직 classic script인 terminal.js/picker.js/grid.js/quickopen.js/search.js/
// snippets.js/theme.js/viewer.js가 bare identifier(sessions, activeId)로 계속
// 읽으므로 window에도 노출한다 — sessions는 스토어와 같은 객체 참조라, classic
// script가 필드를 mutate해도 스토어가 그 값을 그대로 들고 있다.
window.sessions = sessions;
window.activeId = activeId;
window.getSession = getSession;
window.activeSession = activeSession;
window.activeSessionId = activeSessionId;
window.allSessions = allSessions;
window.setActive = setActive;
window.registerSession = registerSession;
window.sessionDisplayName = sessionDisplayName;
window.setSessionTmuxName = setSessionTmuxName;
window.removeSessionRecord = removeSessionRecord;
window.storeSubscribe = subscribe;
