// 레일·폰의 저장소 묶음 모델(세션 로직 점검 단계 5 → 저장소 2차). 순수 모듈 — DOM·전역을 보지 않는다.
//
//   저장소 묶음(머리: 이름 · 메인 작업 트리의 브랜치 · 세션 수 · 「+」)
//     ├ 메인 가지의 세션들
//     ├ 워크트리 가지(「워크트리 <브랜치>」) 아래의 세션들
//     │   └ 위임으로 만든 하위 세션(부모 밑)
//     └ 「종료 N」(이 저장소에서 있었던 끝난 대화 — ProjectEnded.tsx가 펼칠 때 받는다)
//   「저장소 밖」 묶음은 세션이 살아 있는 저장소들 바로 아래 — 그 안은 세션의 지금 폴더별 가지(「폴더 ~/…」).
//
// 저장소 = CLI를 켠 곳(서버 session_catalog/placement.py·projects_view.py). 묶음은 **저장소 목록**(`projects`)에서 만든다 —
// 세션이 없어도 남는다. 실행 중 세션의 자리는 서버가 정한 `home`(CLI가 돌면 CLI 위치, 아니면 셸 위치 — inventory.py)이고, 그
// 저장소가 목록의 저장소(`home.registered`, 숨김 아님)면 그 묶음, 아니면 「저장소 밖」 폴더 가지다. 워크트리 가지는 지금 위치가
// 같은 저장소의 다른 작업 트리일 때만이다(common-dir가 같고 작업 트리가 다를 때).
//
// 정렬(그 맥의 전역 설정 rail.sort — 그 맥을 보는 모든 기기가 같다, rail-data.ts parseRailSort): 저장소는 기준을 1~3단계로
// 잇는다(기본 「세션 있는 저장소 먼저 → 이름순」 — 앞 기준이 같을 때만 다음 기준). 저장소 안 세션은 만든 순(기본)·이름순·
// 최근 활동·수동(rail.order — 세션 열쇠 배열, 순서에 없는 세션은 가지 맨 아래). 「입력 대기 먼저」(기본 끔)는 세션에만 —
// 수동에서는 쓰지 않는다(사람이 정한 순서를 흔들지 않는다; 입력 대기는 「개입 필요」 구역이 따로 위에 보인다).
// 「저장소 밖」은 정렬 기준과 상관없이 세션이 살아 있는 마지막 저장소 바로 아래다(세션 있는 저장소가 없으면 맨 위).

export interface RepoRowLike {
  tmuxName: string | null;
  name: string;
  status: string;
  physicalKey?: string;
  home?: { project_id: string; root: string; name: string; repository: boolean; kind?: string; path?: string;
          registered?: boolean; hidden?: boolean } | null;
  context?: any;
  branch?: string | null;
  /** 지금 폴더(저장소 밖 세션의 폴더 가지). */
  cwd?: string | null;
  lineage?: { parent: string | null; state: string; createdAt?: number | null } | null;
  since?: number | null;
  /** 세션을 만든 시각(초) — 「만든 순」 정렬. */
  created?: number | null;
  /** 카탈로그 세션 id — 수동 순서의 열쇠(이름을 바꿔도 같다). */
  catalogSessionId?: string;
}

/** 수동 순서의 열쇠 — 카탈로그 세션 id, 아직 기록 전이면 tmux 이름. */
export function orderKey(row: Pick<RepoRowLike, 'catalogSessionId' | 'tmuxName'>): string {
  return row.catalogSessionId ? row.catalogSessionId : row.tmuxName ? 'tmux:' + row.tmuxName : '';
}

/** 저장소 목록 API(/api/session-catalog/projects)의 한 줄. */
export interface ProjectLike {
  id: string; name: string; path: string; kind: string;
  branch?: string | null; last_activity?: number | null; registered?: number | null; ended_count?: number;
}

/** 저장소 정렬 기준 — sessions: 세션 있는 저장소 먼저 · waiting: 입력 대기 있는 저장소 먼저 · name: 이름순 ·
 * registered: 등록순(최근 등록 먼저) · activity: 최근 활동순. */
