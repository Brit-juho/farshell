// A5 — 서버가 판정한 4상태(idle/working/waiting/done)를 프런트에서 들고 있는
// **유일한 곳**. 소비자(탭·pane 헤더·rail 목록·파비콘)는 여기서 읽기만 한다.
//
// A1 이전에는 각 소비자가 `state.tool` 유무로 상태를 각자 파생시켰다. 그래서
// 규칙이 흩어졌고(favicon은 자기만의 화이트리스트를 들고 있었다), 같은 화면에서
// 탭과 카드가 다른 판단을 하는 일이 가능했다. 이제 판정은 서버가 하고, 이
// 파일은 그걸 tmux 세션 이름으로 색인해 나눠주기만 한다.
//
// **키는 tmux 세션 이름이다.** 서버 상태 엔트리의 키(훅 session_id)는 프런트가
// 알 방법이 없고, 웹 세션 id는 재attach마다 바뀐다. A2가 각 엔트리에 붙여주는
// `tmux_session`이 두 세계를 잇는 안정적인 유일한 키다.
import { vtFetch } from '../core/api.js';
import { STATE_LABEL, URGENCY, normalizeState, entryUrgency, stateRank } from '../design/state-classes.js';

export { URGENCY };

const IDLE = 'idle';

// tmux 세션 이름 → 'idle'|'working'|'waiting'|'done' (알 수 없는 값은 들어올 때 idle로 접는다)
const _byTmux = new Map();
const _listeners = new Set();

// 2026-09-30 — 상태만이 아니라 **상태 문장에 쓰는 세부**(도구·경과 시간·질문·
// 선택지)와 **어떤 CLI인가**(에이전트 이름)도 여기서 들고 있는다. 레일·플릿이
// 같은 값을 4초마다 /api/agent/status·/api/agents로 따로 긁어 오고 있었는데,
// 세부는 /ws-agent가 이미 push하고 있었다(86분에 1,850회 — 거의 전부 중복).
// 엔트리 원본(엔트리 키 → 공개 엔트리)과, 그걸 세션 이름으로 접은 결과.
const _entries = new Map();
let _details = {};   // tmux 이름 → { since, tool, question, options, promptId, kind, badge }
let _names = {};     // tmux 이름 → CLI 이름(claude/codex/ssh/shell …)

// 「확인 안 함」(D4, 세션 로직 점검 단계 4). **서버가 정한 완료(done) 가운데 아직 어느 기기에서도 보지 않은
// 완료 턴**이다 — 모든 기기 공통이다. 엔트리의 `turn`(완료 턴 번호) > `seen_turn`(본 턴)이면 확인 안 함.
// 「본다」 = 그 세션이 현재 뷰의 포커스 칸이고 창이 보이는 상태(agent/paint.js가 probe로 알려준다) — 그때 이
// 기기가 서버에 그 턴을 봤다고 알리고(`/api/agent/seen`), 서버가 모든 기기에 `seen_turn`을 내려보낸다.
// 예전(N37)에는 기기마다 따로 셌고 작업 중·입력 대기로 바뀐 것까지 셌다 — 폰에서 본 완료가 맥에 계속 남고,
// 상태 표시와 겹치는 신호가 많았다. `turn`이 없는 옛 서버는 done 자체를 확인 안 함으로 본다(이 기기 안에서만 지움).
const _unseen = new Map();     // tmux 이름 → 확인 안 한 완료 턴(옛 서버면 null)
const _seenLocal = new Map();  // tmux 이름 → 이 기기가 이미 봤다고 보낸 턴(서버 반영 전 깜빡임 방지, 옛 서버는 -1)

// 「지금 보고 있는 세션인가」 — paint.js가 레이아웃·가시성을 알아서 주입한다.
// state.js는 core 싱글톤이라 layout/store를 끌어오지 않으려고 의존성을 뒤집었다.
let _seenProbe = () => false;
export function setSeenProbe(fn) {
  _seenProbe = typeof fn === 'function' ? fn : () => false;
}

