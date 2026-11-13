// 모바일 플릿 홈 — 저장소 묶음 2열 타일(세션 로직 점검 단계 7, 시안 5차 T1). compact(<720px) 하단 내비의 첫 탭.
//
//   [폴더] farshell  ⑂ dev                 2  [+]
//   ┌──────────┐ ┌──────────┐
//   │(링)   2분 │ │(링)  12초 │      ← 타일: 상태 링 + 에이전트 마크(작업 중 내부 에이전트 수 배지) · 경과
//   │결제 버그  │ │훅 정리    │      ← 보이는 이름
//   │승인 요청 npm│ │Edit app.ts│     ← 할 말이 있을 때만(대기 유형·도구·확인 안 함·마지막 지시), 없으면 변경량
//   │⑂ fix/x    │ │           │      ← 워크트리 가지면 브랜치
//   └──────────┘ └──────────┘
//   ┌ ─ ─ ─ ─ ┐
//     ◌ 종료 2                         ← 누르면 그 저장소의 종료 세션 시트(이어서 시작 · 기록)
//   └ ─ ─ ─ ─ ┘
//   [집] 저장소 밖 …
//   [보관함 12 ›]
//
// 데이터·묶는 규칙은 데스크톱 레일과 하나다(rail-fetch.ts 행 조립, repo-tree.ts 저장소 묶음). 목록 안의 답 버튼·정렬·
// 위임 레인은 없다 — 답은 터미널에서, 위임 하위 세션은 부모 바로 뒤 타일(↳ 부모 이름). 꾹 누르면 행 메뉴 시트.
//
// Rail.tsx와 같은 이유로 데이터 fetch(세션 목록·agent 상태·git diff)를 여기서 다시 부른다(정적 import 금지 — 지연 청크가
// core/store.js 등 상태 모듈을 정적 import하면 Vite lib 모드가 복제한다, ADR-26).
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render, Portal } from 'solid-js/web';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { t } from '../core/i18n.js';
import { ArchiveView, ArchiveEntry, mergeCatalogPage, type CatalogSnapshot } from './ArchiveView.js';
import { EndedItem } from './EndedFold.js';
import { historyApi } from '../history/api.js';
import { renameSession as renameCatalogSession, agoLabel } from '../history/session-actions.js';
import { agentMark } from './agent-mark.js';
import { ringSvg, subBadge } from './compact-mark.js';
import { normalizeSubagents } from './native-subagents.js';
// core 청크의 공유 인스턴스로 연결된다(shell.js가 core.js에서 import — 복제되지 않는다).
import { onWorkspaceEvent } from '../core/workspace-ws.js';
// 상태 없는 순수 모듈 — 지연 청크에 복제돼도 된다.
import { createPoll, isStale } from '../core/poll.js';
// git 배지 캐시·변경 알림 구독·행 조립은 레일과 **같은 것**을 쓴다(같은 shell 청크).
import {
  collectSessionRows, syncAgentsFromServer, createDiffWatcher, useLayoutVersion, pollFetch, isNoResponse, type RailDeps,
} from './rail-fetch.js';
import {
  buildRailLists, rowMenuItems, rowSummaryDetails, runRowMenu, renameViewGroup, formatRelativeTime, nodeLine, kidLabel, type SessionRow,
} from './rail-data.js';
import { buildRepoTree, endedActivity, OUTSIDE_KEY, type RepoGroup } from './repo-tree.js';
import { createRailPrefs } from './rail-prefs.js';
import { onSheetKeyDown } from './sheet-keys.js';
import { shortenPath } from './rail-hover-card.js';
import { MenuSummary, ShareMark, ShellsMark, ROW_MENU_ICON } from './RailRow.js';
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
// 카탈로그(종료 세션·자리·최근 활동 — 로컬·폰 폭에서만). 가장 짧은 주기라 갱신 지연 판정의 기준이다.
const CATALOG_POLL_MS = 5000;

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

/** 타일 하나에 그릴 것 — 저장소 묶음을 펼친 순서(가지 → 세션 → 그 하위 세션). */
export interface TileEntry {
  row: SessionRow;
  /** 워크트리 가지면 그 브랜치. */
  worktree: string | null;
  /** 저장소 밖의 폴더 가지면 그 폴더(절대 경로). */
  folder?: string | null;
  parent: SessionRow | null;
}

