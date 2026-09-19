// N36 §5 — 워크트리 레일 252px. 2.1.1(N8/N44)~2.1.6은 행 = Worktree였다
// (30-worktree.md §4). ADR-29(2026-09-18)로 되돌렸다 — 행은 다시 세션이고,
// 워크트리는 저장소·브랜치 배지로만 남는다(로컬 호스트에 한함). 원격
// 호스트는 아직 옛 3버킷(개입 필요/작업 중/열려 있지 않음) 그대로다(2.2 범위).
//
// ⚠ 이 파일은 core/store.js·layout/store.js·agent/state.js·term/session.js를
// 직접 import하지 않는다(panels/viewer-lazy.js·shell/Hud.tsx와 같은 이유 —
// 지연 청크가 이들을 정적 import하면 Vite lib 모드가 상태를 복제한다,
// ADR-26/N35 커밋 참고). 전부 그 파일들이 이미 노출해 둔 window 브리지로만
// 읽는다. vtFetch·getAction만 main.js가 인자로 넘긴다(Hud.tsx와 같은 이유).
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render, Portal } from 'solid-js/web';
import {
  buildRailSections, buildSessionSections, buildSleepingEntries,
  GROUP_LABEL, COLLAPSIBLE_GROUPS, groupCollapseKey, GROUP_COLLAPSED_DEFAULT, defaultRailCollapsed,
  type RailGroup, type OtherRailRowInput, type DesktopRailRowInput,
} from './rail-data.js';
import {
  buildHostMenu, remoteSessionRows, resolveActiveHost, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';
import {
  actionSessionId, fetchAgentDetails, fetchDiffCount, safeFetch,
  useAgentVersion, useSessionsVersion, useLayoutVersion, fetchAgentNames,
  type RailDeps, type AgentDetail,
  cachedDiffCount,
  diffCountStale,
} from './rail-fetch.js';
import { REGULAR_MAX } from '../layout/breakpoints.js';
import { Menu, Row, type MenuItem } from './RailRow.js';
import { icon } from '../ui/icons.js';

export type { RailDeps } from './rail-fetch.js';
import { wireRatioResizer } from '../layout/resizer.js';
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
  const layoutVersion = useLayoutVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  const [agentDetails, setAgentDetails] = createSignal<Record<string, AgentDetail>>({});
  // 정체(어떤 CLI인가)는 상태(무엇을 하는 중인가)와 **다른 엔드포인트**다.
  // 한 응답에 둘 다 있을 거라 짐작했다가 마크가 한 개도 안 그려졌다.
  const [agentNames, setAgentNames] = createSignal<Record<string, string>>({});
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
  // ADR-29 B — 그룹 섹션 접힘. 옛 groupCollapsed(아래)는 RailGroup 3종 전용
  // 열거형 키라 임의 그룹 id를 못 담는다 — 그룹은 사용자가 몇 개든 만들 수
  // 있으므로 여기는 Set이다. 기기 간 지속은 아직 안 한다(그룹 자체가 이제
  // 막 생긴 개념이라 "설정에 무엇을 남길지"는 D/E에서 실제 사용을 보고
  // 정한다 — 지금 잘못 정하면 나중에 마이그레이션이 생긴다). 잠자는 구역만
  // 기본 접힘으로 시작한다(옛 idle 그룹과 같은 판단 — 당장 볼 일이 적다).
  const [collapsedSessionGroups, setCollapsedSessionGroups] = createSignal<Set<string>>(new Set(['sleeping']));
  const toggleSessionGroup = (key: string) => setCollapsedSessionGroups((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
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

  // 2026-09-18 후속 — 예전엔 여기가 "저장소 N · 워크트리 N"이었다(그 시절
  // 헤더 자체가 이미 자기모순을 알고 있었다: "ADR-29로 행의 정체는 세션이
  // 됐지만 이 요약 문구가 세는 대상은 안 바뀌었다"). 사용자 지적으로 바로
  // 잡는다 — 이 레일은 이제 세션 목록이지 저장소 목록이 아니므로, 머리글도
  // 저장소를 안 센다. 아래 각 그룹 헤더가 이미 자기 몫의 개수를 보여주므로
  // 여기서 총합을 또 셀 필요도 없다.
  const railTitle = () => '세션';
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
  // ADR-29 B — 컨텍스트 메뉴 대상은 이제 항상 세션이다(워크트리 삭제는
  // C단계에서 저장소 시트로 옮겨간다). sessionId가 없어도(잠자는 세션)
  // tmuxName만 있으면 메뉴가 성립한다 — 「깨우기」·「완전 종료」는 웹 세션이
  // 없어도 할 수 있는 일이다.
  const [ctxMenu, setCtxMenu] = createSignal<{
    x: number; y: number; sessionId: string | null; tmuxName: string | null;
  } | null>(null);
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [hostMenu, setHostMenu] = createSignal<{ x: number; y: number } | null>(null);
  const [activeHostId, setActiveHostId] = createSignal<string>(
    String((window as any).vtSettingsGet?.(SETTINGS_HOST_KEY) || LOCAL_HOST),
  );

  // compact(<720px)에서는 이 데스크톱 Rail이 CSS로 숨고 mobile-nav가 Fleet을
  // 마운트한다. 예전엔 숨은 Rail도 계속 살아 있어 Fleet과 똑같은 tmux·agent·git
  // 조회를 중복 실행했다. 마운트 자체는 반응형 전환을 위해 유지하되, 보이지 않는
  // 동안 네트워크/서브프로세스 폴링만 쉰다.
  const desktopMq = typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 720px)')
    : null;
  const railPollingActive = () => desktopMq ? desktopMq.matches : window.innerWidth >= 720;

  const refreshSessions = async () => {
    if (!railPollingActive()) return;
    const list = await safeFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (list) setTmuxSessions(list);
  };
  const refreshAgent = async () => {
    if (!railPollingActive()) return;
    setAgentDetails(await fetchAgentDetails(props.deps));
    setAgentNames(await fetchAgentNames(props.deps));
  };
  // ADR-29 B — /api/worktrees(평면) 대신 /api/repos(저장소별로 이미 묶임).
  const refreshRepos = async () => {
    if (!railPollingActive()) return;
    const data = await safeFetch<{ repos?: any[]; truncated?: boolean; hiddenCount?: number }>(
      props.deps, '/api/repos',
    );
    setRepos(data?.repos || []);
    setTruncated(!!data?.truncated);
    setHiddenCount(Number(data?.hiddenCount) || 0);
  };
  // C1 — 로컬+원격을 한 목록으로. 실패하면(라우터가 없는 옛 서버 등) 빈 배열이
  // 남아 스위처가 아예 안 그려진다 — 멀티호스트를 안 쓰는 사람에게는 그게 맞다.
  const refreshHosts = async () => {
    if (!railPollingActive()) return;
    const data = await safeFetch<{ hosts?: HostEntry[] }>(props.deps, '/api/hosts');
    if (data?.hosts) setHosts(data.hosts);
  };

  const refreshAll = () => {
    refreshSessions();
    refreshAgent();
    refreshRepos();
    refreshHosts();
  };
  refreshAll();
  const onViewportChange = (e: MediaQueryListEvent) => {
    // compact에서 쉴 동안 놓친 상태를 데스크톱으로 돌아오는 즉시 한 번 맞춘다.
    if (e.matches) refreshAll();
  };
  desktopMq?.addEventListener('change', onViewportChange);
  const t1 = setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_POLL_MS);
  const t2 = setInterval(() => { if (!document.hidden) refreshAgent(); }, STATUS_POLL_MS);
  const t3 = setInterval(() => { if (!document.hidden) refreshRepos(); }, WORKTREES_POLL_MS);
  const t4 = setInterval(() => { if (!document.hidden) refreshHosts(); }, HOSTS_POLL_MS);
  // 서버가 워크트리 생성·삭제·열기 때 쏘는 push. 폴링(60초)보다 훨씬 빠르게
  // 반영되고, 평시에는 요청이 아예 나가지 않는다.
  const offWt = onWorkspaceEvent('worktrees_changed', () => { refreshRepos(); });
  onCleanup(() => {
    clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4);
    desktopMq?.removeEventListener('change', onViewportChange);
    offWt();
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
  const effectiveHostId = createMemo(() =>
    hosts().length === 0 ? activeHostId() : resolveActiveHost(hosts(), activeHostId()));
  const isRemoteHost = createMemo(() => effectiveHostId() !== LOCAL_HOST);
  const activeHost = createMemo(() => hosts().find((h) => h.id === effectiveHostId()) || null);

  // ADR-29 B — 행은 이제 tmux 세션 목록이 출처다(잠자는 것까지 나온다).
  // 예전엔 웹 세션 맵(w.allSessions())이 출처라 이 브라우저에 안 열어둔
  // tmux 세션은 화면 어디에도 안 나왔다 — 그게 이 재설계의 발단이었다.
  // 2026-09-18 후속(그룹 재정의, 사용자 요청: "같은 화면에 묶인 것만
  // 그룹") — layout/store.js의 tabGroups()를 window 브리지로 읽는다. 태그
  // (`@fsh_grp`)가 아니라 지금 pane 트리에 2개 이상의 세션이 같이 떠 있는
  // 탭을 실시간으로 계산한 결과다. layoutVersion(화면 변경)과
  // sessionsVersion(세션 추가/삭제) 둘 다에 반응한다.
  const liveTabGroups = createMemo<{ id: string; label: string; sessionIds: string[] }[]>(() => {
    layoutVersion(); sessionsVersion();
    return (window as any).getTabGroups?.() || [];
  });

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
    const details = agentDetails();

    // 웹 세션 id → 그 세션이 지금 떠 있는 그룹(탭) id. 그룹 아닌(혼자인)
    // 세션은 이 지도에 없다 — 그때 groupId는 null이다.
    const sessionIdToGroup = new Map<string, string>();
    for (const g of liveTabGroups()) for (const sid2 of g.sessionIds) sessionIdToGroup.set(sid2, g.id);

    // tmux 세션 이름 → 웹에 이미 열린 세션 id(있으면). 서버가 준
    // web_session_id가 아니라 **이 브라우저의 세션 맵**을 출처로 삼는다 —
    // "깨어있음"은 기기별 개념이라, 다른 기기가 열어둔 걸 이 기기에서
    // 깨어있다고 하면 안 된다.
    const tmuxToWebSid: Record<string, string> = {};
    for (const [sid, s] of Object.entries<any>(all)) {
      const tn = s.tmuxName || s.tmux_name;
      if (tn && !(tn in tmuxToWebSid)) tmuxToWebSid[tn] = sid;
    }

    // wt_id → 브랜치 배지 정보. /api/repos(저장소별로 이미 묶임)에서 뽑는다.
    // repoId·gitRemote는 안 담는다 — 그룹 자동 제안(repoId)과 저장소 색점
    // (gitRemote)을 둘 다 없앤 뒤로(2026-09-18 후속) 쓸 데가 없다.
    // 2026-09-20 — `repoName`도 같은 이유로 뺐다. 마지막 소비처였던 그룹 라벨
    // 폴백이 ADR-29 후속 #4에서 사라진 뒤로 아무도 읽지 않았다.
    const wtInfo = new Map<string, { branch: string; isMain: boolean; changed: any }>();
    for (const repo of repos()) {
      for (const wt of repo.worktrees || []) {
        wtInfo.set(wt.id, { branch: wt.branch, isMain: !!wt.isMain, changed: wt.changed || null });
      }
    }

    const out: OtherRailRowInput[] = [];
    const seenTmux = new Set<string>();
    for (const t of tmuxSessions()) {
      seenTmux.add(t.name);
      const sid = tmuxToWebSid[t.name] || '';
      const awake = !!sid;
      const info = t.wt_id ? wtInfo.get(t.wt_id) : undefined;
      const detail = details[t.name];
      const status = awake && w.getStatus ? w.getStatus(t.name) : (w.getStatus ? w.getStatus(t.name) : 'idle');
      const cwd = t.cwd || null;
      out.push({
        kind: 'session',
        sessionId: sid,
        tmuxName: t.name,
        name: awake && w.sessionDisplayName ? w.sessionDisplayName(sid) : t.name,
        awake,
        groupId: (sid && sessionIdToGroup.get(sid)) || null,
        status,
        since: detail?.since ?? null,
        tool: detail?.tool ?? null,
        diffFiles: cwd ? (cachedDiffCount(cwd) ?? null) : null,
        changed: info?.changed ?? null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
        agent: agentNames()[t.name] ?? null,
        branch: info?.branch ?? null,
        isMainWorktree: info?.isMain ?? false,
        worktreeId: t.wt_id || null,
      });
      if (cwd && diffCountStale(cwd)) {
        fetchDiffCount(props.deps, cwd).then(() => setDiffTick((n) => n + 1));
      }
    }

    // 일반(비 tmux) 세션 — 서버 쪽에 지속되는 대상이 없어 늘 깨어있다.
    for (const [sid, s] of Object.entries<any>(all)) {
      const tn = s.tmuxName || s.tmux_name;
      if (tn && seenTmux.has(tn)) continue;
      if (tn) continue; // tmux 이름은 있는데 목록에 없다 — 방금 죽은 세션, 다음 폴링에서 정리된다.
      out.push({
        kind: 'session',
        sessionId: sid,
        tmuxName: null,
        name: w.sessionDisplayName ? w.sessionDisplayName(sid) : sid.slice(0, 8),
        awake: true,
        groupId: sessionIdToGroup.get(sid) || null,
        status: 'idle',
        since: null,
        tool: null,
        diffFiles: null,
        agent: null,
        branch: null,
        isMainWorktree: false,
      });
    }
    return out;
  });

  // ADR-29 B — 두 갈래: 깨어있는 세션의 그룹 섹션(개입 필요 고정 뷰 포함),
  // 잠자는 세션의 구역(그룹째 잠들었으면 덩어리, 아니면 낱개). 원격
  // 호스트는 옛 3버킷(개입 필요/작업 중/열려 있지 않음) 그대로 — 거긴
  // 그룹·수면 개념이 아직 없다(2.2 범위).
  const localSessionRows = createMemo<OtherRailRowInput[]>(() =>
    isRemoteHost() ? [] : (rows() as OtherRailRowInput[]));
  const groupLabelMap = createMemo(() => new Map(liveTabGroups().map((g) => [g.id, g.label])));
  const sessionSections = createMemo(() => {
    diffTick();
    return buildSessionSections(localSessionRows(), groupLabelMap());
  });
  const sleepingEntries = createMemo(() => buildSleepingEntries(localSessionRows()));
  const remoteSections = createMemo(() => (isRemoteHost() ? buildRailSections(rows() as OtherRailRowInput[]) : []));
  const totalRows = createMemo(() => isRemoteHost()
    ? remoteSections().reduce((n, s) => n + s.rows.length, 0)
    : sessionSections().reduce((n, s) => n + s.rows.length, 0) + sleepingEntries().length);

  const activeId = createMemo(() => { sessionsVersion(); return (window as any).activeSessionId?.() ?? null; });

  const openRow = async (e: MouseEvent, row: OtherRailRowInput) => {
    const w = window as any;
    // C1+3단계 — 원격 행은 프록시 경로로 연다(term/remote.js가 window에 건다).
    if (row.remote) {
      const host = effectiveHostId();
      if (typeof w.attachRemoteSession === 'function' && row.tmuxName) {
        await w.attachRemoteSession(host, row.tmuxName);
      }
      return;
    }
    const meta = e.metaKey || e.ctrlKey;
    // 세션을 열기 전에 그 세션이 이미 떠 있는 화면(탭)으로 먼저 옮긴다 —
    // 다른 세션과 같은 화면을 공유 중이면(그룹) 그 화면으로, 혼자 있는
    // 탭이면 그 탭으로. switchTo/attachTmux는 항상 **지금 활성 탭**에
    // 배정하므로(addSession의 마지막 줄) 순서가 바뀌면 엉뚱한 탭에 얹힌다.
    // ⌘클릭 분할은 예외다 — "지금 보는 화면 옆에 놓기"라는 뜻이라 탭을
    // 바꾸면 안 된다. 아직 안 깨어난 세션도 예외 — 탭 소속은 웹 세션 id로만
    // 알 수 있고(2026-09-18 후속, 그룹 재정의), 깨우면 새 탭이 생긴다.
    if (!meta && row.awake) {
      w.openGroupTab?.({ sessionId: actionSessionId(row), worktreeId: row.worktreeId || null, hostId: 'local' });
    }
    // ADR-29 B — 잠자는 세션을 클릭하면 깨운다(attach). 여는 것은 언제나
    // 세션이라는 원칙이다.
    if (!row.awake) {
      if (row.tmuxName) await w.attachTmux?.(row.tmuxName);
      return;
    }
    const sid = actionSessionId(row);
    if (sid) {
      if (meta) w.splitActivePane?.('row', sid);
      else w.switchTo?.(sid);
    }
  };

  const contextRow = (e: MouseEvent, row: OtherRailRowInput) => {
    e.preventDefault();
    if (row.remote) return; // 원격 세션은 로컬 액션(맥에서 열기 등)이 안 맞는다 — 지금까지도 메뉴가 없었다.
    const sid = actionSessionId(row);
    if (!sid && !row.tmuxName) return; // 웹 세션도 tmux 이름도 없으면 메뉴가 성립하지 않는다.
    setCtxMenu({ x: e.clientX, y: e.clientY, sessionId: sid, tmuxName: row.tmuxName });
  };

  // 2026-09-18 후속(그룹 재정의, 사용자 요청) — 그룹을 만드는 유일한 길은
  // 이제 **화면(터미널 pane)에 직접 드래그해서 나란히 놓는 것**이다. 레일
  // 안에서 세션을 세션/그룹 헤더에 끄는 옛 방식(태그를 직접 썼다)은 없앴다
  // — 그룹은 태그가 아니라 pane 트리에서 도출되므로 레일 자신이 쓸 수
  // 있는 "그룹에 넣기" 동작 자체가 없어졌다. 대신 세션 행을 pane 분할
  // 드롭존(layout/dnd.js)의 드래그 소스로 만든다 — mime 이름은 그 파일의
  // `SESSION_MIME` 상수와 같은 문자열이어야 한다(이 파일은 레이아웃 모듈을
  // 정적 import 못 해 리터럴로 맞춘다, 파일 머리말 참고). 잠자는 세션은
  // 웹 세션 id가 없어 끌 수 없다.
  const onRowDragStart = (e: DragEvent, row: OtherRailRowInput) => {
    if (!row.awake || !row.sessionId) { e.preventDefault(); return; }
    e.dataTransfer?.setData('text/vt-tab-id', row.sessionId);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  };

  // 그룹 이름 짓기, 제자리 편집(window.prompt 안 씀). 「묶지 않음」
  // (groupId null)은 대상이 아니다 — 이름은 탭 자체(layout/store.js의
  // label)에 붙는다.
  const [editingGroupKey, setEditingGroupKey] = createSignal<string | null>(null);
  const [editingGroupValue, setEditingGroupValue] = createSignal('');
  const startRenameGroup = (e: MouseEvent, groupId: string, currentLabel: string) => {
    e.stopPropagation(); // 부모 토글 버튼까지 눌리면 접혔다 펴진다.
    setEditingGroupValue(currentLabel);
    setEditingGroupKey(groupId);
  };
  const commitRenameGroup = (groupId: string) => {
    if (editingGroupKey() !== groupId) return; // blur가 Enter 뒤에도 한 번 더 온다 — 중복 커밋 방지.
    setEditingGroupKey(null);
    const trimmed = editingGroupValue().trim();
    if (!trimmed) return; // 빈 값은 취소로 본다 — 그룹을 이름 없는 상태로 만들 수는 없다.
    // groupId는 곧 탭 id다(2026-09-18 후속, 그룹 재정의) — 서버 왕복 없이
    // layout/store.js의 탭 레코드에 바로 쓴다.
    const ok = (window as any).vtRenameTab?.(groupId, trimmed);
    if (!ok) (window as any).showToast?.('이름 변경 실패', 'error');
  };

  // 세션 이름 변경, 제자리 편집(그룹 이름과 같은 관용구, window.prompt 안
  // 씀) — layout/tabbar.js의 주석은 "닫기·이름 변경은 레일 세션 행으로"
  // 옮겼다고 했지만 실제로는 이 메뉴에 빠져 있었다(사용자 지적으로 발견한
  // 회귀). 웹 세션이 있어야 한다(term/session.js의 renameSession).
  const [editingSessionKey, setEditingSessionKey] = createSignal<string | null>(null);
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
  };

  const ctxMenuItems = () => {
    const m = ctxMenu();
    if (!m) return [];
    const w = window as any;
    const items: MenuItem[] = [];
    if (m.sessionId) {
      items.push(
        { label: '이름 변경', run: () => startRenameSession(m.sessionId as string, w.sessionDisplayName?.(m.sessionId) || '') },
        { label: '새 세션', run: () => w.createSession?.() },
        { label: '지금 이 세션 맥에서 열기', run: () => { w.switchTo?.(m.sessionId); (props.deps.getAction('session.open-on-mac') as (() => void) | undefined)?.(); } },
        { label: '연결된 화면', run: () => { w.switchTo?.(m.sessionId); (props.deps.getAction('clients.show') as (() => void) | undefined)?.(); } },
        // 2.1 D3 — "닫기"는 화면마다 다른 뜻이었다. 이 메뉴가 하는 건 웹
        // 세션을 놓는 것뿐(tmux는 계속 산다)이라 "세션 놓기"로 불렀다.
        // ADR-29 D — 레일이 이미 쓰는 잠·깸 어휘와 맞춰 "재우기"로 다시
        // 부른다(동작은 그대로, term/session-actions.js의 vtDetachSession).
        { label: '재우기', run: () => w.vtDetachSession?.(m.sessionId) },
      );
    } else if (m.tmuxName) {
      // ADR-29 B — 잠자는 행의 컨텍스트 메뉴: 웹 세션이 없으니 위 넷은 못
      // 쓰지만(맥에서 열기·연결된 화면은 "지금 활성 세션"을 전제한다), 깨우기와
      // 완전 종료는 tmux 이름만으로 된다.
      items.push({ label: '깨우기', run: () => w.attachTmux?.(m.tmuxName) });
    }
    if (m.tmuxName) {
      // ADR-29 B — 지금까지 tmux-panel.js 팝업에만 있던 완전 종료를 레일
      // 세션 메뉴에도 연결한다(최종 설계안 §4: "완전 종료는 세션 메뉴 맨
      // 아래 위험 구역"). 확인 문구는 session-actions.js가 상태(waiting/
      // working)를 보고 만든다 — 여기서 다시 만들지 않는다.
      items.push({
        label: '완전 종료', detail: '되돌릴 수 없음',
        run: () => w.vtConfirmAndKillSession?.(m.tmuxName, m.sessionId || null),
      });
    }
    return items;
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
        <div class="vt-wgrail-head-actions">
          {/* 2026-09-18 — 이 버튼은 예전엔 「열려 있지 않음」 그룹 머리에만
              있었다. buildRailSections()가 빈 그룹을 통째로 지우므로, 모든
              저장소에 세션이 붙어 있으면(=idle 그룹이 안 생기면) 이 버튼이
              화면에서 완전히 사라져 "저장소 표시" 설정에 닿을 방법이 없었다.
              레일 헤더 줄은 섹션 유무와 무관하게 항상 그려지므로 여기 하나만
              둔다(그래서 목록 바닥의 "숨긴 저장소 N개" 링크도 후속에서
              지울 수 있었다 — 유일한 진입점이라는 이유가 사라졌다). */}
          <Show when={!collapsed()}>
            <button
              type="button"
              class="vt-icon-btn sm vt-wgrail-settings"
              aria-label="저장소 관리"
              data-tip="저장소 관리"
              data-tip-sub={hiddenCount() ? `${hiddenCount()}개 숨김` : '표시 · 워크트리 · 세션'}
              data-tip-side="bottom"
              onClick={() => setRepoSheet(true)}
              innerHTML={icon('settings', 13, 2)}
            />
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
      </div>
      <div class="vt-wgrail-body">
        <Show when={totalRows() === 0 && !collapsed() && !isRemoteHost()}>
          <div class="vt-wgrail-empty">
            아직 세션이 없습니다.
            <button
              type="button"
              class="vt-btn sm vt-wgrail-empty-new"
              onClick={(e) => (props.deps.getAction('session.add-menu') as ((el?: HTMLElement, e?: MouseEvent) => void) | undefined)?.(e.currentTarget, e)}
            >+ 새 세션</button>
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
        {/* 원격 호스트: 옛 3버킷 그대로(개입 필요/작업 중/열려 있지 않음) —
            그룹·수면 개념은 2.2 범위(로컬 전용, ADR-29 결정문 참고). */}
        <Show when={isRemoteHost()}>
          <For each={remoteSections()}>
            {(section) => (
              <>
                <Show when={!collapsed()} fallback={<div class="vt-wgrail-group-sep" role="separator" />}>
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
        </Show>
        {/* 로컬 호스트: ADR-29 B — 깨어있는 세션의 그룹 섹션. */}
        <Show when={!isRemoteHost()}>
          <For each={sessionSections()}>
            {(section) => (
              <>
                <Show when={!collapsed()} fallback={<div class="vt-wgrail-group-sep" role="separator" />}>
                  <div class="vt-wgrail-group-head">
                    <Show when={section.kind === 'attention'}>
                      <span class="vt-wgrail-group-label">
                        {section.label}
                        <span class="vt-wgrail-group-count">{section.rows.length}</span>
                      </span>
                    </Show>
                    {/* ADR-29 후속 — 제자리 편집. window.prompt 같은 네이티브
                        다이얼로그를 안 쓴다(사용자 지적: "절대 기본 컴포넌트
                        쓰지 마라"). 편집 중엔 토글·연필 버튼을 통째로
                        input으로 갈아 끼운다 — 버튼 위에 input을 얹으면
                        클릭이 토글로 새는 것과 포커스 다툼이 생긴다. */}
                    <Show when={section.kind !== 'attention' && editingGroupKey() !== section.key}>
                      <button
                        type="button"
                        class="vt-wgrail-group-toggle"
                        classList={{ collapsed: collapsedSessionGroups().has(section.key) }}
                        aria-expanded={!collapsedSessionGroups().has(section.key)}
                        onClick={() => toggleSessionGroup(section.key)}
                      >
                        <span class="vt-wgrail-group-caret" innerHTML={icon('chevron-down', 12, 2)} />
                        <span class="vt-wgrail-group-label">{section.label}</span>
                        <span class="vt-wgrail-group-count">{section.rows.length}</span>
                      </button>
                      {/* 「묶지 않음」은 실제 그룹이 아니라 이름 지을 대상이 없다. */}
                      <Show when={section.kind === 'group' && section.groupId}>
                        <button
                          type="button"
                          class="vt-icon-btn xs vt-wgrail-group-rename"
                          aria-label={`${section.label} 이름 바꾸기`}
                          data-tip="그룹 이름 바꾸기"
                          data-tip-side="bottom"
                          onClick={(e) => startRenameGroup(e, section.groupId as string, section.label)}
                          innerHTML={icon('pencil', 12, 2)}
                        />
                      </Show>
                    </Show>
                    <Show when={section.kind !== 'attention' && editingGroupKey() === section.key}>
                      <input
                        type="text"
                        class="vt-input vt-wgrail-group-edit"
                        value={editingGroupValue()}
                        ref={(el) => { queueMicrotask(() => { el.focus(); el.select(); }); }}
                        onInput={(e) => setEditingGroupValue(e.currentTarget.value)}
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') { e.preventDefault(); commitRenameGroup(section.groupId as string); }
                          else if (e.key === 'Escape') { e.preventDefault(); setEditingGroupKey(null); }
                        }}
                        onBlur={() => commitRenameGroup(section.groupId as string)}
                      />
                    </Show>
                  </div>
                </Show>
                <Show when={section.kind === 'attention' || !collapsedSessionGroups().has(section.key) || collapsed()}>
                <For each={section.rows}>
                  {(row) => (
                    <Row
                      row={row}
                      compact={collapsed()}
                      active={actionSessionId(row) === activeId()}
                      draggable={!!row.awake && !!row.sessionId}
                      onDragStart={(e) => onRowDragStart(e, row)}
                      editing={!!row.sessionId && editingSessionKey() === row.sessionId}
                      editingValue={editingSessionValue()}
                      onEditInput={setEditingSessionValue}
                      onEditCommit={() => commitRenameSession(row.sessionId)}
                      onEditCancel={() => setEditingSessionKey(null)}
                      onOpen={(e) => openRow(e, row)}
                      onContext={(e) => contextRow(e, row)}
                    />
                  )}
                </For>
                </Show>
              </>
            )}
          </For>
          {/* ADR-29 B — 잠자는 구역. 깨어있는 섹션과 절대 안 섞인다(최종
              설계안 §1의 정정: "잠든 것은 그룹 안에 안 들어간다"). 2026-09-18
              후속(그룹 재정의)으로 그룹째 잠드는 덩어리 표시는 없앴다(그
              근거였던 태그 자체가 없어졌다) — 전부 낱개 행이다(Row를 그대로
              재사용, sleeping=true라서 클릭 한 번으로 그 세션을 깨운다). */}
          <Show when={sleepingEntries().length > 0}>
            <Show when={!collapsed()} fallback={<div class="vt-wgrail-group-sep" role="separator" />}>
              <div class="vt-wgrail-group-head">
                <button
                  type="button"
                  class="vt-wgrail-group-toggle"
                  classList={{ collapsed: collapsedSessionGroups().has('sleeping') }}
                  aria-expanded={!collapsedSessionGroups().has('sleeping')}
                  onClick={() => toggleSessionGroup('sleeping')}
                >
                  <span class="vt-wgrail-group-caret" innerHTML={icon('chevron-down', 12, 2)} />
                  <span class="vt-wgrail-group-label">잠자는 중</span>
                  <span class="vt-wgrail-group-count">{sleepingEntries().length}</span>
                </button>
              </div>
            </Show>
            <Show when={!collapsedSessionGroups().has('sleeping') || collapsed()}>
              <For each={sleepingEntries()}>
                {(entry) => (
                  <Row
                    row={{ ...entry.row, statusSentence: '' }}
                    compact={collapsed()}
                    sleeping
                    active={false}
                    onOpen={(e) => openRow(e, entry.row)}
                    onContext={(e) => contextRow(e, entry.row)}
                  />
                )}
              </For>
            </Show>
          </Show>
        </Show>
        {/* §1 — 목록이 200개 상한에서 잘렸다. 여기 한 줄이 없으면 "몇 개가
            빠졌다"가 이번에 고친 깊이 버그와 똑같은 증상으로 보인다. */}
        <Show when={truncated() && !collapsed()}>
          <div class="vt-wgrail-note">저장소가 200개를 넘어 일부만 표시합니다.</div>
        </Show>
        {/* 2026-09-18 후속 — "숨긴 저장소 N개" 링크를 여기서 지웠다. 애초에
            이 자리에 둔 이유가 "그룹 머리에만 있던 저장소 관리 진입점이
            그룹이 하나도 없으면 사라진다"는 문제였는데(주석은 head-actions
            블록에 남아 있다), 지금은 머리글 줄의 ⚙ 버튼이 접힌 상태가
            아닌 한 항상 떠 있어 같은 문제가 없다 — 게다가 그 ⚙의 툴팁이
            이미 숨김 개수를 보여준다(hiddenCount 참고). 세션 목록 한복판에
            저장소 이야기를 또 꺼낼 이유가 없어졌다. */}
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
        <button
          type="button"
          class="vt-wgrail-new"
          disabled={isRemoteHost()}
          data-tip={isRemoteHost() ? '새 세션' : (collapsed() ? '새 세션' : undefined)}
          data-tip-sub={isRemoteHost() ? '원격 호스트에서는 불가 (2.2)' : undefined}
          data-tip-side="right"
          onClick={(e) => (props.deps.getAction('session.add-menu') as ((el?: HTMLElement, e?: MouseEvent) => void) | undefined)?.(e.currentTarget, e)}
        >
          <Show when={!collapsed()} fallback={<span class="vt-wgrail-new-mark" innerHTML={icon('plus', 15, 2)} />}>+ 새 세션</Show>
        </button>
      </div>
      <div ref={wireResizerOnMount} class="vt-wgrail-resizer" />
      <Show when={ctxMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setCtxMenu(null)} items={ctxMenuItems()} />}
      </Show>
      <Show when={hostMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setHostMenu(null)} items={hostMenuItems()} />}
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
            onClose={() => setRepoSheet(false)}
            onChanged={() => refreshRepos()}
          />
        </Portal>
      </Show>
    </aside>
  );
}

export function mountRail(root: HTMLElement, deps: RailDeps) {
  return render(() => <Rail deps={deps} />, root);
}
