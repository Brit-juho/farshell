// L8 — 레이아웃 트리 영속화. localStorage(프리렌더)와 `/api/workspace`(정본)에
// 함께 저장한다. 둘을 쓰는 이유가 서로 다르다:
//   - localStorage: 같은 기기에서 새로고침했을 때 네트워크 왕복 없이 즉시
//     복원해 "한 칸 → 갑자기 두 칸"으로 튀는 걸 막는다(프리렌더).
//   - /api/workspace: 기기가 바뀌어도 따라오는 정본. 저장 시각(savedAt)이
//     로컬보다 최신이면 로컬을 덮어쓴다.
//
// **leaf에 웹 세션 id를 그대로 저장하면 안 된다** — tmux 세션은 새로고침 때마다
// `/api/tmux/attach`로 새 PTY id를 발급받으므로, 저장해둔 id는 다음 부팅에서
// 100% 죽은 참조가 된다(그게 바로 §"엣지 케이스" 표의 "새로고침 후 죽은 세션
// 참조 → 유령 pane"). 그래서 leaf는 `{id, tmux}` 두 값을 함께 적고, 복원할 때
// **tmux 이름을 먼저** 살아있는 세션에 매칭한다. tmux가 아닌 순수 PTY 세션은
// 서버가 재시작되지 않았을 때만 id로 되살아나고, 아니면 빈 pane으로 강등된다.
//
// 복원은 항상 term/workspace.js의 세션 복원이 **끝난 뒤에** 불려야 한다
// (그 전엔 allSessions()가 비어 있어 전부 빈 pane으로 강등돼 버린다).
import { allSessions, getSession } from '../core/store.js';
import { vtFetch } from '../core/api.js';
import { getTree, getActivePaneId, onLayoutChange, replaceTree, countLeaves } from './store.js';

const LS_KEY = 'vt-layout-v1';
const SAVE_DEBOUNCE_MS = 400;

// ── 직렬화 ────────────────────────────────────────────────────────────────
// 트리 노드를 그대로 옮기되 leaf.session만 {id, tmux}로 바꾼다. worktree/host는
// ADR-10의 확장 자리라 2.0에선 항상 고정값이므로 저장하지 않는다(복원 시
// tree.js의 makeLeaf 기본값으로 채워진다).
export function serializeTree(tree, lookup) {
  if (tree.t === 'leaf') {
    const info = tree.session ? lookup(tree.session) : null;
    return { t: 'leaf', id: tree.id, session: info };
  }
  return {
    t: 'split', id: tree.id, dir: tree.dir, ratio: tree.ratio,
    a: serializeTree(tree.a, lookup), b: serializeTree(tree.b, lookup),
  };
}

function _lookupLive(sessionId) {
  const s = getSession(sessionId);
  return { id: sessionId, tmux: (s && s.tmuxName) || null };
}

// ── 역직렬화 ──────────────────────────────────────────────────────────────
// resolve(info) → 살아있는 세션 id 또는 null. 이미 다른 leaf가 가져간 세션은
// 두 번 배정하지 않는다(중복 attach 금지 정책 — tree.js setSession과 같은
// 규칙이지만 여기선 트리를 한 번에 만들므로 직접 지킨다).
export function deserializeTree(node, resolve, taken = new Set()) {
  if (!node || typeof node !== 'object') return null;
  if (node.t === 'leaf') {
    if (typeof node.id !== 'string') return null;
    let session = resolve(node.session);
    if (session && taken.has(session)) session = null;
    if (session) taken.add(session);
    return { t: 'leaf', id: node.id, session, worktree: null, host: 'local' };
  }
  if (node.t !== 'split' || typeof node.id !== 'string') return null;
  const a = deserializeTree(node.a, resolve, taken);
  const b = deserializeTree(node.b, resolve, taken);
  if (!a || !b) return null;
  const ratio = typeof node.ratio === 'number' && node.ratio > 0 && node.ratio < 1 ? node.ratio : 0.5;
  return { t: 'split', id: node.id, dir: node.dir === 'row' ? 'row' : 'col', a, b, ratio };
}

