// 레일의 저장소 가지 그래프 모델(세션 로직 점검 단계 5, 설계 P1). 순수 모듈 — DOM·전역을 보지 않는다.
//
//   저장소 묶음(머리: 이름 · 메인 작업 트리의 브랜치 · 세션 수)
//     ├ 메인 가지의 세션들
//     ├ 워크트리 가지(「워크트리 <브랜치>」) 아래의 세션들
//     │   └ 위임으로 만든 하위 세션(부모 밑)
//     └ 「종료 N」(이 저장소에서 끝난 세션 — 카탈로그)
//   「저장소 밖」 묶음은 맨 아래.
//
// 세션의 저장소는 카탈로그가 정한 자리(`home`, 처음 시작한 곳 — cd해도 안 바뀜)다. 자리가 아직 없으면(방금 만든
// 세션·옛 서버) 지금 위치(context)의 저장소로 그린다. 워크트리 가지는 지금 위치가 같은 저장소의 다른 작업 트리일
// 때만이다(common-dir가 같고 작업 트리가 다를 때).
//
// 정렬: 저장소는 「입력 대기가 있는 저장소 > 최근 활동」, 저장소 안은 최근 활동순. 저장소 밖은 늘 맨 아래.

export interface RepoRowLike {
  tmuxName: string | null;
  name: string;
  status: string;
  physicalKey?: string;
  home?: { project_id: string; root: string; name: string; repository: boolean; kind?: string; path?: string } | null;
  context?: any;
  branch?: string | null;
  lineage?: { parent: string | null; state: string; createdAt?: number | null } | null;
  since?: number | null;
}

export interface EndedLike {
  id: string;
  workspace_id?: string | null;
  last_activity?: number | null;
  updated?: number | null;
  ended?: number | null;
  archived?: number | null;
  runtime_id?: string | null;
  tmux_name?: string | null;
  physical_key?: string | null;
}

