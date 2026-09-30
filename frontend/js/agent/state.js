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
import { STATE_LABEL } from '../design/state-classes.js';

const IDLE = 'idle';

// tmux 세션 이름 → 'idle'|'working'|'waiting'|'done'|'error'
const _byTmux = new Map();
const _listeners = new Set();

// 2026-09-30 — 상태만이 아니라 **상태 문장에 쓰는 세부**(도구·경과 시간·질문·
// 선택지)와 **어떤 CLI인가**(에이전트 이름)도 여기서 들고 있는다. 레일·플릿이
// 같은 값을 4초마다 /api/agent/status·/api/agents로 따로 긁어 오고 있었는데,
// 세부는 /ws-agent가 이미 push하고 있었다(86분에 1,850회 — 거의 전부 중복).
// 엔트리 원본(엔트리 키 → 공개 엔트리)과, 그걸 세션 이름으로 접은 결과.
const _entries = new Map();
let _details = {};   // tmux 이름 → { since, tool, question, options, promptId }
let _names = {};     // tmux 이름 → CLI 이름(claude/codex/ssh/shell …)

// N37 — 「읽지 않음」. 정의(10-shell-layout.md §4): **상태가 `done`인데 그
// 세션이 그 뒤로 한 번도 사용자 눈에 띈 적이 없음.** done 자체와 구분되는
// 이유는, done은 서버가 정하고 「읽지 않음」은 **이 브라우저가** 정하기
// 때문이다 — 폰에서 확인한 완료가 맥에서도 읽음이 되면 안 되고, 그 반대도
// 안 된다(기기마다 본 것이 다르다).
//
// 여기 담기는 건 "아직 안 본 done"의 tmux 이름들이다. done → 다른 상태로
// 바뀌면 지운다(더 이상 알릴 완료가 아니다).
const _unseen = new Set();

// 2-6 정렬 우선순위 — "내 개입이 필요한 것이 항상 맨 위". 서버(agent_status의
// _URGENCY)와 **같은 순서**여야 한다: 서버는 큐 판정에, 여기는 목록 정렬에
// 쓰지만 사용자에게는 하나의 규칙으로 보여야 한다.
export const URGENCY = { waiting: 0, done: 1, working: 2, error: 3, idle: 4 };

export function getStatus(tmuxName) {
  return (tmuxName && _byTmux.get(tmuxName)) || IDLE;
}

// N37 — 이 세션에 「읽지 않음」 배지를 붙여야 하는가.
export function isUnseen(tmuxName) {
  return !!tmuxName && _unseen.has(tmuxName);
}

export function unseenCount() {
  return _unseen.size;
}

// 사용자가 그 세션을 실제로 봤다 — 탭/페인이 활성이 되면 호출된다. done
// 상태 자체는 건드리지 않는다(ackLocal과 다른 점): "봤다"와 "완료를 치웠다"는
// 다른 사건이고, 완료 표시를 언제 내릴지는 ackLocal이 따로 정한다.
export function markSeen(tmuxName) {
  if (tmuxName && _unseen.delete(tmuxName)) _notify();
}

// 상태 전이에 따라 unseen을 갱신한다. done으로 **새로** 들어올 때만 unseen에
// 넣는다 — 이미 done인 채로 스냅샷이 다시 와도(폴링) 방금 읽음 처리한 걸
// 되살리면 안 된다.
function _trackUnseen(name, prev, next) {
  if (next === 'done' && prev !== 'done') _unseen.add(name);
  else if (next !== 'done') _unseen.delete(name);
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
  const next = new Map();
  for (const entry of Object.values(all || {})) {
    const name = entry && entry.tmux_session;
    if (!name) continue;
    const status = entry.status || IDLE;
    // 같은 tmux 세션에 엔트리가 여럿이면(훅 + pane 자기보고) 더 급한 쪽을 쓴다.
    const cur = next.get(name);
    if (!cur || URGENCY[status] < URGENCY[cur]) next.set(name, status);
  }
  const changed = next.size !== _byTmux.size
    || [...next].some(([k, v]) => _byTmux.get(k) !== v);
  // N37 — 전이를 보고 unseen을 갱신한다. 스냅샷에서 사라진 세션(=서버가 TTL로
  // 정리했거나 세션이 닫힘)은 알릴 완료도 같이 사라진다.
  for (const [name, status] of next) _trackUnseen(name, _byTmux.get(name), status);
  for (const name of _byTmux.keys()) if (!next.has(name)) _unseen.delete(name);
  _byTmux.clear();
  for (const [k, v] of next) _byTmux.set(k, v);
  const detailsChanged = _rebuildDetails();
  if (changed || detailsChanged) _notify();
}

// 단일 이벤트(agent_event) 반영. 서버가 세션을 특정하지 못했으면(같은 cwd에
// 세션이 여럿 등) 아무것도 하지 않는다 — "모호하면 안 켠다" 규칙 그대로다.
export function applyEvent(state) {
  const name = state && state.tmux_session;
  if (!name) return;
  const key = _entryKey(null, state);
  if (key != null) _entries.set(key, state);
  const detailsChanged = _rebuildDetails();
  const status = state.status || IDLE;
  if (_byTmux.get(name) === status) {
    // 상태는 그대로여도 도구가 바뀌거나 질문이 붙었으면 상태 문장은 다시 그려야 한다.
    if (detailsChanged) _notify();
    return;
  }
  _trackUnseen(name, _byTmux.get(name), status);
  _byTmux.set(name, status);
  _notify();
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
    const name = ent && ent.tmux_session;
    if (!name) continue;
    const u = URGENCY[ent.status || IDLE] ?? 9;
    if (name in next && u >= rank[name]) continue;
    rank[name] = u;
    next[name] = {
      since: ent.since ?? null,
      tool: ent.tool || ent.last_tool || null,
      question: ent.question ?? null,
      options: ent.options ?? null,
      promptId: ent.prompt_id ?? null,
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

// 사용자가 완료를 확인했다(탭/카드 클릭) → 로컬에서 즉시 idle로 내린다.
// 서버 ack API는 A6 이후에 붙인다 — 지금은 화면 반응성이 목적이고, 서버 쪽은
// done TTL(30분)이 어차피 정리한다.
export function ackLocal(tmuxName) {
  const wasUnseen = _unseen.delete(tmuxName);
  if (_byTmux.get(tmuxName) === 'done') {
    _byTmux.set(tmuxName, IDLE);
    _notify();
  } else if (wasUnseen) {
    _notify();
  }
}

// 세션 목록 정렬 — rail·팔레트·세션 시트 전용.
// **탭 순서는 정렬하지 않는다**(사용자가 드래그로 정한 의도라 건드리면 안 된다).
export function sortByUrgency(items, getName) {
  return [...items].sort((a, b) => {
    const ua = URGENCY[getStatus(getName(a))] ?? 9;
    const ub = URGENCY[getStatus(getName(b))] ?? 9;
    return ua - ub;
  });
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