// 저장된 leaf 정보 → 지금 살아있는 세션 id. tmux 이름 우선(파일 상단 주석).
export function makeResolver(sessions) {
  const byTmux = new Map();
  const liveIds = new Set();
  for (const [id, s] of Object.entries(sessions)) {
    liveIds.add(id);
    if (s && s.tmuxName && !byTmux.has(s.tmuxName)) byTmux.set(s.tmuxName, id);
  }
  return (info) => {
    if (!info) return null;
    if (info.tmux && byTmux.has(info.tmux)) return byTmux.get(info.tmux);
    // tmux 세션이었는데 그 이름이 지금 없다 → 그 세션은 죽었다. id는 어차피
    // 이전 부팅의 값이라 볼 필요도 없다(빈 pane 강등).
    if (info.tmux) return null;
    return info.id && liveIds.has(info.id) ? info.id : null;
  };
}

// ── 저장 ──────────────────────────────────────────────────────────────────
function _snapshot() {
  return {
    v: 1,
    savedAt: Date.now(),
    active: getActivePaneId(),
    tree: serializeTree(getTree(), _lookupLive),
  };
}

let _saveTimer = null;
let _enabled = false; // 복원이 끝나기 전엔 저장하지 않는다(빈 초기 트리로 덮어쓰기 방지)

export function saveLayoutNow() {
  if (!_enabled) return;
  const snap = _snapshot();
  try { localStorage.setItem(LS_KEY, JSON.stringify(snap)); } catch (_) { /* 용량 초과 등 무시 */ }
  // 저장 실패는 조용히 무시 — ADR-5: 실패해도 UI는 현재 메모리 상태로 계속 동작한다.
  vtFetch('/api/workspace', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ui: { layout: snap } }),
  }).catch(() => {});
}

function _scheduleSave() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(saveLayoutNow, SAVE_DEBOUNCE_MS);
}

// 트리가 바뀔 때마다 저장. 리사이저 드래그처럼 초당 수십 번 바뀌는 경로가
// 있어 디바운스는 필수다(엣지 케이스 표의 "리사이저 드래그 중 resize 폭풍"과
// 같은 이유 — 여기선 PUT 폭풍).
onLayoutChange(_scheduleSave);

// ── 복원 ──────────────────────────────────────────────────────────────────
function _readLocal() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    const s = raw ? JSON.parse(raw) : null;
    return s && s.v === 1 && s.tree ? s : null;
  } catch (_) { return null; }
}

function _applySnapshot(snap) {
  const resolve = makeResolver(allSessions());
  const tree = deserializeTree(snap.tree, resolve);
  if (!tree) return false;
  return replaceTree(tree, snap.active);
}

// 부팅 시 1회. 로컬 스냅샷을 먼저 적용(프리렌더)하고, 서버 정본이 더 최신이면
// 그걸로 다시 적용한다. 서버 조회 실패는 무시한다 — 로컬만으로도 정상 동작.
export async function restoreLayout() {
  let applied = false;
  const local = _readLocal();
  if (local) applied = _applySnapshot(local);
  _enabled = true; // 이 시점 이후의 변경부터 저장한다

  try {
    const ws = await vtFetch('/api/workspace');
    const remote = ws?.ui?.layout;
    if (remote && remote.v === 1 && remote.tree) {
      const newer = !local || (remote.savedAt || 0) > (local.savedAt || 0);
      if (newer && _applySnapshot(remote)) applied = true;
    }
  } catch (_) { /* 서버 미응답/미인증 — 로컬 복원 결과를 그대로 쓴다 */ }

  // 복원 결과가 leaf 1개짜리 기본 트리와 다를 때만 "복원했다"로 본다 —
  // 호출자(boot)가 굳이 로그를 남기거나 하진 않지만, 테스트에서 의미가 있다.
  return applied && countLeaves() > 0;
}

export function clearLayout() {
  try { localStorage.removeItem(LS_KEY); } catch (_) {}
}
window.clearLayout = clearLayout; // 콘솔에서 호출 가능(clearWorkspace와 같은 관례)
