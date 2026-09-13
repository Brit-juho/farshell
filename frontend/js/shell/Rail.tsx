// N36 §5 — 워크트리 레일 252px. 2.1.1(N8/N44)부터 행 = Worktree다(30-worktree.md
// §4). 어떤 워크트리에도 안 속하는 세션은 「기타」 그룹의 세션 행으로 남는다
// (같은 §4 규칙) — 워크트리가 0개인 환경(수용 기준)은 이 「기타」 규칙 하나로
// 그대로 성립한다(레일이 비지 않는다).
//
// ⚠ 이 파일은 core/store.js·layout/store.js·agent/state.js·term/session.js를
// 직접 import하지 않는다(panels/viewer-lazy.js·shell/Hud.tsx와 같은 이유 —
// 지연 청크가 이들을 정적 import하면 Vite lib 모드가 상태를 복제한다,
// ADR-26/N35 커밋 참고). 전부 그 파일들이 이미 노출해 둔 window 브리지로만
// 읽는다. vtFetch·getAction만 main.js가 인자로 넘긴다(Hud.tsx와 같은 이유).
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import {
  buildRailSections, mostUrgentStatus, GROUP_LABEL, hashRepoColorIndex,
  type WorktreeRailRowInput, type OtherRailRowInput, type DesktopRailRowInput,
} from './rail-data.js';
import {
  buildHostMenu, remoteSessionRows, resolveActiveHost, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';
import { wireRatioResizer } from '../layout/resizer.js';
import { WorktreeDialog } from './WorktreeDialog.js';

const SESSIONS_POLL_MS = 5000;   // tmux 목록(attached·cwd) — 자주 안 바뀌어도 짧게, 값싸다.
const STATUS_POLL_MS = 4000;     // since/tool 보강 — 서버가 아직 질문 텍스트를 안 줘서(2.1.0 gap) 상태 문장 갱신용.
const GIT_CACHE_MS = 60000;      // §5 원문: "60초 캐시" — 「기타」 세션 행에만 쓴다(워크트리 행은 changed 요약을 서버가 준다).
const WORKTREES_POLL_MS = 8000;  // N8(30-worktree.md) — 서버가 이미 5초 캐시라 자주 불러도 싸다.
// C1 — 호스트 목록. 서버가 원격 세션을 30초 캐시하므로(routes/hosts.py) 그보다
// 짧게 불러도 네트워크 왕복이 늘지 않는다. 15초면 "호스트가 꺼졌다"를 반 캐시
// 주기 안에 알아챈다.
const HOSTS_POLL_MS = 15000;
const MIN_W = 240, MAX_W = 480, DEFAULT_W = 252;
// N3(60-settings-palette.md §1)가 생겨 device-settings 정식 스토어로
// 옮겼다 — 이전엔 여기 주석이 "N3 전이라 임시로 localStorage"였다. core/
// settings.js도 지연 청크가 정적 import 못 하는 모듈이라(파일 상단 주석과
// 같은 이유) window 브리지(vtSettingsGet/Set)로만 읽는다.
const SETTINGS_W_KEY = 'ui.rail.width';
const SETTINGS_COLLAPSE_KEY = 'ui.rail.collapsed';
const SETTINGS_HOST_KEY = 'ui.activeHostId';

export interface RailDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
  getAction: (name: string) => unknown;
}

function safeFetch<T>(deps: RailDeps, path: string): Promise<T | null> {
  return deps.vtFetch(path).then((v) => v as T).catch(() => null);
}

// ---- window 브리지 시그널 어댑터 (core/signals.ts의 지연-청크 안전 버전) ----

function useSessionsVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).storeSubscribe?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

function useAgentVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).onStatusChange?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

// ---- 데이터 수집 ----

interface AgentDetail {
  since: number | null;
  tool: string | null;
  question: string | null;
  options: { key: string; label: string }[] | null;
}

