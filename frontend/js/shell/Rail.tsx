// 레일 252px. 행 = tmux 세션(docs/ref/terminal.md 「Rail」). 구역은 둘 —
// 「개입 필요」(waiting, 접고 끌 수 있다)와 「세션」(묶음 헤더 + 칸 순서 구성원, 나머지 단독은 이름순). 묶지 않음·
// 잠자는 중 구역(폐지)과 상태 우선순위 정렬은 없다. 워크트리는 행의 브랜치 줄로만 남는다.
// 원격 호스트는 화면 전체를 그 맥으로 전환하는 방식(core/host-route.js)이라 같은
// 행 구성을 쓴다 — 목록은 그 맥의 API(중계)에서 온다.
//
// ⚠ 이 파일은 core/store.js·layout/store.js·agent/state.js·term/session.js를
// 직접 import하지 않는다(panels/viewer-lazy.js·shell/Hud.tsx와 같은 이유 —
// 지연 청크가 이들을 정적 import하면 Vite lib 모드가 상태를 복제한다,
// ADR-26/N35 커밋 참고). 전부 그 파일들이 이미 노출해 둔 window 브리지로만
// 읽는다. vtFetch·getAction만 main.js가 인자로 넘긴다(Hud.tsx와 같은 이유).
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import {
  buildRailLists, splitRunning, groupSessionRows, compactRailOrder, rowMenuItems, rowSummaryDetails, runRowMenu,
  ATTENTION_COLLAPSED_KEY, ATTENTION_SHOW_KEY, ORDER_KEY, LINEAGE_KEY, VIEW_KEY, encodeOrder, encodeLineagePrefs, encodeRailView, toggleGroupCollapsed,
  buildSessionTree, kidsProgress, moveInOrder,
  type SessionRow, type RailSort, type TreeEntry,
} from './rail-data.js';
import {
  buildHostMenu, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';
import {
  actionSessionId, collectSessionRows, pollFetch, isNoResponse,
  useAgentVersion, useSessionsVersion, useLayoutVersion, syncAgentsFromServer,
  type RailDeps,
  createDiffWatcher, answerSessionPrompt,
} from './rail-fetch.js';
import { createRailPrefs } from './rail-prefs.js';
import { Menu, Row, type MenuItem } from './RailRow.js';
import { NativeChildren } from './NativeChildren.js';
import { CatalogTree, RecentEnded, ArchiveEntry, ArchiveList, type CatalogSnapshot } from './CatalogTree.js';
import { renameSession as renameCatalogSession } from '../history/session-actions.js';
import { historyApi } from '../history/api.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';

export type { RailDeps } from './rail-fetch.js';
import { RepoVisibility } from './RepoVisibility.js';
import { RailSettingsLead } from './RailSettings.js';
import { ViewGroup } from './ViewGroup.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';
// 상태 없는 순수 모듈이라 지연 청크에 복제돼도 된다(위 ⚠의 예외).
import { createPoll, isStale } from '../core/poll.js';

// tmux 목록(attached·cwd)의 정본은 `/ws-workspace`의 `tmux_sessions` push다
// (서버가 바뀌었을 때만 보낸다). 이 폴링은 그 채널이 끊긴 동안의 안전망(예전 5초).
const SESSIONS_SAFETY_POLL_MS = 60000;
// 에이전트 상태·세부·이름의 정본은 /ws-agent push다(agent/state.js). 이 폴링은
// 그 채널이 끊긴 동안을 위한 안전망이라 길게 둔다(예전 4초 → 60초).
const AGENT_SAFETY_POLL_MS = 60000;
// N8(30-worktree.md). **정본은 `/ws-workspace`의 `worktrees_changed` push다** —
// 이 폴링은 WS가 끊긴 동안과, 터미널에서 직접 `git worktree add`를 해서 서버가
// 변경을 모르는 경우를 위한 안전망이다. 8초였을 땐 서버가 매번 저장소마다 git
// 서브프로세스를 도는 1~2초 작업을 다시 했다(캐시 TTL 5초 < 폴링 8초라 적중률 0).
const WORKTREES_POLL_MS = 60000;
// C1 — 호스트 목록. 서버가 원격 세션을 30초 캐시하므로(routes/hosts.py) 그보다
// 짧게 불러도 네트워크 왕복이 늘지 않는다. 15초면 "호스트가 꺼졌다"를 반 캐시
// 주기 안에 알아챈다.
const HOSTS_POLL_MS = 15000;
// 보관 세션 카탈로그(로컬에서만). 레일의 가장 짧은 주기라 갱신 지연 판정의 기준이다.
const CATALOG_POLL_MS = 5000;
// 폭·접힘은 작업대(Workbench.tsx)가 정한다 — 사이드바 폭은 활동 전체가 하나를 쓴다
// (`ui.side.*`, shell/side-prefs.js). 레일은 받은 접힘 상태대로 그리기만 한다.

export interface RailProps {
  deps: RailDeps;
  /** 사이드바가 접혀 48px 띠로 그려지는가(세션 활동일 때만 의미가 있다). */
  collapsed: () => boolean;
  /** 레일 머리의 접기/펼치기 버튼 — 작업대의 사이드바 토글과 같은 동작이다. */
  onToggleCollapse: () => void;
  /** waiting 세션 수가 바뀔 때 — 활동 막대 「세션」 배지(Workbench.tsx). 레일 설정의 숨김과 무관하게 센다. */
  onWaitingCount?: (n: number) => void;
}

export function Rail(props: RailProps) {
  const sessionsVersion = useSessionsVersion();
  const agentVersion = useAgentVersion();
  const layoutVersion = useLayoutVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  // 에이전트 상태·세부·이름은 공용 저장소(agent/state.js)에서 읽는다(rail-fetch.ts의
  // collectSessionRows). 행 메모가 agentVersion()을 구독하므로 저장소가 바뀌면 다시 계산된다.
  // ADR-29 B — 워크트리 행 대신 저장소·브랜치 배지 텍스트의 입력으로만
  // 쓴다(자동 그룹 제안·색점은 둘 다 2026-09-18 후속에서 없앴다).
  // /api/worktrees(평면) 대신 /api/repos(저장소별로 이미 묶임, 2단계에서
  // 만들고 아무도 안 쓰던 그 라우트)를 쓴다 — wt_id → repoId 매핑을 직접
  // 계산할 필요가 없어진다.
  const [repos, setRepos] = createSignal<any[]>([]);
  // §1 — 탐색이 200개 상한에서 잘렸다. 조용히 자르면 "몇 개가 안 보인다"가
  // 정확히 이 기능을 만들게 한 그 버그로 읽힌다.
  const [truncated, setTruncated] = createSignal(false);
  const [hiddenCount, setHiddenCount] = createSignal(0);
  const [repoSheet, setRepoSheet] = createSignal(false);
  // 개입 필요 구역의 표시·접힘(device 스코프 설정의 거울).
  const { prefs, set: setPref } = createRailPrefs();
  const toggleAttentionCollapsed = () => setPref(ATTENTION_COLLAPSED_KEY, !prefs().attentionCollapsed);

  const railTitle = () => t('세션');
  const [diffTick, setDiffTick] = createSignal(0); // git 조회가 끝나면 다시 그리라는 신호
  // 2026-09-30 — 세션 cwd의 변경 알림. 오면 캐시가 dirty가 되고, 다시 셀 수 있는
  // 때(rail-fetch.ts의 간격 규칙)에 한 번 다시 그려 그 행이 fetchDiffCount를 부르게 한다.
  const diffWatcher = createDiffWatcher(() => setDiffTick((n) => n + 1));
  createEffect(() => diffWatcher.sync(tmuxSessions().filter((t: any) => !t.context).map((t: any) => t.cwd)));
  onCleanup(() => diffWatcher.dispose());
  const collapsed = () => props.collapsed();
  // 행 메뉴 — ⋯ 버튼과 우클릭이 같은 상태를 연다. 대상은 항상 세션 하나다.
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; row: SessionRow } | null>(null);
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [hostMenu, setHostMenu] = createSignal<{ x: number; y: number } | null>(null);
  // 2026-09-28(사용자 요청) — 스위처는 이제 목록 필터가 아니라 **화면 전체의
  // 전환**이다(core/host-route.js). 이 화면이 보고 있는 맥은 부팅 때 정해지고,
  // 레일의 세션 행은 그 맥의 API(중계)에서 오므로 로컬과 같은 경로로 그린다.
  const viewHostId = String((window as any).vtRelayHost?.() || LOCAL_HOST);
  const [catalog, setCatalog] = createSignal<CatalogSnapshot | null>(null);
  // 보관함을 열면 같은 자리의 목록이 보관된 세션으로 바뀐다(탭은 없다).
  const [archiveOpen, setArchiveOpen] = createSignal(false);
  // 갱신 지연 표식 — 폴링이든 push든 서버에서 새 값을 마지막으로 받은 시각.
  // 가장 짧은 폴링(카탈로그 5초)을 기준으로 두 주기(최소 90초, core/poll.js)를 넘기면 띄운다.
  const mountedAt = Date.now();
  let lastOkAt = 0;
  const [stale, setStale] = createSignal(false);
  const checkStale = () => setStale(isStale(lastOkAt, CATALOG_POLL_MS, Date.now(), mountedAt));
  const markOk = () => { lastOkAt = Date.now(); checkStale(); };
  const onSettled = (ok: boolean) => { if (ok) markOk(); else checkStale(); };
  const staleTimer = setInterval(checkStale, 15000);
  onCleanup(() => clearInterval(staleTimer));

  let catalogRequest = 0;
  const refreshCatalog = async (more = false) => {
    if (viewHostId !== LOCAL_HOST) return;
    const request = ++catalogRequest;
    try {
      const params = new URLSearchParams({ archived: String(archiveOpen()), limit: '200' });
      if (more && catalog()?.next) params.set('after', catalog()!.next!);
      const data = await historyApi('/api/session-catalog?' + params);
      if (request !== catalogRequest) return;
      setCatalog(more && catalog() ? { ...data, sessions: [...catalog()!.sessions, ...data.sessions] } : data);
    } catch (e) {
      // 옛 호스트(카탈로그 없음)는 지금의 라이브 레일을 그대로 쓴다. 답 자체가 없던
      // 실패(마감·네트워크)만 폴러에 알려 간격을 늘린다.
      if (isNoResponse(e)) throw e;
    }
  };
  // 카탈로그 폴링 — 이전 조회가 안 끝났으면 건너뛰고 실패하면 간격을 늘린다(core/poll.js).
  // 「더 보기」(more)는 사용자가 누른 일이라 폴러를 거치지 않는다.
  const catalogPoll = createPoll(() => refreshCatalog(), { interval: CATALOG_POLL_MS, immediate: true, onSettled });
  catalogPoll.start();
  const catalogChanged = () => { catalogPoll.kick(); };
  window.addEventListener('vt:catalog-changed', catalogChanged);
  onCleanup(() => { catalogPoll.stop(); window.removeEventListener('vt:catalog-changed', catalogChanged); });


  // compact(<720px)에서는 이 데스크톱 Rail이 CSS로 숨고 mobile-nav가 Fleet을
  // 마운트한다. 예전엔 숨은 Rail도 계속 살아 있어 Fleet과 똑같은 tmux·agent·git
  // 조회를 중복 실행했다. 마운트 자체는 반응형 전환을 위해 유지하되, 보이지 않는
  // 동안 네트워크/서브프로세스 폴링만 쉰다.
  const desktopMq = typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 720px)')
    : null;
  const railPollingActive = () => desktopMq ? desktopMq.matches : window.innerWidth >= 720;

  let tmuxRevision = 0;
  // 아래 refresh*는 폴러(core/poll.js)를 통해서만 불린다 — 실패를 던져 간격을 늘리게
  // 하고, 진행 중이면 겹쳐 쏘지 않는다. 즉시 다시 읽어야 할 때는 kick()을 부른다.
  const refreshSessions = async () => {
    if (!railPollingActive()) return;
    const request = ++tmuxRevision;
    const list = await pollFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (request === tmuxRevision && Array.isArray(list)) setTmuxSessions(list);
  };
  const refreshAgent = async () => {
    if (!railPollingActive()) return;
    if (!(await syncAgentsFromServer(props.deps))) throw new Error('agent: no response');
  };
  // ADR-29 B — /api/worktrees(평면) 대신 /api/repos(저장소별로 이미 묶임).
  const refreshRepos = async () => {
    if (!railPollingActive()) return;
    const data = await pollFetch<{ repos?: any[]; truncated?: boolean; hiddenCount?: number } | null>(
      props.deps, '/api/repos',
    );
    if (!data) return;
    setRepos(data.repos || []);
    setTruncated(!!data?.truncated);
    setHiddenCount(Number(data?.hiddenCount) || 0);
  };
  // C1 — 로컬+원격을 한 목록으로. 실패하면(라우터가 없는 옛 서버 등) 빈 배열이
  // 남아 스위처가 아예 안 그려진다 — 멀티호스트를 안 쓰는 사람에게는 그게 맞다.
  const refreshHosts = async () => {
    if (!railPollingActive()) return;
    try {
      const data = await pollFetch<{ hosts?: HostEntry[] } | null>(props.deps, '/api/hosts');
      if (data?.hosts) setHosts(data.hosts);
    } catch (e) {
      if (isNoResponse(e)) throw e;   // 옛 서버(404)는 실패가 아니다 — 간격을 늘리지 않는다
    }
  };

  const sessionsPoll = createPoll(refreshSessions, { interval: SESSIONS_SAFETY_POLL_MS, onSettled });
  const agentPoll = createPoll(refreshAgent, { interval: AGENT_SAFETY_POLL_MS, onSettled });
  const reposPoll = createPoll(refreshRepos, { interval: WORKTREES_POLL_MS, onSettled });
  const hostsPoll = createPoll(refreshHosts, { interval: HOSTS_POLL_MS, onSettled });
  const railPolls = [sessionsPoll, agentPoll, reposPoll, hostsPoll];
  for (const p of railPolls) p.start();
  const refreshAll = () => { for (const p of railPolls) p.kick(); };
  refreshAll();
  const onViewportChange = (e: MediaQueryListEvent) => {
    // compact에서 쉴 동안 놓친 상태를 데스크톱으로 돌아오는 즉시 한 번 맞춘다.
    if (e.matches) refreshAll();
  };
  desktopMq?.addEventListener('change', onViewportChange);
  // 서버가 워크트리 생성·삭제·열기 때 쏘는 push. 폴링(60초)보다 훨씬 빠르게
  // 반영되고, 평시에는 요청이 아예 나가지 않는다.
  const offWt = onWorkspaceEvent('worktrees_changed', () => { reposPoll.kick(); });
  const offSessions = onWorkspaceEvent('tmux_sessions', (msg: any) => {
    if (Array.isArray(msg?.sessions)) { tmuxRevision += 1; setTmuxSessions(msg.sessions); markOk(); }
  });
  // 2026-09-28 — 원격 세션 생성·종료·이름 변경 직후(term/remote.js). 15초 주기를
  // 기다리면 방금 만든 세션이 목록에 없어 "안 됐나?" 하고 또 누른다.
  const onHostsChanged = () => { hostsPoll.kick(); };
  window.addEventListener('vt:hosts-changed', onHostsChanged);
  onCleanup(() => window.removeEventListener('vt:hosts-changed', onHostsChanged));
  onCleanup(() => {
    for (const p of railPolls) p.stop();
    desktopMq?.removeEventListener('change', onViewportChange);
    offWt();
    offSessions();
  });

  // 키맵(worktreeNew, core/keymap.js) · 팔레트 등 이 파일을 정적 import 못 하는
  // 곳(위 파일 상단 주석과 같은 이유)이 다이얼로그를 열 수 있도록 하는 브리지.
  // ADR-29 C — 워크트리 만들기 다이얼로그가 레일 자신이 아니라 저장소 시트
  // (RepoVisibility.tsx) 안으로 옮겨갔다 — 이 브리지는 이제 그 시트를 연다.
  // 시트 안에서 저장소를 고르고 "+ 워크트리"를 눌러야 실제 다이얼로그가 뜬다.
  (window as any).vtOpenWorktreeDialog = () => setRepoSheet(true);
  onCleanup(() => { if ((window as any).vtOpenWorktreeDialog) delete (window as any).vtOpenWorktreeDialog; });

  // 세션 스토어(sessionsVersion)가 바뀔 때마다(탭 추가/삭제/전환) 실제 목록을
  // 다시 구성한다 — window.allSessions()가 진짜 웹 세션 맵의 단일 출처다.
  // C1 — 저장된 값이 아직 등록된 호스트를 가리키는지. 목록이 아직 안 왔을 때
  // (hosts()가 빈 배열) 로컬로 튕기지 않도록 목록이 비면 저장값을 그대로 쓴다.
  const activeHost = createMemo(() => hosts().find((h) => h.id === viewHostId) || null);
  // 전환해 둔 맥이 꺼져 있으면 세션 목록이 비는데, 그걸 "세션이 없다"로 보이면 안 된다.
  const viewHostOffline = createMemo(() => viewHostId !== LOCAL_HOST && !!activeHost() && !activeHost()!.online);

  // 행은 tmux 세션 목록이 출처다(열려 있지 않은 세션까지 나온다).
  // 이름을 편집 중에는 메모를 얼린다 — 폴링·push마다 새 객체 배열을 돌려주면 <For>가 행
  // DOM을 통째로 다시 만들어 편집 중이던 <input>이 뜯겨나가고 강제 blur로 입력값이 그대로
  // 커밋됐다(2026-09-21 사용자 보고). 끝나면 다음 갱신이 바로 따라잡는다.
  const [editingSessionKey, setEditingSessionKey] = createSignal<string | null>(null);
  let lastRows: SessionRow[] = [];
  const rows = createMemo<SessionRow[]>((prev) => {
    if (editingSessionKey()) return prev ?? lastRows;
    // 구독 트리거용 — 값 자체는 안 쓴다. diffTick: git 조회 완료·변경 알림.
    sessionsVersion(); agentVersion(); layoutVersion(); diffTick();
    const out = collectSessionRows({
      tmux: tmuxSessions(),
      repos: repos(),
      groups: (window as any).getSessionViewGroups?.() || (window as any).getTabGroups?.() || [],
      deps: props.deps,
      onDiff: () => setDiffTick((n) => n + 1),
    });
    lastRows = out;
    return out;
  });

  // 활동 막대 「세션」 배지 — 지금 입력을 기다리는 세션 수. 개입 필요 구역을 숨겨도 센다(그때가 더 필요하다).
  const waitingCount = createMemo(() => rows().filter((r) => r.status === 'waiting').length);
  createEffect(() => props.onWaitingCount?.(waitingCount()));
  onCleanup(() => props.onWaitingCount?.(0));
  const hideAttention = () => setPref(ATTENTION_SHOW_KEY, false);

  const lists = createMemo(() => buildRailLists(rows(), prefs()));
  // 「실행 중」 구역 = 세션 전체에서 개입 필요에 올라간 행을 뺀 것(같은 세션이 두 번 나오지 않는다).
  const running = createMemo(() => splitRunning(lists()));
  const viewGroups = createMemo(() => { layoutVersion(); return groupSessionRows(running(),
    (window as any).getSessionViewGroups?.() || (window as any).getTabGroups?.() || []); });
  // ── 계보 트리 · 정렬 ─────────────────────────────────────────────────────
  // 분할 화면 묶음(viewGroups의 id 있는 것)은 지금처럼 따로 그리고, 나머지 단독 행을 부모 → 자식
  // 트리로 묶는다(rail-data.ts buildSessionTree). 자식은 정렬과 상관없이 부모 밑에 붙는다.
  const groupedViews = createMemo(() => viewGroups().filter((g) => g.id));
  const tree = createMemo<TreeEntry<SessionRow>[]>(() =>
    buildSessionTree(viewGroups().filter((g) => !g.id).map((g) => g.rows[0]), prefs().sort, prefs().order));
  const rowKey = (r: SessionRow) => r.tmuxName || r.name;
  const topOrder = () => tree().map((e) => rowKey(e.row));
  const saveOrder = (names: string[]) => setPref(ORDER_KEY, encodeOrder('manual', names));
  const setSort = (s: RailSort) => {
    if (s === prefs().sort) return;
    // 수동으로 바꿀 때는 지금 보이는 순서를 출발점으로 삼는다(바꾸자마자 섞이지 않게). 이름순으로 돌아가도
    // 수동 순서는 지우지 않는다 — 다시 수동을 고르면 그대로 돌아온다.
    setPref(ORDER_KEY, encodeOrder(s, s === 'manual' ? topOrder() : prefs().order));
  };
  const flip = (list: string[], name: string) => (list.includes(name) ? list.filter((n) => n !== name) : [...list, name]);
  const toggleKids = (name: string) => setPref(LINEAGE_KEY, encodeLineagePrefs(flip(prefs().kidsCollapsed, name), prefs().closedOpen));
  const toggleClosed = (name: string) => setPref(LINEAGE_KEY, encodeLineagePrefs(prefs().kidsCollapsed, flip(prefs().closedOpen, name)));
  const kidsCollapsed = (r: SessionRow) => prefs().kidsCollapsed.includes(rowKey(r));
  const closedOpen = (r: SessionRow) => prefs().closedOpen.includes(rowKey(r));
  // 접힌 띠(48px)의 칸 순서 — 펼침이 쓰는 목록(개입 필요 · 묶음 · 트리)을 그대로 펴서 같은 순서가 나온다.
  const compact = createMemo(() => compactRailOrder(lists().attention, viewGroups(), tree(),
    { kidsCollapsed, closedOpen }));

  // 수동 정렬 드래그. 최상위 항목(부모는 자식과 한 덩어리)만 옮긴다. 터미널 칸 분할 드래그(text/vt-tab-id)와
  // 같은 드래그에 두 형식을 함께 싣는다 — 레일 안에 놓으면 순서, 칸에 놓으면 분할이다.
  const ORDER_MIME = 'text/vt-rail-order';
  const [dropAt, setDropAt] = createSignal<{ name: string; place: 'before' | 'after' } | null>(null);
  const isTop = (r: SessionRow) => tree().some((e) => e.row === r);
  const onEntryDragOver = (e: DragEvent, entry: TreeEntry<SessionRow>) => {
    if (prefs().sort !== 'manual' || !e.dataTransfer?.types.includes(ORDER_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setDropAt({ name: rowKey(entry.row), place: e.clientY < r.top + Math.min(r.height, 60) / 2 ? 'before' : 'after' });
  };
  const onEntryDrop = (e: DragEvent) => {
    const name = e.dataTransfer?.getData(ORDER_MIME);
    const at = dropAt();
    setDropAt(null);
    if (!name || !at) return;
    e.preventDefault();
    saveOrder(moveInOrder(topOrder(), name, at.name, at.place));
  };
  const totalRows = createMemo(() => lists().sessions.length);

  const activeId = createMemo(() => { sessionsVersion(); return (window as any).activeSessionId?.() ?? null; });

  // 2026-10-03(세션·뷰 규칙 "레일 클릭") — 열린 세션이면 그 뷰·칸으로 이동,
  // 열리지 않았으면 기억된 닫힌 묶음 뷰를 통째로 다시 열거나 새 단독 뷰로 연다.
  // 판정은 전부 term/session-actions.js `openSessionFromRail` 한 곳이다(모바일
  // 세션 시트·Fleet도 같은 함수). ⌘/Ctrl 클릭으로 현재 뷰를 분할하던 동작은
  // 없앴다 — 묶기는 드래그앤드롭만이다. 이동이 switchTo가 아니라
  // revealSession인 이유(짝을 밀어내지 않는다)는 2026-09-21 수정 그대로다.
  const openRow = async (_e: MouseEvent, row: SessionRow) => {
    const sid = actionSessionId(row);
    if (!sid && !row.tmuxName) return;
    await (window as any).vtOpenSessionFromRail?.({ sessionId: sid, tmuxName: row.tmuxName || null, catalogSessionId: row.catalogSessionId, runtimeId: row.runtimeId });
  };

  // ⋯ 버튼과 우클릭이 이 함수 하나를 부른다. 버튼은 좌표가 클릭 지점이라 메뉴가
  // 포인터 옆에 뜬다(우클릭과 같다).
  const contextRow = (e: MouseEvent, row: SessionRow) => {
    e.preventDefault();
    if (!row.sessionId && !row.tmuxName) return; // 웹 세션도 tmux 이름도 없으면 메뉴가 성립하지 않는다.
    // ⋯ 버튼에서 열었으면 버튼 아래에, 우클릭이면 포인터 옆에 띄운다.
    const btn = (e.target as HTMLElement | null)?.closest?.('.vt-srow-menu');
    const r = btn?.getBoundingClientRect();
    setCtxMenu({ x: r ? r.left : e.clientX, y: r ? r.bottom + 2 : e.clientY, row });
  };

  // 묶음은 화면(터미널 pane)에 직접 끌어다 놓아 만든다 — 세션 행은 pane 분할 드롭존
  // (layout/dnd.js)의 드래그 소스다. mime 이름은 그 파일의 `SESSION_MIME`과 같은
  // 문자열이어야 한다(이 파일은 레이아웃 모듈을 정적 import 못 한다). 열려 있지 않은
  // 세션은 웹 세션 id가 없어 끌 수 없다.
  const onRowDragStart = (e: DragEvent, row: SessionRow) => {
    const reorder = prefs().sort === 'manual' && isTop(row);
    const split = row.open && !!row.sessionId;
    if (!reorder && !split) { e.preventDefault(); return; }
    if (split) e.dataTransfer?.setData('text/vt-tab-id', row.sessionId);
    if (reorder) e.dataTransfer?.setData(ORDER_MIME, rowKey(row));
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  };

  // 세션 이름 변경, 제자리 편집(window.prompt 안 씀). 웹 세션이 있어야 한다(term/session.js의
  // renameSession). editingSessionKey는 rows 메모 앞에서 선언했다.
  const [editingSessionValue, setEditingSessionValue] = createSignal('');
  const startRenameSession = (sessionId: string, currentName: string) => {
    setEditingSessionValue(currentName);
    setEditingSessionKey(sessionId);
  };
  const commitRenameSession = async (sessionId: string) => {
    if (editingSessionKey() !== sessionId) return;
    setEditingSessionKey(null);
    const trimmed = editingSessionValue().trim();
    if (!trimmed) return;
    await (window as any).renameSession?.(sessionId, trimmed);
    // 카탈로그 제목도 같이 바꾼다 — 이름 변경은 어느 행에서든 같은 결과여야 한다.
    const row = rows().find((r) => r.sessionId === sessionId);
    const linked = row && catalogOf(row);
    if (linked && linked.title !== trimmed) {
      try { await historyApi(`/api/session-catalog/sessions/${linked.id}`, { body: { title: trimmed, revision: linked.revision } }); catalogPoll.kick(); } catch (_) { /* 웹 세션 이름은 이미 바뀌었다 */ }
    }
  };

  // 실행 중 행 ↔ 카탈로그 세션. 행 객체는 그대로 두고(폴링마다 새로 만들면 DOM이 뜯긴다) 찾아서 쓴다.
  const catalogOf = (row: SessionRow): any | undefined =>
    catalog()?.sessions.find((s) => s.ended == null && s.runtime_id && s.tmux_name === row.tmuxName && s.physical_key === row.physicalKey);
  // 실행 중 행에서 열면 tmux 이름도 넘긴다 — 작업 기록 창 왼쪽에 내부 서브에이전트, 오른쪽에 가장 최근 것을 고른다(시안 O15).
  const openRecordOf = async (session: any, tmuxName?: string | null) => {
    const { openSessionRecord } = await import('../history/session-record.js');
    openSessionRecord(session.id, () => { catalogPoll.kick(); }, { tmuxName: tmuxName || session.tmux_name || '' });
  };
  const recentCollapsedToggle = () => setPref(VIEW_KEY, encodeRailView({ ...prefs().view, recentCollapsed: !prefs().view.recentCollapsed }));
  // 「실행 중」 구역과 묶음 접힘 — 같은 rail.view 한 덩어리(기기별). 접힌 묶음도 머리(제목 · 개수)는 남는다.
  const toggleRunningCollapsed = () => setPref(VIEW_KEY, encodeRailView({ ...prefs().view, runningCollapsed: !prefs().view.runningCollapsed }));
  const groupCollapsed = (id: string) => !!id && prefs().view.groupsCollapsed.includes(id);
  const toggleGroup = (id: string) => setPref(VIEW_KEY, encodeRailView({ ...prefs().view, groupsCollapsed: toggleGroupCollapsed(prefs().view.groupsCollapsed, id) }));

  // 폭 720px 이상 + 터치일 때만 「오른쪽에 나란히/아래에 열기」가 들어간다(term/session-actions.js).
  const splitAvailable = (row: SessionRow): boolean => !!(window as any).vtTouchSplitAvailable?.(row.sessionId || null);
  // 메뉴 항목 목록은 rail-data.ts의 rowMenuItems 하나다(폰 Fleet의 롱프레스 시트도 같다).
  const ctxMenuItems = (): MenuItem[] => {
    const m = ctxMenu();
    if (!m) return [];
    const row = m.row;
    const linked = catalogOf(row);
    return rowMenuItems(row, { split: splitAvailable(row), share: viewHostId === LOCAL_HOST, record: !!linked }).map((it) => ({
      label: it.label, detail: it.detail, danger: it.danger,
      run: () => {
        if (it.id === 'record' && linked) { void openRecordOf(linked, row.tmuxName); return; }
        // 열려 있지 않은 행은 웹 세션이 없어 제자리 편집이 안 된다 — 카탈로그 제목만 바꾼다.
        if (it.id === 'rename' && linked && !row.sessionId) { void renameCatalogSession(linked).then(() => catalogPoll.kick()); return; }
        runRowMenu(it.id, row, window, () => startRenameSession(row.sessionId, row.name));
      },
    }));
  };

  // "⋯ 더보기" — §5가 기존 48px 아이콘 레일을 완전히 대체하기로 하면서 갈 곳을
  // 잃은 것들의 임시 정류장(§6 dock·§60 설정 화면이 생기기 전까지). 대부분은
  // getAction으로 바로 되지만, 마이크·테마는 아직 rail.js의 #vt-rail-panel
  // 플라이아웃 안에만 산다(desktop mic의 유일한 자리) — 그 DOM은 지우지 않고
  // CSS로만 숨겨 뒀으므로, 숨은 버튼을 그대로 다시 눌러 같은 경로를 그대로 쓴다.
  // C1 — 호스트 스위처.
  const openHostMenu = (e: MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    hostsPoll.kick(); // 열 때 한 번 더 — 목록이 15초 지나 있을 수 있다.
    setHostMenu({ x: r.left, y: r.bottom + 4 });
  };
  // 2026-09-28(사용자 요청) — 고르면 화면 **전체**가 그 맥으로 간다: 세션·탭·
  // 포트·파일·첨부·설정. 같은 주소에서 새로고침한다(core/host-route.js 머리말).
  const selectHost = (id: string) => {
    if (id === viewHostId) return;
    (window as any).vtSwitchHost?.(id);
  };
  const hostMenuItems = (): MenuItem[] =>
    buildHostMenu(hosts(), viewHostId).map((h) => ({
      label: h.label,
      detail: h.detail,
      dim: !h.online,
      checked: h.active,
      run: () => selectHost(h.id),
    }));

  const toggleCollapse = () => props.onToggleCollapse();
  // 「새 세션」 — 머리 + 버튼과 바닥 버튼이 같은 액션(session.add-menu)을 부른다. 원격 맥을 보고 있으면
  // 그 맥에 만든다(모든 API가 그 맥으로 중계된다, core/host-route.js). 꺼진 맥에서만 막는다.
  const newSession = (e: MouseEvent) =>
    (props.deps.getAction('session.add-menu') as ((el?: HTMLElement, e?: MouseEvent) => void) | undefined)?.(e.currentTarget as HTMLElement, e);

  // 같은 행이 두 구역(개입 필요·세션)에 나올 수 있다 — 같은 세션이라 편집·활성 상태도 같이 따라간다.
  const renderRow = (row: SessionRow, lin?: { kid?: boolean; entry?: TreeEntry<SessionRow> }) => (
    <>
    <Row
      row={row}
      compact={collapsed()}
      kid={!!lin?.kid}
      progress={lin?.entry ? kidsProgress(lin.entry.kids) : null}
      kidsCount={lin?.entry ? lin.entry.kids.length + lin.entry.closedKids.length : 0}
      kidsCollapsed={lin?.entry ? kidsCollapsed(row) : false}
      onToggleKids={lin?.entry ? () => toggleKids(rowKey(row)) : undefined}
      active={!!row.sessionId && row.sessionId === activeId()}
      draggable={(row.open && !!row.sessionId) || (prefs().sort === 'manual' && !lin?.kid && !!lin?.entry)}
      onDragStart={(e) => onRowDragStart(e, row)}
      editing={!!row.sessionId && editingSessionKey() === row.sessionId}
      editingValue={editingSessionValue()}
      onEditInput={setEditingSessionValue}
      onEditCommit={() => commitRenameSession(row.sessionId)}
      onEditCancel={() => setEditingSessionKey(null)}
      onOpen={(e) => openRow(e, row)}
      onContext={(e) => contextRow(e, row)}
      onAnswer={(key) => answerSessionPrompt(props.deps, row, key)}
    />
    <Show when={!collapsed()}><NativeChildren row={row} /></Show>
    </>
  );

  return (
    <aside id="vt-wgrail" classList={{ collapsed: collapsed() }} aria-label={t('세션')}>
      {/* 머리 36: 「세션 N」(호스트가 둘 이상이면 호스트 전환) · 대화 기록 · 레일 설정 · 새 세션 · 접기.
          접힌 48px 띠는 여닫기 버튼만 둔다. */}
      <div class="vt-wgrail-head">
        <Show when={!collapsed()}>
          {/* C1 — 호스트가 로컬 하나뿐이면 칩을 아예 안 그린다. 멀티호스트를
              안 쓰는 사람에게는 "고를 게 없는 드롭다운"이 잡음일 뿐이다. */}
          <Show
            when={hosts().length > 1}
            fallback={<span class="vt-wgrail-title">{railTitle()}<span class="vt-wgrail-title-count">{totalRows()}</span></span>}
          >
            <button
              type="button"
              class="vt-host-btn vt-wgrail-host"
              classList={{ offline: activeHost() ? !activeHost()!.online : false, remote: viewHostId !== LOCAL_HOST }}
              onClick={openHostMenu}
              data-tip={activeHost()?.label || t('호스트 선택')}
              data-tip-sub={activeHost() ? hostDetail(activeHost()!) : undefined}
              data-tip-side="bottom"
            >
              <span class="vt-wgrail-host-name">{activeHost()?.label || viewHostId}</span>
              <span class="vt-wgrail-host-caret"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
            </button>
          </Show>
        </Show>
        <div class="vt-wgrail-head-actions">
          {/* 갱신 지연 — 폴링·push 어느 쪽으로도 한동안 새 값을 못 받았을 때만. */}
          <Show when={!collapsed() && stale()}>
            <span class="vt-stale-mark" role="status" title={t('서버에서 새 값을 한동안 받지 못했습니다. 보이는 값은 마지막으로 받은 것입니다.')}>{t('갱신 지연')}</span>
          </Show>
          <Show when={!collapsed()}>
            <button type="button" class="vt-icon-btn sm" aria-label={t('대화 기록')} data-tip={t('대화 기록')} data-tip-sub={t('보관된 대화 검색 · 복구')}
              data-tip-side="bottom"
              onClick={() => { void import('../history/panel.js').then(m => m.openHistory()); }} innerHTML={shellIcon('history', 16, 2)} />
            {/* 레일 설정 — 개입 필요 표시, 만든 곳별 표시, 저장소 표시. 머리 줄은 구역 유무와 무관하게 항상 있어 설정에 늘 닿는다. */}
            <button
              type="button"
              class="vt-icon-btn sm vt-wgrail-settings"
              aria-label={t('세션 목록 설정')}
              data-tip={t('세션 목록 설정')}
              data-tip-sub={hiddenCount() ? t('저장소 {n}개 숨김', { n: hiddenCount() }) : t('구역 · 종료 세션 · 저장소')}
              data-tip-side="bottom"
              onClick={() => setRepoSheet(true)}
              innerHTML={icon('sliders-horizontal', 16, 2)}
            />
            <button
              type="button"
              class="vt-icon-btn sm vt-wgrail-head-new"
              aria-label={t('새 세션')}
              data-tip={t('새 세션')}
              data-tip-sub={viewHostOffline() ? t('호스트에 연결할 수 없습니다') : undefined}
              data-tip-side="bottom"
              disabled={viewHostOffline()}
              onClick={(e) => newSession(e)}
              innerHTML={icon('plus', 16, 2)}
            />
          </Show>
          {/* chevron 하나를 CSS로 돌린다(펼침 = ‹, 접힘 = ›). */}
          <button
            type="button"
            class="vt-icon-btn sm vt-wgrail-collapse"
            classList={{ collapsed: collapsed() }}
            onClick={toggleCollapse}
            aria-label={collapsed() ? t('펼치기') : t('접기')}
            data-tip={collapsed() ? t('세션 목록 펼치기') : t('세션 목록 접기')}
            data-tip-side="bottom"
            innerHTML={icon('chevron-down', 16, 2)}
          />
        </div>
      </div>
      <div class="vt-wgrail-body">
        <Show when={rows().length === 0 && !catalog()?.sessions.length && !collapsed() && !viewHostOffline()}>
          <div class="vt-wgrail-empty">
            {t('아직 세션이 없습니다.')}
            <button
              type="button"
              class="vt-btn sm vt-wgrail-empty-new"
              onClick={(e) => (props.deps.getAction('session.add-menu') as ((el?: HTMLElement, e?: MouseEvent) => void) | undefined)?.(e.currentTarget, e)}
            >+ {t('새 세션')}</button>
          </div>
        </Show>
        {/* 전환해 둔 맥이 꺼져 있으면 "빈 목록"과 "연결 안 됨"을 구분해서 보여준다. */}
        <Show when={viewHostOffline() && !collapsed()}>
          <div class="vt-wgrail-empty">{hostDetail(activeHost()!)}</div>
        </Show>
        <Show when={!viewHostOffline()}>
          {/* 접힌 레일(48px)은 구역 머리글이 없어 한 목록이다 — 개입 필요가 위, 나머지가 아래,
              같은 순서 소스를 펴서 각 세션은 한 번만(compactRailOrder). 펼친 레일은 waiting이 두 구역에 모두 나온다. */}
          <Show when={collapsed()}>
            <For each={compact().entries}>
              {(entry, i) => (
                <>
                  <Show when={compact().split > 0 && i() === compact().split}>
                    <div class="vt-wgrail-group-sep" role="separator" />
                  </Show>
                  <Show when={entry.kid} fallback={renderRow(entry.row)}>
                    {/* 하위 세션: 부모 바로 아래 축소한 칸 + 왼쪽 가지선(펼침의 .vt-lin-kid와 같은 last·merged·failed). */}
                    <div class="vt-wgrail-kidcell" classList={{ last: entry.kid!.last, merged: entry.kid!.merged, failed: entry.kid!.failed, closed: entry.kid!.closed }}>
                      {renderRow(entry.row, { kid: true })}
                    </div>
                  </Show>
                </>
              )}
            </For>
          </Show>
          {/* 개입 필요 — waiting 세션만. 접을 수 있고(제목 줄 전체가 토글), 레일 설정에서 끈다. */}
          <Show when={!collapsed() && !archiveOpen() && lists().attention.length > 0}>
            <div class="vt-wgrail-group-head is-wait">
              <button
                type="button"
                class="vt-wgrail-group-toggle"
                classList={{ collapsed: prefs().attentionCollapsed }}
                aria-expanded={!prefs().attentionCollapsed}
                onClick={toggleAttentionCollapsed}
              >
                <span class="vt-wgrail-group-caret" innerHTML={icon('chevron-down', 12, 2)} />
                <span class="vt-wgrail-group-label">{t('개입 필요')}</span>
                <span class="vt-wgrail-group-count">{lists().attention.length}</span>
              </button>
              {/* 눈 = 이 구역 숨기기(레일 설정의 「개입 필요」 끄기와 같은 값). waiting 행은 「실행 중」으로 내려간다. */}
              <button
                type="button"
                class="vt-icon-btn sm vt-wgrail-group-act"
                aria-label={t('이 구역 숨기기')}
                data-tip={t('이 구역 숨기기')}
                data-tip-sub={t('세션 목록 설정에서 다시 켭니다')}
                data-tip-side="bottom"
                onClick={hideAttention}
                innerHTML={shellIcon('eye', 14, 2)}
              />
            </div>
            <Show when={!prefs().attentionCollapsed || collapsed()}>
              <For each={lists().attention}>{(row) => renderRow(row)}</For>
            </Show>
          </Show>
          {/* 보관함 — 탭이 아니라 같은 자리의 목록 전환이다. 위의 「최근 종료」 아래 한 줄이 여기로 온다. */}
          <Show when={!collapsed() && archiveOpen() && catalog()}>
            {data => <ArchiveList data={data()} onBack={() => { setArchiveOpen(false); catalogPoll.kick(); }}
              onChanged={() => { catalogPoll.kick(); }} onMore={() => { void refreshCatalog(true).catch(() => {}); }} />}
          </Show>
          {/* 「저장소별로 묶기」(레일 설정) — 저장소 → 작업 공간 보기. 정렬·위임 트리는 이 보기에서 쓰지 않는다. */}
          <Show when={!collapsed() && !archiveOpen() && prefs().view.byRepo && catalog()}>
            {data => <CatalogTree data={data()} live={lists().sessions} renderLive={renderRow} onChanged={() => { catalogPoll.kick(); }} onMore={() => { void refreshCatalog(true).catch(() => {}); }} />}
          </Show>
          <Show when={!collapsed() && !archiveOpen() && !(prefs().view.byRepo && catalog()) && running().length > 0}>
            <Show when={!collapsed()}>
              <div class="vt-wgrail-group-head">
                {/* 제목 줄(캐럿 · 실행 중 · 개수)이 접기 토글이다 — 「개입 필요」와 같은 모양. 접힘은 기기별(rail.view). */}
                <button
                  type="button"
                  class="vt-wgrail-group-toggle"
                  classList={{ collapsed: prefs().view.runningCollapsed }}
                  aria-expanded={!prefs().view.runningCollapsed}
                  onClick={toggleRunningCollapsed}
                >
                  <span class="vt-wgrail-group-caret" innerHTML={icon('chevron-down', 12, 2)} />
                  <span class="vt-wgrail-group-label">{t('실행 중')}</span>
                  <span class="vt-wgrail-group-count">{running().length}</span>
                </button>
                <div class="vt-rail-sort" role="radiogroup" aria-label={t('세션 정렬')}>
                  <For each={[['name', t('이름순')], ['manual', t('수동')]] as [RailSort, string][]}>{([id, label]) => (
                    <button
                      type="button"
                      role="radio"
                      class="vt-rail-sort-opt"
                      aria-checked={prefs().sort === id}
                      data-tip={id === 'manual' ? t('끌어서 순서를 바꿉니다.') : undefined}
                      data-tip-side="bottom"
                      onClick={() => setSort(id)}
                    >{label}</button>
                  )}</For>
                </div>
              </div>
            </Show>
            <Show when={!prefs().view.runningCollapsed}>
            <For each={groupedViews()}>{(group) => (
              <ViewGroup group={group} onOpen={() => { void openRow({} as MouseEvent, group.rows[0]); }}
                collapsed={groupCollapsed(group.id)} onToggle={() => toggleGroup(group.id)}>
                <For each={group.rows}>{(row) => renderRow(row)}</For>
              </ViewGroup>
            )}</For>
            <For each={tree()}>{(entry) => (
              <div
                class="vt-lin"
                classList={{
                  'has-kids': entry.kids.length + entry.closedKids.length > 0 && !kidsCollapsed(entry.row),
                  'drop-before': dropAt()?.name === rowKey(entry.row) && dropAt()?.place === 'before',
                  'drop-after': dropAt()?.name === rowKey(entry.row) && dropAt()?.place === 'after',
                }}
                data-lane={entry.row.status}
                onDragOver={(e) => onEntryDragOver(e, entry)}
                onDragLeave={(e) => { if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) setDropAt(null); }}
                onDrop={onEntryDrop}
                onDragEnd={() => setDropAt(null)}
              >
                {renderRow(entry.row, { entry })}
                <Show when={(entry.kids.length + entry.closedKids.length) > 0 && !kidsCollapsed(entry.row)}>
                  <div class="vt-lin-kids" role="group" aria-label={t('{name}의 하위 세션', { name: entry.row.name })}>
                    <For each={entry.kids}>{(kid, i) => (
                      <div
                        class="vt-lin-kid"
                        classList={{
                          last: i() === entry.kids.length - 1 && entry.closedKids.length === 0,
                          merged: kid.lineage?.state === 'reported',
                          failed: kid.lineage?.outcome === 'failed',
                        }}
                      >{renderRow(kid, { kid: true })}</div>
                    )}</For>
                    <Show when={entry.closedKids.length > 0}>
                      <div class="vt-lin-kid vt-lin-fold" classList={{ last: !closedOpen(entry.row) }}>
                        <button
                          type="button"
                          class="vt-lin-fold-btn"
                          aria-expanded={closedOpen(entry.row)}
                          onClick={() => toggleClosed(rowKey(entry.row))}
                        >
                          <span class="vt-lin-fold-caret" innerHTML={icon('chevron-down', 12, 2)} />
                          {t('닫힌 하위 {n}', { n: entry.closedKids.length })}
                        </button>
                      </div>
                      <Show when={closedOpen(entry.row)}>
                        <For each={entry.closedKids}>{(kid, i) => (
                          <div class="vt-lin-kid closed" classList={{ last: i() === entry.closedKids.length - 1 }}>
                            {renderRow(kid, { kid: true })}
                          </div>
                        )}</For>
                      </Show>
                    </Show>
                  </div>
                </Show>
              </div>
            )}</For>
            </Show>
          </Show>
          {/* 최근 종료 → 보관함 한 줄. 카탈로그가 없는 옛 호스트는 위의 실행 중 목록만 남는다. */}
          <Show when={!collapsed() && !archiveOpen() && catalog()}>
            {data => <>
              <Show when={!prefs().view.byRepo}>
                <RecentEnded data={data()} live={lists().sessions} count={prefs().view.recent} collapsed={prefs().view.recentCollapsed}
                  onToggle={recentCollapsedToggle} onChanged={() => { catalogPoll.kick(); }} />
              </Show>
              <ArchiveEntry count={data().archived_count ?? 0} onOpen={() => { setArchiveOpen(true); catalogPoll.kick(); }} />
            </>}
          </Show>
          <Show when={rows().length > lists().sessions.length && !collapsed()}>
            <div class="vt-wgrail-note">{t('세션 목록 설정에서 {n}개를 숨겼습니다.', { n: rows().length - lists().sessions.length })}</div>
          </Show>
        </Show>
        {/* §1 — 목록이 200개 상한에서 잘렸다. 여기 한 줄이 없으면 "몇 개가
            빠졌다"가 이번에 고친 깊이 버그와 똑같은 증상으로 보인다. */}
        <Show when={truncated() && !collapsed()}>
          <div class="vt-wgrail-note">{t('저장소가 200개를 넘어 일부만 표시합니다.')}</div>
        </Show>
      </div>
      {/* 2026-09-18(2차) — 설정 ⚙과 마이크는 여기 살다가 사용자 요청으로 반대편
          dock(Dock.tsx의 .vt-dock-tabs 아래)으로 옮겼다. 이 레일의 발자국을
          "+ 새 세션" 하나로 줄인다.
          ADR-29 C — 이 버튼의 동작이 "워크트리 만들기"에서 "새 세션"으로
          바뀌었다: 행이 다시 세션인데(B) 발자국은 여전히 워크트리를 만들고
          있으면 어긋난다. 워크트리 만들기는 저장소 시트(⚙, RepoVisibility.tsx)
          안으로 옮겨서, 어느 저장소에 만들지 먼저 고르게 한다 — 여기서는
          더 이상 "기본 저장소"를 추측하지 않는다. */}
      <div class="vt-wgrail-footer">
        {/* 2026-09-18 후속(사용자 요청) — "새 세션" 진입점을 상단 탭 바의
            #add-btn에서 이 버튼 하나로 합쳤다(#add-btn은 index.html에서
            지웠다). session.add-menu는 원격을 보고 있을 때 누르면 "맥에"
            세션이 생겨 화면과 결과가 어긋나므로 워크트리 만들기와 같은
            이유로 막는다. */}
        {/* 2026-09-28 — 다른 맥으로 전환해 있으면 이 버튼도 그 맥에 만든다(모든 API가
            그 맥으로 중계된다, core/host-route.js). 꺼진 맥에서만 막는다. */}
        <button
          type="button"
          class="vt-wgrail-new"
          disabled={viewHostOffline()}
          aria-label={t('새 세션')}
          data-tip={collapsed() ? t('새 세션') : undefined}
          data-tip-sub={viewHostOffline() ? t('호스트에 연결할 수 없습니다') : undefined}
          data-tip-side="right"
          onClick={(e) => newSession(e)}
        >
          <span class="vt-wgrail-new-mark" innerHTML={icon('plus', collapsed() ? 16 : 14, 2)} />
          <Show when={!collapsed()}><span class="vt-wgrail-new-label">{t('새 세션')}</span></Show>
        </button>
      </div>
      {/* 2026-09-21 — RepoVisibility와 같은 이유로 Portal로 body에 붙인다.
          `.vt-sidepanel`이 z-index:70짜리 쌓임 맥락을 만들어서, `.vt-menu`의
          z-index:520이 레일 밖의 더 높은 오버레이(예: 온보딩 z:500 이상의
          다른 화면)에 가려질 수 있었다 — 지금까지는 우연히 충돌 경로가 없어
          증상이 안 드러났을 뿐이다. x/y는 이미 뷰포트 좌표(클릭 좌표 또는
          버튼 bounding rect)라 Portal로 옮겨도 그대로 맞는다. */}
      <Show when={ctxMenu()}>
        {(m) => (
          <Portal>
            <Menu x={m().x} y={m().y} onClose={() => setCtxMenu(null)} items={ctxMenuItems()} summary={rowSummaryDetails(m().row)} />
          </Portal>
        )}
      </Show>
      <Show when={hostMenu()}>
        {(m) => (
          <Portal>
            <Menu x={m().x} y={m().y} onClose={() => setHostMenu(null)} items={hostMenuItems()} />
          </Portal>
        )}
      </Show>
      {/* 2026-09-20 — **Portal로 body에 붙인다.** 이 시트는 레일 JSX 안에
          있었는데, `.vt-sidepanel`이 `position:fixed; z-index:70`이라 **쌓임
          맥락**을 만든다. 그 안에서는 시트 백드롭의 `z-index:600`이 레일 밖으로
          나가지 못해, 루트 맥락에 있는 `.vt-onboarding`(z:500)이 시트를 통째로
          덮었다 — 세션이 0개일 때(부팅 직후·마지막 탭을 닫은 직후) 시트가 화면에
          보이기는 하는데 클릭이 온보딩으로 가버린다. 실브라우저로 확인한 모습:
          시트 카드 중앙의 최상위 요소가 `DIV#onboarding.vt-onboarding`이었다.
          z-index 숫자를 올려도 소용없다 — 맥락 안의 값이라 바깥과 비교되지
          않는다. 맥락 밖으로 꺼내는 것이 유일한 해법이다. */}
      <Show when={repoSheet()}>
        <Portal>
          <RepoVisibility
            deps={props.deps}
            title={t('세션 목록 설정')}
            lead={<RailSettingsLead />}
            onClose={() => setRepoSheet(false)}
            onChanged={() => reposPoll.kick()}
          />
        </Portal>
      </Show>
    </aside>
  );
}
