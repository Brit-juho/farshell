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
window.removeSessionRecord = removeSessionRecord;
window.storeSubscribe = subscribe;
