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
// 2026-10-03(세션·뷰 규칙 2단계) — 레이아웃은 **브라우저 탭별**(core/screen-id.js)
// 로 저장한다. 새 탭(저장본 없음)은 가장 최근 저장본을 복사해 시작한다. 복원은
// 저장된 레이아웃(열린 뷰 + 기억된 닫힌 묶음 뷰)만 되살린다 — 부팅은 이 스냅샷의
// 칸에 있던 세션만 attach하고(term/workspace.js attachLayoutSessions) 그 뒤에
// 적용한다(boot.js).
import { allSessions, getSession } from '../core/store.js';
import { vtFetch } from '../core/api.js';
import { hostKey } from '../core/host-route.js';
import { screenId } from '../core/screen-id.js';
import { getTree, getActivePaneId, onLayoutChange, replaceTree, countLeaves, getTabsWithTrees, getActiveTabId, replaceTabs,
  getClosedViews, replaceClosedViews } from './store.js';
// N4 — 스냅샷의 `panel` 이름을 검증할 때만 쓴다(상수 Set 하나). 화면 모듈은
// 여기서 import 하지 않는다 — 그러면 panels 청크가 core로 승격된다(ADR-26).
import { PANEL_KINDS } from '../panels/registry.js';

// 호스트 전환(core/host-route.js) — 맥마다 탭 배치를 따로 둔다. LS_KEY는
// "가장 최근 저장본"(어느 브라우저 탭이든 마지막으로 저장한 것 — 새 탭의 복사
// 원본이자 v4 이하 클라이언트가 읽던 자리), _screenKey()는 이 탭 것이다.
const LS_KEY = hostKey('vt-layout-v1');
const _screenKey = () => hostKey(`vt-layout-v1.${screenId()}`);
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
    // N4 — 패널 칸은 **패널 이름**만 저장한다. 그 이름이 곧 무엇을 그릴지이고,
    // 내용은 열릴 때 서버에서 다시 받으므로 저장할 상태가 따로 없다(뷰어의
    // 경로와 같은 성격).
    if (tree.kind === 'panel') return { t: 'leaf', id: tree.id, session: null, kind: 'panel', panel: tree.panel || null };
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
      return { t: 'leaf', id: node.id, session: null, kind: 'viewer', file, panel: null, worktree: null, host: 'local' };
    }
    if (node.kind === 'panel') {
      // 모르는 패널 이름(옛 스냅샷·오타)은 빈 pane으로 떨어뜨린다 — 그리지
      // 못할 이름을 들고 있느니 고를 수 있는 빈 칸이 낫다.
      const panel = PANEL_KINDS.has(node.panel) ? node.panel : null;
      if (!panel) return { t: 'leaf', id: node.id, session: null, kind: 'terminal', file: null, panel: null, worktree: null, host: 'local' };
      return { t: 'leaf', id: node.id, session: null, kind: 'panel', panel, file: null, worktree: null, host: 'local' };
    }
    let session = resolve(node.session);
    if (session && taken.has(session)) session = null;
    if (session) taken.add(session);
    // C3 — 호스트는 leaf(신규) → 저장된 session 정보(구버전 스냅샷) 순으로 읽는다.
    const host = node.host || (node.session && node.session.host) || 'local';
    const leaf = { t: 'leaf', id: node.id, session, kind: 'terminal', file: null, panel: null, worktree: null, host };
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
// 승격한다(_applySnapshot).
// 2.1 D4 — v3: 탭 레코드에 `repoId`/`hostId`가 붙었다(탭의 정체성이 워크트리에서
// 저장소로 바뀐 것). ADR-29 D(2026-09-18)는 그걸 `groupId`로 일반화했다(v4).
// **2026-09-18 후속(그룹 재정의, 사용자 요청)이 그 필드 자체를 없앤다** —
// 그룹은 더 이상 탭에 붙는 태그가 아니라 pane 트리에서 실시간으로 계산되는
// 값이라(layout/store.js 머리말), 탭 레코드가 들고 다닐 이유가 없다.
// `repoId`/`groupId`를 계속 써 주던 v3 롤백 미러도 함께 지운다 — 그 값을
// 읽던 옛 클라이언트로 되돌아갈 계획이 없다(ADR-29가 이미 확정·완료됐다).
// v3/v4로 저장된 옛 스냅샷은 계속 읽는다(_applySnapshot) — 탭 자체(id·tree·
// worktreeId·label)는 그대로 복원되고, 이제 뜻이 없어진 groupId/repoId만
// 버린다. `worktreeId`는 v2와 같은 자리에 계속 적는다.
// v5(2026-10-03) — `screen`(저장한 브라우저 탭)과 `closed`(기억된 닫힌 묶음 뷰,
// 트리는 이미 직렬화 모양)가 붙었다. v1~v4도 계속 읽는다.
function _snapshot() {
  const tabs = getTabsWithTrees();
  return {
    v: 5,
    savedAt: Date.now(),
    screen: screenId(),
    closed: getClosedViews(),
    activeTab: getActiveTabId(),
    // v1 필드도 계속 적는다 — **되돌리기 위한 것**이다. 2.1.2를 롤백하면 옛
    // 코드가 이 스냅샷을 읽는데, 없으면 배치가 통째로 날아간다.
    // 활성 탭의 트리를 v1 자리에 그대로 둔다(그게 옛 코드가 보던 화면이다).
    active: getActivePaneId(),
    tree: serializeTree(getTree(), _lookupLive),
    tabs: tabs.map((t) => ({
      id: t.id,
      worktreeId: t.worktreeId || null,
      hostId: t.hostId || 'local',
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
  try {
    const raw = JSON.stringify(snap);
    localStorage.setItem(_screenKey(), raw);
    localStorage.setItem(LS_KEY, raw);
  } catch (_) { /* 용량 초과 등 무시 */ }
  // 저장 실패는 조용히 무시 — ADR-5: 실패해도 UI는 현재 메모리 상태로 계속 동작한다.
  // ui.layouts는 서버가 탭 id별로 병합한다(server/workspace.py) — 다른 탭 것을 안 지운다.
  vtFetch('/api/workspace', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ui: { layout: snap, layouts: { [snap.screen]: snap } } }),
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
function _readLocal(key) {
  try {
    const raw = localStorage.getItem(key);
    const s = raw ? JSON.parse(raw) : null;
    return _isSnapshot(s) ? s : null;
  } catch (_) { return null; }
}

// v1(트리 하나) / v2~v5(탭 배열) 다 받는다. v5는 열린 뷰가 0개여도(마지막 뷰를
// 닫았다) 유효하다 — 기억된 닫힌 묶음만 남은 상태도 저장할 가치가 있다.
function _isSnapshot(s) {
  if (!s) return false;
  if (s.v === 5 && Array.isArray(s.tabs)) return true;
  if ((s.v === 2 || s.v === 3 || s.v === 4) && Array.isArray(s.tabs) && s.tabs.length) return true;
  return s.v === 1 && !!s.tree;
}

const _newer = (a, b) => (!a ? b : !b ? a : ((b.savedAt || 0) > (a.savedAt || 0) ? b : a));

/** 부팅 때 쓸 스냅샷을 고른다: 이 브라우저 탭의 저장본(로컬·서버 중 최신),
 * 없으면 가장 최근 저장본의 사본(새 탭). 없으면 null. 서버 실패는 무시한다. */
export async function loadLayoutSnapshot() {
  let own = _readLocal(_screenKey());
  let latest = _readLocal(LS_KEY);
  try {
    const ws = await vtFetch('/api/workspace');
    const mine = ws?.ui?.layouts?.[screenId()];
    if (_isSnapshot(mine)) own = _newer(own, mine);
    if (_isSnapshot(ws?.ui?.layout)) latest = _newer(latest, ws.ui.layout);
  } catch (_) { /* 서버 미응답/미인증 — 로컬만으로 */ }
  return own || latest || null;
}

/** 스냅샷의 **열린 뷰** 칸에 있던 세션 정보({id, tmux, host}) — 부팅이 이것만
 * attach한다(기억된 닫힌 묶음은 레일에서 열 때 attach한다). 트리 순서. */
export function snapshotSessionInfos(snap) {
  const out = [];
  const walk = (node) => {
    if (!node || typeof node !== 'object') return;
    if (node.t === 'leaf') {
      if (node.kind === 'viewer' || node.kind === 'panel' || !node.session) return;
      out.push({ ...node.session, host: node.host || node.session.host || 'local' });
      return;
    }
    walk(node.a); walk(node.b);
  };
  for (const t of snapshotTabs(snap)) walk(t.tree);
  return out;
}

/** 스냅샷 → 항상 "탭 배열" 모양으로. v1은 탭 1개로 승격한다. */
export function snapshotTabs(snap) {
  if ((snap.v === 2 || snap.v === 3 || snap.v === 4 || snap.v === 5) && Array.isArray(snap.tabs)) return snap.tabs;
  return [{ id: 'tab-legacy', worktreeId: null, hostId: 'local', label: null,
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
    // v3/v4 스냅샷의 `repoId`/`groupId`는 2026-09-18 후속(그룹 재정의)으로
    // 뜻을 잃었다 — 읽지 않는다. `label`도 옛 기본값 "작업 공간"이 그대로
    // 저장돼 있을 수 있다(그때는 그게 _makeTab의 기본값이었다) — 그건 사용자가
    // 지은 이름이 아니라 예전 placeholder이므로 null로 되돌린다(안 그러면
    // tabDisplayLabel이 "사용자가 지었다"로 착각해 영원히 그 글자만 보인다).
    const label = (raw.label && raw.label !== '작업 공간') ? raw.label : null;
    tabs.push({ id: raw.id,
                worktreeId: raw.worktreeId || null,
                hostId: raw.hostId || 'local',
                label, tree, activePaneId: raw.active });
  }
  replaceClosedViews(Array.isArray(snap.closed) ? snap.closed : []);
  if (!tabs.length) return false;
  // 2026-10-03 — 예전엔 빈 칸을 남는 살아있는 세션으로 채웠다(_fillEmptyLeaves).
  // 그건 사용자가 닫은 칸을 새로고침이 다시 채우는 일이라(불변식 3) 없앴다.
  const activeIdx = Math.max(0, tabs.findIndex((t) => t.id === (snap.activeTab || snap.tabs?.[0]?.id)));
  return replaceTabs(tabs, tabs[activeIdx]?.id || tabs[0].id);
}

// 부팅 시 1회 — boot.js가 loadLayoutSnapshot()으로 고르고 그 칸의 세션을
// attach한 **뒤에** 부른다. snap이 없거나 뷰가 0개여도 저장 가드는 연다.
export async function restoreLayout(snap = undefined) {
  if (snap === undefined) snap = await loadLayoutSnapshot();
  const applied = snap ? _applySnapshot(snap) : false;
  _enabled = true; // 이 시점 이후의 변경부터 저장한다
  return applied && countLeaves() > 0;
}

function clearLayout() {
  try { localStorage.removeItem(LS_KEY); localStorage.removeItem(_screenKey()); } catch (_) {}
}
window.clearLayout = clearLayout; // 콘솔에서 호출 가능(clearWorkspace와 같은 관례)
