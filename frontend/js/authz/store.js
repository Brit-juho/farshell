// 승인 대기 목록의 단일 저장소 + 이벤트 처리. 화면(inbox·grants·배지)은 여기를 구독한다.
//
// 이벤트 규칙(`/ws-workspace`): 새 이벤트 `authz_request`/`authz_resolved`/`authz_revoked` 는 id 와 종류만 싣는다(내용 없음) —
// 받으면 목록을 서버에서 다시 읽는다. 옛 `agent_share_request*` 는 새 이벤트가 한 번도 오지 않은 동안에만 같은 일을 한다
// (서버가 한 릴리스 동안 둘을 함께 보낸다 — 새 이벤트를 한 번이라도 받으면 옛 것은 무시해 같은 요청을 두 번 처리하지 않는다).
// 다시 읽기는 짧게 묶어(coalesce) 옛·새가 잇따라 와도 요청 한 번이다.
// 페어·폴더 lease 화면은 `{type:'pairs'}`·`{type:'leases'}` 변경 알림을 구독해 다시 읽는다 — 같은 이벤트 묶음(요청→결정→회수)이 잇따라 와도
// 종류마다 한 번만 알린다(signal). 서버는 페어를 만들거나 코드를 받거나 역할을 바꿀 때는 이벤트를 내지 않으므로 그 화면은 자기 동작 뒤에 직접 다시 읽는다.
import { authzApi } from './api.js';

export const COALESCE_MS = 40;

let list = [];
let sawNew = false;
let timer = null;
let inflight = null;
let dirty = false;
let lastError = '';
const subs = new Set();
const sigTimers = {};

const byCreated = (a, b) => (a.created || 0) - (b.created || 0);
const emit = (ev) => { for (const fn of [...subs]) { try { fn(ev); } catch (e) { console.error('[FarShell authz]', e); } } };

export function pending() { return list.slice(); }
export function lastRefreshError() { return lastError; }
/** 새 이벤트(authz_*)를 한 번이라도 받았나 — 테스트·진단용. */
export function sawNewEvents() { return sawNew; }

/** 변경 구독. 콜백은 `{type:'pending', added:[id…]}` 또는 `{type:'grants'}`. 해제 함수를 돌려준다. */
export function onChange(fn) { subs.add(fn); return () => subs.delete(fn); }

/** `{type}` 변경 알림을 짧게 묶어 한 번만 보낸다. */
function signal(type) {
  if (sigTimers[type]) return;
  sigTimers[type] = setTimeout(() => { sigTimers[type] = null; emit({ type }); }, COALESCE_MS);
}

/** 서버 목록으로 통째로 맞춘다. 처리된 것은 빠지고, 새로 생긴 id 는 `added` 로 알린다. */
export function setPending(next) {
  const clean = (next || []).filter((r) => r && r.id && (!r.state || r.state === 'pending')).sort(byCreated);
  const known = new Set(list.map((r) => r.id));
  const added = clean.filter((r) => !known.has(r.id)).map((r) => r.id);
  list = clean;
  emit({ type: 'pending', added });
}

/** 다른 화면·경로에서 처리됐다. 목록에서만 뺀다(서버 호출 없음). */
export function removePending(id) {
  const before = list.length;
  list = list.filter((r) => r.id !== id);
  if (list.length !== before) emit({ type: 'pending', added: [] });
}

/** 서버에서 목록을 다시 읽는다. 읽는 중에 또 요청되면 한 번 더 읽는다. */
export function refresh() {
  if (inflight) { dirty = true; return inflight; }
  inflight = (async () => {
    try {
      setPending(await authzApi.approvals());
      lastError = '';
    } catch (e) {
      lastError = (e && e.message) || 'error';
      emit({ type: 'error' });
    } finally {
      inflight = null;
    }
    if (dirty) { dirty = false; await refresh(); }
  })();
  return inflight;
}

function schedule() {
  if (timer) return;
  timer = setTimeout(() => { timer = null; void refresh(); }, COALESCE_MS);
}

/** `/ws-workspace` 메시지 하나. 승인함과 관계없는 타입은 무시한다. */
export function onEvent(msg) {
  if (!msg || typeof msg.type !== 'string') return;
  switch (msg.type) {
    case 'authz_request':
      sawNew = true; schedule();
      if (msg.kind === 'pair') signal('pairs');       // 합류 — 페어가 「승인 대기」가 된다
      break;
    case 'authz_resolved':
      sawNew = true;
      if (msg.id === '*') { list = []; emit({ type: 'pending', added: [] }); } else removePending(String(msg.id));
      emit({ type: 'grants' });
      if (msg.kind === 'pair' || msg.kind === 'all') { signal('pairs'); signal('leases'); }   // 승인하면 lease 도 생긴다
      break;
    case 'authz_revoked':
      sawNew = true; emit({ type: 'grants' });
      if (msg.kind === 'pair' || msg.kind === 'all') signal('pairs');
      signal('leases');                                // 권한을 거두면 그 묶음의 lease 도 놓인다(회수 saga)
      break;
    case 'lease_notice':
      signal('leases'); break;                         // 사람의 쓰기가 남의 lease 경로 안에서 일어남 — 막지 않고 알리기만 한다
    case 'agent_share_request':
      if (!sawNew) schedule(); break;
    case 'agent_share_request_resolved':
      if (!sawNew) { removePending(String(msg.id)); emit({ type: 'grants' }); }
      break;
    default:
  }
}

/** 테스트용. */
export function _reset() {
  list = []; sawNew = false; if (timer) clearTimeout(timer); timer = null;
  for (const k of Object.keys(sigTimers)) { if (sigTimers[k]) clearTimeout(sigTimers[k]); sigTimers[k] = null; } inflight = null; dirty = false; lastError = ''; subs.clear();
}
