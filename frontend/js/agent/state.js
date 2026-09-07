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

// 2-6 정렬 우선순위 — "내 개입이 필요한 것이 항상 맨 위". 서버(agent_status의
// _URGENCY)와 **같은 순서**여야 한다: 서버는 큐 판정에, 여기는 목록 정렬에
// 쓰지만 사용자에게는 하나의 규칙으로 보여야 한다.
export const URGENCY = { waiting: 0, done: 1, working: 2, error: 3, idle: 4 };

export function getStatus(tmuxName) {
  return (tmuxName && _byTmux.get(tmuxName)) || IDLE;
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
  _byTmux.clear();
  for (const [k, v] of next) _byTmux.set(k, v);
  if (changed) _notify();
}

// 단일 이벤트(agent_event) 반영. 서버가 세션을 특정하지 못했으면(같은 cwd에
// 세션이 여럿 등) 아무것도 하지 않는다 — "모호하면 안 켠다" 규칙 그대로다.
export function applyEvent(state) {
  const name = state && state.tmux_session;
  if (!name) return;
  const status = state.status || IDLE;
  if (_byTmux.get(name) === status) return;
  _byTmux.set(name, status);
  _notify();
}

// 사용자가 완료를 확인했다(탭/카드 클릭) → 로컬에서 즉시 idle로 내린다.
// 서버 ack API는 A6 이후에 붙인다 — 지금은 화면 반응성이 목적이고, 서버 쪽은
// done TTL(30분)이 어차피 정리한다.
export function ackLocal(tmuxName) {
  if (_byTmux.get(tmuxName) === 'done') {
    _byTmux.set(tmuxName, IDLE);
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
export function statusDot(status) {
  const el = document.createElement('span');
  el.className = 'status-dot';
  el.dataset.state = status || IDLE;
  el.title = STATE_LABEL[status] || STATE_LABEL.idle;
  el.setAttribute('aria-label', el.title);
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
    dot.title = STATE_LABEL[status] || STATE_LABEL.idle;
    dot.setAttribute('aria-label', dot.title);
  }
  return dot;
}