export type RepoSort = 'sessions' | 'waiting' | 'name' | 'registered' | 'activity';
export const REPO_SORTS: RepoSort[] = ['sessions', 'waiting', 'name', 'registered', 'activity'];
/** 저장소 정렬 단계 수의 상한. */
export const REPO_SORT_LEVELS = 3;
export type SessionSort = 'created' | 'name' | 'activity' | 'manual';
export const SESSION_SORTS: SessionSort[] = ['created', 'name', 'activity', 'manual'];
export interface TreeSort { repos: RepoSort[]; sessions: SessionSort; waitingFirst: boolean }
export const DEFAULT_SORT: TreeSort = { repos: ['sessions', 'name'], sessions: 'created', waitingFirst: false };

export interface SessionNodeEntry<R> { row: R; kids: R[] }
export interface Lane<R> {
  /** null이면 메인 작업 트리(머리 없이 바로 세션). 저장소 밖에서는 폴더 가지(`folder`: 그 폴더의 절대 경로). */
  worktree: { key: string; name: string; branch: string | null; folder?: string } | null;
  sessions: SessionNodeEntry<R>[];
}
export interface RepoGroup<R> {
  key: string;
  name: string;
  /** 목록의 저장소인가(저장소 밖이 아니다). */
  repository: boolean;
  /** 'git' · 'directory'(git이 아닌 폴더 저장소) · null(저장소 밖). */
  kind: string | null;
  root: string | null;
  mainBranch: string | null;
  lanes: Lane<R>[];
  /** 「종료 N」 — 이 저장소의 끝난 대화 수(저장소 목록 API). 목록은 펼칠 때 받는다. */
  endedCount: number;
  /** 머리의 세션 수(실행 중 최상위 + 하위). */
  count: number;
  /** 그중 입력(질문·승인)을 기다리는 세션 수(하위 포함) — 접힌 머리의 칩이 보여 준다. */
  waiting: number;
  attention: boolean;
  lastActivity: number;
  /** 처음 CLI를 켠 시각(등록순 정렬). */
  registered: number;
}

export const OUTSIDE_KEY = '__outside__';

const base = (p: string | null | undefined) => (p ? p.replace(/\/+$/, '').split('/').pop() || p : '');

/** 행의 지금 위치에서 읽은 저장소(메인 작업 트리 경로)와 작업 트리. */
function currentPlace(row: RepoRowLike): { repoRoot: string | null; treeRoot: string | null; linked: boolean } {
  const ctx = row.context || {};
  const wt = ctx.worktree || null;
  const treeRoot = ctx.root || null;
  if (!treeRoot) return { repoRoot: null, treeRoot: null, linked: false };
  const linked = wt?.kind === 'linked';
  return { repoRoot: (linked ? wt?.repositoryRoot : treeRoot) || treeRoot, treeRoot, linked };
}

interface Placed<R> {
  row: R; key: string; name: string; repository: boolean; root: string | null; lane: Lane<R>['worktree'];
  /** 이 행이 메인 작업 트리에 있으면 그 브랜치 — /api/repos에 없는(등록 안 된) 저장소의 머리 브랜치를 채운다. */
  mainBranch: string | null;
}

function place<R extends RepoRowLike>(row: R): Placed<R> {
  const now = currentPlace(row);
  const home = row.home;
  if (home && home.registered && !home.hidden) {
    // 지금 위치가 같은 저장소의 다른(링크드) 작업 트리면 그 워크트리 가지에 둔다(git 저장소만).
    const lane = home.repository && now.linked && now.repoRoot === home.root && now.treeRoot
      ? { key: now.treeRoot, name: base(now.treeRoot), branch: row.branch || null } : null;
    const inMain = home.repository && !now.linked && !!now.treeRoot && now.treeRoot === home.root;
    return { row, key: home.project_id, name: home.name, repository: true, root: home.root, lane, mainBranch: inMain ? row.branch || null : null };
  }
  // 목록의 저장소가 아니다(셸로 들어간 미등록 저장소·숨긴 저장소·자리 모름) — 저장소 밖, 지금 폴더별 가지.
  const dir = typeof row.cwd === 'string' && row.cwd ? row.cwd.replace(/\/+$/, '') || '/' : '';
  const lane = dir ? { key: 'dir:' + dir, name: base(dir) || dir, branch: null, folder: dir } : null;
  return { row, key: OUTSIDE_KEY, name: '', repository: false, root: null, lane, mainBranch: null };
}