export function getStatus(tmuxName) {
  return (tmuxName && _byTmux.get(tmuxName)) || IDLE;
}

/** 이 세션에 「확인 안 함」 표시를 붙여야 하는가. */
export function isUnseen(tmuxName) {
  return !!tmuxName && _unseen.has(tmuxName);
}

export function unseenCount() {
  return _unseen.size;
}

/** 이 세션의 확인 안 한 완료 턴(없으면 null). */
export function unseenTurn(tmuxName) {
  return (tmuxName && _unseen.get(tmuxName)) ?? null;
}

// onRetry: 연결 실패·서버 오류(5xx)면 불린다 — 호출자가 표시를 되돌려 다음에 다시 보내게 한다. 4xx(중계 화면·
// 비밀번호 없는 서버의 403 등)는 다시 보내도 같으므로 이 기기 표시만 내린 채 둔다(되돌리면 계속 다시 보낸다).
function _post(path, body, onRetry) {
  vtFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch((e) => {
    const status = e && typeof e.status === 'number' ? e.status : 0;
    if (onRetry && (status === 0 || status >= 500)) onRetry();
  });
}

function _sendSeen(name, turn) {
  // 실패하면 이 기기 기록만 지운다 — 다음 스냅샷·이벤트(재접속, 15초 하트비트)가 다시 보낸다. 여기서 바로 다시
  // 계산하면 보고 있는 세션이라 곧바로 또 보내, 연결이 끊긴 동안 요청을 쉬지 않고 쏟는다.
  _post('/api/agent/seen', { tmux_session: name, turn }, () => {
    if (_seenLocal.get(name) === turn) _seenLocal.delete(name);
  });
}

// 사용자가 그 세션을 실제로 봤다 — 포커스 칸이 되고 창이 보일 때 불린다. done 상태 자체는 건드리지 않는다
// (ackLocal과 다른 점): 「봤다」와 「완료를 치웠다」는 다른 사건이다.
export function markSeen(tmuxName) {
  if (_markSeen(tmuxName)) _notify();
}

function _markSeen(name) {
  if (!name || !_unseen.has(name)) return false;
  const turn = _unseen.get(name);
  _unseen.delete(name);
  _seenLocal.set(name, turn ?? -1);
  if (turn != null) _sendSeen(name, turn);
  return true;
}

// 엔트리들 → 세션마다 확인 안 한 완료 턴. 같은 세션에 done 엔트리가 여럿이면 가장 새 턴.
function _computeUnseen() {
  const next = new Map();
  for (const ent of _entries.values()) {
    const name = _localName(ent);
    if (!name || normalizeState(ent.status) !== 'done') continue;
    const turn = Number.isInteger(ent.turn) ? ent.turn : null;
    if (turn != null && turn <= (Number.isInteger(ent.seen_turn) ? ent.seen_turn : 0)) continue;
    const local = _seenLocal.get(name);
    if (turn == null ? local === -1 : (local ?? 0) >= turn) continue;
    const cur = next.get(name);
    if (!next.has(name) || (turn != null && (cur == null || turn > cur))) next.set(name, turn);
  }
  // 이 기기 기록은 그 세션이 다시 done이 아니게 되면 비운다(옛 서버의 다음 완료를 다시 알리려고).
  for (const name of [..._seenLocal.keys()]) {
    const done = [..._entries.values()].some((e) => _localName(e) === name && normalizeState(e.status) === 'done');
    if (!done) _seenLocal.delete(name);
  }
  // 보고 있는 세션의 완료는 바로 본 것으로 친다(rAF 뒤에 지우면 한 프레임 깜박인다).
  for (const name of [...next.keys()]) {
    if (_seenProbe(name)) {
      const turn = next.get(name);
      next.delete(name);
      _seenLocal.set(name, turn ?? -1);
      if (turn != null) _sendSeen(name, turn);
    }
  }
  const changed = next.size !== _unseen.size || [...next].some(([k, v]) => !_unseen.has(k) || _unseen.get(k) !== v);
  _unseen.clear();
  for (const [k, v] of next) _unseen.set(k, v);
  return changed;
}

