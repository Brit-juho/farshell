// N38(70-mobile.md §2) — 모바일 플릿 홈. compact(<720px) 하단 내비의 첫 탭
// (ui.mobile.home 기본값). Rail.tsx와 **같은 데이터·같은 구역 규칙**을 쓴다 —
// 행 조립(rail-fetch.ts의 collectSessionRows)과 구역(rail-data.ts의 buildRailLists:
// 개입 필요 · 세션, 묶음과 이름순 단독)이 하나라서 "데스크톱 레일에서 보이는 게 폰에서도 보인다".
// 상태 키워드·상태 아이콘·읽지 않음 링·만든 곳 아이콘도 레일과 같은 컴포넌트(RailRow.tsx)다.
// 폰에만 있는 것은 인라인 승인 버튼(개입 필요 구역의 행)과 꾹 눌러 여는 메뉴 시트다.
//
// Rail.tsx와 같은 이유로 데이터 fetch(세션 목록·agent 상태·git diff)를
// 직접 다시 구현한다(정적 import 금지 — 지연 청크가 core/store.js 등 상태
// 모듈을 정적 import하면 Vite lib 모드가 복제한다, ADR-26). Dock.tsx의 배지
// 폴링도 같은 이유로 각자 반복하고 있다 — 이 파일도 그 관행을 따른다.
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { icon, stateIcon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { t } from '../core/i18n.js';
import { CatalogTree, RecentEnded, ArchiveEntry, ArchiveList, type CatalogSnapshot } from './CatalogTree.js';
import { historyApi } from '../history/api.js';
import { renameSession as renameCatalogSession } from '../history/session-actions.js';
import { agentMark } from './agent-mark.js';
// core 청크의 공유 인스턴스로 연결된다(shell.js가 core.js에서 import — 복제되지 않는다).
import { onWorkspaceEvent } from '../core/workspace-ws.js';
// 상태 없는 순수 모듈 — 지연 청크에 복제돼도 된다.
import { createPoll, isStale } from '../core/poll.js';
// git 배지 캐시·변경 알림 구독·행 조립은 레일과 **같은 것**을 쓴다(같은 shell 청크).
import {
  collectSessionRows, syncAgentsFromServer, createDiffWatcher, useLayoutVersion, pollFetch, isNoResponse, answerSessionPrompt, type RailDeps,
} from './rail-fetch.js';
import {
  buildRailLists, splitRunning, groupSessionRows, rowMenuItems, rowSummaryDetails, runRowMenu, diffLabel, formatRelativeTime, type SessionRow,
  buildSessionTree, kidsProgress, stepInOrder, ORDER_KEY, LINEAGE_KEY, VIEW_KEY, encodeOrder, encodeLineagePrefs, encodeRailView, type TreeEntry,
} from './rail-data.js';
import { createRailPrefs } from './rail-prefs.js';
import { NativeChip } from './NativeChildren.js';
import { MenuSummary, KeywordLine, OriginMark, ScreensMark, ShareMark, LineageMark, KidsToggle, AnswerButtons, ROW_MENU_ICON } from './RailRow.js';
import { ViewGroup } from './ViewGroup.js';
import {
  buildHostMenu, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';

// 세션 목록의 정본은 `/ws-workspace`의 `tmux_sessions` push다(바뀔 때만 온다).
// 이건 그 채널이 끊긴 동안의 안전망(예전 5초).
const SESSIONS_SAFETY_POLL_MS = 60000;
// 에이전트 정보의 정본은 /ws-agent push다. 이건 끊긴 동안의 안전망(예전 4초).
const AGENT_SAFETY_POLL_MS = 60000;
const HOSTS_POLL_MS = 15000;
// 보관 세션 카탈로그(로컬·폰 폭에서만). 가장 짧은 주기라 갱신 지연 판정의 기준이다.
const CATALOG_POLL_MS = 5000;

// C1 — Rail.tsx와 **같은 설정 키**를 읽고 쓴다. 폰과 데스크톱이 각자 다른
// 호스트를 기억하면 창 폭을 바꿨을 때 목록이 이유 없이 바뀐 것처럼 보인다.

export interface FleetDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
  /** 행을 열었다(터미널로 이동) — mobile-nav.js가 하단 내비 활성 탭을
   * '터미널'로 바꾼다. Fleet.tsx 자신은 "어느 탭이 지금 보이는가"를 모른다
   * (그건 mobile-nav.js의 책임 — §1 골격과 §2 플릿 홈을 같은 파일이 겸하면
   * 상태 소유가 흩어진다). */
  onOpenTerminal: () => void;
  /** Rail.tsx의 "+ 새 세션" 버튼과 같은 액션(`session.add-menu`,
   * term/session.js의 `showAddMenu`)을 부르는 데 쓴다. core/dom.js는 지연
   * 청크가 정적 import 못 하므로(파일 머리말), main.js가 하듯 mobile-nav.js가
   * 대신 import해 넘긴다. */
  getAction: (name: string) => unknown;
}

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

// 꾹 눌러 메뉴 — dnd.js의 `wireTouchDragSource`와 같은 상수(500ms·8px)를
// 쓰지만, 그건 드래그 소스를 만드는 함수라 여기 그대로 재사용할 수 없다(끄는
// 대상이 다르다 — 드래그가 아니라 탭 자체를 삼킨다). pointer 이벤트만 보고
// 마우스는 뺀다(같은 이유 — 데스크톱은 ⋯ 버튼이 이미 있다, Rail.tsx 참고).
const LONGPRESS_MS = 500;
const LONGPRESS_MOVE_PX = 8;

function useLongPress(onLongPress: () => void) {
  let timer: number | undefined;
  let startX = 0;
  let startY = 0;
  let fired = false;
  const clear = () => { if (timer != null) { clearTimeout(timer); timer = undefined; } };
  return {
    fired: () => fired,
    onPointerDown: (e: PointerEvent) => {
      if (e.pointerType === 'mouse') return;
      startX = e.clientX; startY = e.clientY; fired = false;
      clear();
      timer = window.setTimeout(() => { fired = true; timer = undefined; onLongPress(); }, LONGPRESS_MS);
    },
    onPointerMove: (e: PointerEvent) => {
      if (timer == null) return;
      if (Math.abs(e.clientX - startX) > LONGPRESS_MOVE_PX || Math.abs(e.clientY - startY) > LONGPRESS_MOVE_PX) clear();
    },
    onPointerUp: clear,
    onPointerCancel: clear,
  };
}

/**
 * 세션 카드(리디자인 8단계, 시안 M01) — 두 줄 64px.
 *   1줄 20  상태 아이콘 16(읽지 않음은 둘레 링) · 에이전트 14 · 이름 15/600 · 위임 토글 · 경과(mono 12, 오른쪽 끝)
 *   2줄 16  (왼쪽 24 들여) 상태 키워드 12/600 · 도구·배지 · 위치(WT · 디렉터리 · 브랜치, mono) · 변경량 · 글리프(오른쪽)
 * 개입 필요 구역의 열린 waiting 카드만 테두리·왼쪽 3px 띠·질문 한 줄(터미널 색)·44px 답 버튼을 갖는다.
 * 부품(키워드·공유·만든 곳·다른 화면·위임 표시)은 데스크톱 레일 행과 같은 컴포넌트(RailRow.tsx)다.
 */
export function FleetRow(props: {
  row: SessionRow;
  /** 인라인 승인 버튼(개입 필요 구역의 행에만). */
  answers?: boolean;
  /** 위임 하위 세션(레일과 같은 규칙 — 키워드 대신 위임 상태). */
  kid?: boolean;
  entry?: TreeEntry<SessionRow>; kidsCollapsed?: boolean; onToggleKids?: () => void;
  onOpen: () => void; onLongPress: () => void;
  onAnswer: (key: string) => Promise<void>; onSeen: () => void;
}) {
  const [now, setNow] = createSignal(Date.now());
  const clock = setInterval(() => setNow(Date.now()), 30000);
  onCleanup(() => clearInterval(clock));
  const diff = () => diffLabel(props.row);
  const elapsed = () => formatRelativeTime(props.row.since, now());
  const card = () => !!props.answers && props.row.status === 'waiting' && props.row.open;

  const longPress = useLongPress(props.onLongPress);

  const onRowClick = () => {
    // 꾹 눌러 메뉴가 막 떴으면 뒤이어 오는 click은 삼킨다 — 안 그러면 메뉴가
    // 뜬 그 손가락을 떼는 순간 행이 열리기까지 한다(dnd.js의 `_suppressNextClick`과
    // 같은 관용구, 별도 타이머 없이 `fired` 플래그 하나로 충분하다).
    if (longPress.fired()) return;
    // 열려 있지 않은 행은 탭하면 곧장 열어서 이동한다 — 확인/미확인 개념이 없다.
    if (!props.row.open) { props.onOpen(); return; }
    // 「완료 · 읽지 않음」 행은 탭하면 seen 처리(이동하지 않는다) — 나머지는 전부 터미널로 이동.
    // 어느 쪽이든 먼저 onSeen을 부른다(ackLocal이라 done→idle까지 내린다) — 안 그러면
    // 이미 「확인함」으로 넘어온 완료 행이 파란 tone을 못 푼다(2026-09-22).
    const ackOnly = props.row.status === 'done' && props.row.unseen;
    props.onSeen();
    if (!ackOnly) props.onOpen();
  };

  return (
    <div
      class="vt-srow lg vt-fleet-row"
      classList={{ 'is-waiting': props.row.status === 'waiting', closed: !props.row.open, 'vt-fleet-card': card(), kid: !!props.kid }}
      data-state={props.row.status}
      onClick={onRowClick}
      onPointerDown={longPress.onPointerDown}
      onPointerMove={longPress.onPointerMove}
      onPointerUp={longPress.onPointerUp}
      onPointerCancel={longPress.onPointerCancel}
      onContextMenu={(e) => { e.preventDefault(); props.onLongPress(); }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        onRowClick();
      }}
      role="button"
      tabindex="0"
    >
      <div class="vt-srow-main vt-fleet-row-main">
        <div class="vt-srow-top vt-fleet-row-top">
          <span class="vt-srow-state" innerHTML={stateIcon(props.row.status, 16, props.row.unseen && props.row.status !== 'idle')} />
          <Show when={props.row.agent}>
            {(a) => <span class="vt-srow-agent vt-fleet-agent" innerHTML={agentMark(a())} />}
          </Show>
          <span class="vt-srow-name vt-fleet-name">{props.row.name}</span>
          <Show when={props.entry}>{(e) => (
            <KidsToggle progress={kidsProgress(e().kids)} count={e().kids.length + e().closedKids.length}
              collapsed={!!props.kidsCollapsed} name={props.row.name} onToggle={props.onToggleKids} />
          )}</Show>
          <span class="vt-srow-elapsed vt-fleet-el">{elapsed()}</span>
        </div>
        <div class="vt-srow-sub vt-fleet-row-sub vt-fleet-line2">
          {/* 하위 행: 진행 중이면 에이전트 상태(작업 중·입력 대기)가 정보고, 끝났으면 위임 결과가 정보다 — 둘을 같이 쓰면
              「보고 · 성공 완료」처럼 같은 말이 겹친다(2026-10-13 실화면). */}
          <Show when={props.kid && props.row.lineage?.state !== 'running'} fallback={<KeywordLine row={props.row} now={now} noSince />}>
            <LineageMark row={props.row} />
          </Show>
          <span class="vt-fleet-loc">
            <Show when={props.row.contextStatus === 'unavailable'} fallback={<>
              <Show when={props.row.workspaceKind}><span class="vt-srow-workspace-kind" role="img" aria-label={props.row.workspaceKind === 'WT' ? t('별도 워크트리') : props.row.workspaceKind}>{props.row.workspaceKind}</span></Show>
              <Show when={props.row.directory}><span class="vt-srow-directory">{props.row.directory}</span></Show>
              <Show when={props.row.branch}><span class="vt-srow-branch">{props.row.branch}</span></Show>
            </>}>{t('정보 갱신 대기')}</Show>
          </span>
          <Show when={diff()}><span class="vt-srow-meta vt-fleet-diff">{diff()}</span></Show>
          <span class="vt-fleet-gls">
            <NativeChip row={props.row} />
            <ShareMark share={props.row.share} />
            <ScreensMark row={props.row} />
            <OriginMark origin={props.row.origin} host={props.row.originHost} />
          </span>
        </div>
        {/* 질문 한 줄 — 터미널에 뜬 것을 터미널 색으로. 열려 있지 않은 세션은 키를 보낼 곳(열린 pane)이 없다 — 인라인 승인 버튼 대신 탭해서 연다. */}
        <Show when={card()}>
          <Show when={props.row.question}>{(q) => <div class="vt-fleet-q" data-tip={q()}><span class="vt-fleet-q-mark" aria-hidden="true">$</span>{q()}</div>}</Show>
          <AnswerButtons row={props.row} variant="fleet" onAnswer={props.onAnswer} onTerminal={props.onOpen} />
        </Show>
      </div>
    </div>
  );
}

