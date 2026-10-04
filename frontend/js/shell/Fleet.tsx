// N38(70-mobile.md §2) — 모바일 플릿 홈. compact(<720px) 하단 내비의 첫 탭
// (ui.mobile.home 기본값). Rail.tsx와 **같은 데이터·같은 구역 규칙**을 쓴다 —
// 행 조립(rail-fetch.ts의 collectSessionRows)과 구역(rail-data.ts의 buildRailLists:
// 개입 필요 · 세션, 이름순)이 하나라서 "데스크톱 레일에서 보이는 게 폰에서도 보인다".
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
import { agentMark } from './agent-mark.js';
// core 청크의 공유 인스턴스로 연결된다(shell.js가 core.js에서 import — 복제되지 않는다).
import { onWorkspaceEvent } from '../core/workspace-ws.js';
// git 배지 캐시·변경 알림 구독·행 조립은 레일과 **같은 것**을 쓴다(같은 shell 청크).
import {
  safeFetch, collectSessionRows, syncAgentsFromServer, createDiffWatcher, type RailDeps,
} from './rail-fetch.js';
import {
  buildRailLists, rowMenuItems, runRowMenu, diffLabel, worktreeIconName, type SessionRow,
} from './rail-data.js';
import { createRailPrefs } from './rail-prefs.js';
import { KeywordLine, OriginMark, ScreensMark } from './RailRow.js';
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

function Row(props: {
  row: SessionRow;
  /** 인라인 승인 버튼(개입 필요 구역의 행에만). */
  answers?: boolean;
  onOpen: () => void; onLongPress: () => void;
  onAnswer: (key: string) => Promise<void>; onSeen: () => void;
}) {
  const [now, setNow] = createSignal(Date.now());
  const clock = setInterval(() => setNow(Date.now()), 30000);
  onCleanup(() => clearInterval(clock));
  const hasOptions = () => props.row.status === 'waiting' && props.row.options && props.row.options.length > 0;
  const diff = () => diffLabel(props.row);
  // P1-1(ADR-33) — 같은 화면의 이중 탭을 여기서 먼저 막는다(서버 409는
  // 그 다음 방어선 — 다른 화면·느린 네트워크가 만드는 경쟁까지는 이걸로 못
  // 막는다). 응답이 돌아오기 전까지 이 행의 버튼을 다시 못 누르게 한다.
  const [answering, setAnswering] = createSignal(false);
  const handleAnswer = async (key: string) => {
    if (answering()) return;
    setAnswering(true);
    try {
      await props.onAnswer(key);
    } finally {
      setAnswering(false);
    }
  };

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
      classList={{ 'is-waiting': props.row.status === 'waiting', closed: !props.row.open }}
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
          <Show when={props.row.open}>
            <span class="vt-srow-open" role="img" aria-label="열려 있음" innerHTML={icon('app-window', 13, 2)} />
          </Show>
        </div>
        <Show when={props.row.branch || diff()}>
          <div class="vt-srow-sub vt-fleet-row-sub vt-srow-line2">
            <Show when={props.row.branch}>
              <span
                class="vt-srow-wt"
                role="img"
                aria-label={props.row.isMainWorktree ? '메인 워크트리' : '워크트리'}
                innerHTML={icon(worktreeIconName(props.row), 12, 2)}
              />
              <span class="vt-srow-branch">{props.row.branch}</span>
            </Show>
            <Show when={diff()}><span class="vt-srow-meta vt-fleet-diff">{diff()}</span></Show>
          </div>
        </Show>
        <div class="vt-srow-sub vt-fleet-row-sub vt-srow-line3">
          <KeywordLine row={props.row} now={now} />
          <OriginMark origin={props.row.origin} />
        </div>
        <Show when={props.row.viewLabel || props.row.otherScreens > 0 || props.row.otherClients > 0}>
          <div class="vt-srow-sub vt-fleet-row-sub vt-srow-line4">
            <Show when={props.row.viewLabel}><span class="vt-srow-view">{props.row.viewLabel}</span></Show>
            <ScreensMark row={props.row} />
          </div>
        </Show>
        {/* 열려 있지 않은 세션은 키를 보낼 곳(열린 pane)이 없다 — 인라인 승인 버튼 대신 탭해서 연다. */}
        <Show when={props.answers && props.row.status === 'waiting' && props.row.open}>
          <div class="vt-fleet-answers" onClick={(e) => e.stopPropagation()}>
            <Show
              when={hasOptions()}
              fallback={
                <button type="button" class="vt-btn sm solid" onClick={props.onOpen}>
                  터미널로
                </button>
              }
            >
              <For each={props.row.options}>
                {(opt) => (
                  <button type="button" class="vt-btn sm" disabled={answering()} onClick={() => void handleAnswer(opt.key)}>
                    {opt.key} · {opt.label}
                  </button>
                )}
              </For>
            </Show>
          </div>
        </Show>
      </div>
    </div>
  );
}