export function allStatuses() {
  return new Map(_byTmux);
}

export function onStatusChange(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

function _notify() {
  for (const fn of _listeners) {
    try { fn(); } catch (_) { /* 소비자 하나가 터져도 나머지는 갱신된다 */ }
  }
}

// 서버 스냅샷(`/ws-agent`의 agent_snapshot.all 또는 GET /api/agent/status의 all)
// 전체를 반영한다. 스냅샷에 없는 세션은 idle로 되돌린다 — 재접속 시 예전 상태가
// 유령으로 남지 않게(서버가 TTL로 이미 정리했을 수 있다).
export function applySnapshot(all) {
  _entries.clear();
  for (const [k, entry] of Object.entries(all || {})) {
    if (entry) _entries.set(_entryKey(k, entry), entry);
  }
  _recompute();
}

// 단일 이벤트(agent_event) 반영. 서버가 세션을 특정하지 못했으면(같은 cwd에
// 세션이 여럿 등) 아무것도 하지 않는다 — "모호하면 안 켠다" 규칙 그대로다.
//
// 2026-10-13 — 이벤트로 온 엔트리 **하나의** 상태로 세션 상태를 덮어쓰던 것을, 스냅샷과
// 같은 규칙(그 세션 엔트리들 중 가장 급한 것)으로 다시 계산하게 바꿨다. 예전엔 훅마다
// working, 15초 하트비트 스냅샷마다 옛 엔트리의 done으로 깜빡였다. `released`(반납·은퇴·
// TTL 삭제)는 그 엔트리를 지운다 — 서버가 지운 엔트리를 다음 스냅샷까지 들고 있지 않게.
export function applyEvent(state) {
  if (!state) return;
  // session_id가 없는 옛 형태의 이벤트는 세션당 엔트리 하나로 취급한다.
  const key = _entryKey(null, state) ?? (state.tmux_session ? `?\n${state.tmux_session}` : null);
  if (state.released) {
    if (key == null || !_entries.delete(key)) return;
  } else {
    if (!state.tmux_session) return;
    _entries.set(key, state);
  }
  _recompute();
}

// _entries → 세션별 상태·세부·확인 안 한 완료.
function _recompute() {
  const next = new Map();
  for (const entry of _entries.values()) {
    const name = _localName(entry);
    if (!name) continue;
    const status = normalizeState(entry.status);
    // 같은 tmux 세션에 엔트리가 여럿이면(소유자 없는 세션의 자기보고 + 화면 감지) 더 급한 쪽을 쓴다.
    const cur = next.get(name);
    if (!cur || entryUrgency(status) < entryUrgency(cur)) next.set(name, status);
  }
  const changed = next.size !== _byTmux.size
    || [...next].some(([k, v]) => _byTmux.get(k) !== v);
  _byTmux.clear();
  for (const [k, v] of next) _byTmux.set(k, v);
  const unseenChanged = _computeUnseen();
  const detailsChanged = _rebuildDetails();
  if (changed || unseenChanged || detailsChanged) _notify();
}

// 2026-10-15 — 이 서버 자신의 엔트리만 tmux 이름 지도에 들어간다. 피어 엔트리(`host`가 다른
// 호스트)는 이름만 같은 **다른 기계의 세션**이다 — M1의 `server-2`(done)가 로컬 `server-2`
// (working)를 덮어 「완료」로 굳었고, Claude를 끝내 로컬 엔트리가 반납돼도 그 done이 남아
// 「터미널」로 돌아가지 않았다. 서버의 이름 판정(_apply_entry_agent_overrides)은 이미 같은
// 규칙으로 거른다. 원격 호스트 화면은 relay로 그 서버의 엔트리를 받으니 거기선 그게 `local`이다.
function _localName(ent) {
  if (!ent || (ent.host && ent.host !== 'local')) return null;
  return ent.tmux_session || null;
}

// 스냅샷의 키는 서버의 불투명 키, 이벤트에는 키가 없다 — 둘을 같은 엔트리로
// 맞추려고 엔트리 자신의 host/session_id를 쓴다(agent_status._entry가 넣는다).
function _entryKey(objKey, ent) {
  if (ent && ent.session_id != null) return `${ent.host || 'local'}\n${ent.session_id}`;
  return objKey;
}

// 키 순서와 무관하게 같은 값이면 같은 문자열 — "실제로 바뀔 때만 통지"용.
function _canon(obj) {
  return JSON.stringify(Object.keys(obj).sort().map((k) => [k, obj[k]]));
}

// 세션마다 세부는 **상태를 정한 엔트리**(더 급한 쪽)에서 가져온다 — 상태는
// waiting인데 문장은 다른 엔트리의 도구 이름을 말하면 안 된다.
function _rebuildDetails() {
  const next = {};
  const rank = {};
  for (const ent of _entries.values()) {
    const name = _localName(ent);
    if (!name) continue;
    const u = entryUrgency(ent.status);
    if (name in next && u >= rank[name]) continue;
    rank[name] = u;
    next[name] = {
      since: ent.since ?? null,
      tool: ent.tool || ent.last_tool || null,
      question: ent.question ?? null,
      options: ent.options ?? null,
      promptId: ent.prompt_id ?? null,
      // 서버가 정한 waiting의 유형(approval/choice/plan/question)과 done의 보조 표시(question/plan).
      kind: ent.kind ?? null,
      badge: ent.badge ?? null,
      // 서버가 만든 안전한 도구 요약(파일 경로·프로그램 이름)과 마지막으로 사람이 친 지시(이 맥 화면에만 온다).
      summary: ent.summary ?? null,
      lastPrompt: ent.last_prompt ?? null,
      ...(ent.subagents ? { subagents: ent.subagents } : {}),
    };
  }
  if (_canon(next) === _canon(_details)) return false;
  _details = next;
  return true;
}

/** tmux 이름 → 상태 문장 세부. 소비자는 읽기만 한다. */
export function agentDetails() {
  return { ..._details };
}

/** 서버 감지 결과(`/ws-agent`의 agents, GET /api/agents와 같은 모양)를 반영한다. */
export function applyAgentNames(agents) {
  const next = {};
  for (const [name, v] of Object.entries(agents || {})) {
    if (v && v.agent) next[name] = v.agent;
  }
  if (_canon(next) === _canon(_names)) return;
  _names = next;
  _notify();
}

/** tmux 이름 → 떠 있는 CLI 이름. */
export function agentNames() {
  return { ..._names };
}

// 사용자가 완료를 확인했다(탭/카드 클릭) → 로컬에서 즉시 idle로 내리고, 서버에도 알린다.
// 서버가 done을 그대로 들고 있으면 다음 스냅샷(15초 하트비트)이 "idle→done 전이"로 읽혀
// 완료와 「확인하지 않음」이 되살아난다 — 서버가 내려야 모든 기기가 같은 idle을 받는다.
// 서버 호출이 실패해도 로컬 동작은 그대로다(다음 확인 때 다시 시도한다).
// 완료 턴을 함께 보낸다 — 그 사이 새 완료가 왔으면 서버는 새 완료를 치우지 않는다.
export function ackLocal(tmuxName) {
  const turn = _doneTurn(tmuxName);
  const wasUnseen = _unseen.delete(tmuxName);
  if (_byTmux.get(tmuxName) === 'done') {
    _seenLocal.set(tmuxName, turn ?? -1);
    _byTmux.set(tmuxName, IDLE);
    _notify();
    // done일 때만 보낸다 — 세션을 클릭할 때마다(작업 중이어도) 요청이 나가지 않게.
    _post('/api/agent/ack', turn == null ? { tmux_session: tmuxName } : { tmux_session: tmuxName, turn });
  } else if (wasUnseen) {
    _notify();
  }
}

// 그 세션 done 엔트리들의 가장 새 완료 턴(옛 서버면 null).
function _doneTurn(name) {
  let best = null;
  for (const ent of _entries.values()) {
    if (_localName(ent) !== name || normalizeState(ent.status) !== 'done' || !Number.isInteger(ent.turn)) continue;
    if (best == null || ent.turn > best) best = ent.turn;
  }
  return best;
}

// 세션 목록 정렬 — rail·팔레트·세션 시트 전용(정렬을 이름순으로 바꾸는 건 5단계).
// **탭 순서는 정렬하지 않는다**(사용자가 드래그로 정한 의도라 건드리면 안 된다).
export function sortByUrgency(items, getName) {
  const rank = (x) => { const n = getName(x); return stateRank(getStatus(n), isUnseen(n)); };
  return [...items].sort((a, b) => rank(a) - rank(b));
}

// 상태 dot 하나를 만든다. 색·breathing 애니메이션은 CSS가 data-state로 전부
// 따라오므로(styles/layers/components.css) 여기서는 속성만 세팅한다.
function statusDot(status) {
  const el = document.createElement('span');
  el.className = 'status-dot';
  el.dataset.state = status || IDLE;
  const label = STATE_LABEL[status] || STATE_LABEL.idle;
  el.setAttribute('data-tip', label);
  el.setAttribute('aria-label', label);
  return el;
}

// 이미 있는 dot을 갱신하거나, 없으면 붙인다(재생성하면 CSS 애니메이션이 매번
// 처음부터 다시 돈다 — waiting의 breathing이 끊겨 보인다).
export function applyStatusDot(container, status) {
  let dot = container.querySelector(':scope > .status-dot');
  if (!dot) {
    dot = statusDot(status);
    container.prepend(dot);
    return dot;
  }
  if (dot.dataset.state !== (status || IDLE)) {
    dot.dataset.state = status || IDLE;
    const label = STATE_LABEL[status] || STATE_LABEL.idle;
    dot.setAttribute('data-tip', label);
    dot.setAttribute('aria-label', label);
  }
  return dot;
}

// N36(워크트리 레일) — shell/Rail.tsx는 지연 청크(panels/viewer-lazy.js와
// 같은 이유, ADR-26/N35 커밋 참고)라 이 모듈을 직접 import하면 상태가
// 복제된다. classic script 호환용으로 이미 이런 값들을 window에 거는
// 관행(core/store.js 등)을 그대로 따른다.
window.getStatus = getStatus;
window.onStatusChange = onStatusChange;
window.isUnseen = isUnseen;
window.sortByUrgency = sortByUrgency;
// N38(70-mobile.md §2) — Fleet.tsx(shell 지연 청크)가 "완료 · 확인 안 함" 행
// 탭 처리에 쓴다. 위 값들과 같은 이유로 window 브리지만 노출한다.
window.markSeen = markSeen;
window.allStatuses = allStatuses;
// 2026-09-22(사용자 지적) — Fleet.tsx가 markSeen만 불러서 폰에서는 완료(파란
// tone)가 「확인 안 함」 배지만 지우고 영원히 안 풀렸다(데스크톱 레일도 같은
// 증상이었고, 거긴 data-session-id 배선 누락이 원인 — 여기는 애초에 markSeen을
// 불렀다). ackLocal이 markSeen의 상위집합(unseen 해제 + done→idle)이라
// Fleet.tsx 쪽 호출을 이걸로 바꾼다.
window.ackLocal = ackLocal;
// 2026-09-30 — 레일·플릿(지연 청크)이 세부·이름을 폴링 대신 여기서 읽는다.
// 쓰기 쪽 둘은 그 청크들의 저빈도 안전망 폴링이 결과를 이 저장소에 넣는 길이다.
window.agentDetails = agentDetails;
window.agentNames = agentNames;
window.vtApplyAgentSnapshot = applySnapshot;
window.vtApplyAgentNames = applyAgentNames;
