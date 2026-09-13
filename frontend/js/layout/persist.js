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
import { getTree, getActivePaneId, onLayoutChange, replaceTree, countLeaves, getTabsWithTrees, getActiveTabId, replaceTabs } from './store.js';

const LS_KEY = 'vt-layout-v1';
const SAVE_DEBOUNCE_MS = 400;

// ── 직렬화 ────────────────────────────────────────────────────────────────
// 트리 노드를 그대로 옮기되 leaf.session만 {id, tmux}로 바꾼다. worktree는
// ADR-10의 확장 자리라 아직 항상 고정값이므로 저장하지 않는다(복원 시
// tree.js의 makeLeaf 기본값으로 채워진다).
//
// C3(2.1.2) — host는 저장한다. 원격 호스트가 **잠깐 꺼진 것**과 세션이
// **죽은 것**은 다르다: 둘을 똑같이 빈 pane으로 강등하면, 호스트를 다시
// 켜도 그 배치가 영영 사라진다. 어느 호스트의 세션이었는지를 적어 둬야
// 복원 쪽이 그 둘을 구분할 수 있다.
export function serializeTree(tree, lookup) {
  if (tree.t === 'leaf') {
    const info = tree.session ? lookup(tree.session) : null;
    // N35 §6 — 뷰어 칸은 세션이 아니라 **경로**를 저장한다. 경로는 새로고침
    // 뒤에도 그대로 유효하므로(세션 id와 달리) 그대로 되살아난다.
    if (tree.kind === 'viewer') return { t: 'leaf', id: tree.id, session: null, kind: 'viewer', file: tree.file || null };
    // C3 — 아직 못 닿은(unreachable) 칸은 세션 객체가 없다. 그대로 저장하면
    // 다음 저장 한 번에 배치가 사라지므로(레이아웃은 아무 변경에나 저장된다)
    // 무엇을 기다리는 중이었는지를 그대로 다시 적는다.
    const u = !info && tree.unreachable ? { id: null, tmux: tree.unreachable.tmux } : null;
    return { t: 'leaf', id: tree.id, session: info || u, host: tree.host || 'local' };
  }
  return {
    t: 'split', id: tree.id, dir: tree.dir, ratio: tree.ratio,
    a: serializeTree(tree.a, lookup), b: serializeTree(tree.b, lookup),
  };
}

function _lookupLive(sessionId) {
  const s = getSession(sessionId);
  return { id: sessionId, tmux: (s && s.tmuxName) || null, host: (s && s.host) || 'local' };
}

