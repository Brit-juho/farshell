// 워크트리 레일의 **데이터 수집**과 window 브리지 어댑터. 그리기는 Rail.tsx.
//
// ⚠ Rail.tsx와 같은 제약을 그대로 진다: core/store.js·layout/store.js·
// agent/state.js를 **직접 import하지 않는다.** 이 파일도 `shell` 지연 청크에
// 들어가므로, 정적 import하면 Vite lib 모드가 그 상태를 복제한다(ADR-26/N35).
// 그래서 전부 window 브리지로만 읽고 vtFetch는 인자로 받는다.
//
// Rail.tsx에서 떼어낸 이유: "무엇을 그리는가"와 "무엇을 가져오는가"가 한
// 파일에 있으면 폴링 주기·캐시 같은 것을 고칠 때 JSX 400줄을 지나쳐야 한다.
import { createSignal, onCleanup } from 'solid-js';

import { normalizeOrigin, normalizeKind, normalizeBadge, type SessionRow } from './rail-data.js';
import { confirmDialog } from '../core/dialog.js';
import { watchPath, isWatchLive } from './fs-watch.js';

// §5 원문: "60초 캐시" — 워크트리에 안 속한(changed 요약을 서버가 안 주는)
// 세션에만 쓴다.
//
// 2026-09-30 — 변경 알림(/ws-fs-watch의 `repo` 구독)이 살아 있으면 바뀌었을
// 때만 다시 센다. 86분에 326회 돌던 git status가, 안 바뀐 저장소에서는 10분에
// 한 번으로 준다. 알림이 없는 경우(거절·감시 실패·연결 끊김)는 예전 60초 그대로.
const GIT_CACHE_MS = 60000;              // 감시가 없을 때
const GIT_WATCHED_MAX_MS = 10 * 60000;   // 감시 중이어도 이만큼 지나면 한 번 확인(놓친 알림 대비)
const GIT_DIRTY_GAP_MS = 30000;          // 바뀌었어도 이보다 자주는 안 센다 — 에이전트가 쉴 새 없이 고칠 때
const GIT_NOREPO_MS = 5 * 60000;         // git 저장소가 아닌 곳 — 저장소가 될 일이 드물다

export interface RailDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
  getAction: (name: string) => unknown;
}

export function safeFetch<T>(deps: Pick<RailDeps, 'vtFetch'>, path: string): Promise<T | null> {
  return deps.vtFetch(path).then((v) => v as T).catch(() => null);
}

// ---- window 브리지 시그널 어댑터 (core/signals.ts의 지연-청크 안전 버전) ----

export function useSessionsVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).storeSubscribe?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

export function useAgentVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).onStatusChange?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

/** 2026-09-18 후속(그룹 재정의) — 그룹은 이제 화면(pane 트리)에서 도출된다.
 * `storeSubscribe`(세션 추가/삭제)만으로는 "이미 있는 세션을 다른 pane으로
 * 옮겼다" 같은 순수 레이아웃 변경을 못 잡으므로, layout/store.js가 따로
 * 노출한 `window.onLayoutChange`를 구독한다. */
export function useLayoutVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).onLayoutChange?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

// ---- 데이터 수집 ----

export interface AgentDetail {
  since: number | null;
  tool: string | null;
  question: string | null;
  options: { key: string; label: string }[] | null;
  /** P1-1(ADR-33) — 서버 `agent_status.py`의 `prompt_id`. 승인 응답 경쟁을
   * 막는 토큰(rail-data.ts의 `RailRowInput.promptId` 참고). */
  promptId: string | null;
  /** waiting의 유형(approval/choice/plan/question)과 done의 보조 표시. 서버가 정한다. */
  kind: string | null;
  badge: string | null;
}

/** 2026-09-30 — 상태 문장 세부·에이전트 이름은 폴링하지 않고 공용 저장소
 * (agent/state.js)에서 읽는다. 그 저장소는 `/ws-agent` push(연결 스냅샷·훅
 * 이벤트·15초 하트비트·`agents_change`)로 채워진다. 예전엔 여기서 4초마다
 * /api/agent/status와 /api/agents를 따로 긁었다(86분에 1,850회).
 *
 * 정체(어떤 CLI인가)와 상태(무엇을 하는 중인가)는 서버에서 다른 소스다 —
 * `/api/agent/status`에는 CLI 이름이 없다. 그래서 저장소도 둘을 따로 든다. */
