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
// 세션 **순서**의 단일 출처(N37 3단계). 2.1.3까지 순서의 출처는 `#tabs .tab`의
// DOM 순서였다 — 좌우 순환(switchTabByOffset)도, 워크스페이스 스냅샷 저장도
// 그 DOM을 읽었다. 세션 탭 줄이 사라지면 그 출처도 같이 사라지므로 명시적
// 배열로 옮긴다. `sessions` 객체의 삽입 순서를 쓰지 않는 이유: 재정렬(드래그·
// 단축키)이 키 순서를 못 바꾼다 — 바꾸려면 객체를 통째로 다시 만들어야 한다.
const order = [];
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
 * 2.1.2까지 이 값의 출처는 탭 DOM이었다(`.tab-name`의 textContent). 그래서
 * 이름이 필요한 곳마다 DOM을 뒤졌고, 그 자리가 10개 파일에 흩어져 있었다.
 * 세션 탭 줄(#tabs)을 워크트리 탭 안으로 흡수하려면(10-shell-layout.md §4
 * 3단계) 그 DOM이 사라져야 했고, 그러면 이름도 같이 사라진다 — 먼저 출처를
 * 레코드로 옮겼다.
 *
 * 이름을 **쓰는** 곳은 세 군데뿐이다: 세션 생성(addSession), 사용자 변경
 * (renameSession), 워크트리 자동 라벨(term/tab-worktree.js). 그 셋이
 * `displayName`을 갱신하고, 나머지는 전부 이 함수로 읽는다.
 */
export function sessionDisplayName(id) {
  const s = sessions[id];
  if (!s) return String(id || '').slice(0, 8);
  const tmux = s.tmuxName || s.tmux_name;
  // 2026-09-19 후속(사용자 지적) — 예전 서버 코드가 tmux 세션의 이름을
  // `tmux:<이름>` 꼴로 붙여서 돌려줬다. 그 값이 이 세션의 displayName에
  // 이미 저장돼 있으면(워크스페이스 스냅샷에 박혀 서버를 고쳐도, 재시작해도
  // 안 지워진다) 계속 "tmux:1"처럼 보인다 — 진짜 사용자가 지은 이름이
  // 아니라 그 옛 접두사이므로 무시하고 깨끗한 tmux 이름으로 되돌린다.
  // 사용자가 그 뒤 정말로 이름을 바꾸면(renameSession) 이 조건에 안
  // 걸리므로 새 이름이 그대로 보인다.
  if (s.displayName && s.displayName !== `tmux:${tmux}`) return s.displayName;
  if (tmux) return tmux;
  // 일반(비 tmux) 세션의 폴백 이름이 id를 8자로 잘라 붙인 의미 없는
  // 문자열이었다 — 열린 순서대로 "터미널 1", "터미널 2"…
  return `터미널 ${plainSessionOrdinal(id)}`;
}

/** id가 몇 번째로 열린 일반(비 tmux) 세션인지 — 순서(order)를 훑어 tmux
 * 세션은 건너뛰고 센다. 세션이 닫히면 뒤쪽 번호가 하나씩 당겨진다(고정
 * 배지가 아니라 "지금 열려 있는 것들 중 몇 번째"라는 뜻 — 터미널 앱들의
 * 기본 창 번호 매기기와 같은 방식). */
function plainSessionOrdinal(id) {
  let n = 0;
  for (const oid of order) {
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

export function setActive(id) {
  activeId = id;
  window.activeId = activeId;
  notify();
}

// 세션 생성 — terminal.js의 addSession()이 xterm/WS/DOM을 다 갖춘 rich object를
// 만든 뒤 등록한다. 이후 필드 mutation은 getSession(id)로 얻은 참조에 직접 한다
// (같은 객체이므로 스토어에도 즉시 반영된다).
export function registerSession(id, data, insertBeforeId) {
  sessions[id] = data;
  const at = insertBeforeId ? order.indexOf(insertBeforeId) : -1;
  const cur = order.indexOf(id);
  if (cur !== -1) order.splice(cur, 1);
  if (at === -1) order.push(id);
  else order.splice(at, 0, id);
  notify();
}

export function removeSessionRecord(id) {
  delete sessions[id];
  const at = order.indexOf(id);
  if (at !== -1) order.splice(at, 1);
  notify();
}

/**
 * 세션 순서 — **단일 출처**. 스토어에 실제로 존재하는 것만 돌려준다
 * (레코드 없이 order에만 남는 유령 id는 방어적으로 걸러낸다).
 */
export function orderedSessionIds() {
  return order.filter((id) => !!sessions[id]);
}

/**
 * `draggedId`를 `targetId`의 앞(또는 `after=true`면 뒤)으로 옮긴다.
 * 반환값은 순서가 실제로 바뀌었는지 여부 — 호출자가 불필요한 저장을 피할 수 있다.
 */
export function moveSessionBefore(draggedId, targetId, after = false) {
  const from = order.indexOf(draggedId);
  if (from === -1 || draggedId === targetId) return false;
  const before = order.slice();
  order.splice(from, 1);
  let at = order.indexOf(targetId);
  if (at === -1) at = order.length;
  else if (after) at += 1;
  order.splice(at, 0, draggedId);
  const changed = before.some((id, i) => id !== order[i]);
  if (changed) notify();
  return changed;
}

/** 현재 세션을 delta(-1/+1)만큼 순서상에서 밀어낸다. 끝에서는 멈춘다(순환하지 않는다 —
 *  재정렬이 순환하면 "끝까지 밀었다"는 걸 알 수 없다). */
export function moveSessionByOffset(id, delta) {
  const ids = orderedSessionIds();
  const i = ids.indexOf(id);
  if (i === -1) return false;
  const j = i + delta;
  if (j < 0 || j >= ids.length) return false;
  return moveSessionBefore(id, ids[j], delta > 0);
}

/** 워크스페이스 복원처럼 "순서 전체"를 한 번에 정하는 경우. 목록에 없는 기존
 *  세션은 뒤에 그대로 남긴다(복원 중 새로 생긴 세션을 잃지 않는다). */
export function setSessionOrder(ids) {
  const seen = new Set();
  const next = [];
  for (const id of ids) {
    if (sessions[id] && !seen.has(id)) { seen.add(id); next.push(id); }
  }
  for (const id of order) if (!seen.has(id)) { seen.add(id); next.push(id); }
  order.length = 0;
  order.push(...next);
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
window.orderedSessionIds = orderedSessionIds;
window.setActive = setActive;
window.registerSession = registerSession;
window.sessionDisplayName = sessionDisplayName;
window.removeSessionRecord = removeSessionRecord;
window.storeSubscribe = subscribe;