/** 저장소 묶음 → 타일 순서. 메인 가지가 먼저, 워크트리 가지의 세션은 브랜치 표시, 위임 하위는 부모 바로 뒤. */
export function tilesOf(group: RepoGroup<SessionRow, any>): TileEntry[] {
  const out: TileEntry[] = [];
  for (const lane of group.lanes) {
    const folder = lane.worktree?.folder || null;
    const wt = lane.worktree && !folder ? (lane.worktree.branch || lane.worktree.name) : null;
    for (const s of lane.sessions) {
      out.push({ row: s.row, worktree: wt, folder, parent: null });
      // 자손은 깊이 우선으로 온다(repo-tree.ts) — 「↳ 부모」는 실제 위임한 세션(손자면 그 위의 하위 세션)이다.
      const byName = new Map([s.row, ...s.kids].map((r) => [r.tmuxName || '', r] as const));
      for (const k of s.kids) out.push({ row: k, worktree: wt, folder, parent: byName.get(k.lineage?.parent || '') || s.row });
    }
  }
  return out;
}

/** 세션 타일(시안 T1). 누르면 열고(완료·확인 안 함이면 확인만), 꾹 누르면 행 메뉴 시트. */
export function FleetTile(props: {
  entry: TileEntry; onOpen: () => void; onLongPress: () => void; onSeen: () => void;
}) {
  const [now, setNow] = createSignal(Date.now());
  const clock = setInterval(() => setNow(Date.now()), 30000);
  onCleanup(() => clearInterval(clock));
  const row = () => props.entry.row;
  const line = () => nodeLine(row());
  const changed = () => { const c = row().changed; return c && (c.add > 0 || c.del > 0) ? c : null; };
  const sub = () => normalizeSubagents(row().subagents);
  const badge = () => subBadge(sub().items.filter((r: any) => r.state === 'working').length, sub().limited);
  const unseen = () => row().unseen && row().status !== 'idle';
  const kid = () => (props.entry.parent && row().lineage ? kidLabel(row().lineage!) : null);
  const longPress = useLongPress(props.onLongPress);
  const onClick = () => {
    // 꾹 눌러 메뉴가 막 떴으면 뒤이어 오는 click은 삼킨다(메뉴를 띄운 손가락을 떼는 순간 열리지 않게).
    if (longPress.fired()) return;
    if (!row().open) { props.onOpen(); return; }
    // 「완료 · 확인 안 함」 타일은 누르면 확인만 한다(이동하지 않는다) — 나머지는 터미널로 이동.
    const ackOnly = row().status === 'done' && row().unseen;
    props.onSeen();
    if (!ackOnly) props.onOpen();
  };
  return (
    <div
      class="vt-tile"
      classList={{ closed: !row().open, kid: !!props.entry.parent }}
      data-state={row().status}
      role="button" tabindex="0"
      aria-label={row().name}
      onClick={onClick}
      onPointerDown={longPress.onPointerDown}
      onPointerMove={longPress.onPointerMove}
      onPointerUp={longPress.onPointerUp}
      onPointerCancel={longPress.onPointerCancel}
      onContextMenu={(e) => { e.preventDefault(); props.onLongPress(); }}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onClick(); } }}
    >
      <div class="vt-tile-top">
        <span class="vt-tile-ring" data-state={row().status}>
          <span class="vt-tile-ring-svg" innerHTML={ringSvg(row().status)} />
          <span class="vt-tile-mark" innerHTML={agentMark(row().agent || 'shell')} />
          <Show when={unseen()}><span class="vt-tile-unseen" aria-label={t('아직 확인 안 함')} /></Show>
          <Show when={badge()}>{(b) => <span class="vt-tile-badge" classList={{ limited: b().limited }}>{b().text}</span>}</Show>
        </span>
        <ShellsMark shells={row().shells} />
        <span class="vt-tile-sp" />
        <span class="vt-tile-tm" classList={{ waiting: row().status === 'waiting' }}>{formatRelativeTime(row().since, now())}</span>
      </div>
      <div class="vt-tile-namerow"><b class="vt-tile-name">{row().name}</b><ShareMark share={row().share} /></div>
      <Show when={line()} fallback={
        <span class="vt-tile-line">
          <Show when={changed()} fallback={'\u00a0'}>
            {(c) => <><Show when={c().add > 0}><span class="vt-diff-add">+{c().add}</span></Show>{' '}<Show when={c().del > 0}><span class="vt-diff-del">−{c().del}</span></Show></>}
          </Show>
        </span>
      }>
        {(l) => <span class="vt-tile-line" data-tone={l().tone} data-tip={row().question || undefined}>{l().text}<Show when={l().detail}><span class="mono">{' '}{l().detail}</span></Show></span>}
      </Show>
      <Show when={props.entry.parent}>
        {(p) => <span class="vt-tile-sub" data-tone={kid()?.tone}>↳ {p().name}<Show when={kid()}>{(k) => <> · {k().text}</>}</Show></span>}
      </Show>
      <Show when={!props.entry.parent && props.entry.worktree}>
        <span class="vt-tile-wt"><span innerHTML={shellIcon('git-fork', 11, 2)} /><span class="vt-tile-wt-name">{props.entry.worktree}</span></span>
      </Show>
      {/* 저장소 밖 세션 — 지금 폴더(홈은 ~, 길면 가운데 줄임). 저장소가 없으니 이 줄이 위치다. */}
      <Show when={!props.entry.parent && props.entry.folder}>
        {(f) => <span class="vt-tile-wt vt-tile-dir" title={f()}><span innerHTML={shellIcon('folder', 11, 2)} /><span class="vt-tile-wt-name">{shortenPath(f(), 22)}</span></span>}
      </Show>
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
  const { prefs } = createRailPrefs();
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
  // 「종료 세션 더 불러오기」로 첫 쪽(200개) 뒤를 받았는가 — 받아 둔 뒤쪽은 폴링에 버리지 않는다(레일과 같은 규칙).
  let extraPages = false;
  const refreshCatalog = async (more = false) => {
    if (viewHostId !== LOCAL_HOST) return;
    const request = ++catalogRequest;
    try {
      // 보관된 세션은 보관함 화면(ArchiveView)이 따로 읽는다 — 여기는 실행 중·종료(최근 활동순).
      const params = new URLSearchParams({ archived: 'false', limit: '200', order: 'activity' });
      if (more && catalog()?.next) params.set('cursor', catalog()!.next!);
      const value = await historyApi('/api/session-catalog?' + params);
      if (request !== catalogRequest) return;
      setCatalog(mergeCatalogPage(catalog(), value, more, extraPages));
      if (more) extraPages = true;
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
  // 만든 곳별 표시 설정(레일 설정 「보기」)은 같이 따른다. 개입 필요 구역은 따로 두지 않는다 — 입력 대기 타일은
  // 호박색 테두리이고, 그 저장소가 맨 위로 온다(repo-tree.ts 정렬).
  const lists = createMemo(() => buildRailLists(rows(), prefs()));
  // 실행 중 행 ↔ 카탈로그 세션(레일과 같은 규칙) — 메뉴의 작업 기록·이름 변경이 쓴다.
  const catalogOf = (row: SessionRow): any | undefined =>
    catalog()?.sessions.find((x) => x.ended == null && x.runtime_id && x.tmux_name === row.tmuxName && x.physical_key === row.physicalKey);
  // 실행 중 행에서 열면 tmux 이름도 넘긴다 — 작업 기록 창 왼쪽에 내부 서브에이전트, 오른쪽에 가장 최근 것을 고른다(시안 O15).
  const openRecordOf = async (session: any, tmuxName?: string | null) => {
    const { openSessionRecord } = await import('../history/session-record.js');
    openSessionRecord(session.id, () => { catalogPoll.kick(); }, { tmuxName: tmuxName || session.tmux_name || '' });
  };
  const mainBranches = createMemo(() => {
    const m = new Map<string, string | null>();
    for (const repo of repos()) {
      const main = (repo.worktrees || []).find((w: any) => w.isMain);
      if (main && typeof main.path === 'string') m.set(main.path, main.branch || null);
    }
    return m;
  });
  const activityByKey = createMemo(() => {
    const m = new Map<string, number>();
    for (const c of catalog()?.sessions || []) if (c.physical_key && c.ended == null) m.set(c.physical_key, Number(c.last_activity) || 0);
    return m;
  });
  const tree = createMemo<RepoGroup<SessionRow, any>[]>(() => buildRepoTree({
    rows: lists().sessions,
    ended: viewHostId === LOCAL_HOST ? catalog()?.sessions || [] : [],
    workspaces: catalog()?.workspaces || [],
    projects: catalog()?.projects || [],
    mainBranches: mainBranches(),
    activityOf: (r) => (r.physicalKey && activityByKey().get(r.physicalKey)) || Number(r.since || 0),
    isLive: (c: any) => c.ended == null && !!c.runtime_id && rows().some((r) => r.tmuxName === c.tmux_name && r.physicalKey === c.physical_key),
  }));
  // 「종료 N」 타일을 누르면 그 저장소의 종료 세션 시트(열쇠 = 저장소 묶음 key).
  // 닫으면 누른 「종료 N」 타일로 포커스를 돌려준다(꾹 눌러 메뉴 시트와 같은 규칙, 아래 openMenu).
  const [endedSheet, setEndedSheet] = createSignal<string | null>(null);
  const endedGroup = () => tree().find((g) => g.key === endedSheet()) || null;
  let endedReturn: HTMLElement | null = null;
  const openEnded = (key: string) => { endedReturn = document.activeElement as HTMLElement | null; setEndedSheet(key); };
  const closeEnded = () => {
    const key = endedSheet();
    setEndedSheet(null);
    const back = endedReturn; endedReturn = null;
    queueMicrotask(() => {
      // 그 사이 「종료 N」 타일이 사라졌으면(마지막 종료 세션을 보관·이어서 시작) 같은 저장소 구역, 그것도 없으면 목록으로.
      const section = key ? Array.from(document.querySelectorAll<HTMLElement>('#vt-fleet .vt-fleet-repo')).find((x) => x.dataset.repo === key) : undefined;
      const target = back?.isConnected ? back
        : section?.querySelector<HTMLElement>('.vt-tile, .vt-fleet-repo-new')
          ?? document.querySelector<HTMLElement>('#vt-fleet .vt-tile, #vt-fleet .vt-fleet-tool');
      target?.focus();
    });
  };
  // 폴링으로 그 저장소의 종료 세션이 모두 사라지면 시트가 저절로 없어진다 — 그때도 닫기와 같은 길로 포커스를 돌려준다.
  createEffect(() => { if (endedSheet() && !endedGroup()) closeEnded(); });

  const openRow = async (row: SessionRow) => {
    const w = window as any;
    // 레일 클릭과 같은 함수(term/session-actions.js openSessionFromRail): 열린 세션은 그 자리로, 아니면 기억된
    // 묶음 뷰를 통째로 다시 열거나 새 단독 뷰로 연다.
    await w.vtOpenSessionFromRail?.({ sessionId: row.open ? row.sessionId : null, tmuxName: row.tmuxName || null, catalogSessionId: row.catalogSessionId, runtimeId: row.runtimeId });
    props.deps.onOpenTerminal();
  };
  const newIn = (root: string) => {
    (props.deps.getAction('session.create-in') as ((cwd: string) => void) | undefined)?.(root);
    props.deps.onOpenTerminal();
  };

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

  // 꾹 누른 손가락 밑에서 OS가 방금 단어를 선택해 두었을 수 있다(안드로이드 크롬) — 시트가 그 자리에 뜨면 제목에 선택·핸들이 남는다.
  // 시트·행의 user-select:none(98-mobile.css)이 새로 생기는 선택을 막고, 이미 잡힌 DOM 선택은 여기서 지운다.
  // xterm의 선택은 DOM Range가 아니라 자체 모델(term/touch-select.js)이라 이 호출이 터미널 복사 선택을 건드리지 않는다.
  // 시트를 닫으면 누른 타일로 포커스를 돌려준다(키보드·스크린리더가 목록에서 길을 잃지 않게).
  let returnFocus: HTMLElement | null = null;
  const openMenu = (row: SessionRow) => {
    try { window.getSelection?.()?.removeAllRanges(); } catch { /* 선택 API가 없으면 CSS만으로 충분하다 */ }
    returnFocus = document.activeElement as HTMLElement | null;
    setRenaming(false); setMenu({ row });
  };
  const closeMenu = () => {
    setMenu(null); setRenaming(false);
    const back = returnFocus; returnFocus = null;
    if (back?.isConnected) queueMicrotask(() => back.focus());
  };
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
        <button type="button" class="vt-icon-btn vt-fleet-tool" classList={{ active: editViews() }} aria-pressed={editViews()}
          aria-label={t('뷰 편집')} data-tip={t('뷰 편집')} onClick={() => setEditViews((v) => !v)} innerHTML={icon('square-pen', 20, 2)} />
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
      {/* 「뷰 편집」 — 뷰 단위 순서·닫기(layout/session-order.js). 켜져 있으면 목록 대신 편집기를 보인다. */}
      <Show when={editViews()}><div class="vt-fleet-order" ref={(el) => { editorEl = el; }} /></Show>
      <Show when={!editViews() && !archiveOpen() && !viewHostOffline()}>
        {/* 저장소는 열쇠(문자열)로 돈다 — 폴링마다 새 묶음 객체가 와도 타일 DOM이 그대로다(꾹 누르는 중인 손가락·포커스). */}
        <For each={tree().map((g) => g.key)}>{(key) => (
          <Show when={tree().find((g) => g.key === key)}>
            {(g) => (
              <section class="vt-fleet-repo" data-repo={g().key} classList={{ outside: g().key === OUTSIDE_KEY }} aria-label={g().repository ? g().name : t('저장소 밖')}>
                <div class="vt-fleet-repo-head">
                  <span class="vt-fleet-repo-ic" innerHTML={shellIcon(g().repository ? 'folder' : 'home', 14, 2)} />
                  <b class="vt-fleet-repo-name">{g().repository ? g().name : t('저장소 밖')}</b>
                  <Show when={g().mainBranch}><span class="vt-fleet-repo-branch" title={g().mainBranch!}><span innerHTML={icon('git-branch', 11, 2)} /><span class="vt-fleet-repo-bn">{g().mainBranch}</span></span></Show>
                  <span class="vt-tile-sp" />
                  <Show when={g().count > 0}><span class="vt-fleet-repo-n">{g().count}</span></Show>
                  <Show when={g().repository && g().root && !viewHostOffline()}>
                    <button type="button" class="vt-icon-btn vt-fleet-repo-new" aria-label={t('{name}에서 새 세션', { name: g().name })}
                      onClick={() => newIn(g().root!)} innerHTML={icon('plus', 16, 2)} />
                  </Show>
                </div>
                <div class="vt-tile-grid">
                  <For each={tilesOf(g()).map((e) => e.row.tmuxName || e.row.sessionId || e.row.name)}>{(tk) => (
                    <Show when={tilesOf(g()).find((e) => (e.row.tmuxName || e.row.sessionId || e.row.name) === tk)}>
                      {(e) => <FleetTile entry={e()} onOpen={() => { void openRow(e().row); }}
                        onLongPress={() => openMenu(e().row)} onSeen={() => seenRow(e().row)} />}
                    </Show>
                  )}</For>
                  <Show when={g().ended.length > 0}>
                    <button type="button" class="vt-tile end" onClick={() => openEnded(g().key)}
                      aria-label={t('{name}의 종료 세션 {n}', { name: g().repository ? g().name : t('저장소 밖'), n: g().ended.length })}>
                      <span class="vt-tile-hol" aria-hidden="true" />
                      <b class="vt-tile-name">{t('종료 {n}', { n: g().ended.length })}</b>
                      <span class="vt-tile-line">{t('마지막 {ago}', { ago: agoLabel(endedActivity(g().ended[0])) })}</span>
                    </button>
                  </Show>
                </div>
              </section>
            )}
          </Show>
        )}</For>
        {/* 카탈로그 한 쪽(200개) 밖의 오래된 종료 세션 — 더 불러오면 각 저장소의 「종료 N」에 붙는다. */}
        <Show when={viewHostId === LOCAL_HOST && catalog()?.next}>
          <button type="button" class="vt-catalog-more-link" onClick={() => { void refreshCatalog(true).catch(() => {}); }}>
            {t('종료 세션 더 불러오기')}
          </button>
        </Show>
        <Show when={catalog()}>
          {(data) => <ArchiveEntry count={data().archived_count ?? 0} onOpen={() => setArchiveOpen(true)} />}
        </Show>
      </Show>
      {/* 보관함 — 같은 자리의 목록이 바뀐다(데스크톱과 같은 화면, 칸 모양은 .vt-rg-scope). */}
      <Show when={archiveOpen()}>
        <div class="vt-rg-scope vt-fleet-archive">
          <ArchiveView remote={viewHostId !== LOCAL_HOST} onBack={() => { setArchiveOpen(false); catalogPoll.kick(); }}
            onChanged={() => { catalogPoll.kick(); }} />
        </div>
      </Show>
      {/* 「종료 N」 시트 — 그 저장소의 종료 세션. 「이어서」가 늘 보이고, 누르면 연결된 대화·이어서 시작 미리보기. */}
      <Show when={endedGroup()}>
        {(g) => (
          <Portal>
            <div class="vt-session-backdrop vt-fleet-menu-backdrop" onClick={closeEnded}>
              <div class="vt-session-sheet vt-fleet-menu-sheet vt-rg-scope vt-fleet-ended-sheet" role="dialog" aria-modal="true"
                aria-label={t('종료 {n}', { n: g().ended.length })} onClick={(e) => e.stopPropagation()}
                ref={(el) => { queueMicrotask(() => el.querySelector<HTMLElement>('button')?.focus()); }}
                onKeyDown={(e) => { onSheetKeyDown(e, e.currentTarget, closeEnded); }}>
                <div class="vt-session-head"><h2>{g().repository ? g().name : t('저장소 밖')} · {t('종료 {n}', { n: g().ended.length })}</h2></div>
                <div class="vt-session-list">
                  <For each={g().ended.map((x: any) => x.id)}>{(id, i) => (
                    <Show when={g().ended.find((x: any) => x.id === id)}>
                      {(x) => <EndedItem session={x()} last={i() === g().ended.length - 1} onChanged={() => { catalogPoll.kick(); }} />}
                    </Show>
                  )}</For>
                </div>
                <div class="vt-session-footer">
                  <button type="button" class="vt-menu-item vt-session-footer-item" onClick={closeEnded}>{t('닫기')}</button>
                </div>
              </div>
            </div>
          </Portal>
        )}
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
      {/* 시트는 body로 Portal한다 — #vt-fleet-slot이 z-index 쌓임 맥락(500)이라 안에 두면 시트의 z-index가 하단 내비(650)를
          못 넘어, 시트 바닥(취소)이 내비에 가려 터치가 내비로 갔다(2026-11-10 실기기). 시안 M04처럼 막이 내비까지 덮는다. */}
      <Show when={menu()}>
        {(m) => {
          const row = m().row;
          return (
            <Portal>
            <div class="vt-session-backdrop vt-fleet-menu-backdrop" onClick={closeMenu}>
              {/* 시트는 대화상자다 — 열리면 첫 항목으로 포커스, Tab은 시트 안에서 돌고, Esc로 닫고, 닫으면 누른 타일로 돌아간다. */}
              <div class="vt-session-sheet vt-fleet-menu-sheet" role="dialog" aria-modal="true" aria-label={row.name}
                onClick={(e) => e.stopPropagation()}
                ref={(el) => { queueMicrotask(() => el.querySelector<HTMLElement>('.vt-menu-item, input')?.focus()); }}
                onKeyDown={(e) => { onSheetKeyDown(e, e.currentTarget, closeMenu); }}>
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
                    {/* 분할 화면 묶음에 든 세션 — 묶음 이름 바꾸기(레일 메뉴와 같은 함수). 타일을 누르면 묶음이 통째로 열리므로 「묶음 전체 열기」는 따로 두지 않는다. */}
                    <Show when={row.viewId && row.viewLabel}>
                      <button type="button" class="vt-menu-item" onClick={() => runMenuAction(() => { void renameViewGroup(row); })}>
                        <span class="vt-menu-item-ico" innerHTML={shellIcon('pencil', 14, 2)} />
                        {t('묶음 이름 바꾸기')} · {row.viewLabel}
                      </button>
                    </Show>
                    <For each={rowMenuItems(row, { split: !!(window as any).vtTouchSplitAvailable?.(row.sessionId || null), share: viewHostId === LOCAL_HOST, record: !!catalogOf(row) })}>
                      {(it) => (
                        <button
                          type="button"
                          class="vt-menu-item"
                          classList={{ danger: !!it.danger }}
                          onClick={() => {
                            if (it.id === 'record') { const linked = catalogOf(row); if (linked) runMenuAction(() => { void openRecordOf(linked, row.tmuxName); }); return; }
                            if (it.id === 'rename') { if (row.sessionId) startRename(); else { const linked = catalogOf(row); if (linked) runMenuAction(() => { void renameCatalogSession(linked).then(() => catalogPoll.kick()); }); } return; }
                            runMenuAction(() => runRowMenu(it.id, row, window, () => {}));
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
            </Portal>
          );
        }}
      </Show>
    </div>
  );
}

export function mountFleet(root: HTMLElement, deps: FleetDeps) {
  return render(() => <Fleet deps={deps} />, root);
}