export function readAgentDetails(): Record<string, AgentDetail> {
  return (window as any).agentDetails?.() || {};
}

export function readAgentNames(): Record<string, string> {
  return (window as any).agentNames?.() || {};
}

/** 안전망 — `/ws-agent`가 끊긴 동안(재시도를 포기한 뒤 포함)에도 목록이 멎지
 * 않게 가끔 직접 받아 **같은 저장소에** 넣는다. 화면은 저장소만 본다. */
export async function syncAgentsFromServer(deps: RailDeps): Promise<void> {
  const w = window as any;
  const [st, names] = await Promise.all([
    safeFetch<{ all?: Record<string, any> }>(deps, '/api/agent/status'),
    safeFetch<Record<string, { agent?: string }>>(deps, '/api/agents'),
  ]);
  if (st && st.all) w.vtApplyAgentSnapshot?.(st.all);
  if (names) w.vtApplyAgentNames?.(names);
}

interface GitCacheEntry {
  at: number;
  files: number | null;
  /** 서버가 "저장소 아님"이라고 답했다(요청 실패로 null이 된 것과 구분한다). */
  noRepo: boolean;
  /** 센 뒤로 변경 알림이 왔다. */
  dirty: boolean;
}
const _gitCache = new Map<string, GitCacheEntry>();

function _dueAt(cwd: string, hit: GitCacheEntry): number {
  if (hit.noRepo) return hit.at + GIT_NOREPO_MS;
  if (!isWatchLive('repo', cwd)) return hit.at + GIT_CACHE_MS;
  return hit.at + (hit.dirty ? GIT_DIRTY_GAP_MS : GIT_WATCHED_MAX_MS);
}

/** 다음 조회까지 남은 시간(ms). 변경 알림을 받고 언제 다시 그릴지 정할 때 쓴다. */
export function msUntilDiffRefresh(cwd: string): number {
  const hit = _gitCache.get(cwd);
  return hit ? Math.max(0, _dueAt(cwd, hit) - Date.now()) : 0;
}

// Rail.tsx가 이 캐시를 **직접** 들여다보고 있었는데 `_gitCache`도 `GIT_CACHE_MS`도
// 이 모듈 밖으로 나가지 않는 값이라 런타임에 `ReferenceError: _gitCache is not
// defined`가 났다(실브라우저 콘솔에서 발견, 2026-09-17). 스토어 구독자 안에서
// 던지므로 그 뒤의 행 조립이 통째로 중단된다 — 레일에 세션 행이 안 그려지던 원인.
// 빌드가 못 잡은 이유는 vite가 타입 검사 없이 트랜스파일만 하기 때문이다.
// 캐시를 또 복제하지 않고(Fleet.tsx가 이미 사본을 갖고 있다) 접근자만 연다.

/** 캐시에 있는 값. `undefined`는 "아직 모름", `null`은 "git 저장소가 아님" — 둘을 구분한다. */
export function cachedDiffCount(cwd: string): number | null | undefined {
  return _gitCache.get(cwd)?.files;
}

/** 비었거나 낡았으면 true. 호출자가 fetchDiffCount로 채운다. */
export function diffCountStale(cwd: string): boolean {
  const hit = _gitCache.get(cwd);
  return !hit || Date.now() >= _dueAt(cwd, hit);
}
// 같은 cwd에 세션이 여럿이면(흔하다 — 한 저장소에 탭 여러 개) 행마다 이 함수를
// 불러 같은 git status가 동시에 여러 번 나갔다(실측: 세션 3개면 3배). 진행 중인
// 조회는 cwd별로 하나만 두고 나머지는 그 결과를 기다린다.
const _inflight = new Map<string, Promise<number | null>>();