export interface BuildInput<R> {
  rows: R[];
  /** 목록의 저장소(보이는 것). 세션이 없어도 묶음이 생긴다. */
  projects?: ProjectLike[];
  /** 저장소 경로 → 메인 작업 트리의 브랜치(/api/repos — 저장소 목록에 브랜치가 없을 때). 그것도 없으면 메인 작업 트리 세션의 브랜치. */
  mainBranches?: Map<string, string | null>;
  /** 행의 최근 활동(카탈로그 last_activity, 초). 없으면 상태 진입 시각. */
  activityOf?: (row: R) => number;
  sort?: TreeSort;
  /** 수동 순서(orderKey 배열) — sort.sessions가 'manual'일 때만 쓴다. */
  order?: string[];
}

export function buildRepoTree<R extends RepoRowLike>(inp: BuildInput<R>): RepoGroup<R>[] {
  const activity = (r: R) => inp.activityOf?.(r) ?? Number(r.since ?? 0);
  const sort = inp.sort ?? DEFAULT_SORT;
  const names = new Set(inp.rows.map((r) => r.tmuxName).filter(Boolean) as string[]);
  // 위임 하위 세션은 부모 밑으로 — 부모가 목록에 있을 때만(부모가 사라졌으면 최상위로 올린다).
  // 계보가 고리를 이루면(데이터 이상) 고리에 든 세션만 최상위로 둔다 — 하위로 두면 어느 최상위 밑에도 안 그려져 목록에서
  // 사라진다. 고리에 매달린 자손(C → 고리의 A)은 그대로 하위다(A가 최상위가 되므로 그 밑에 그려진다).
  const byName = new Map(inp.rows.filter((r) => r.tmuxName).map((r) => [r.tmuxName!, r] as const));
  const inCycle = (r: R): boolean => {
    const self = r.tmuxName;
    if (!self) return false;
    const seen = new Set<string>();
    let p = r.lineage?.parent;
    while (p && byName.has(p) && !seen.has(p)) {
      if (p === self) return true;
      seen.add(p);
      p = byName.get(p)!.lineage?.parent;
    }
    return false;
  };
  const isKid = (r: R) => !!(r.lineage && r.lineage.parent && names.has(r.lineage.parent)) && !inCycle(r);
  const kidsOf = new Map<string, R[]>();
  for (const r of inp.rows) {
    if (!isKid(r)) continue;
    const p = r.lineage!.parent!;
    if (!kidsOf.has(p)) kidsOf.set(p, []);
    kidsOf.get(p)!.push(r);
  }
  for (const list of kidsOf.values()) list.sort((a, b) => (a.lineage?.createdAt || 0) - (b.lineage?.createdAt || 0));
  // 위임은 여러 단계일 수 있다(하위가 또 위임) — 최상위 세션 밑에 자손 전부를 깊이 우선(만든 순서)으로 둔다.
  // 직계만 붙이면 손자부터는 어디에도 그려지지 않았다. 계보가 고리를 이뤄도 한 번만 넣는다.
  const descendants = (name: string, seen: Set<string>): R[] => {
    const out: R[] = [];
    for (const k of kidsOf.get(name) || []) {
      const kn = k.tmuxName || '';
      if (!kn || seen.has(kn)) continue;
      seen.add(kn);
      out.push(k, ...descendants(kn, seen));
    }
    return out;
  };

  const groups = new Map<string, RepoGroup<R>>();
  const group = (key: string, name: string, repository: boolean, root: string | null) => {
    let g = groups.get(key);
    if (!g) {
      g = { key, name, repository, kind: repository ? 'git' : null, root, mainBranch: root ? inp.mainBranches?.get(root) ?? null : null,
            lanes: [], endedCount: 0, count: 0, waiting: 0, attention: false, lastActivity: 0, registered: 0 };
      groups.set(key, g);
    }
    return g;
  };
  // 저장소 목록 — 세션이 없어도 묶음이 남는다(머리 · 「+」 · 「종료 N」).
  for (const p of inp.projects || []) {
    const g = group(p.id, p.name, true, p.path);
    g.kind = p.kind === 'git' ? 'git' : 'directory';
    g.mainBranch = p.branch || g.mainBranch;
    g.endedCount = Math.max(0, Number(p.ended_count) || 0);
    g.lastActivity = Number(p.last_activity) || 0;
    g.registered = Number(p.registered) || 0;
  }
  const urgent = (r: R) => r.status === 'waiting' || (r.tmuxName ? descendants(r.tmuxName, new Set([r.tmuxName])) : []).some((k) => k.status === 'waiting');

  for (const r of inp.rows) {
    if (isKid(r)) continue;
    const p = place(r);
    const g = group(p.key, p.name, p.repository, p.root);
    // 메인 작업 트리 브랜치는 /api/repos(등록된 저장소)가 먼저고, 거기 없으면 메인 작업 트리에 있는 세션의 브랜치로 채운다.
    if (!g.mainBranch && p.mainBranch) g.mainBranch = p.mainBranch;
    const laneKey = p.lane?.key ?? '';
    let lane = g.lanes.find((l) => (l.worktree?.key ?? '') === laneKey);
    if (!lane) { lane = { worktree: p.lane, sessions: [] }; g.lanes.push(lane); }
    const kids = r.tmuxName ? descendants(r.tmuxName, new Set([r.tmuxName])) : [];
    lane.sessions.push({ row: r, kids });
    g.count += 1 + kids.length;
    g.waiting += [r, ...kids].filter((x) => x.status === 'waiting').length;
    g.attention = g.attention || urgent(r);
    g.lastActivity = Math.max(g.lastActivity, activity(r), ...kids.map(activity));
  }

  const created = (r: R) => Number(r.created) || 0;
  const waiting = (r: R) => r.status === 'waiting';
  const manual = sort.sessions === 'manual';
  const rank = new Map<string, number>();
  if (manual) (inp.order || []).forEach((k, i) => { if (k && !rank.has(k)) rank.set(k, i); });
  // 순서에 없는 세션(새로 생김)은 가지 맨 아래 — 그들끼리는 먼저 만든 것이 위(붙은 순서 그대로 쌓인다).
  const rankOf = (r: R) => rank.get(orderKey(r)) ?? Number.MAX_SAFE_INTEGER;
  const bySession = (a: SessionNodeEntry<R>, b: SessionNodeEntry<R>) => {
    if (manual) return rankOf(a.row) - rankOf(b.row) || created(a.row) - created(b.row) || a.row.name.localeCompare(b.row.name);
    if (sort.waitingFirst && waiting(a.row) !== waiting(b.row)) return waiting(a.row) ? -1 : 1;
    if (sort.sessions === 'name') return a.row.name.localeCompare(b.row.name) || created(b.row) - created(a.row);
    if (sort.sessions === 'activity') return activity(b.row) - activity(a.row);
    return created(b.row) - created(a.row) || a.row.name.localeCompare(b.row.name);
  };
  const waitingFirst = sort.waitingFirst && !manual;
  for (const g of groups.values()) {
    for (const l of g.lanes) l.sessions.sort(bySession);
    // 메인 가지가 먼저, 워크트리·폴더 가지는 그 뒤 — 이름순이면 가지 이름(경로)순, 아니면 가지 안 첫 세션 순서를 따른다.
    // 「입력 대기 먼저」를 켜면 입력 대기 세션이 있는 가지가 그보다 먼저다(설정 안내 「어떤 정렬에서도 위로」).
    const laneWaiting = (l: Lane<R>) => l.sessions.some((x) => waiting(x.row) || x.kids.some(waiting));
    g.lanes.sort((a, b) => (waitingFirst && laneWaiting(a) !== laneWaiting(b) ? (laneWaiting(a) ? -1 : 1) : 0)
      || (a.worktree ? 1 : 0) - (b.worktree ? 1 : 0)
      || (sort.sessions === 'name'
        ? (a.worktree?.folder || a.worktree?.branch || a.worktree?.name || '').localeCompare(b.worktree?.folder || b.worktree?.branch || b.worktree?.name || '')
        : (a.sessions[0] && b.sessions[0] ? bySession(a.sessions[0], b.sessions[0]) : laneActivity(b, activity) - laneActivity(a, activity))));
  }
  const byKey: Record<RepoSort, (a: RepoGroup<R>, b: RepoGroup<R>) => number> = {
    sessions: (a, b) => (b.count > 0 ? 1 : 0) - (a.count > 0 ? 1 : 0),
    waiting: (a, b) => (b.attention ? 1 : 0) - (a.attention ? 1 : 0),
    name: (a, b) => a.name.localeCompare(b.name),
    registered: (a, b) => b.registered - a.registered,
    activity: (a, b) => b.lastActivity - a.lastActivity,
  };
  // 정한 기준을 차례로 — 앞 기준이 같을 때만 다음. 끝까지 같으면 이름 → 등록순(순서가 폴링마다 흔들리지 않게).
  const chain = [...sort.repos, 'name', 'registered'] as RepoSort[];
  const repos = [...groups.values()].filter((g) => g.key !== OUTSIDE_KEY).sort((a, b) => {
    for (const k of chain) { const d = byKey[k](a, b); if (d) return d; }
    return 0;
  });
  // 저장소 밖 — 세션이 살아 있는 마지막 저장소 바로 아래(없으면 맨 위). 저장소 밖에는 실행 중 세션만 있다.
  const outside = groups.get(OUTSIDE_KEY);
  if (outside) {
    let at = 0;
    repos.forEach((g, i) => { if (g.count > 0) at = i + 1; });
    repos.splice(at, 0, outside);
  }
  return repos;
}