export interface SessionNodeEntry<R> { row: R; kids: R[] }
export interface Lane<R> {
  /** null이면 메인 작업 트리(머리 없이 바로 세션). */
  worktree: { key: string; name: string; branch: string | null } | null;
  sessions: SessionNodeEntry<R>[];
}
export interface RepoGroup<R, E> {
  key: string;
  name: string;
  repository: boolean;
  root: string | null;
  mainBranch: string | null;
  lanes: Lane<R>[];
  ended: E[];
  /** 머리의 세션 수(실행 중 최상위 + 하위). */
  count: number;
  attention: boolean;
  lastActivity: number;
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

interface Placed<R> { row: R; key: string; name: string; repository: boolean; root: string | null; lane: Lane<R>['worktree'] }

function place<R extends RepoRowLike>(row: R): Placed<R> {
  const now = currentPlace(row);
  const home = row.home;
  if (home && home.repository) {
    // 지금 위치가 같은 저장소의 다른(링크드) 작업 트리면 그 워크트리 가지에 둔다.
    const lane = now.linked && now.repoRoot === home.root && now.treeRoot
      ? { key: now.treeRoot, name: base(now.treeRoot), branch: row.branch || null } : null;
    return { row, key: home.project_id, name: home.name, repository: true, root: home.root, lane };
  }
  if (!home && now.repoRoot) {
    const lane = now.linked && now.treeRoot ? { key: now.treeRoot, name: base(now.treeRoot), branch: row.branch || null } : null;
    return { row, key: 'path:' + now.repoRoot, name: base(now.repoRoot), repository: true, root: now.repoRoot, lane };
  }
  return { row, key: OUTSIDE_KEY, name: '', repository: false, root: null, lane: null };
}

export interface BuildInput<R, E> {
  rows: R[];
  /** 카탈로그 스냅샷의 세션들(실행 중·종료·보관 섞임). 종료(보관 안 함)만 「종료 N」으로 간다. */
  ended?: E[];
  workspaces?: { id: string; project_id: string | null; path?: string }[];
  projects?: { id: string; name: string; path: string; kind: string }[];
  /** 저장소 경로 → 메인 작업 트리의 브랜치(/api/repos). */
  mainBranches?: Map<string, string | null>;
  /** 행의 최근 활동(카탈로그 last_activity, 초). 없으면 상태 진입 시각. */
  activityOf?: (row: R) => number;
  /** 이 카탈로그 세션이 지금 실행 중 행으로 그려지고 있는가. */
  isLive?: (s: E) => boolean;
}

export function endedActivity(s: EndedLike): number {
  return Number(s.last_activity ?? s.ended ?? s.updated ?? 0) || 0;
}

export function buildRepoTree<R extends RepoRowLike, E extends EndedLike>(inp: BuildInput<R, E>): RepoGroup<R, E>[] {
  const activity = (r: R) => inp.activityOf?.(r) ?? Number(r.since ?? 0);
  const names = new Set(inp.rows.map((r) => r.tmuxName).filter(Boolean) as string[]);
  // 위임 하위 세션은 부모 밑으로 — 부모가 목록에 있을 때만(부모가 사라졌으면 최상위로 올린다).
  const isKid = (r: R) => !!(r.lineage && r.lineage.parent && names.has(r.lineage.parent));
  const kidsOf = new Map<string, R[]>();
  for (const r of inp.rows) {
    if (!isKid(r)) continue;
    const p = r.lineage!.parent!;
    if (!kidsOf.has(p)) kidsOf.set(p, []);
    kidsOf.get(p)!.push(r);
  }
  for (const list of kidsOf.values()) list.sort((a, b) => (a.lineage?.createdAt || 0) - (b.lineage?.createdAt || 0));

  const groups = new Map<string, RepoGroup<R, E>>();
  const group = (key: string, name: string, repository: boolean, root: string | null) => {
    let g = groups.get(key);
    if (!g) {
      g = { key, name, repository, root, mainBranch: root ? inp.mainBranches?.get(root) ?? null : null,
            lanes: [], ended: [], count: 0, attention: false, lastActivity: 0 };
      groups.set(key, g);
    }
    return g;
  };
  const urgent = (r: R) => r.status === 'waiting' || (kidsOf.get(r.tmuxName || '') || []).some((k) => k.status === 'waiting');

  for (const r of inp.rows) {
    if (isKid(r)) continue;
    const p = place(r);
    const g = group(p.key, p.name, p.repository, p.root);
    const laneKey = p.lane?.key ?? '';
    let lane = g.lanes.find((l) => (l.worktree?.key ?? '') === laneKey);
    if (!lane) { lane = { worktree: p.lane, sessions: [] }; g.lanes.push(lane); }
    const kids = kidsOf.get(r.tmuxName || '') || [];
    lane.sessions.push({ row: r, kids });
    g.count += 1 + kids.length;
    g.attention = g.attention || urgent(r);
    g.lastActivity = Math.max(g.lastActivity, activity(r), ...kids.map(activity));
  }

  // 종료(보관 안 함): 카탈로그 자리(workspace → project)로 저장소를 찾는다.
  const ws = new Map((inp.workspaces || []).map((w) => [w.id, w]));
  const projects = new Map((inp.projects || []).map((p) => [p.id, p]));
  for (const s of inp.ended || []) {
    if (s.archived || s.ended == null || inp.isLive?.(s)) continue;
    const w = s.workspace_id ? ws.get(s.workspace_id) : undefined;
    const proj = w?.project_id ? projects.get(w.project_id) : undefined;
    const g = proj && proj.kind === 'git' ? group(proj.id, proj.name, true, proj.path) : group(OUTSIDE_KEY, '', false, null);
    g.ended.push(s);
  }
  for (const g of groups.values()) {
    g.ended.sort((a, b) => endedActivity(b) - endedActivity(a));
    // 메인 가지가 먼저, 워크트리 가지는 그 뒤(가지 안 최근 활동순).
    g.lanes.sort((a, b) => (a.worktree ? 1 : 0) - (b.worktree ? 1 : 0)
      || laneActivity(b, activity) - laneActivity(a, activity));
    for (const l of g.lanes) l.sessions.sort((a, b) => activity(b.row) - activity(a.row));
    if (!g.lastActivity && g.ended.length) g.lastActivity = endedActivity(g.ended[0]);
  }
  return [...groups.values()].sort((a, b) => {
    if ((a.key === OUTSIDE_KEY) !== (b.key === OUTSIDE_KEY)) return a.key === OUTSIDE_KEY ? 1 : -1;
    if (a.attention !== b.attention) return a.attention ? -1 : 1;
    return b.lastActivity - a.lastActivity || a.name.localeCompare(b.name);
  });
}

function laneActivity<R>(l: Lane<R>, activity: (r: R) => number): number {
  return Math.max(0, ...l.sessions.map((s) => activity(s.row)));
}

/** 접힌 48px 띠의 칸 순서 — 펼친 가지와 같은 순서(저장소 → 가지 → 세션 → 하위). */
export function flattenRows<R, E>(tree: RepoGroup<R, E>[]): { row: R; kid: boolean }[] {
  const out: { row: R; kid: boolean }[] = [];
  for (const g of tree) for (const l of g.lanes) for (const s of l.sessions) {
    out.push({ row: s.row, kid: false });
    for (const k of s.kids) out.push({ row: k, kid: true });
  }
  return out;
}