export function fetchDiffCount(deps: Pick<RailDeps, 'vtFetch'>, cwd: string): Promise<number | null> {
  const hit = _gitCache.get(cwd);
  if (hit && !diffCountStale(cwd)) return Promise.resolve(hit.files);
  const running = _inflight.get(cwd);
  if (running) return running;
  // 조회 중에 온 변경 알림을 잃지 않도록 dirty는 요청 **전에** 내린다.
  if (hit) hit.dirty = false;
  const p = (async () => {
    const data = await safeFetch<{ repo?: boolean; files?: unknown[] }>(deps, `/api/git/status?repo=${encodeURIComponent(cwd)}`);
    const files = data?.repo ? (data.files || []).length : null;
    const dirty = _gitCache.get(cwd)?.dirty ?? false;
    _gitCache.set(cwd, { at: Date.now(), files, noRepo: data?.repo === false, dirty });
    return files;
  })().finally(() => { _inflight.delete(cwd); });
  _inflight.set(cwd, p);
  return p;
}

/**
 * 세션 cwd들의 변경 알림 구독을 관리한다(레일·플릿이 각자 하나씩). 알림이 오면
 * 캐시를 dirty로 표시하고, 다시 셀 수 있는 때(msUntilDiffRefresh)에 `onDue()`를
 * 부른다 — 호출자는 다시 그리기만 하면 행이 fetchDiffCount를 부른다. 탭이
 * 숨어 있으면 보일 때까지 미룬다(예전 폴링도 숨은 탭에서는 git을 안 돌렸다).
 * 같은 저장소를 여럿이 구독해도 서버 감시는 하나다.
 */
export function createDiffWatcher(onDue: () => void) {
  const subs = new Map<string, () => void>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  let pendingWhileHidden = false;
  const fire = () => {
    if (document.hidden) { pendingWhileHidden = true; return; }
    onDue();
  };
  const onVisible = () => {
    if (!document.hidden && pendingWhileHidden) { pendingWhileHidden = false; onDue(); }
  };
  document.addEventListener('visibilitychange', onVisible);
  const onChange = (cwd: string) => {
    if (timers.has(cwd)) return;
    timers.set(cwd, setTimeout(() => { timers.delete(cwd); fire(); }, msUntilDiffRefresh(cwd)));
  };
  return {
    sync(cwds: Iterable<string>) {
      const want = new Set<string>();
      for (const c of cwds) if (c) want.add(c);
      for (const [c, off] of subs) if (!want.has(c)) { off(); subs.delete(c); }
      for (const c of want) {
        if (subs.has(c)) continue;
        // 감시가 서기 전에 센 값이면 그 사이 변경을 놓쳤을 수 있다 — 한 번은 다시 센다.
        const hit = _gitCache.get(c);
        if (hit) hit.dirty = true;
        subs.set(c, watchPath('repo', c, () => {
          const hit = _gitCache.get(c);
          if (hit) hit.dirty = true;
          onChange(c);
        }));
      }
    },
    dispose() {
      for (const off of subs.values()) off();
      subs.clear();
      for (const t of timers.values()) clearTimeout(t);
      timers.clear();
      document.removeEventListener('visibilitychange', onVisible);
    },
  };
}