/** 수동 순서를 옮긴 결과 — `moved`를 `target`의 앞(before) 또는 뒤에 둔다. `stored`는 저장돼 있던 순서, `visible`은 지금 보이는
 * 최상위 세션의 열쇠(화면 순서). 저장 순서에 없던 보이는 열쇠는 화면 순서대로 뒤에 붙인다(화면이 이미 그렇게 그린다).
 * 지금 안 보이는 열쇠(입력 대기 구역으로 간 세션·기기별 필터로 숨긴 세션·끝난 세션)도 자리를 지킨다 — 돌아왔을 때 맨 뒤로
 * 밀리지 않게. 다만 `max`개를 넘으면 안 보이는 것부터 앞에서 버린다(끝난 세션이 끝없이 쌓이지 않게). */
export function moveInOrder(stored: string[], visible: string[], moved: string, target: string, place: 'before' | 'after', max = 500): string[] {
  const full: string[] = [];
  const seen = new Set<string>();
  for (const k of [...stored, ...visible]) if (k && !seen.has(k)) { seen.add(k); full.push(k); }
  if (moved && target && moved !== target && full.includes(moved) && full.includes(target)) {
    full.splice(full.indexOf(moved), 1);
    const i = full.indexOf(target);
    full.splice(place === 'before' ? i : i + 1, 0, moved);
  }
  const shown = new Set(visible);
  while (full.length > max) {
    const i = full.findIndex((k) => !shown.has(k));
    full.splice(i >= 0 ? i : full.length - 1, 1);
  }
  return full;
}

function laneActivity<R>(l: Lane<R>, activity: (r: R) => number): number {
  return Math.max(0, ...l.sessions.map((s) => activity(s.row)));
}

/** 접힌 48px 띠의 칸 순서 — 펼친 가지와 같은 순서(저장소 → 가지 → 세션 → 하위). */
export function flattenRows<R>(tree: RepoGroup<R>[]): { row: R; kid: boolean }[] {
  const out: { row: R; kid: boolean }[] = [];
  for (const g of tree) for (const l of g.lanes) for (const s of l.sessions) {
    out.push({ row: s.row, kid: false });
    for (const k of s.kids) out.push({ row: k, kid: true });
  }
  return out;
}
