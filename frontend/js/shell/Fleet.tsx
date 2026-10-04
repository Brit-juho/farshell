// N38(70-mobile.md §2) — 모바일 플릿 홈. compact(<720px) 하단 내비의 첫 탭
// (ui.mobile.home 기본값). Rail.tsx(N36 §5)와 **같은 데이터 소스**를 쓴다 —
// 같은 그룹핑·정렬 로직(rail-data.ts의 buildRailSections)을 그대로 재사용해,
// "데스크톱 레일에서 맨 위인 게 폰에서도 맨 위"가 항상 성립하게 한다.
//
// Rail.tsx와 같은 이유로 데이터 fetch(세션 목록·agent 상태·git diff)를
// 직접 다시 구현한다(정적 import 금지 — 지연 청크가 core/store.js 등 상태
// 모듈을 정적 import하면 Vite lib 모드가 복제한다, ADR-26). Dock.tsx의 배지
// 폴링도 같은 이유로 각자 반복하고 있다 — 이 파일도 그 관행을 따른다.
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { agentLabel } from '../ui/icons.js';
import { agentMark } from './agent-mark.js';
// core 청크의 공유 인스턴스로 연결된다(shell.js가 core.js에서 import — 복제되지 않는다).
import { onWorkspaceEvent } from '../core/workspace-ws.js';
// git 배지 캐시·변경 알림 구독은 레일과 **같은 캐시**를 쓴다(같은 shell 청크).
// 예전엔 사본을 따로 들어 같은 저장소를 두 번 셌다.
import {
  cachedDiffCount, diffCountStale, fetchDiffCount, createDiffWatcher,
} from './rail-fetch.js';
import {
  buildRailSections, buildSleepingEntries, GROUP_LABEL, type OtherRailRowInput,
} from './rail-data.js';
import {
  buildHostMenu, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';

// 2026-09-23(70-mobile.md §2, 사용자 요청) — 데스크톱 레일과 같은 소스로
// 갈아탔다: `/api/tmux/sessions`(잠든 것까지 나온다) + `allSessions()`(이
// 브라우저에서 깨어있는 것). 예전엔 `allSessions()`만 봐서 잠든 세션이 이
// 화면에 아예 나올 수 없었다 — Rail.tsx(로컬 경로)와 같은 `OtherRailRowInput`
// 모양을 그대로 쓴다(그룹/워크트리 필드는 안 채운다 — 모바일 세션 탭은
// 「개입 필요 → 작업 중 → 열려 있음 → 잠자는 중」 4단만 본다, 탭 그룹 개념
// 없음. 데스크톱의 buildSessionSections이 아니라 buildRailSections를 계속
// 쓰는 이유).
type FleetRow = OtherRailRowInput & { statusSentence: string };

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

function safeFetch<T>(deps: FleetDeps, path: string): Promise<T | null> {
  return deps.vtFetch(path).then((v) => v as T).catch(() => null);
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

interface AgentDetail {
  since: number | null;
  tool: string | null;
  question: string | null;
  options: { key: string; label: string }[] | null;
  /** P1-1(ADR-33) — rail-fetch.ts의 같은 이름 필드와 같은 뜻. */
  promptId: string | null;
}

// Rail.tsx와 같은 이유로 여기서도 따로 둔다(지연 청크 정적 import 금지).
// 2026-09-30 — 세부·이름은 폴링하지 않고 공용 저장소(agent/state.js, /ws-agent
// push로 채워진다)에서 읽는다. 서버에서 직접 받는 건 WS가 끊긴 동안을 위한
// 안전망뿐이고, 그 결과도 같은 저장소에 넣는다 — 화면은 저장소만 본다.
// **정체(CLI 이름)와 상태 세부는 서버에서 서로 다른 소스다.**
const readAgentDetails = (): Record<string, AgentDetail> => (window as any).agentDetails?.() || {};
const readAgentNames = (): Record<string, string> => (window as any).agentNames?.() || {};

async function syncAgentsFromServer(deps: FleetDeps): Promise<void> {
  const w = window as any;
  const [st, names] = await Promise.all([
    safeFetch<{ all?: Record<string, any> }>(deps, '/api/agent/status'),
    safeFetch<Record<string, { agent?: string }>>(deps, '/api/agents'),
  ]);
  if (st && st.all) w.vtApplyAgentSnapshot?.(st.all);
  if (names) w.vtApplyAgentNames?.(names);
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
  row: FleetRow; sleeping?: boolean;
  onOpen: () => void; onLongPress: () => void;
  onAnswer: (key: string) => Promise<void>; onSeen: () => void;
}) {
  const w = () => window as any;
  const isUnseenDone = () => !props.sleeping && props.row.status === 'done' && w().isUnseen?.(props.row.tmuxName);
  const hasOptions = () => props.row.status === 'waiting' && props.row.options && props.row.options.length > 0;
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
    // 같은 관용구, 여기서는 별도 타이머 없이 `fired` 플래그 하나로 충분하다 —
    // click은 pointerup 직후 같은 틱에서 오므로 다음 pointerdown 전까지는 값이
    // 그대로 남아 있다).
    if (longPress.fired()) return;
    // 잠자는 행은 탭하면 곧장 깨워서 연다 — 확인/미확인 개념이 없다(원래부터
    // 이 브라우저에 열려 있던 적이 없으므로 "확인 안 함" 배지가 성립하지 않는다).
    if (props.sleeping) { props.onOpen(); return; }
    // §2: 「완료 · 확인 안 함」 행은 탭하면 seen 처리(이동하지 않는다) —
    // 나머지는 전부 터미널로 이동한다.
    //
    // 2026-09-22(사용자 지적) — 어느 쪽이든 먼저 onSeen을 부른다(이제
    // ackLocal이라 done→idle까지 내린다). 안 그러면 이미 「확인함」 상태로
    // 넘어온 완료 행(다른 기기가 먼저 봤거나, 방금 이 탭으로 확인 처리된 뒤
    // 재렌더 전)은 else 분기로 곧장 onOpen만 타서 파란 tone이 안 풀린다.
    const ackOnly = isUnseenDone();
    props.onSeen();
    if (!ackOnly) props.onOpen();
  };

  return (
    <div
      class="vt-srow lg vt-fleet-row"
      classList={{ 'is-waiting': props.row.status === 'waiting', sleeping: !!props.sleeping }}
      onClick={onRowClick}
      onPointerDown={longPress.onPointerDown}
      onPointerMove={longPress.onPointerMove}
      onPointerUp={longPress.onPointerUp}
      onPointerCancel={longPress.onPointerCancel}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        onRowClick();
      }}
      role="button"
      tabindex="0"
    >
      <span class={`vt-srow-mark vt-fleet-bar tone-${props.row.status}`} />
      <div class="vt-srow-main vt-fleet-row-main">
        <div class="vt-srow-top vt-fleet-row-top">
          {/* 2.1.6 — 데스크톱 레일 행·헤더 탭과 같은 마크. 이 화면에만 없어서
              폰에서는 "어떤 CLI가 도는 세션인지"를 이름으로 추측해야 했다. */}
          <Show when={props.row.agent}>
            {(a) => (
              <span class="vt-srow-agent vt-fleet-agent" data-tip={agentLabel(a())} innerHTML={agentMark(a())} />
            )}
          </Show>
          <span class="vt-srow-name vt-fleet-name">{props.row.name}</span>
          <Show when={isUnseenDone()}>
            <span class="vt-fleet-unseen-badge" aria-label="확인 안 함" data-tip="확인 안 함" />
          </Show>
          <Show when={props.row.diffFiles != null && props.row.diffFiles! > 0}>
            <span class="vt-srow-meta vt-fleet-diff">+{props.row.diffFiles}</span>
          </Show>
        </div>
        <div class="vt-srow-sub vt-fleet-row-sub">{props.row.statusSentence}</div>
        <Show when={props.row.status === 'waiting' && props.row.question}>
          <div class="vt-srow-question vt-fleet-question">? {props.row.question}</div>
        </Show>
        {/* 잠자는 세션은 열린 pane이 없어 키를 보낼 곳이 없다 — 인라인 승인
            버튼 대신 탭해서 깨우게 한다(질문 문구는 위에서 그대로 보인다). */}
        <Show when={props.row.status === 'waiting' && !props.sleeping}>
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
  // 행 메모가 agentVersion()을 구독하므로 저장소가 바뀌면 다시 계산된다.
  const agentDetails = readAgentDetails;
  const agentNames = readAgentNames;
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
  const refreshAgent = () => syncAgentsFromServer(props.deps);
  const refreshHosts = async () => {
    const data = await safeFetch<{ hosts?: HostEntry[] }>(props.deps, '/api/hosts');
    if (data?.hosts) setHosts(data.hosts);
  };

  const isFleetActive = () => document.documentElement.dataset.mobileTab === 'fleet';
  const refreshAll = () => {
    if (document.hidden || !isFleetActive()) return;
    void refreshSessions();
    void refreshAgent();
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

  // ADR-29 B와 같은 소스 — `/api/tmux/sessions`(잠든 것까지) + `allSessions()`
  // (이 브라우저에서 깨어있는 것). Rail.tsx의 로컬 세션 블록과 사실상 같은
  // 코드다(그룹·워크트리·브랜치 필드만 뺐다 — 위 상단 주석 참고).
  const rows = createMemo<OtherRailRowInput[]>(() => {
    // diffTick — 세션 목록은 바뀔 때만 push되므로 git 조회 완료·변경 알림도 여기서 받는다.
    sessionsVersion(); agentVersion(); diffTick();
    const w = window as any;

    const all = w.allSessions ? w.allSessions() : {};

    // tmux 이름 → 이 브라우저의 웹 세션 id(있으면). "깨어있음"은 기기별
    // 개념이라 서버가 주는 web_session_id가 아니라 이 맵을 출처로 삼는다.
    const tmuxToWebSid: Record<string, string> = {};
    for (const [sid, s] of Object.entries<any>(all)) {
      const tn = s.tmuxName || s.tmux_name;
      if (tn && !(tn in tmuxToWebSid)) tmuxToWebSid[tn] = sid;
    }

    const out: OtherRailRowInput[] = [];
    const seenTmux = new Set<string>();
    for (const t of tmuxSessions()) {
      seenTmux.add(t.name);
      const sid = tmuxToWebSid[t.name] || '';
      const awake = !!sid;
      const detail = agentDetails()[t.name];
      const cwd = t.cwd || null;
      out.push({
        kind: 'session',
        sessionId: sid,
        tmuxName: t.name,
        // 2026-09-21 설계 — tmux 세션의 이름은 tmux 세션 이름 그 자체다
        // (core/store.js의 sessionDisplayName). awake일 때만 그걸 따로
        // 조회하던 옛 분기는 t.name과 같은 값을 내는 동어반복이었다.
        name: t.name,
        awake,
        groupId: null, // 모바일 세션 탭은 탭 그룹 개념이 없다(상단 주석)
        status: w.getStatus ? w.getStatus(t.name) : 'idle',
        since: detail?.since ?? null,
        tool: detail?.tool ?? null,
        diffFiles: cwd ? (cachedDiffCount(cwd) ?? null) : null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
        promptId: detail?.promptId ?? null,
        agent: agentNames()[t.name] ?? null,
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
        groupId: null,
        status: 'idle',
        since: null,
        tool: null,
        diffFiles: null,
        agent: null,
      });
    }
    return out;
  });

  // 깨어있는 것만 3버킷(개입 필요/작업 중/열려 있음) — 잠든 것은
  // sleepingEntries가 별도 구역으로 다룬다(buildRailSections에 섞으면 잠든
  // waiting 세션이 「개입 필요」에 끼어드는데, 열린 pane이 없어 답을 보낼
  // 수도 없다 — 데스크톱 buildSessionSections와 같은 원칙).
  const awakeRows = createMemo(() => rows().filter((r) => r.awake));
  const sections = createMemo(() => { diffTick(); return buildRailSections(awakeRows()); });
  const sleepingEntries = createMemo(() => buildSleepingEntries(rows()));

  const openRow = async (row: FleetRow) => {
    const w = window as any;
    // 2026-10-03(세션·뷰 규칙) — 레일 클릭과 같은 함수(term/session-actions.js
    // openSessionFromRail): 열린 세션은 그 자리로, 아니면 기억된 묶음 뷰를
    // 통째로 다시 열거나 새 단독 뷰로 연다.
    await w.vtOpenSessionFromRail?.({ sessionId: row.awake ? row.sessionId : null, tmuxName: row.tmuxName || null });
    props.deps.onOpenTerminal();
  };

  const answerRow = (row: FleetRow, key: string): Promise<void> => {
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

  const seenRow = (row: FleetRow) => {
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
  const [menu, setMenu] = createSignal<{ row: FleetRow; sleeping: boolean } | null>(null);
  const [renaming, setRenaming] = createSignal(false);
  const [renameValue, setRenameValue] = createSignal('');

  const openMenu = (row: FleetRow, sleeping: boolean) => { setRenaming(false); setMenu({ row, sleeping }); };
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
      {/* 2026-09-23 — 잠든 세션도 이제 이 화면의 데이터에 들어오므로(위
          rows() 주석), "awake 섹션이 0개"만으로 "세션이 없다"고 하면 잠든
          세션이 있는데도 이 빈 화면이 뜬다. 잠든 것까지 0개일 때만 진짜
          빈 상태다. */}
      <Show when={sections().length === 0 && sleepingEntries().length === 0
        && !viewHostOffline()}>
        <div class="vt-fleet-empty">
          <div>세션이 없습니다</div>
          <div class="vt-fleet-empty-actions">
            <button type="button" class="vt-btn solid" onClick={() => { (window as any).createTmuxSession?.(); props.deps.onOpenTerminal(); }}>tmux 세션 시작</button>
            <button type="button" class="vt-btn" onClick={() => { (window as any).createSession?.(); props.deps.onOpenTerminal(); }}>일반 터미널</button>
          </div>
        </div>
      </Show>
      <For each={sections()}>
        {(section) => (
          <div class="vt-fleet-group">
            <div class="vt-fleet-group-head">{GROUP_LABEL[section.group]} · {section.rows.length}</div>
            <For each={section.rows}>
              {(row) => (
                <Row
                  row={row}
                  onOpen={() => { void openRow(row); }}
                  onLongPress={() => openMenu(row, false)}
                  onAnswer={(key) => answerRow(row, key)}
                  onSeen={() => seenRow(row)}
                />
              )}
            </For>
          </div>
        )}
      </For>
      {/* ADR-29 B와 같은 이름·같은 규칙 — 그룹째 안 섞고 전부 낱개 행이다.
          statusSentence를 비우는 것도 Rail.tsx의 sleepingEntries 렌더와 같은
          이유(구역 머리글이 이미 "잠자는 중"이라고 말한다, 행마다 또 안 씀). */}
      <Show when={sleepingEntries().length > 0}>
        <div class="vt-fleet-group">
          <div class="vt-fleet-group-head">열리지 않음 · {sleepingEntries().length}</div>
          <For each={sleepingEntries()}>
            {(entry) => {
              const row = { ...entry.row, statusSentence: '' };
              return (
                <Row
                  row={row}
                  sleeping
                  onOpen={() => { void openRow(row); }}
                  onLongPress={() => openMenu(row, true)}
                  onAnswer={() => Promise.resolve()}
                  onSeen={() => {}}
                />
              );
            }}
          </For>
        </div>
      </Show>
      {/* Rail.tsx 하단의 "+ 새 세션"과 같은 액션(session.add-menu) — 목록이
          비었을 때는 위 empty-state 버튼 둘이 이미 같은 일을 하므로 겹치지
          않게 뺀다. */}
      <Show when={sections().length > 0 || sleepingEntries().length > 0}>
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
          꺼내 평범한 변수(`row`/`sleeping`)에 담아 두고, 아래 모든 클릭
          핸들러는 그 변수만 읽는다 — 시트가 열려 있는 동안 대상이 바뀔 일은
          없다(다른 행을 길게 누르면 새 메뉴가 새로 열린다). */}
      <Show when={menu()}>
        {(m) => {
          const row = m().row;
          const sleeping = m().sleeping;
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
                  <div class="vt-session-list">
                    <Show when={!sleeping}>
                      <button type="button" class="vt-menu-item" onClick={startRename}>이름 변경</button>
                      <button type="button" class="vt-menu-item" onClick={() => runMenuAction(() => (window as any).vtCloseSession?.(row.sessionId))}>닫기</button>
                      <button type="button" class="vt-menu-item" onClick={() => runMenuAction(() => (window as any).vtOpenSessionOnMac?.(row.sessionId))}>맥에서 열기</button>
                      <button type="button" class="vt-menu-item" onClick={() => runMenuAction(() => (window as any).vtShowClients?.(row.sessionId))}>연결된 화면</button>
                    </Show>
                    <Show when={sleeping}>
                      <button type="button" class="vt-menu-item" onClick={() => runMenuAction(() => (window as any).vtOpenSessionFromRail?.({ tmuxName: row.tmuxName }))}>열기</button>
                    </Show>
                    {/* Rail.tsx의 ctxMenuItems와 같은 조건 — tmux 세션일 때만
                        성립한다(일반 세션은 tmux가 아니라 "완전 종료" 개념이
                        없다, 놓으면 그걸로 끝이다). */}
                    <Show when={row.tmuxName}>
                      <button
                        type="button"
                        class="vt-menu-item danger"
                        onClick={() => runMenuAction(() => (window as any).vtConfirmAndKillSession?.(row.tmuxName, row.sessionId || null))}
                      >
                        완전 종료 · 되돌릴 수 없음
                      </button>
                    </Show>
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