function Fleet(props: { deps: FleetDeps }) {
  const sessionsVersion = useSessionsVersion();
  const agentVersion = useAgentVersion();
  const layoutVersion = useLayoutVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  const [repos, setRepos] = createSignal<any[]>([]);
  // 행 메모가 agentVersion()을 구독하므로 에이전트 저장소가 바뀌면 다시 계산된다.
  const { prefs, set: setPref } = createRailPrefs();
  const [diffTick, setDiffTick] = createSignal(0);
  // 2026-09-30 — 세션 cwd의 변경 알림. 오면 캐시가 dirty가 되고, 다시 셀 수 있는
  // 때(rail-fetch.ts의 간격 규칙)에 한 번 다시 그려 그 행이 fetchDiffCount를 부르게 한다.
  const diffWatcher = createDiffWatcher(() => setDiffTick((n) => n + 1));
  createEffect(() => diffWatcher.sync(tmuxSessions().filter((t: any) => !t.context).map((t: any) => t.cwd)));
  onCleanup(() => diffWatcher.dispose());
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [hostMenuOpen, setHostMenuOpen] = createSignal(false);
  // 이 기기 이름(layout/env-chip.js가 html[data-vt-machine]에 두고 늦게 오면 'vt:machine'을 쏜다).
  const [machine, setMachine] = createSignal(document.documentElement.dataset.vtMachine || '');
  const onMachine = () => setMachine(document.documentElement.dataset.vtMachine || '');
  document.addEventListener('vt:machine', onMachine);
  onCleanup(() => document.removeEventListener('vt:machine', onMachine));
  // 2026-09-28(사용자 요청) — 스위처는 목록 필터가 아니라 화면 전체의 전환이다
  // (core/host-route.js, Rail.tsx와 같은 규칙). 세션 목록은 그 맥의 API에서 온다.
  const viewHostId = String((window as any).vtRelayHost?.() || LOCAL_HOST);
  const [catalog, setCatalog] = createSignal<CatalogSnapshot | null>(null);
  const [archiveOpen, setArchiveOpen] = createSignal(false);
  let catalogRequest = 0;
  const refreshCatalog = async (more = false) => {
    if (viewHostId !== LOCAL_HOST) return;
    const request = ++catalogRequest;
    try {
      const params = new URLSearchParams({ archived: String(archiveOpen()), limit: '200' });
      if (more && catalog()?.next) params.set('after', catalog()!.next!);
      const value = await historyApi('/api/session-catalog?' + params);
      if (request !== catalogRequest) return;
      setCatalog(more && catalog() ? { ...value, sessions: [...catalog()!.sessions, ...value.sessions] } : value);
    } catch (e) {
      // 옛 호스트는 지금의 라이브 목록을 그대로 쓴다. 답 자체가 없던 실패만 폴러에 알린다.
      if (isNoResponse(e)) throw e;
    }
  };
  // 갱신 지연 표식 — Rail.tsx와 같은 규칙(폴링·push로 마지막으로 새 값을 받은 시각).
  const mountedAt = Date.now();
  let lastOkAt = 0;
  const [stale, setStale] = createSignal(false);
  const checkStale = () => setStale(isStale(lastOkAt, CATALOG_POLL_MS, Date.now(), mountedAt));
  const markOk = () => { lastOkAt = Date.now(); checkStale(); };
  const onSettled = (ok: boolean) => { if (ok) markOk(); else checkStale(); };
  const staleTimer = setInterval(checkStale, 15000);
  onCleanup(() => clearInterval(staleTimer));

  // 첫 조회는 폭과 무관하게 한 번(예전과 같다), 그 뒤 주기 조회는 폰 폭에서만.
  let catalogFirst = true;
  const catalogPoll = createPoll(() => {
    if (!catalogFirst && window.innerWidth >= 720) return undefined;
    catalogFirst = false;
    return refreshCatalog();
  }, { interval: CATALOG_POLL_MS, immediate: true, onSettled });
  catalogPoll.start();
  const catalogChanged = () => { void refreshCatalog().catch(() => {}); };
  window.addEventListener('vt:catalog-changed', catalogChanged);
  onCleanup(() => { catalogPoll.stop(); window.removeEventListener('vt:catalog-changed', catalogChanged); });


  let tmuxRevision = 0;
  // 아래 refresh*는 폴러(core/poll.js)를 통해서만 불린다 — 실패를 던져 간격을 늘리게
  // 하고, 진행 중이면 겹쳐 쏘지 않는다. 플릿 탭이 아닐 때는 아무것도 안 하고 끝난다.
  const isFleetActive = () => document.documentElement.dataset.mobileTab === 'fleet';
  const refreshSessions = async () => {
    if (!isFleetActive()) return;
    const request = ++tmuxRevision;
    const list = await pollFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (request === tmuxRevision && Array.isArray(list)) setTmuxSessions(list);
  };
  const refreshAgent = async () => {
    if (!isFleetActive()) return;
    if (!(await syncAgentsFromServer(props.deps as RailDeps))) throw new Error('agent: no response');
  };
  // 브랜치·변경량(레일과 같은 행)의 출처.
  const refreshRepos = async () => {
    if (!isFleetActive()) return;
    const data = await pollFetch<{ repos?: any[] } | null>(props.deps, '/api/repos');
    if (data?.repos) setRepos(data.repos);
  };
  const refreshHosts = async () => {
    if (!isFleetActive()) return;
    try {
      const data = await pollFetch<{ hosts?: HostEntry[] } | null>(props.deps, '/api/hosts');
      if (data?.hosts) setHosts(data.hosts);
    } catch (e) {
      if (isNoResponse(e)) throw e;   // 옛 서버(404)는 실패가 아니다
    }
  };

  const sessionsPoll = createPoll(refreshSessions, { interval: SESSIONS_SAFETY_POLL_MS, onSettled });
  const agentPoll = createPoll(refreshAgent, { interval: AGENT_SAFETY_POLL_MS, onSettled });
  const hostsPoll = createPoll(refreshHosts, { interval: HOSTS_POLL_MS, onSettled });
  // 저장소 목록은 주기 조회가 없다(예전과 같다) — 탭을 열 때만. 그래도 겹쳐 쏘지 않게 폴러에 싣는다.
  const reposPoll = createPoll(refreshRepos, { interval: 24 * 60 * 60 * 1000, onSettled });
  const fleetPolls = [sessionsPoll, agentPoll, hostsPoll, reposPoll];
  for (const p of fleetPolls) p.start();
  const refreshAll = () => {
    if (document.hidden || !isFleetActive()) return;
    for (const p of fleetPolls) p.kick();
  };
  refreshAll();
  const onMobileTab = (e: Event) => {
    if ((e as CustomEvent).detail?.tab === 'fleet') refreshAll();
  };
  document.addEventListener('vt:mobile-tab', onMobileTab);
  const offSessions = onWorkspaceEvent('tmux_sessions', (msg: any) => {
    if (Array.isArray(msg?.sessions)) { tmuxRevision += 1; setTmuxSessions(msg.sessions); markOk(); }
  });
  onCleanup(() => {
    for (const p of fleetPolls) p.stop();
    document.removeEventListener('vt:mobile-tab', onMobileTab);
    offSessions();
  });

  const activeHost = createMemo(() => hosts().find((h) => h.id === viewHostId) || null);
  const viewHostOffline = createMemo(() => viewHostId !== LOCAL_HOST && !!activeHost() && !activeHost()!.online);

  const selectHost = (id: string) => {
    setHostMenuOpen(false);
    if (id !== viewHostId) (window as any).vtSwitchHost?.(id);
  };

  // 레일과 같은 행 조립·같은 구역(rail-fetch.ts · rail-data.ts). 개입 필요 구역의 행에만
  // 인라인 승인 버튼이 붙는다 — 같은 세션이 아래 「세션」에도 있지만 거기엔 버튼이 없다.
  const rows = createMemo<SessionRow[]>(() => {
    sessionsVersion(); agentVersion(); layoutVersion(); diffTick(); // 구독 트리거용
    return collectSessionRows({
      tmux: tmuxSessions(),
      repos: repos(),
      groups: (window as any).getSessionViewGroups?.() || (window as any).getTabGroups?.() || [],
      deps: props.deps,
      onDiff: () => setDiffTick((n) => n + 1),
    });
  });
  const lists = createMemo(() => buildRailLists(rows(), prefs()));
  // 「실행 중」 = 세션 전체에서 「개입 필요」에 올라간 행을 뺀 것(레일과 같은 규칙).
  const running = createMemo(() => splitRunning(lists()));
  // 실행 중 행 ↔ 카탈로그 세션(레일과 같은 규칙) — 메뉴의 작업 기록·이름 변경이 쓴다.
  const catalogOf = (row: SessionRow): any | undefined =>
    catalog()?.sessions.find((x) => x.ended == null && x.runtime_id && x.tmux_name === row.tmuxName && x.physical_key === row.physicalKey);
  // 실행 중 행에서 열면 tmux 이름도 넘긴다 — 작업 기록 창 왼쪽에 내부 서브에이전트, 오른쪽에 가장 최근 것을 고른다(시안 O15).
  const openRecordOf = async (session: any, tmuxName?: string | null) => {
    const { openSessionRecord } = await import('../history/session-record.js');
    openSessionRecord(session.id, () => { catalogPoll.kick(); }, { tmuxName: tmuxName || session.tmux_name || '' });
  };
  const viewGroups = createMemo(() => { layoutVersion(); return groupSessionRows(running(),
    (window as any).getSessionViewGroups?.() || (window as any).getTabGroups?.() || []); });
  // 레일과 같은 계보 트리·정렬(rail-data.ts). 폰에는 드래그가 없어 수동 순서는 행 시트의 위로·아래로 이동으로 바꾼다.
  const fleetTree = createMemo<TreeEntry<SessionRow>[]>(() =>
    buildSessionTree(viewGroups().filter((g) => !g.id).map((g) => g.rows[0]), prefs().sort, prefs().order));
  const fleetKey = (r: SessionRow) => r.tmuxName || r.name;
  const fleetOrder = () => fleetTree().map((e) => fleetKey(e.row));
  const fleetMove = (row: SessionRow, dir: -1 | 1) => setPref(ORDER_KEY, encodeOrder('manual', stepInOrder(fleetOrder(), fleetKey(row), dir)));
  const fleetMoveOpts = (row: SessionRow) => {
    const at = prefs().sort === 'manual' ? fleetOrder().indexOf(fleetKey(row)) : -1;
    return at < 0 ? undefined : { up: at > 0, down: at < fleetOrder().length - 1 };
  };
  const fleetFlip = (list: string[], name: string) => (list.includes(name) ? list.filter((n) => n !== name) : [...list, name]);

  const openRow = async (row: SessionRow) => {
    const w = window as any;
    // 2026-10-03(세션·뷰 규칙) — 레일 클릭과 같은 함수(term/session-actions.js
    // openSessionFromRail): 열린 세션은 그 자리로, 아니면 기억된 묶음 뷰를
    // 통째로 다시 열거나 새 단독 뷰로 연다.
    await w.vtOpenSessionFromRail?.({ sessionId: row.open ? row.sessionId : null, tmuxName: row.tmuxName || null, catalogSessionId: row.catalogSessionId, runtimeId: row.runtimeId });
    props.deps.onOpenTerminal();
  };

  // 답 보내기는 데스크톱 레일과 같은 함수(rail-fetch.ts answerSessionPrompt).
  const answerRow = (row: SessionRow, key: string): Promise<void> => answerSessionPrompt(props.deps, row, key);

  const seenRow = (row: SessionRow) => {
    // ackLocal — markSeen의 상위집합(unseen 해제 + done이면 idle로도 내림).
    // 데스크톱 레일(RailRow.tsx)의 data-session-id 배선과 같은 목적, 폰은
    // 세션 카드에 그 속성이 없어 같은 델리게이트를 못 타므로 직접 부른다.
    if (row.tmuxName) (window as any).ackLocal?.(row.tmuxName);
  };

  // 2026-09-23(사용자 요청) — 꾹 눌러 메뉴. 데스크톱 Rail.tsx의 ctxMenuItems와
  // 같은 액션(같은 window 브리지)을 쓰지만, 좌표 팝업(Rail.tsx의 Menu)이
  // 아니라 시트로 띄운다 — 손가락으로 여는 메뉴가 화면 밖으로 나가면 안 된다
  // (호스트 스위처 시트와 같은 이유). 이름 변경은 그 자리에서 시트 내용을
  // 입력창으로 바꾼다(Rail.tsx가 이미 지킨 규칙 — window.prompt 안 씀).
  const [menu, setMenu] = createSignal<{ row: SessionRow } | null>(null);

  // 「뷰 편집」 — 뷰 단위 순서·닫기. 예전 상단 세션 시트가 하던 일이다(시트는 이 탭으로 합쳤다).
  // 편집기 DOM은 layout/session-order.js(screens 청크)가 그린다 — 지연 청크는 레이아웃 모듈을
  // 정적 import 못 해서 열 때 동적으로 받고, 레이아웃이 바뀔 때마다 다시 그린다.
  const [editViews, setEditViews] = createSignal(false);
  let editorEl: HTMLDivElement | undefined;
  createEffect(() => {
    layoutVersion();
    if (!editViews()) return;
    void import('../layout/session-order.js').then((m) => {
      if (editorEl && editorEl.isConnected && editViews()) m.renderOrderEditor(editorEl);
    });
  });
  const [renaming, setRenaming] = createSignal(false);
  const [renameValue, setRenameValue] = createSignal('');

  const openMenu = (row: SessionRow) => { setRenaming(false); setMenu({ row }); };
  const closeMenu = () => { setMenu(null); setRenaming(false); };
  const runMenuAction = (fn: () => void) => { closeMenu(); fn(); };

  const startRename = () => {
    const m = menu(); if (!m) return;
    setRenameValue(m.row.name);
    setRenaming(true);
  };
  const commitRename = async () => {
    const m = menu(); if (!m) return;
    const trimmed = renameValue().trim();
    closeMenu();
    if (!trimmed || !m.row.sessionId) return;
    await (window as any).renameSession?.(m.row.sessionId, trimmed);
  };

  return (
    <div id="vt-fleet" aria-label={t('플릿')}>
      {/* 맨 위 도구 줄(시안 M01 상단바 오른쪽) — 이 기기 이름 · 대화 보관함 · 새 세션(44px 아이콘). 호스트가 둘 이상이면
          기기 이름 대신 아래 호스트 스위처가 그 역할을 한다. */}
      <div class="vt-fleet-toolbar">
        <span class="vt-fleet-machine">{hosts().length > 1 ? '' : machine()}</span>
        <button type="button" class="vt-icon-btn vt-fleet-tool" aria-label={t('대화 보관함')} data-tip={t('대화 보관함')}
          onClick={() => { void import('../history/panel.js').then(m => m.openHistory()); }} innerHTML={shellIcon('history', 20, 2)} />
        <button type="button" class="vt-icon-btn vt-fleet-tool" aria-label={t('새 세션')} data-tip={t('새 세션')}
          onClick={(e) => (props.deps.getAction('session.add-menu') as ((el?: HTMLElement, ev?: MouseEvent) => void) | undefined)?.(e.currentTarget, e)}
          innerHTML={icon('plus', 20, 2)} />
      </div>
      <Show when={stale()}>
        <span class="vt-stale-mark vt-fleet-stale" role="status" title={t('서버에서 새 값을 한동안 받지 못했습니다. 보이는 값은 마지막으로 받은 것입니다.')}>{t('갱신 지연')}</span>
      </Show>
      {/* C1 — 폰의 호스트 스위처. compact에서는 레일이 통째로 숨으므로 여기가
          유일한 진입점이다. 레일과 같은 규칙으로 호스트가 하나뿐이면 아예 안
          그린다. 좌표 팝업 대신 목록 위로 펼치는 시트 — 손가락으로 여는
          메뉴가 화면 밖으로 나가는 일이 없다. */}
      <Show when={hosts().length > 1}>
        <div class="vt-fleet-hostbar">
          <button
            type="button"
            class="vt-host-btn vt-fleet-host"
            classList={{ offline: activeHost() ? !activeHost()!.online : false, remote: viewHostId !== LOCAL_HOST, open: hostMenuOpen() }}
            aria-haspopup="listbox"
            aria-expanded={hostMenuOpen()}
            onClick={() => setHostMenuOpen((v) => !v)}
          >
            <span class="vt-fleet-host-name">{activeHost()?.label || viewHostId}</span>
            <span class="vt-fleet-host-detail">{activeHost() ? hostDetail(activeHost()!) : ''}</span>
            {/* 캐럿은 SVG 하나를 CSS로 뒤집는다(.open일 때 180도) — 글리프
                ▴▾는 폰트마다 중심이 달라 옆 글자와 광학 정렬이 안 맞았다. */}
            <span class="vt-fleet-host-caret" classList={{ open: hostMenuOpen() }}><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
          </button>
        </div>
        <Show when={hostMenuOpen()}>
          <div class="vt-fleet-hostmenu" role="listbox">
            <For each={buildHostMenu(hosts(), viewHostId)}>
              {(h) => (
                <button
                  type="button"
                  class="vt-fleet-hostitem"
                  classList={{ active: h.active, offline: !h.online }}
                  role="option"
                  aria-selected={h.active}
                  onClick={() => selectHost(h.id)}
                >
                  <span class="vt-fleet-hostitem-name">{h.active ? '✓ ' : ''}{h.label}</span>
                  <span class="vt-fleet-hostitem-detail">{h.detail}</span>
                </button>
              )}
            </For>
          </div>
        </Show>
      </Show>
      {/* 원격은 "세션이 없다"와 "연결이 안 됐다"를 구분해 보여준다(레일과 같은
          이유 — 둘을 뭉개면 "세션이 없는 건가?"로 읽힌다). */}
      <Show when={viewHostOffline()}>
        <div class="vt-fleet-empty">{hostDetail(activeHost()!)}</div>
      </Show>
      <Show when={rows().length === 0 && !catalog()?.sessions.length && !viewHostOffline()}>
        <div class="vt-fleet-empty">
          <div>{t('세션이 없습니다')}</div>
          <div class="vt-fleet-empty-actions">
            <button type="button" class="vt-btn solid" onClick={() => { (window as any).createTmuxSession?.(); props.deps.onOpenTerminal(); }}>{t('tmux 세션 시작')}</button>
            <button type="button" class="vt-btn" onClick={() => { (window as any).createSession?.(); props.deps.onOpenTerminal(); }}>{t('일반 터미널')}</button>
          </div>
        </div>
      </Show>
      {/* 개입 필요 — waiting 세션. 인라인 승인 버튼은 여기 행에만 붙는다. */}
      <Show when={!archiveOpen() && lists().attention.length > 0}>
        <div class="vt-fleet-group">
          <div class="vt-fleet-group-head is-wait">{t('개입 필요')}<span class="vt-fleet-group-n">{lists().attention.length}</span></div>
          <For each={lists().attention}>
            {(row) => (
              <FleetRow
                row={row}
                answers
                onOpen={() => { void openRow(row); }}
                onLongPress={() => openMenu(row)}
                onAnswer={(key) => answerRow(row, key)}
                onSeen={() => seenRow(row)}
              />
            )}
          </For>
        </div>
      </Show>
      <Show when={!archiveOpen() && !(prefs().view.byRepo && catalog()) && running().length > 0}>
        <div class="vt-fleet-group">
          <div class="vt-fleet-group-head vt-fleet-group-head-sort">
            <span>{t('실행 중')}<span class="vt-fleet-group-n">{running().length}</span></span>
            {/* 폰에도 정렬 전환이 있어야 수동 순서를 이 화면에서 시작할 수 있다(레일 머리와 같은 값) — 순서는 행 시트의 위로·아래로 이동. */}
            <div class="vt-rail-sort" role="radiogroup" aria-label={t('세션 정렬')}>
              <For each={[['name', t('이름순')], ['manual', t('수동')]] as ['name' | 'manual', string][]}>{([id, label]) => (
                <button type="button" role="radio" class="vt-rail-sort-opt" aria-checked={prefs().sort === id}
                  onClick={() => { if (prefs().sort !== id) setPref(ORDER_KEY, encodeOrder(id, id === 'manual' ? fleetOrder() : prefs().order)); }}>{label}</button>
              )}</For>
            </div>
            <button type="button" class="vt-icon-btn xl vt-fleet-edit-btn" classList={{ active: editViews() }}
              aria-pressed={editViews()} aria-label={t('뷰 편집')} data-tip={t('뷰 편집')}
              onClick={() => setEditViews((v) => !v)} innerHTML={icon('square-pen', 16)} />
          </div>
          <Show when={editViews()}><div class="vt-fleet-order" ref={(el) => { editorEl = el; }} /></Show>
          <Show when={!editViews()}>
          <For each={viewGroups().filter((g) => g.id)}>{(group) => (
            <ViewGroup group={group} onOpen={() => { void openRow(group.rows[0]); }}>
              <For each={group.rows}>{(row) => <FleetRow row={row}
                onOpen={() => { void openRow(row); }} onLongPress={() => openMenu(row)}
                onAnswer={() => Promise.resolve()} onSeen={() => seenRow(row)} />}</For>
            </ViewGroup>
          )}</For>
          <For each={fleetTree()}>{(entry) => {
            const collapsed = () => prefs().kidsCollapsed.includes(fleetKey(entry.row));
            const closedOpen = () => prefs().closedOpen.includes(fleetKey(entry.row));
            const kid = (row: SessionRow) => <FleetRow row={row} kid
              onOpen={() => { void openRow(row); }} onLongPress={() => openMenu(row)}
              onAnswer={() => Promise.resolve()} onSeen={() => seenRow(row)} />;
            return (
              <div class="vt-lin vt-lin-fleet" classList={{ 'has-kids': entry.kids.length + entry.closedKids.length > 0 && !collapsed() }}
                data-lane={entry.row.status}>
                <FleetRow row={entry.row} entry={entry} kidsCollapsed={collapsed()}
                  onToggleKids={() => setPref(LINEAGE_KEY, encodeLineagePrefs(fleetFlip(prefs().kidsCollapsed, fleetKey(entry.row)), prefs().closedOpen))}
                  onOpen={() => { void openRow(entry.row); }} onLongPress={() => openMenu(entry.row)}
                  onAnswer={() => Promise.resolve()} onSeen={() => seenRow(entry.row)} />
                <Show when={entry.kids.length + entry.closedKids.length > 0 && !collapsed()}>
                  <div class="vt-lin-kids" role="group" aria-label={t('{name}의 하위 세션', { name: entry.row.name })}>
                    <div class="vt-child-heading">{t('FarShell 위임 세션')}<span>{t('별도 터미널')}</span></div>
                    <For each={entry.kids}>{(k, i) => (
                      <div class="vt-lin-kid" classList={{
                        last: i() === entry.kids.length - 1 && entry.closedKids.length === 0,
                        merged: k.lineage?.state === 'reported', failed: k.lineage?.outcome === 'failed',
                      }}>{kid(k)}</div>
                    )}</For>
                    <Show when={entry.closedKids.length > 0}>
                      <div class="vt-lin-kid vt-lin-fold" classList={{ last: !closedOpen() }}>
                        <button type="button" class="vt-lin-fold-btn" aria-expanded={closedOpen()}
                          onClick={() => setPref(LINEAGE_KEY, encodeLineagePrefs(prefs().kidsCollapsed, fleetFlip(prefs().closedOpen, fleetKey(entry.row))))}>
                          <span class="vt-lin-fold-caret" innerHTML={icon('chevron-down', 12, 2)} />
                          {t('닫힌 하위 {n}', { n: entry.closedKids.length })}
                        </button>
                      </div>
                      <Show when={closedOpen()}>
                        <For each={entry.closedKids}>{(k, i) => (
                          <div class="vt-lin-kid closed" classList={{ last: i() === entry.closedKids.length - 1 }}>{kid(k)}</div>
                        )}</For>
                      </Show>
                    </Show>
                  </div>
                </Show>
              </div>
            );
          }}</For>
          </Show>
        </div>
      </Show>
      {/* 종료 세션 — 레일과 같은 구역(CatalogTree.tsx): 최근 종료 → 보관함 한 줄, 보관함을 열면 같은 자리가 보관 목록으로 바뀐다. */}
      <Show when={!viewHostOffline() && catalog()}>
        {data => <Show when={archiveOpen()} fallback={<>
          <Show when={prefs().view.byRepo} fallback={
            <RecentEnded data={data()} live={lists().sessions} count={prefs().view.recent} collapsed={prefs().view.recentCollapsed}
              onToggle={() => setPref(VIEW_KEY, encodeRailView({ ...prefs().view, recentCollapsed: !prefs().view.recentCollapsed }))}
              onChanged={() => { catalogPoll.kick(); }} />}>
            <CatalogTree data={data()} live={lists().sessions}
              renderLive={row => <FleetRow row={row} onOpen={() => { void openRow(row); }} onLongPress={() => openMenu(row)}
                onAnswer={() => Promise.resolve()} onSeen={() => seenRow(row)} />}
              onChanged={() => { catalogPoll.kick(); }} onMore={() => { void refreshCatalog(true).catch(() => {}); }} />
          </Show>
          <ArchiveEntry count={data().archived_count ?? 0} onOpen={() => { setArchiveOpen(true); catalogPoll.kick(); }} />
        </>}>
          <ArchiveList data={data()} onBack={() => { setArchiveOpen(false); catalogPoll.kick(); }}
            onChanged={() => { catalogPoll.kick(); }} onMore={() => { void refreshCatalog(true).catch(() => {}); }} />
        </Show>}
      </Show>
      {/* Rail.tsx 하단의 "+ 새 세션"과 같은 액션(session.add-menu) — 목록이
          비었을 때는 위 empty-state 버튼 둘이 이미 같은 일을 하므로 겹치지
          않게 뺀다. */}
      <Show when={lists().sessions.length > 0}>
        <button
          type="button"
          class="vt-fleet-add-btn"
          onClick={(e) => (props.deps.getAction('session.add-menu') as ((el?: HTMLElement, ev?: MouseEvent) => void) | undefined)?.(e.currentTarget, e)}
        >
          + {t('새 세션')}
        </button>
      </Show>
      {/* 꾹 눌러 메뉴 — 시트. 위 상단 주석 참고.
          ⚠ `m()`은 이 블록이 마운트된 동안만 유효한 접근자다 — `runMenuAction`이
          `closeMenu()`(= `setMenu(null)`, 이 <Show> 블록을 그 자리에서 정리)를
          먼저 부르고 그 다음에 넘겨받은 함수를 실행하므로, 콜백 안에서 그때
          가서 `m()`을 다시 읽으면 이미 정리된 뒤다(실측: 재우기가 UI 클릭으론
          아무 일도 안 하면서 콘솔 에러도 없었다 — 같은 함수를 `window`에서
          직접 부르면 멀쩡히 됐다). 그래서 값은 여기 진입 시점에 **한 번**
          꺼내 평범한 변수(`row`)에 담아 두고, 아래 모든 클릭
          핸들러는 그 변수만 읽는다 — 시트가 열려 있는 동안 대상이 바뀔 일은
          없다(다른 행을 길게 누르면 새 메뉴가 새로 열린다). */}
      <Show when={menu()}>
        {(m) => {
          const row = m().row;
          return (
            <div class="vt-session-backdrop vt-fleet-menu-backdrop" onClick={closeMenu}>
              <div class="vt-session-sheet vt-fleet-menu-sheet" onClick={(e) => e.stopPropagation()}>
                <Show when={!renaming()} fallback={
                  <>
                    <div class="vt-session-head"><h2>{t('이름 변경')}</h2></div>
                    <input
                      type="text"
                      class="vt-input"
                      value={renameValue()}
                      ref={(el) => { queueMicrotask(() => { el.focus(); el.select(); }); }}
                      onInput={(e) => setRenameValue(e.currentTarget.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') { e.preventDefault(); void commitRename(); }
                        else if (e.key === 'Escape') { e.preventDefault(); closeMenu(); }
                      }}
                    />
                    <div class="vt-dialog-actions">
                      <button type="button" class="vt-btn" onClick={closeMenu}>{t('취소')}</button>
                      <button type="button" class="vt-btn solid" onClick={() => void commitRename()}>{t('저장')}</button>
                    </div>
                  </>
                }>
                  <div class="vt-session-head"><h2>{row.name}</h2></div>
                  <MenuSummary summary={rowSummaryDetails(row)} />
                  {/* 항목은 레일 메뉴와 같은 목록(rowMenuItems)이다. 이름 변경만 시트 안에서 입력창으로
                      바뀌고, 나머지는 시트를 닫은 뒤 실행한다 — 콜백에서 m()을 다시 읽으면 이미
                      정리된 뒤라 값은 진입 시점에 한 번 꺼내 쓴다(2026-09-23 실측). */}
                  <div class="vt-session-list">
                    <For each={rowMenuItems(row, { split: !!(window as any).vtTouchSplitAvailable?.(row.sessionId || null), share: viewHostId === LOCAL_HOST, move: fleetMoveOpts(row), record: !!catalogOf(row) })}>
                      {(it) => (
                        <button
                          type="button"
                          class="vt-menu-item"
                          classList={{ danger: !!it.danger }}
                          onClick={() => {
                            if (it.id === 'record') { const linked = catalogOf(row); if (linked) runMenuAction(() => { void openRecordOf(linked, row.tmuxName); }); return; }
                            if (it.id === 'rename') { if (row.sessionId) startRename(); else { const linked = catalogOf(row); if (linked) runMenuAction(() => { void renameCatalogSession(linked).then(() => catalogPoll.kick()); }); } return; }
                            runMenuAction(() => runRowMenu(it.id, row, window, () => {}, (d) => fleetMove(row, d)));
                          }}
                        >
                          <Show when={ROW_MENU_ICON[it.id]}><span class="vt-menu-item-ico" innerHTML={shellIcon(ROW_MENU_ICON[it.id], 14, 2)} /></Show>
                          {it.label}{it.detail ? ` · ${it.detail}` : ''}
                        </button>
                      )}
                    </For>
                  </div>
                  <div class="vt-session-footer">
                    <button type="button" class="vt-menu-item vt-session-footer-item" onClick={closeMenu}>{t('취소')}</button>
                  </div>
                </Show>
              </div>
            </div>
          );
        }}
      </Show>
    </div>
  );
}

export function mountFleet(root: HTMLElement, deps: FleetDeps) {
  return render(() => <Fleet deps={deps} />, root);
}