// 30-worktree.md §2: 세션이 하나도 없는 워크트리를 여는 API. ADR-29 B부터
// 레일에는 이걸 부를 자리(워크트리 행)가 없다 — C단계(저장소 시트)가
// 이어받는다. 그때까지 쓰이지 않고 대기한다.
export async function openWorktree(deps: RailDeps, wtId: string): Promise<string | null> {
  try {
    const data = await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}/open`, { method: 'POST' }) as { tmux_session?: string };
    return data?.tmux_session || null;
  } catch (_) {
    return null;
  }
}

// 2.1 D5 — 서버엔 있었지만 어느 화면도 안 부르던 삭제 API를 여기서 처음
// 연결했다(30-worktree.md §3). ADR-29 C(저장소 시트)가 호출자를 이어받는다
// — B단계부터 레일에는 워크트리 행 자체가 없어 이 함수를 부를 자리가
// 없다(잠시 미사용, C가 곧 이어받는다). 확인은 두 단계다: 먼저 평범한 확인,
// 서버가 409(더러움)로 거절하면 "그래도 지울지"를 한 번 더 물어 force로
// 재시도한다 — 뜻하지 않게 커밋 안 된 변경을 날리는 사고를 막기 위해서다.
// 메인 워크트리는 애초에 호출자가 메뉴에 안 띄운다(서버도 400으로 거절하지만,
// 거절당하는 것 자체가 이미 "왜 안 되지"라는 물음표다).
export async function deleteWorktreeRow(
  deps: RailDeps, wtId: string, label: string, hasSessions: boolean,
): Promise<{ ok: boolean; error?: string }> {
  const firstWarn = hasSessions
    ? `'${label}' 워크트리를 삭제합니다. 열려 있는 세션의 tmux는 그대로 남지만 다음에 자동으로 재연결되지는 않습니다. 되돌릴 수 없습니다.`
    : `'${label}' 워크트리를 삭제합니다. 되돌릴 수 없습니다.`;
  if (!await confirmDialog(firstWarn, { title: '워크트리 삭제', okLabel: '삭제', danger: true })) return { ok: false };
  try {
    await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
    return { ok: true };
  } catch (e: any) {
    if (e?.status === 409 && e?.data?.dirty) {
      if (!await confirmDialog(`'${label}'에 커밋하지 않은 변경사항이 있습니다. 그래도 삭제할까요? 그 변경은 사라집니다.`, { title: '변경사항 있음', okLabel: '그래도 삭제', danger: true })) {
        return { ok: false };
      }
      try {
        await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}`, {
          method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: true }),
        });
        return { ok: true };
      } catch (e2: any) {
        return { ok: false, error: e2?.data?.error || e2?.message || '삭제 실패' };
      }
    }
    return { ok: false, error: e?.data?.error || e?.message || '삭제 실패' };
  }
}

/** 행에 해당하는 웹 세션 id. 열려 있지 않으면 null(웹 세션 id가 필요한 동작은 못 한다). */
export function actionSessionId(row: Pick<SessionRow, 'sessionId'>): string | null {
  return row.sessionId || null;
}

export interface CollectInput {
  /** GET /api/tmux/sessions 또는 `/ws-workspace`의 tmux_sessions. */
  tmux: any[];
  /** GET /api/repos의 repos — 브랜치·변경량의 출처(없으면 브랜치 줄이 비는다). */
  repos?: any[];
  /** layout/store.js의 getTabGroups() — 묶음 뷰 이름의 출처. */
  groups?: { id: string; label: string; sessionIds: string[] }[];
  deps: Pick<RailDeps, 'vtFetch'>;
  /** git 조회가 끝나 다시 그려야 할 때. */
  onDiff: () => void;
}

/**
 * 레일과 폰 Fleet이 같이 쓰는 행 조립. 출처: tmux 목록(이름·만든 곳·붙은 화면 수) +
 * 이 브라우저의 세션 맵(열림) + 공용 에이전트 저장소(상태·유형) + /api/repos(브랜치).
 * 일반(비 tmux) 웹 세션도 행으로 낸다 — 서버에 지속되는 대상이 없어 늘 열려 있다.
 *
 * 부수 효과: 낡은 cwd의 변경량 조회를 시작한다(끝나면 onDiff).
 */
/**
 * 서버가 준 webScreens(PTY를 쥔 웹 화면 수)·otherClients(웹이 아닌 attach)로 배지 두 값을 만든다.
 * 웹 화면에는 이 브라우저 탭의 PTY(열려 있으면 1개)가 들어 있어 뺀다.
 * 구버전 서버처럼 webScreens가 없으면 예전 계산(attached − 내 PTY)을 쓰고 otherClients는 0.
 */
function screenCounts(t: any, attached: number, openHere: boolean): { otherScreens: number; otherClients: number } {
  const own = openHere ? 1 : 0;
  if (typeof t.webScreens !== 'number') return { otherScreens: Math.max(0, attached - own), otherClients: 0 };
  return {
    otherScreens: Math.max(0, t.webScreens - own),
    otherClients: Math.max(0, Number(t.otherClients) || 0),
  };
}

