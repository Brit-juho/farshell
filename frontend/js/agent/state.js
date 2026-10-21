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

// N37 — 「읽지 않음」. 정의(terminal.md 「States」): **상태가 어떤 값으로든 바뀐 뒤
// 사용자가 그 세션을 아직 보지 않았음.** done만이 아니라 working·waiting·idle로
// 바뀐 것도 해당한다. 「본다」 = 그 세션이 현재 뷰의 포커스 칸이고 창이 보이는
// 상태(agent/paint.js가 probe로 알려준다). **보고 있는 세션의 전이는 unseen을
// 만들지 않는다** — 비활성 세션의 전이만 만든다.
//
// done 자체와 구분되는 이유는, 상태는 서버가 정하고 「읽지 않음」은 **이
// 브라우저가** 정하기 때문이다 — 폰에서 확인한 완료가 맥에서도 읽음이 되면
// 안 되고, 그 반대도 안 된다(기기마다 본 것이 다르다).
const _unseen = new Set();

// 「지금 보고 있는 세션인가」 — paint.js가 레이아웃·가시성을 알아서 주입한다.
// state.js는 core 싱글톤이라 layout/store를 끌어오지 않으려고 의존성을 뒤집었다.
let _seenProbe = () => false;
export function setSeenProbe(fn) {
  _seenProbe = typeof fn === 'function' ? fn : () => false;
}

// 첫 스냅샷(페이지 로드 직후)은 "방금 바뀐 것"이 아니라 현재 상태의 보고다 —
// 로드 때마다 작업 중인 세션 전부에 링이 뜨면 노이즈다. done만 예전 규칙 그대로
// (끝났는데 못 본 완료는 로드 직후에도 알려야 한다).
let _hydrated = false;

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

// 상태 전이에 따라 unseen을 갱신한다. 값이 **바뀔 때만** 건드린다 — 같은 상태의
// 스냅샷이 폴링으로 다시 와도 방금 읽음 처리한 걸 되살리면 안 된다.
function _trackUnseen(name, prev, next, live = false) {
  const before = prev || IDLE;
  if (before === next) return;
  if (_seenProbe(name)) { _unseen.delete(name); return; }
  if (!_hydrated && !live && next !== 'done') return;
  _unseen.add(name);
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
  _recompute(false);
  _hydrated = true;
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
  _recompute(true);
}

// _entries → 세션별 상태·세부. live=true면 실제 전이(단일 이벤트)라 로드 직후 규칙을
// 건너뛰고 「읽지 않음」을 매긴다.
function _recompute(live) {
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
  // N37 — 전이를 보고 unseen을 갱신한다. 사라진 세션(=서버가 TTL로 정리했거나 세션이
  // 닫힘·반납)은 알릴 완료도 같이 사라진다.
  for (const [name, status] of next) _trackUnseen(name, _byTmux.get(name), status, live);
  for (const name of _byTmux.keys()) if (!next.has(name)) _unseen.delete(name);
  _byTmux.clear();
  for (const [k, v] of next) _byTmux.set(k, v);
  const detailsChanged = _rebuildDetails();
  if (changed || detailsChanged) _notify();
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
export function ackLocal(tmuxName) {
  const wasUnseen = _unseen.delete(tmuxName);
  if (_byTmux.get(tmuxName) === 'done') {
    _byTmux.set(tmuxName, IDLE);
    _notify();
    _ackServer(tmuxName);
  } else if (wasUnseen) {
    _notify();
  }
}

// done일 때만 부른다 — 세션을 클릭할 때마다(작업 중이어도) 요청이 나가지 않게.
function _ackServer(tmuxName) {
  vtFetch('/api/agent/ack', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tmux_session: tmuxName }),
  }).catch(() => { /* 로컬은 이미 내렸다 — 서버가 다음 확인 때 다시 받는다 */ });
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