// ── 역직렬화 ──────────────────────────────────────────────────────────────
// resolve(info) → 살아있는 세션 id 또는 null. 이미 다른 leaf가 가져간 세션은
// 두 번 배정하지 않는다(중복 attach 금지 정책 — tree.js setSession과 같은
// 규칙이지만 여기선 트리를 한 번에 만들므로 직접 지킨다).
export function deserializeTree(node, resolve, taken = new Set()) {
  if (!node || typeof node !== 'object') return null;
  if (node.t === 'leaf') {
    if (typeof node.id !== 'string') return null;
    if (node.kind === 'viewer') {
      // 경로는 서버가 다시 검사한다(fsguard) — 여기서는 문자열인지만 본다.
      const file = typeof node.file === 'string' ? node.file : null;
      return { t: 'leaf', id: node.id, session: null, kind: 'viewer', file, worktree: null, host: 'local' };
    }
    let session = resolve(node.session);
    if (session && taken.has(session)) session = null;
    if (session) taken.add(session);
    // C3 — 호스트는 leaf(신규) → 저장된 session 정보(구버전 스냅샷) 순으로 읽는다.
    const host = node.host || (node.session && node.session.host) || 'local';
    const leaf = { t: 'leaf', id: node.id, session, kind: 'terminal', file: null, worktree: null, host };
    // 원격 호스트의 세션을 못 찾았다 = 세션이 죽었을 수도 있지만 **호스트가
    // 잠깐 꺼진 것일 수도** 있다. 강등하지 않고 `unreachable`로 남겨 배치를
    // 지킨다. 로컬은 그대로 강등한다 — 로컬 tmux에 이름이 없으면 그 세션은
    // 실제로 없는 것이고, 더 기다릴 이유가 없다.
    if (!session && host !== 'local' && node.session && node.session.tmux) {
      leaf.unreachable = { host, tmux: node.session.tmux };
    }
    return leaf;
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
// 10 §4 2단계 — v2: 화면에 pane 트리가 **탭마다 하나씩** 있다. v1은 트리가
// 하나뿐인 스냅샷이라 "탭 1개짜리 v2"와 정확히 같은 의미다 — 복원에서 그렇게
// 승격한다(_applySnapshot). 저장은 언제나 v2로만 한다.
function _snapshot() {
  const tabs = getTabsWithTrees();
  return {
    v: 2,
    savedAt: Date.now(),
    activeTab: getActiveTabId(),
    // v1 필드도 계속 적는다 — **되돌리기 위한 것**이다. 2.1.2를 롤백하면 옛
    // 코드가 이 스냅샷을 읽는데, v2만 있으면 배치가 통째로 날아간다.
    // 활성 탭의 트리를 v1 자리에 그대로 둔다(그게 옛 코드가 보던 화면이다).
    active: getActivePaneId(),
    tree: serializeTree(getTree(), _lookupLive),
    tabs: tabs.map((t) => ({
      id: t.id,
      worktreeId: t.worktreeId || null,
      label: t.label,
      active: t.activePaneId,
      tree: serializeTree(t.tree, _lookupLive),
    })),
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
    return _isSnapshot(s) ? s : null;
  } catch (_) { return null; }
}

// v1(트리 하나) / v2(탭 배열) 둘 다 받는다.
function _isSnapshot(s) {
  if (!s) return false;
  if (s.v === 2 && Array.isArray(s.tabs) && s.tabs.length) return true;
  return s.v === 1 && !!s.tree;
}

/** 스냅샷 → 항상 "탭 배열" 모양으로. v1은 탭 1개로 승격한다. */
export function snapshotTabs(snap) {
  if (snap.v === 2 && Array.isArray(snap.tabs)) return snap.tabs;
  return [{ id: 'tab-legacy', worktreeId: null, label: '작업 공간',
            active: snap.active, tree: snap.tree }];
}

function _applySnapshot(snap) {
  const resolve = makeResolver(allSessions());
  // 세션 중복 배정 금지는 **탭을 건너서도** 지켜야 한다 — 같은 세션이 두 탭의
  // leaf에 동시에 들어가면 wrapper 하나를 두 자리가 서로 뺏어간다(표면 레이어는
  // DOM 노드 하나를 옮겨 다니게 설계돼 있다). taken 집합을 탭들 사이에서 공유한다.
  const taken = new Set();
  const tabs = [];
  for (const raw of snapshotTabs(snap)) {
    const tree = deserializeTree(raw.tree, resolve, taken);
    if (!tree) continue;
    tabs.push({ id: raw.id, worktreeId: raw.worktreeId || null,
                label: raw.label || '작업 공간', tree, activePaneId: raw.active });
  }
  if (!tabs.length) return false;
  // 빈 leaf 채우기는 **활성 탭에만** 적용한다(그 화면이 비어 보이는 것이 문제였다).
  const activeIdx = Math.max(0, tabs.findIndex((t) => t.id === (snap.activeTab || snap.tabs?.[0]?.id)));
  _fillEmptyLeaves(tabs[activeIdx]?.tree || tabs[0].tree);
  return replaceTabs(tabs, tabs[activeIdx]?.id || tabs[0].id);
}

// S5 검증에서 발견한 결함: 저장된 트리의 leaf가 전부 비어 있으면(그 세션들이
// 죽어서 강등됐거나, 애초에 빈 pane 상태로 저장됐거나) **살아있는 세션이
// 탭에는 있는데 화면에는 빈 pane만 보인다.** 사용자가 탭을 눌러야 비로소
// 터미널이 나타나는데, 새로고침 직후엔 "세션이 사라졌다"로 읽힌다.
//
// 그래서 배정되지 않은 살아있는 세션이 있으면 빈 leaf를 앞에서부터 채운다.
// 순서는 세션 스토어의 삽입 순서(=탭 순서)를 따른다.
function _fillEmptyLeaves(tree) {
  const used = new Set();
  const empties = [];
  (function walk(node) {
    if (node.t === 'leaf') {
      if (node.session) used.add(node.session);
      // C3 — unreachable leaf는 "빈 칸"이 아니다. 여기에 엉뚱한 로컬 세션을
      // 채워 넣으면 호스트가 돌아왔을 때 되살릴 자리가 사라진다.
      else if (!node.unreachable) empties.push(node);
      return;
    }
    walk(node.a); walk(node.b);
  })(tree);
  if (!empties.length) return;
  const spare = Object.keys(allSessions()).filter((id) => !used.has(id));
  for (const leaf of empties) {
    const id = spare.shift();
    if (!id) break;
    leaf.session = id;   // deserializeTree가 만든 새 객체라 여기서 직접 채워도 안전하다
  }
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

function clearLayout() {
  try { localStorage.removeItem(LS_KEY); } catch (_) {}
}
window.clearLayout = clearLayout; // 콘솔에서 호출 가능(clearWorkspace와 같은 관례)
