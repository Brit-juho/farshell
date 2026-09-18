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
import { createSignal, createMemo, createEffect, onCleanup, onMount, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import {
  buildRailSections, mostUrgentStatus, GROUP_LABEL, hashRepoColorIndex,
  COLLAPSIBLE_GROUPS, groupCollapseKey, GROUP_COLLAPSED_DEFAULT, defaultRailCollapsed,
  type RailGroup,
  type WorktreeRailRowInput, type OtherRailRowInput, type DesktopRailRowInput,
} from './rail-data.js';
import {
  buildHostMenu, remoteSessionRows, resolveActiveHost, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';
import {
  actionSessionId, fetchAgentDetails, fetchDiffCount, openWorktree, safeFetch,
  useAgentVersion, useSessionsVersion, fetchAgentNames,
  type DesktopRailRow, type RailDeps, type AgentDetail,
  cachedDiffCount,
  diffCountStale,
} from './rail-fetch.js';
import { REGULAR_MAX } from '../layout/breakpoints.js';
import { Menu, Row, type MenuItem } from './RailRow.js';
import { icon } from '../ui/icons.js';
// 마이크 노드를 이 레일 바닥으로 옮긴다. 자리를 정하는 곳이 keybar.js 하나뿐이어야
// 데스크톱/터치가 갈리는 판정이 두 군데로 흩어지지 않는다(L7).
import { placeMicButton } from '../term/keybar.js';

export type { RailDeps } from './rail-fetch.js';
import { wireRatioResizer } from '../layout/resizer.js';
import { WorktreeDialog } from './WorktreeDialog.js';
import { RepoVisibility } from './RepoVisibility.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';

const SESSIONS_POLL_MS = 5000;   // tmux 목록(attached·cwd) — 자주 안 바뀌어도 짧게, 값싸다.
const STATUS_POLL_MS = 4000;     // since/tool 보강 — 서버가 아직 질문 텍스트를 안 줘서(2.1.0 gap) 상태 문장 갱신용.
// N8(30-worktree.md). **정본은 `/ws-workspace`의 `worktrees_changed` push다** —
// 이 폴링은 WS가 끊긴 동안과, 터미널에서 직접 `git worktree add`를 해서 서버가
// 변경을 모르는 경우를 위한 안전망이다. 8초였을 땐 서버가 매번 저장소마다 git
// 서브프로세스를 도는 1~2초 작업을 다시 했다(캐시 TTL 5초 < 폴링 8초라 적중률 0).
const WORKTREES_POLL_MS = 60000;
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

function Rail(props: { deps: RailDeps }) {
  const sessionsVersion = useSessionsVersion();
  const agentVersion = useAgentVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  const [agentDetails, setAgentDetails] = createSignal<Record<string, AgentDetail>>({});
  // 정체(어떤 CLI인가)는 상태(무엇을 하는 중인가)와 **다른 엔드포인트**다.
  // 한 응답에 둘 다 있을 거라 짐작했다가 마크가 한 개도 안 그려졌다.
  const [agentNames, setAgentNames] = createSignal<Record<string, string>>({});
  const [worktrees, setWorktrees] = createSignal<any[]>([]);
  // §1 — 탐색이 200개 상한에서 잘렸다. 조용히 자르면 "몇 개가 안 보인다"가
  // 정확히 이 기능을 만들게 한 그 버그로 읽힌다.
  const [truncated, setTruncated] = createSignal(false);
  const [hiddenCount, setHiddenCount] = createSignal(0);
  const [repoSheet, setRepoSheet] = createSignal(false);
  // §2 — 그룹 접힘. device 스코프 설정이 정본이고(폰에서 접은 게 맥에 새면
  // 안 된다) 여기 시그널은 그 값을 화면에 반영하기 위한 거울이다.
  //
  // 값이 **없을 때**와 **false일 때**를 구분한다. 예전엔 `Boolean(get(...))`
  // 하나였는데, 이 지연 청크가 `window.vtSettingsGet`보다 먼저 마운트되는
  // 순간이 있어서 `undefined → false`가 되어 "기본은 접힘"이 조용히 뒤집혔다
  // (실서버에서 새로고침마다 결과가 달라지는 것으로 재현).
  const readGroupCollapsed = (g: RailGroup): boolean => {
    const v = (window as any).vtSettingsGet?.(groupCollapseKey(g));
    return v === undefined || v === null ? GROUP_COLLAPSED_DEFAULT[g] : Boolean(v);
  };
  const [groupCollapsed, setGroupCollapsed] = createSignal<Record<string, boolean>>(
    Object.fromEntries(COLLAPSIBLE_GROUPS.map((g) => [g, readGroupCollapsed(g)])),
  );
  // 설정이 나중에 도착하면(load 완료 시 전체 키로 한 번 통지된다) 그때 맞춘다.
  // 페이로드에 있는 키만 반영한다 — 다른 설정 하나를 바꾼 통지가 그룹 상태까지
  // 되돌리면 안 된다.
  const unsubGroupSettings = (window as any).vtSettingsSubscribe?.((changed: any) => {
    if (!changed) return;
    setGroupCollapsed((prev) => {
      let next = prev;
      for (const g of COLLAPSIBLE_GROUPS) {
        const key = groupCollapseKey(g);
        if (!(key in changed)) continue;
        const v = Boolean(changed[key]);
        if (v === prev[g]) continue;
        if (next === prev) next = { ...prev };
        next[g] = v;
      }
      return next;
    });
  });
  onCleanup(() => unsubGroupSettings?.());
  const toggleGroup = (g: RailGroup) => {
    const next = !groupCollapsed()[g];
    setGroupCollapsed((prev) => ({ ...prev, [g]: next }));
    (window as any).vtSettingsSet?.(groupCollapseKey(g), next);
  };
  const isGroupCollapsed = (g: RailGroup) =>
    COLLAPSIBLE_GROUPS.includes(g) && !!groupCollapsed()[g];

  // 헤더가 세는 것을 사실대로 말한다. `worktrees()`에는 각 저장소의 **본체
  // 체크아웃**(isMain)이 함께 들어 있다 — git 용어로는 그것도 worktree가
  // 맞지만, 개발자가 "워크트리 13개"를 읽으면 `git worktree add`로 만든 것이
  // 13개라고 이해한다. 실제로 본체만 13개이고 부가 워크트리는 0개인 화면이
  // "워크트리 · 13"이라고 말하고 있었다.
  // 그래서 보이는 그대로 「저장소 N」을 기본으로 하고, 부가 워크트리가 있을
  // 때만 그 수를 덧붙인다. 행이 워크트리 단위라는 구조(ADR-20)는 그대로다 —
  // 바꾸는 것은 요약 문구뿐이다.
  const railTitle = () => {
    const all = worktrees();
    const extra = all.filter((w) => !w.isMain).length;
    const repos = new Set(all.map((w) => w.repoName)).size;
    if (!all.length) return '워크트리';
    return extra ? `저장소 ${repos} · 워크트리 ${extra}` : `저장소 ${repos}`;
  };
  const [diffTick, setDiffTick] = createSignal(0); // git 조회가 끝나면 다시 그리라는 신호
  // 접힘 기본값은 티어를 따른다(§3 표) — wide(≥1280) 미만에서는 접힘으로 시작.
  // 저장된 값이 있으면 그게 이긴다(사용자가 직접 정한 것이므로). Dock.tsx가
  // 같은 판단을 이미 하고 있었는데 레일에만 없었다: 그래서 좁은 화면에서
  // 레일 252 + dock 36이 자리를 먼저 먹고 HUD가 칩을 다 못 싣는 상태로 시작했다.
  const [collapsed, setCollapsed] = createSignal(
    (window as any).vtSettingsHas?.(SETTINGS_COLLAPSE_KEY)
      ? Boolean((window as any).vtSettingsGet?.(SETTINGS_COLLAPSE_KEY))
      : defaultRailCollapsed(window.innerWidth, REGULAR_MAX),
  );
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; sessionId: string } | null>(null);
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
  const refreshAgent = async () => {
    setAgentDetails(await fetchAgentDetails(props.deps));
    setAgentNames(await fetchAgentNames(props.deps));
  };
  const refreshWorktrees = async () => {
    const data = await safeFetch<{ worktrees?: any[]; truncated?: boolean; hiddenCount?: number }>(
      props.deps, '/api/worktrees',
    );
    setWorktrees(data?.worktrees || []);
    setTruncated(!!data?.truncated);
    setHiddenCount(Number(data?.hiddenCount) || 0);
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
  // 서버가 워크트리 생성·삭제·열기 때 쏘는 push. 폴링(60초)보다 훨씬 빠르게
  // 반영되고, 평시에는 요청이 아예 나가지 않는다.
  const offWt = onWorkspaceEvent('worktrees_changed', () => { refreshWorktrees(); });
  onCleanup(() => {
    clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4);
    offWt();
  });

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
        // §4 — 서버가 `.git/config`에서 뽑아 준 origin. 둘째 줄의
        // `github/fornerds`와 소유자 기반 색 배정의 입력이다.
        remote: wt.remote || null,
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
        diffFiles: cwd ? (cachedDiffCount(cwd) ?? null) : null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
        agent: tmuxName ? (agentNames()[tmuxName] ?? null) : null,
      });
      // git status는 별도로 비동기 채운다(캐시 60초) — 도착하면 diffTick으로 재렌더.
      if (cwd && diffCountStale(cwd)) {
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

  // keybar.js는 모듈 평가 시점에 한 번 자리를 잡는데, 그때 이 레일은 아직 없다
  // (지연 로드). 마운트된 뒤 한 번 더 부른다 — 이미 제자리면 아무 일도 안 한다.
  onMount(() => placeMicButton());

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
    // 저장값(ui.rail.width)은 "얼마로 쓰고 싶은가"이고, 화면에 그리는 폭은
    // 거기에 "창이 그만큼 내줄 수 있는가"를 한 번 더 건 값이다. 창을 좁히면
    // 40vw에서 걸리고, 다시 넓히면 저장값으로 알아서 돌아온다 — 저장값 자체는
    // 건드리지 않으므로 사용자가 끌어 맞춘 폭이 창 크기 때문에 지워지지 않는다.
    //
    // 40vw인 이유: 레일이 창의 절반 가까이를 먹으면 터미널이 남지 않는다.
    // 레일이 존재하는 최소 폭이 720px(그 아래는 compact라 레일이 아예 없다)
    // 이므로 40vw는 항상 288px 이상 — 레일 자체의 최소폭 240px을 밑돌지 않는다.
    document.documentElement.style.setProperty(
      '--vt-wgrail-w',
      collapsed() ? '48px' : `min(${storedWidth()}px, 40vw)`,
    );
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
      // 실제로 그려진 폭에서 출발한다. --vt-wgrail-w를 parseInt 하던 걸 바꿨다 —
      // 이제 그 값이 `min(252px, 40vw)` 같은 식이라 숫자로 파싱되지 않고(커스텀
      // 속성은 계산된 px로 안 내려온다) NaN → 기본값으로 튀어, 창이 좁아 clamp가
      // 걸린 상태에서 드래그를 시작하면 레일이 순간 점프한다.
      getStartRatio: () => railRef?.getBoundingClientRect().width || DEFAULT_W,
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
    <aside id="vt-wgrail" class="vt-sidepanel left" ref={railRef} classList={{ collapsed: collapsed() }} aria-label="워크트리">
      <div class="vt-wgrail-head">
        <Show when={!collapsed()}>
          {/* C1 — 호스트가 로컬 하나뿐이면 칩을 아예 안 그린다. 멀티호스트를
              안 쓰는 사람에게는 "고를 게 없는 드롭다운"이 잡음일 뿐이다. */}
          <Show
            when={hosts().length > 1}
            fallback={<span class="vt-wgrail-title">{railTitle()}</span>}
          >
            <button
              type="button"
              class="vt-host-btn vt-wgrail-host"
              classList={{ offline: activeHost() ? !activeHost()!.online : false, remote: isRemoteHost() }}
              onClick={openHostMenu}
              data-tip={activeHost()?.label || '호스트 선택'}
              data-tip-sub={activeHost() ? hostDetail(activeHost()!) : undefined}
              data-tip-side="bottom"
            >
              <span class="vt-wgrail-host-name">{activeHost()?.label || effectiveHostId()}</span>
              <span class="vt-wgrail-host-caret"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
            </button>
          </Show>
        </Show>
        {/* 2026-09-18 — 글리프(‹ ›)를 쓰고 있었다. DESIGN.md §아이콘: icons.js의
            인라인 SVG가 유일한 아이콘 소스다. chevron 하나를 CSS로 돌리는
            관용구(키바 토글·호스트 캐럿과 같다)로 맞춘다. */}
        <button
          type="button"
          class="vt-icon-btn sm vt-wgrail-collapse"
          classList={{ collapsed: collapsed() }}
          onClick={toggleCollapse}
          aria-label={collapsed() ? '펼치기' : '접기'}
          data-tip={collapsed() ? '레일 펼치기' : '레일 접기'}
          data-tip-side="bottom"
          innerHTML={icon('chevron-down', 14, 2)}
        />
      </div>
      <div class="vt-wgrail-body">
        <Show when={totalRows() === 0 && !collapsed() && !isRemoteHost()}>
          <div class="vt-wgrail-empty">
            아직 워크트리가 없습니다.
            <button type="button" class="vt-btn sm vt-wgrail-empty-new" onClick={() => setDialogOpen(true)}>+ 워크트리 만들기</button>
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
              {/* 접었을 때도 그룹 경계는 남긴다 — 라벨만 못 읽는 것과 「개입
                  필요」와 「유휴」가 한 덩어리로 붙어 보이는 것은 다르다. */}
              <Show when={!collapsed()} fallback={<div class="vt-wgrail-group-sep" role="separator" />}>
                {/* §2 — 그룹 헤더가 이름표에서 **버튼 줄**이 됐다.
                    캐럿+이름+개수 전체가 여닫기 클릭 영역이고, 오른쪽 ⚙는
                    저장소 표시 설정(§3)을 연다. 개수를 붙이는 이유는 접었을 때
                    "몇 개가 숨었나"가 보여야 하기 때문이다(Fleet.tsx의 그룹
                    헤더가 이미 같은 모양이다).
                    「개입 필요」는 접기 버튼을 아예 안 그린다 — 승인 대기가
                    접힌 채 숨으면 그 그룹이 존재할 이유가 사라진다. */}
                <div class="vt-wgrail-group-head">
                  <Show
                    when={COLLAPSIBLE_GROUPS.includes(section.group)}
                    fallback={(
                      <span class="vt-wgrail-group-label">
                        {GROUP_LABEL[section.group]}
                        <span class="vt-wgrail-group-count">{section.rows.length}</span>
                      </span>
                    )}
                  >
                    <button
                      type="button"
                      class="vt-wgrail-group-toggle"
                      classList={{ collapsed: isGroupCollapsed(section.group) }}
                      aria-expanded={!isGroupCollapsed(section.group)}
                      onClick={() => toggleGroup(section.group)}
                    >
                      <span class="vt-wgrail-group-caret" innerHTML={icon('chevron-down', 12, 2)} />
                      <span class="vt-wgrail-group-label">{GROUP_LABEL[section.group]}</span>
                      <span class="vt-wgrail-group-count">{section.rows.length}</span>
                    </button>
                  </Show>
                  {/* ⚙는 「열려 있지 않음」에만 둔다. 숨김은 이 그룹을 채우는
                      저장소들에 대한 판단이고, 세 그룹 머리마다 같은 버튼을
                      반복하면 그게 잡음이다. */}
                  <Show when={section.group === 'idle'}>
                    <button
                      type="button"
                      class="vt-icon-btn sm vt-wgrail-group-settings"
                      aria-label="저장소 표시 설정"
                      data-tip="저장소 표시 설정"
                      data-tip-sub={hiddenCount() ? `${hiddenCount()}개 숨김` : undefined}
                      data-tip-side="right"
                      onClick={() => setRepoSheet(true)}
                      innerHTML={icon('settings', 13, 2)}
                    />
                  </Show>
                </div>
              </Show>
              <Show when={!isGroupCollapsed(section.group) || collapsed()}>
              <For each={section.rows}>
                {(row) => (
                  <Row
                    row={row}
                    compact={collapsed()}
                    active={actionSessionId(row) === activeId()}
                    onOpen={(e) => openRow(e, row)}
                    onContext={(e) => contextRow(e, row)}
                  />
                )}
              </For>
              </Show>
            </>
          )}
        </For>
        {/* §1 — 목록이 200개 상한에서 잘렸다. 여기 한 줄이 없으면 "몇 개가
            빠졌다"가 이번에 고친 깊이 버그와 똑같은 증상으로 보인다. */}
        <Show when={truncated() && !collapsed()}>
          <div class="vt-wgrail-note">저장소가 200개를 넘어 일부만 표시합니다.</div>
        </Show>
        <Show when={!truncated() && hiddenCount() > 0 && !collapsed()}>
          <button type="button" class="vt-wgrail-note vt-wgrail-note-btn" onClick={() => setRepoSheet(true)}>
            숨긴 저장소 {hiddenCount()}개
          </button>
        </Show>
      </div>
      <div class="vt-wgrail-footer">
        {/* 워크트리 생성은 로컬 전용이다(원격 워크트리는 2.2 범위) — 원격을 보고
            있을 때 누르면 "맥에" 워크트리가 생겨 화면과 결과가 어긋난다. */}
        <button
          type="button"
          class="vt-wgrail-new"
          disabled={isRemoteHost()}
          data-tip={isRemoteHost() ? '워크트리 만들기' : (collapsed() ? '워크트리 만들기' : undefined)}
          data-tip-sub={isRemoteHost() ? '원격 호스트에서는 불가 (2.2)' : undefined}
          data-tip-side="right"
          onClick={() => setDialogOpen(true)}
        >
          <Show when={!collapsed()} fallback={<span class="vt-wgrail-new-mark" innerHTML={icon('plus', 15, 2)} />}>+ 워크트리 만들기</Show>
        </button>
        {/* 2.1.6 — 설정을 레일 바닥에 **다시 꺼낸다**. 2.1.0에서 48px 아이콘
            레일(#vt-rail)이 이 레일로 대체되면서 그 안에 살던 ⚙ 버튼이
            display:none으로 통째로 사라졌고, 설정에 가는 길이 `⋯` 메뉴 안이나
            Mod+, 뿐이었다 — 매일 쓰는 화면의 입구가 메뉴 두 단계 안으로
            들어가 있었다. ⋯는 그대로 두고 그 옆에 둔다. */}
        <button
          type="button"
          class="vt-icon-btn lg vt-wgrail-icon"
          aria-label="설정"
          data-tip="설정"
          data-tip-sub="Mod+,"
          data-tip-side="right"
          onClick={() => (props.deps.getAction('settings.show') as (() => void) | undefined)?.()}
          innerHTML={icon('settings', 15, 2)}
        />
        {/* 2026-09-18 — `⋯ 더보기`가 있던 자리. 그 메뉴의 6개 항목을 하나씩
            추적해 보니 **마이크 하나 빼고 전부** 다른 입구가 있었다:
            파일 열기=팔레트 `/`, 큐·포트·사용량=dock 탭+팔레트, 스니펫=팔레트,
            테마·푸시·자동복사·음성전용=설정/팔레트. 즉 ⋯는 마이크 때문에
            남아 있던 메뉴였다. 그래서 마이크를 여기로 꺼내고 메뉴를 없앴다
            (ADR-8 "⋯ 폐지 → 레일(포인터) + 팔레트(키보드)"의 마지막 조각).
            버튼을 새로 만들지 않고 **기존 노드를 옮겨 온다** — voice.js가
            `#mic-btn-wrap`을 모듈 최상위에서 캐시하고 `.recording`/.label로
            상태를 그리므로, 같은 노드가 아니면 녹음 표시가 죽는다. */}
        <div id="vt-rail-mic-home" class="vt-wgrail-mic-home" />
      </div>
      <div ref={wireResizerOnMount} class="vt-wgrail-resizer" />
      <Show when={ctxMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setCtxMenu(null)} items={ctxMenuItems()} />}
      </Show>
      <Show when={hostMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setHostMenu(null)} items={hostMenuItems()} />}
      </Show>
      <Show when={repoSheet()}>
        <RepoVisibility
          deps={props.deps}
          onClose={() => setRepoSheet(false)}
          onChanged={() => refreshWorktrees()}
        />
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