function Fleet(props: { deps: FleetDeps }) {
  const sessionsVersion = useSessionsVersion();
  const agentVersion = useAgentVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  const [repos, setRepos] = createSignal<any[]>([]);
  // 행 메모가 agentVersion()을 구독하므로 에이전트 저장소가 바뀌면 다시 계산된다.
  const { prefs } = createRailPrefs();
  const [diffTick, setDiffTick] = createSignal(0);
  // 2026-09-30 — 세션 cwd의 변경 알림. 오면 캐시가 dirty가 되고, 다시 셀 수 있는
  // 때(rail-fetch.ts의 간격 규칙)에 한 번 다시 그려 그 행이 fetchDiffCount를 부르게 한다.
  const diffWatcher = createDiffWatcher(() => setDiffTick((n) => n + 1));
  createEffect(() => diffWatcher.sync(tmuxSessions().map((t: any) => t && t.cwd)));
  onCleanup(() => diffWatcher.dispose());
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [hostMenuOpen, setHostMenuOpen] = createSignal(false);
  // 2026-09-28(사용자 요청) — 스위처는 목록 필터가 아니라 화면 전체의 전환이다
  // (core/host-route.js, Rail.tsx와 같은 규칙). 세션 목록은 그 맥의 API에서 온다.
  const viewHostId = String((window as any).vtRelayHost?.() || LOCAL_HOST);

  const refreshSessions = async () => {
    const list = await safeFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (list) setTmuxSessions(list);
  };
  const refreshAgent = () => syncAgentsFromServer(props.deps as RailDeps);
  // 브랜치·변경량(레일과 같은 행)의 출처.
  const refreshRepos = async () => {
    const data = await safeFetch<{ repos?: any[] }>(props.deps, '/api/repos');
    if (data?.repos) setRepos(data.repos);
  };
  const refreshHosts = async () => {
    const data = await safeFetch<{ hosts?: HostEntry[] }>(props.deps, '/api/hosts');
    if (data?.hosts) setHosts(data.hosts);
  };

  const isFleetActive = () => document.documentElement.dataset.mobileTab === 'fleet';
  const refreshAll = () => {
    if (document.hidden || !isFleetActive()) return;
    void refreshSessions();
    void refreshAgent();
    void refreshRepos();
    void refreshHosts();
  };
  refreshAll();
  const t1 = setInterval(() => { if (!document.hidden && isFleetActive()) void refreshSessions(); }, SESSIONS_SAFETY_POLL_MS);
  const t2 = setInterval(() => { if (!document.hidden && isFleetActive()) void refreshAgent(); }, AGENT_SAFETY_POLL_MS);
  const t3 = setInterval(() => { if (!document.hidden && isFleetActive()) void refreshHosts(); }, HOSTS_POLL_MS);
  const onMobileTab = (e: Event) => {
    if ((e as CustomEvent).detail?.tab === 'fleet') refreshAll();
  };
  document.addEventListener('vt:mobile-tab', onMobileTab);
  const offSessions = onWorkspaceEvent('tmux_sessions', (msg: any) => {
    if (Array.isArray(msg?.sessions)) setTmuxSessions(msg.sessions);
  });
  onCleanup(() => {
    clearInterval(t1); clearInterval(t2); clearInterval(t3);
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
    sessionsVersion(); agentVersion(); diffTick(); // 구독 트리거용
    return collectSessionRows({
      tmux: tmuxSessions(),
      repos: repos(),
      groups: (window as any).getTabGroups?.() || [],
      deps: props.deps,
      onDiff: () => setDiffTick((n) => n + 1),
    });
  });
  const lists = createMemo(() => buildRailLists(rows(), prefs()));

  const openRow = async (row: SessionRow) => {
    const w = window as any;
    // 2026-10-03(세션·뷰 규칙) — 레일 클릭과 같은 함수(term/session-actions.js
    // openSessionFromRail): 열린 세션은 그 자리로, 아니면 기억된 묶음 뷰를
    // 통째로 다시 열거나 새 단독 뷰로 연다.
    await w.vtOpenSessionFromRail?.({ sessionId: row.open ? row.sessionId : null, tmuxName: row.tmuxName || null });
    props.deps.onOpenTerminal();
  };

  const answerRow = (row: SessionRow, key: string): Promise<void> => {
    // Codex의 y/p는 문자 선택 뒤 Enter로 확정하지만 `esc`는 문자열 "esc"가
    // 아니라 실제 ESC 바이트여야 취소된다. 그대로 보내면 프롬프트 입력란에
    // e·s·c가 찍히고 waiting이 풀리지 않는다.
    const text = key === 'esc' ? '\x1b' : `${key}\r`;
    // P1-1(ADR-33) — 토큰이 있으면 함께 보낸다. 서버가 먼저 도착한 요청만
    // 통과시키고 나머지는 409로 떨어뜨린다(docs/plan-2.3/10-approval-race.md
    // §3.3). promptId가 없으면(구버전 서버 등) 필드 자체를 안 보내 기존
    // 동작 그대로 통과한다.
    const body: Record<string, string> = row.promptId ? { text, prompt_id: row.promptId } : { text };
    return props.deps.vtFetch(`/api/sessions/${encodeURIComponent(row.sessionId)}/keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(() => {}).catch(() => {
      // 409(경쟁에서 짐)도 그 외 실패도 여기서 조용히 넘긴다 — 재시도하지
      // 않는다. 다음 폴링(agent status)이 화면을 정정한다.
    });
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
    <div id="vt-fleet" aria-label="플릿">
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
      <Show when={rows().length === 0 && !viewHostOffline()}>
        <div class="vt-fleet-empty">
          <div>세션이 없습니다</div>
          <div class="vt-fleet-empty-actions">
            <button type="button" class="vt-btn solid" onClick={() => { (window as any).createTmuxSession?.(); props.deps.onOpenTerminal(); }}>tmux 세션 시작</button>
            <button type="button" class="vt-btn" onClick={() => { (window as any).createSession?.(); props.deps.onOpenTerminal(); }}>일반 터미널</button>
          </div>
        </div>
      </Show>
      {/* 개입 필요 — waiting 세션. 인라인 승인 버튼은 여기 행에만 붙는다. */}
      <Show when={lists().attention.length > 0}>
        <div class="vt-fleet-group">
          <div class="vt-fleet-group-head">개입 필요 · {lists().attention.length}</div>
          <For each={lists().attention}>
            {(row) => (
              <Row
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
      <Show when={lists().sessions.length > 0}>
        <div class="vt-fleet-group">
          <div class="vt-fleet-group-head">세션 · {lists().sessions.length}</div>
          <For each={lists().sessions}>
            {(row) => (
              <Row
                row={row}
                onOpen={() => { void openRow(row); }}
                onLongPress={() => openMenu(row)}
                onAnswer={() => Promise.resolve()}
                onSeen={() => seenRow(row)}
              />
            )}
          </For>
        </div>
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
          + 새 세션
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
                    <div class="vt-session-head"><h2>이름 변경</h2></div>
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
                      <button type="button" class="vt-btn" onClick={closeMenu}>취소</button>
                      <button type="button" class="vt-btn solid" onClick={() => void commitRename()}>저장</button>
                    </div>
                  </>
                }>
                  <div class="vt-session-head"><h2>{row.name}</h2></div>
                  {/* 항목은 레일 메뉴와 같은 목록(rowMenuItems)이다. 이름 변경만 시트 안에서 입력창으로
                      바뀌고, 나머지는 시트를 닫은 뒤 실행한다 — 콜백에서 m()을 다시 읽으면 이미
                      정리된 뒤라 값은 진입 시점에 한 번 꺼내 쓴다(2026-09-23 실측). */}
                  <div class="vt-session-list">
                    <For each={rowMenuItems(row)}>
                      {(it) => (
                        <button
                          type="button"
                          class="vt-menu-item"
                          classList={{ danger: !!it.danger }}
                          onClick={() => {
                            if (it.id === 'rename') { startRename(); return; }
                            runMenuAction(() => runRowMenu(it.id, row, window, () => {}));
                          }}
                        >
                          {it.label}{it.detail ? ` · ${it.detail}` : ''}
                        </button>
                      )}
                    </For>
                  </div>
                  <div class="vt-session-footer">
                    <button type="button" class="vt-menu-item vt-session-footer-item" onClick={closeMenu}>취소</button>
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