async function fetchAgentDetails(deps: RailDeps): Promise<Record<string, AgentDetail>> {
  const data = await safeFetch<{ all?: Record<string, any> }>(deps, '/api/agent/status');
  const out: Record<string, AgentDetail> = {};
  for (const entry of Object.values(data?.all || {})) {
    const name = (entry as any)?.tmux_session;
    if (!name) continue;
    out[name] = {
      since: (entry as any).since ?? null,
      tool: (entry as any).tool || (entry as any).last_tool || null,
      question: (entry as any).question ?? null,
      options: (entry as any).options ?? null,
    };
  }
  return out;
}

const _gitCache = new Map<string, { at: number; files: number | null }>();
async function fetchDiffCount(deps: RailDeps, cwd: string): Promise<number | null> {
  const hit = _gitCache.get(cwd);
  if (hit && Date.now() - hit.at < GIT_CACHE_MS) return hit.files;
  const data = await safeFetch<{ repo?: boolean; files?: unknown[] }>(deps, `/api/git/status?repo=${encodeURIComponent(cwd)}`);
  const files = data?.repo ? (data.files || []).length : null;
  _gitCache.set(cwd, { at: Date.now(), files });
  return files;
}

// 30-worktree.md §2: 세션이 하나도 없는 워크트리를 여는 API. 흐리게 표시된
// 행을 클릭했을 때만 탄다(대부분은 이미 tmux 세션이 있어 attachTmux로 충분).
async function openWorktree(deps: RailDeps, wtId: string): Promise<string | null> {
  try {
    const data = await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}/open`, { method: 'POST' }) as { tmux_session?: string };
    return data?.tmux_session || null;
  } catch (_) {
    return null;
  }
}

type DesktopRailRow =
  | (WorktreeRailRowInput & { statusSentence: string })
  | (OtherRailRowInput & { statusSentence: string });

// 행이 가리키는 "열 수 있는" 대상. 워크트리 행 중 세션이 전혀 없는(흐리게
// 표시된) 행은 null — 컨텍스트 메뉴(세션 대상 액션)를 못 연다, 클릭은 openRow가
// 별도로 open API로 처리한다.
function actionSessionId(row: DesktopRailRow): string | null {
  // C1: 원격 세션의 sessionId는 `remote:<host>:<name>` 합성 키라 로컬 세션 맵에
  // 없다 — 여기서 null로 잘라야 활성 표시·컨텍스트 메뉴가 로컬 id와 엉키지 않는다.
  if (row.kind === 'session') return row.remote ? null : row.sessionId;
  return row.primarySessionId;
}

function Row(props: { row: DesktopRailRow; active: boolean; onOpen: (e: MouseEvent) => void; onContext: (e: MouseEvent) => void }) {
  const isWt = () => props.row.kind === 'worktree';
  const isRemote = () => props.row.kind === 'session' && !!props.row.remote;
  // 원격 행은 이제 열 수 있으므로 흐리게 그리지 않는다(3단계 전에는 못 열어서
  // no-session으로 뒀다).
  const noSession = () =>
    isWt() && !actionSessionId(props.row) && !(props.row as WorktreeRailRowInput).primaryTmuxName;
  const diffLabel = () => {
    if (props.row.kind === 'worktree') {
      const c = props.row.changed;
      return c && c.files > 0 ? `+${c.add} −${c.del}` : null;
    }
    return props.row.diffFiles != null && props.row.diffFiles > 0 ? `파일 ${props.row.diffFiles}` : null;
  };
  const rowName = () => (props.row.kind === 'worktree' ? props.row.label : props.row.name);

  return (
    <div
      class="vt-wgrail-row"
      classList={{ active: props.active, 'no-session': noSession() }}
      onClick={props.onOpen}
      onContextMenu={props.onContext}
      role="button"
      tabindex="0"
    >
      {/* 20-design-system.md §5(O2): 레일 행 왼쪽 끝 세로 막대는 저장소 해시
          색점(원형 dot과 헷갈리지 않는 "막대") — 상태 5색·acc와는 별개 램프
          (--color-hash-1..8). 그 오른쪽의 기존 막대가 상태색(30-worktree.md
          §4/10-shell-layout.md §5)을 그대로 맡는다. 「기타」 세션 행은 저장소가
          없어 둘 다 "색점 없음"(kind-session이 CSS에서 투명 처리). */}
      <span class={`vt-wgrail-hash ${isWt() ? `hash-${hashRepoColorIndex((props.row as WorktreeRailRowInput).repoName)}` : 'kind-session'}`} />
      <span class={`vt-wgrail-bar ${isWt() ? `tone-${props.row.status}` : 'kind-session'}`} />
      <div class="vt-wgrail-row-main">
        <div class="vt-wgrail-row-top">
          <span class="vt-wgrail-name">{rowName()}</span>
          <Show when={diffLabel()}>
            <span class="vt-wgrail-diff">{diffLabel()}</span>
          </Show>
        </div>
        <div class="vt-wgrail-row-sub">
          {props.row.statusSentence}
          <Show when={isRemote()}><span class="vt-wgrail-remote-note"> · 원격</span></Show>
        </div>
        <Show when={props.row.status === 'waiting' && props.row.question}>
          <div class="vt-wgrail-question">? {props.row.question}</div>
        </Show>
      </div>
    </div>
  );
}

interface MenuItem {
  label: string;
  run: () => void;
  /** C1 호스트 메뉴의 둘째 줄("세션 3 · 12ms" 또는 "응답 없음"). 없으면 안 그린다. */
  detail?: string;
  /** 흐리게(오프라인 호스트). 선택 자체는 막지 않는다 — 꺼진 호스트를 고르면
   * 이유를 보여주는 게 목적이다. */
  dim?: boolean;
  checked?: boolean;
}

function Menu(props: { x: number; y: number; onClose: () => void; items: MenuItem[] }) {
  let ref: HTMLDivElement | undefined;
  const onDocClick = (e: MouseEvent) => { if (ref && !ref.contains(e.target as Node)) props.onClose(); };
  document.addEventListener('mousedown', onDocClick, true);
  onCleanup(() => document.removeEventListener('mousedown', onDocClick, true));

  return (
    <div ref={ref} class="vt-menu" style={{ left: `${props.x}px`, top: `${props.y}px`, right: 'auto' }}>
      <For each={props.items}>
        {(it) => (
          <div
            class="vt-menu-item"
            classList={{ dim: !!it.dim, checked: !!it.checked }}
            onClick={() => { props.onClose(); it.run(); }}
          >
            <span class="vt-menu-item-label">{it.checked ? '✓ ' : ''}{it.label}</span>
            <Show when={it.detail}><span class="vt-menu-item-detail">{it.detail}</span></Show>
          </div>
        )}
      </For>
    </div>
  );
}

function Rail(props: { deps: RailDeps }) {
  const sessionsVersion = useSessionsVersion();
  const agentVersion = useAgentVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  const [agentDetails, setAgentDetails] = createSignal<Record<string, AgentDetail>>({});
  const [worktrees, setWorktrees] = createSignal<any[]>([]);
  const [diffTick, setDiffTick] = createSignal(0); // git 조회가 끝나면 다시 그리라는 신호
  const [collapsed, setCollapsed] = createSignal(Boolean((window as any).vtSettingsGet?.(SETTINGS_COLLAPSE_KEY)));
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; sessionId: string } | null>(null);
  const [moreMenu, setMoreMenu] = createSignal<{ x: number; y: number } | null>(null);
  const [dialogOpen, setDialogOpen] = createSignal(false);
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [hostMenu, setHostMenu] = createSignal<{ x: number; y: number } | null>(null);
  const [activeHostId, setActiveHostId] = createSignal<string>(
    String((window as any).vtSettingsGet?.(SETTINGS_HOST_KEY) || LOCAL_HOST),
  );

  const refreshSessions = async () => {
    const list = await safeFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (list) setTmuxSessions(list);
  };
  const refreshAgent = async () => setAgentDetails(await fetchAgentDetails(props.deps));
  const refreshWorktrees = async () => {
    const data = await safeFetch<{ worktrees?: any[] }>(props.deps, '/api/worktrees');
    setWorktrees(data?.worktrees || []);
  };

  // C1 — 로컬+원격을 한 목록으로. 실패하면(라우터가 없는 옛 서버 등) 빈 배열이
  // 남아 스위처가 아예 안 그려진다 — 멀티호스트를 안 쓰는 사람에게는 그게 맞다.
  const refreshHosts = async () => {
    const data = await safeFetch<{ hosts?: HostEntry[] }>(props.deps, '/api/hosts');
    if (data?.hosts) setHosts(data.hosts);
  };

  refreshSessions();
  refreshAgent();
  refreshWorktrees();
  refreshHosts();
  const t1 = setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_POLL_MS);
  const t2 = setInterval(() => { if (!document.hidden) refreshAgent(); }, STATUS_POLL_MS);
  const t3 = setInterval(() => { if (!document.hidden) refreshWorktrees(); }, WORKTREES_POLL_MS);
  const t4 = setInterval(() => { if (!document.hidden) refreshHosts(); }, HOSTS_POLL_MS);
  onCleanup(() => { clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4); });

  // 키맵(worktreeNew, core/keymap.js) · 팔레트 등 이 파일을 정적 import 못 하는
  // 곳(위 파일 상단 주석과 같은 이유)이 다이얼로그를 열 수 있도록 하는 브리지.
  (window as any).vtOpenWorktreeDialog = () => setDialogOpen(true);
  onCleanup(() => { if ((window as any).vtOpenWorktreeDialog) delete (window as any).vtOpenWorktreeDialog; });

  // 세션 스토어(sessionsVersion)가 바뀔 때마다(탭 추가/삭제/전환) 실제 목록을
  // 다시 구성한다 — window.allSessions()가 진짜 웹 세션 맵의 단일 출처다.
  // C1 — 저장된 값이 아직 등록된 호스트를 가리키는지. 목록이 아직 안 왔을 때
  // (hosts()가 빈 배열) 로컬로 튕기지 않도록 목록이 비면 저장값을 그대로 쓴다.
  const effectiveHostId = createMemo(() =>
    hosts().length === 0 ? activeHostId() : resolveActiveHost(hosts(), activeHostId()));
  const isRemoteHost = createMemo(() => effectiveHostId() !== LOCAL_HOST);
  const activeHost = createMemo(() => hosts().find((h) => h.id === effectiveHostId()) || null);

  const rows = createMemo<DesktopRailRowInput[]>(() => {
    sessionsVersion(); agentVersion(); // 구독 트리거용 — 값 자체는 안 씀
    const w = window as any;

    // 원격 호스트를 고른 상태: 워크트리 API는 로컬 전용이라(2.2 범위) 전부
    // 세션 행이다. 이미 열린 pane은 건드리지 않는다 — 스위처는 필터일 뿐이다.
    if (isRemoteHost()) {
      const h = activeHost();
      return h ? remoteSessionRows(h) : [];
    }
    const all = w.allSessions ? w.allSessions() : {};
    const byName: Record<string, any> = {};
    for (const t of tmuxSessions()) byName[t.name] = t;
    const details = agentDetails();

    // tmux 세션 이름 → 웹에 이미 열린 세션 id(있으면).
    const tmuxToWebSid: Record<string, string> = {};
    for (const [sid, s] of Object.entries<any>(all)) {
      const tn = s.tmuxName || s.tmux_name;
      if (tn && !(tn in tmuxToWebSid)) tmuxToWebSid[tn] = sid;
    }

    // 30-worktree.md §4: 행 = Worktree. 상태는 sessions 중 가장 시급한 것.
    const wtSessionNames = new Set<string>();
    const wtRows: WorktreeRailRowInput[] = worktrees().map((wt: any) => {
      const sessions: string[] = wt.sessions || [];
      for (const s of sessions) wtSessionNames.add(s);
      const statuses = sessions.map((t) => (w.getStatus ? w.getStatus(t) : 'idle'));
      const status = mostUrgentStatus(statuses);

      let since: number | null = null, tool: string | null = null;
      let question: string | null = null, options: { key: string; label: string }[] | null = null;
      let primaryTmux: string | null = null;
      for (const t of sessions) {
        const st = w.getStatus ? w.getStatus(t) : 'idle';
        if (st !== status) continue;
        const d = details[t];
        if (!primaryTmux || (d?.since ?? -Infinity) > (since ?? -Infinity)) {
          primaryTmux = t;
          since = d?.since ?? null;
          tool = d?.tool ?? null;
          question = d?.question ?? null;
          options = d?.options ?? null;
        }
      }
      const attachTarget = primaryTmux || sessions[0] || null;
      const primarySessionId = attachTarget ? (tmuxToWebSid[attachTarget] ?? null) : null;

      return {
        kind: 'worktree',
        worktreeId: wt.id,
        label: wt.isMain ? wt.repoName : `${wt.repoName}/${wt.branch}`,
        repoName: wt.repoName,
        branch: wt.branch,
        isMain: !!wt.isMain,
        path: wt.path,
        primarySessionId,
        primaryTmuxName: primarySessionId ? null : attachTarget,
        status,
        since,
        tool,
        changed: wt.changed || null,
        question,
        options,
      };
    });

    // 「기타」: 어떤 워크트리 sessions 목록에도 없는 세션(30 §4).
    const otherRows: OtherRailRowInput[] = [];
    for (const [sid, s] of Object.entries<any>(all)) {
      const tmuxName = s.tmuxName || s.tmux_name || null;
      if (tmuxName && wtSessionNames.has(tmuxName)) continue;
      const tmuxInfo = tmuxName ? byName[tmuxName] : null;
      const status = tmuxName && w.getStatus ? w.getStatus(tmuxName) : 'idle';
      const detail = tmuxName ? details[tmuxName] : undefined;
      const cwd = tmuxInfo?.cwd || null;
      otherRows.push({
        kind: 'session',
        sessionId: sid,
        tmuxName,
        name: w.sessionDisplayName ? w.sessionDisplayName(sid) : (tmuxName || sid.slice(0, 8)),
        status,
        since: detail?.since ?? null,
        tool: detail?.tool ?? null,
        diffFiles: cwd ? (_gitCache.get(cwd)?.files ?? null) : null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
      });
      // git status는 별도로 비동기 채운다(캐시 60초) — 도착하면 diffTick으로 재렌더.
      if (cwd && (!_gitCache.has(cwd) || Date.now() - (_gitCache.get(cwd)?.at ?? 0) >= GIT_CACHE_MS)) {
        fetchDiffCount(props.deps, cwd).then(() => setDiffTick((n) => n + 1));
      }
    }

    return [...wtRows, ...otherRows];
  });

  const sections = createMemo(() => { diffTick(); return buildRailSections(rows()); });
  const totalRows = createMemo(() => sections().reduce((n, s) => n + s.rows.length, 0));

  const activeId = createMemo(() => { sessionsVersion(); return (window as any).activeSessionId?.() ?? null; });

  const openRow = async (e: MouseEvent, row: DesktopRailRow) => {
    const w = window as any;
    // C1+3단계 — 원격 행은 프록시 경로로 연다(term/remote.js가 window에 건다).
    if (row.kind === 'session' && row.remote) {
      const host = effectiveHostId();
      if (typeof w.attachRemoteSession === 'function' && row.tmuxName) {
        await w.attachRemoteSession(host, row.tmuxName);
      }
      return;
    }
    // 10 §4 2단계 — 워크트리 행을 열면 **그 워크트리의 탭**으로 간다. 탭마다
    // 자기 pane 트리를 가지므로, 여기서 탭을 안 맞추면 다른 워크트리의 배치
    // 위에 남의 세션을 얹게 된다.
    if (row.kind === 'worktree') {
      w.openWorktreeTab?.(row.worktreeId, row.label);
    }
    const sid = actionSessionId(row);
    if (sid) {
      if (e.metaKey || e.ctrlKey) w.splitActivePane?.('row', sid);
      else w.switchTo?.(sid);
      return;
    }
    if (row.kind === 'worktree' && row.primaryTmuxName) {
      await w.attachTmux?.(row.primaryTmuxName);
      return;
    }
    if (row.kind === 'worktree') {
      // 세션이 전혀 없는 워크트리 — 30-worktree.md §2 open API로 새로 연다.
      const tmuxName = await openWorktree(props.deps, row.worktreeId);
      if (tmuxName) await w.attachTmux?.(tmuxName);
      await refreshWorktrees();
    }
  };

  const contextRow = (e: MouseEvent, row: DesktopRailRow) => {
    e.preventDefault();
    const sid = actionSessionId(row);
    if (!sid) return; // 세션 없는 워크트리 행은 세션 대상 메뉴가 성립하지 않는다.
    setCtxMenu({ x: e.clientX, y: e.clientY, sessionId: sid });
  };

  const ctxMenuItems = () => {
    const m = ctxMenu();
    if (!m) return [];
    const w = window as any;
    return [
      { label: '새 세션', run: () => w.createSession?.() },
      { label: '지금 이 세션 맥에서 열기', run: () => { w.switchTo?.(m.sessionId); (props.deps.getAction('session.open-on-mac') as (() => void) | undefined)?.(); } },
      { label: '연결된 화면', run: () => { w.switchTo?.(m.sessionId); (props.deps.getAction('clients.show') as (() => void) | undefined)?.(); } },
      { label: '닫기', run: () => w.removeSession?.(m.sessionId) },
    ];
  };

  // "⋯ 더보기" — §5가 기존 48px 아이콘 레일을 완전히 대체하기로 하면서 갈 곳을
  // 잃은 것들의 임시 정류장(§6 dock·§60 설정 화면이 생기기 전까지). 대부분은
  // getAction으로 바로 되지만, 마이크·테마는 아직 rail.js의 #vt-rail-panel
  // 플라이아웃 안에만 산다(desktop mic의 유일한 자리) — 그 DOM은 지우지 않고
  // CSS로만 숨겨 뒀으므로, 숨은 버튼을 그대로 다시 눌러 같은 경로를 그대로 쓴다.
  // C1 — 호스트 스위처.
  const openHostMenu = (e: MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    refreshHosts(); // 열 때 한 번 더 — 목록이 15초 지나 있을 수 있다.
    setHostMenu({ x: r.left, y: r.bottom + 4 });
  };
  const selectHost = (id: string) => {
    setActiveHostId(id);
    (window as any).vtSettingsSet?.(SETTINGS_HOST_KEY, id);
  };
  // 폰의 플릿 홈(Fleet.tsx)에도 같은 키를 쓰는 스위처가 있다. 창 폭을 오가면
  // 둘 다 살아 있으므로, 저쪽에서 바꾼 값을 여기서도 따라간다.
  const unsubHostSetting = (window as any).vtSettingsSubscribe?.((all: any) => {
    const v = String(all?.[SETTINGS_HOST_KEY] || LOCAL_HOST);
    if (v !== activeHostId()) setActiveHostId(v);
  });
  onCleanup(() => unsubHostSetting?.());
  const hostMenuItems = (): MenuItem[] =>
    buildHostMenu(hosts(), activeHostId()).map((h) => ({
      label: h.label,
      detail: h.detail,
      dim: !h.online,
      checked: h.active,
      run: () => selectHost(h.id),
    }));

  const openMoreMenu = (e: MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMoreMenu({ x: r.left, y: r.bottom + 4 });
  };
  const moreMenuItems = () => {
    const act = (id: string) => (props.deps.getAction(id) as (() => void) | undefined)?.();
    return [
      { label: '파일 열기', run: () => act('viewer.show') },
      { label: '프롬프트 큐', run: () => act('queue.show') },
      { label: '스니펫', run: () => act('snippets.show') },
      { label: '포트', run: () => act('ports.show') },
      { label: '사용량', run: () => act('usage.open') },
      { label: '설정', run: () => act('settings.show') },
      { label: '마이크 · 테마', run: () => document.getElementById('vt-rail-settings')?.click() },
    ];
  };

  let railRef: HTMLElement | undefined;
  // storedWidth는 "펼쳤을 때 폭"이라는 사용자 의도다 — 접힘 여부와 별개로
  // 기억해 둔다. 실제 화면에 반영되는 --vt-wgrail-w는 늘 이 둘을 합친
  // 값이어야 body의 padding-left(같은 변수를 쓴다)가 접힘 상태를 따라간다 —
  // 안 그러면 레일은 48px로 줄었는데 본문은 여전히 252px만큼 밀린 채로
  // 빈 틈이 남는다(실브라우저로 실제 재현·확인).
  const [storedWidth, setStoredWidth] = createSignal(
    Math.min(MAX_W, Math.max(MIN_W, Number((window as any).vtSettingsGet?.(SETTINGS_W_KEY)) || DEFAULT_W)),
  );
  const applyCssVar = () => {
    document.documentElement.style.setProperty('--vt-wgrail-w', `${collapsed() ? 48 : storedWidth()}px`);
  };
  const setWidth = (w: number) => {
    const clamped = Math.min(MAX_W, Math.max(MIN_W, Math.round(w)));
    setStoredWidth(clamped);
    (window as any).vtSettingsSet?.(SETTINGS_W_KEY, clamped);
  };
  const toggleCollapse = () => {
    const next = !collapsed();
    setCollapsed(next);
    (window as any).vtSettingsSet?.(SETTINGS_COLLAPSE_KEY, next);
  };
  createEffect(applyCssVar);

  let resizerRef: HTMLDivElement | undefined;
  const wireResizerOnMount = (el: HTMLDivElement) => {
    resizerRef = el;
    wireRatioResizer(el, {
      dir: 'row',
      getContainerSize: () => 1, // 컨테이너=1로 두고 비율을 그대로 픽셀 델타로 쓴다(§5: layout/resizer.js 재사용)
      getStartRatio: () => parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-wgrail-w'), 10) || DEFAULT_W,
      onRatio: (r: number) => { if (!collapsed()) setWidth(r); },
      onStart: () => {},
      onEnd: () => {},
    });
  };

  const defaultRepo = () => worktrees().find((x: any) => x.isMain)?.repo || worktrees()[0]?.repo || '';

  const onCreated = async (result: any) => {
    setDialogOpen(false);
    await refreshWorktrees();
    const tmuxName = result?.opened?.tmux_session || null;
    if (tmuxName) await (window as any).attachTmux?.(tmuxName);
  };

  return (
    <aside id="vt-wgrail" ref={railRef} classList={{ collapsed: collapsed() }} aria-label="워크트리">
      <div class="vt-wgrail-head">
        <Show when={!collapsed()}>
          {/* C1 — 호스트가 로컬 하나뿐이면 칩을 아예 안 그린다. 멀티호스트를
              안 쓰는 사람에게는 "고를 게 없는 드롭다운"이 잡음일 뿐이다. */}
          <Show
            when={hosts().length > 1}
            fallback={<span class="vt-wgrail-title">워크트리 · {worktrees().length}</span>}
          >
            <button
              type="button"
              class="vt-wgrail-host"
              classList={{ offline: activeHost() ? !activeHost()!.online : false, remote: isRemoteHost() }}
              onClick={openHostMenu}
              title={activeHost() ? `${activeHost()!.label} · ${hostDetail(activeHost()!)}` : '호스트 선택'}
            >
              <span class="vt-wgrail-host-name">{activeHost()?.label || effectiveHostId()}</span>
              <span class="vt-wgrail-host-caret">▾</span>
            </button>
          </Show>
        </Show>
        <button type="button" class="vt-wgrail-collapse" onClick={toggleCollapse} aria-label={collapsed() ? '펼치기' : '접기'} title={collapsed() ? '펼치기' : '접기'}>
          {collapsed() ? '›' : '‹'}
        </button>
      </div>
      <div class="vt-wgrail-body">
        <Show when={totalRows() === 0 && !collapsed() && !isRemoteHost()}>
          <div class="vt-wgrail-empty">
            아직 워크트리가 없습니다.
            <button type="button" class="vt-wgrail-empty-new" onClick={() => setDialogOpen(true)}>+ 워크트리 만들기</button>
          </div>
        </Show>
        {/* C1 — 원격 호스트가 꺼져 있으면 "빈 목록"과 "연결 안 됨"을 구분해서
            보여준다. 둘을 같은 빈 화면으로 뭉개면 "세션이 없는 건가?"로 읽힌다. */}
        <Show when={isRemoteHost() && !collapsed() && activeHost() && !activeHost()!.online}>
          <div class="vt-wgrail-empty">{hostDetail(activeHost()!)}</div>
        </Show>
        <Show when={isRemoteHost() && !collapsed() && totalRows() === 0 && activeHost()?.online}>
          <div class="vt-wgrail-empty">이 호스트에 tmux 세션이 없습니다.</div>
        </Show>
        <For each={sections()}>
          {(section) => (
            <>
              <Show when={!collapsed()}>
                <div class="vt-wgrail-group-head">{GROUP_LABEL[section.group]}</div>
              </Show>
              <For each={section.rows}>
                {(row) => (
                  <Row
                    row={row}
                    active={actionSessionId(row) === activeId()}
                    onOpen={(e) => openRow(e, row)}
                    onContext={(e) => contextRow(e, row)}
                  />
                )}
              </For>
            </>
          )}
        </For>
      </div>
      <div class="vt-wgrail-footer">
        {/* 워크트리 생성은 로컬 전용이다(원격 워크트리는 2.2 범위) — 원격을 보고
            있을 때 누르면 "맥에" 워크트리가 생겨 화면과 결과가 어긋난다. */}
        <button
          type="button"
          class="vt-wgrail-new"
          disabled={isRemoteHost()}
          title={isRemoteHost() ? '원격 호스트에는 워크트리를 만들 수 없습니다(2.2)' : ''}
          onClick={() => setDialogOpen(true)}
        >
          <Show when={!collapsed()} fallback="+">+ 워크트리 만들기</Show>
        </button>
        <button type="button" class="vt-wgrail-more" onClick={openMoreMenu} aria-label="더보기" title="파일 · 큐 · 스니펫 · 포트 · 사용량 · 설정">
          ⋯
        </button>
      </div>
      <div ref={wireResizerOnMount} class="vt-wgrail-resizer" />
      <Show when={ctxMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setCtxMenu(null)} items={ctxMenuItems()} />}
      </Show>
      <Show when={hostMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setHostMenu(null)} items={hostMenuItems()} />}
      </Show>
      <Show when={moreMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setMoreMenu(null)} items={moreMenuItems()} />}
      </Show>
      <Show when={dialogOpen()}>
        <WorktreeDialog deps={props.deps} defaultRepo={defaultRepo()} onClose={() => setDialogOpen(false)} onCreated={onCreated} />
      </Show>
    </aside>
  );
}

export function mountRail(root: HTMLElement, deps: RailDeps) {
  return render(() => <Rail deps={deps} />, root);
}