export function collectSessionRows(inp: CollectInput): SessionRow[] {
  const w = window as any;
  const all = w.allSessions ? w.allSessions() : {};
  const details = readAgentDetails();
  const names = readAgentNames();

  const sidToView = new Map<string, string>();
  for (const g of inp.groups || []) for (const sid of g.sessionIds) sidToView.set(sid, g.label);

  const tmuxToWebSid: Record<string, string> = {};
  for (const [sid, s] of Object.entries<any>(all)) {
    const tn = s.tmuxName || s.tmux_name;
    if (tn && !(tn in tmuxToWebSid)) tmuxToWebSid[tn] = sid;
  }

  const wtInfo = new Map<string, { branch: string; isMain: boolean; changed: any }>();
  for (const repo of inp.repos || []) {
    for (const wt of repo.worktrees || []) {
      wtInfo.set(wt.id, { branch: wt.branch, isMain: !!wt.isMain, changed: wt.changed || null });
    }
  }

  // 세션 → 워크트리: tmux 옵션(@fsh_wt, 워크트리 열기로 만든 세션)이 없으면 서버가 cwd로 맞춘
  // 목록(repos[].worktrees[].sessions)을 쓴다 — 저장소 안에서 그냥 만든 세션도 브랜치가 보이게.
  const nameToWt = new Map<string, string>();
  for (const repo of inp.repos || []) {
    for (const wt of repo.worktrees || []) {
      for (const n of wt.sessions || []) if (!nameToWt.has(n)) nameToWt.set(n, wt.id);
    }
  }

  const out: SessionRow[] = [];
  const seenTmux = new Set<string>();
  for (const t of inp.tmux) {
    seenTmux.add(t.name);
    const sid = tmuxToWebSid[t.name] || '';
    const info = wtInfo.get(t.wt_id || '') ?? wtInfo.get(nameToWt.get(t.name) || '');
    const detail = details[t.name];
    const cwd = t.cwd || null;
    const attached = Number(t.attached) || 0;
    out.push({
      sessionId: sid,
      tmuxName: t.name,
      name: t.name,
      open: !!sid,
      status: w.getStatus ? w.getStatus(t.name) : 'idle',
      unseen: !!(w.isUnseen && w.isUnseen(t.name)),
      since: detail?.since ?? null,
      tool: detail?.tool ?? null,
      kind: detail?.kind ? normalizeKind(detail.kind) : null,
      badge: normalizeBadge(detail?.badge),
      diffFiles: cwd ? (cachedDiffCount(cwd) ?? null) : null,
      changed: info?.changed ?? null,
      question: detail?.question ?? null,
      options: detail?.options ?? null,
      promptId: detail?.promptId ?? null,
      agent: names[t.name] ?? null,
      branch: info?.branch ?? null,
      isMainWorktree: info?.isMain ?? false,
      worktreeId: t.wt_id || null,
      origin: normalizeOrigin(t.origin),
      viewLabel: (sid && sidToView.get(sid)) || null,
      ...screenCounts(t, attached, !!sid),
    });
    if (cwd && diffCountStale(cwd)) fetchDiffCount(inp.deps, cwd).then(inp.onDiff);
  }

  for (const [sid, s] of Object.entries<any>(all)) {
    const tn = s.tmuxName || s.tmux_name;
    if (tn) continue; // tmux 이름이 있는데 목록에 없다 — 방금 죽은 세션, 다음 갱신에서 정리된다.
    out.push({
      sessionId: sid,
      tmuxName: null,
      name: w.sessionDisplayName ? w.sessionDisplayName(sid) : sid.slice(0, 8),
      open: true,
      status: 'idle',
      unseen: false,
      since: null, tool: null, kind: null, badge: null,
      diffFiles: null,
      agent: null, branch: null, isMainWorktree: false,
      origin: 'local',
      viewLabel: sidToView.get(sid) || null,
      otherScreens: 0,
      otherClients: 0,
    });
  }
  return out;
}

// 2026-09-18 후속(그룹 재정의) — `setSessionGroup`/`renameGroup`(둘 다
// `@fsh_grp`/`~/.vt/groups.json` 기반)을 여기서 지웠다. 그룹은 이제 서버
// 왕복 없이 화면(pane 트리)에서 실시간으로 도출된다(layout/store.js의
// tabGroups) — 이름 짓기는 그 탭 자체에 라벨을 붙이는 것뿐이라
// `window.vtRenameTab`(layout/tabbar.js) 하나로 끝난다.
