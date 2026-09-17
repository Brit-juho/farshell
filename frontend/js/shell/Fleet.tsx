// N38(70-mobile.md §2) — 모바일 플릿 홈. compact(<720px) 하단 내비의 첫 탭
// (ui.mobile.home 기본값). Rail.tsx(N36 §5)와 **같은 데이터 소스**를 쓴다 —
// 같은 그룹핑·정렬 로직(rail-data.ts의 buildRailSections)을 그대로 재사용해,
// "데스크톱 레일에서 맨 위인 게 폰에서도 맨 위"가 항상 성립하게 한다.
//
// Rail.tsx와 같은 이유로 데이터 fetch(세션 목록·agent 상태·git diff)를
// 직접 다시 구현한다(정적 import 금지 — 지연 청크가 core/store.js 등 상태
// 모듈을 정적 import하면 Vite lib 모드가 복제한다, ADR-26). Dock.tsx의 배지
// 폴링도 같은 이유로 각자 반복하고 있다 — 이 파일도 그 관행을 따른다.
import { createSignal, createMemo, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { agentIcon, agentLabel } from '../ui/icons.js';
import { buildRailSections, GROUP_LABEL, type RailRowInput, type RailRow } from './rail-data.js';
import {
  buildHostMenu, remoteSessionRows, resolveActiveHost, hostDetail, LOCAL_HOST,
  type HostEntry,
} from './host-data.js';

const SESSIONS_POLL_MS = 5000;
const STATUS_POLL_MS = 4000;
const HOSTS_POLL_MS = 15000;
const GIT_CACHE_MS = 60000;

// C1 — Rail.tsx와 **같은 설정 키**를 읽고 쓴다. 폰과 데스크톱이 각자 다른
// 호스트를 기억하면 창 폭을 바꿨을 때 목록이 이유 없이 바뀐 것처럼 보인다.
const SETTINGS_HOST_KEY = 'ui.activeHostId';

export interface FleetDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
  /** 행을 열었다(터미널로 이동) — mobile-nav.js가 하단 내비 활성 탭을
   * '터미널'로 바꾼다. Fleet.tsx 자신은 "어느 탭이 지금 보이는가"를 모른다
   * (그건 mobile-nav.js의 책임 — §1 골격과 §2 플릿 홈을 같은 파일이 겸하면
   * 상태 소유가 흩어진다). */
  onOpenTerminal: () => void;
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
}

// Rail.tsx와 같은 이유로 여기서도 따로 부른다(지연 청크 정적 import 금지).
// **정체는 /api/agents, 상태는 /api/agent/status** — 서로 다른 소스다.
async function fetchAgentNames(deps: FleetDeps): Promise<Record<string, string>> {
  const data = await safeFetch<Record<string, { agent?: string }>>(deps, '/api/agents');
  const out: Record<string, string> = {};
  for (const [name, v] of Object.entries(data || {})) if (v && v.agent) out[name] = v.agent;
  return out;
}

async function fetchAgentDetails(deps: FleetDeps): Promise<Record<string, AgentDetail>> {
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
async function fetchDiffCount(deps: FleetDeps, cwd: string): Promise<number | null> {
  const hit = _gitCache.get(cwd);
  if (hit && Date.now() - hit.at < GIT_CACHE_MS) return hit.files;
  const data = await safeFetch<{ repo?: boolean; files?: unknown[] }>(deps, `/api/git/status?repo=${encodeURIComponent(cwd)}`);
  const files = data?.repo ? (data.files || []).length : null;
  _gitCache.set(cwd, { at: Date.now(), files });
  return files;
}

function Row(props: { row: RailRow; onOpen: () => void; onAnswer: (key: string) => void; onSeen: () => void }) {
  const w = () => window as any;
  const isUnseenDone = () => props.row.status === 'done' && w().isUnseen?.(props.row.tmuxName);
  const hasOptions = () => props.row.status === 'waiting' && props.row.options && props.row.options.length > 0;

  const onRowClick = () => {
    // §2: 「완료 · 확인 안 함」 행은 탭하면 seen 처리(이동하지 않는다) —
    // 나머지는 전부 터미널로 이동한다.
    if (isUnseenDone()) props.onSeen();
    else props.onOpen();
  };

  return (
    <div class="vt-fleet-row" classList={{ 'is-waiting': props.row.status === 'waiting' }} onClick={onRowClick} role="button" tabindex="0">
      <span class={`vt-fleet-bar tone-${props.row.status}`} />
      <div class="vt-fleet-row-main">
        <div class="vt-fleet-row-top">
          {/* 2.1.6 — 데스크톱 레일 행·헤더 탭과 같은 마크. 이 화면에만 없어서
              폰에서는 "어떤 CLI가 도는 세션인지"를 이름으로 추측해야 했다. */}
          <Show when={props.row.agent}>
            {(a) => (
              <span class="vt-fleet-agent" title={agentLabel(a())} innerHTML={agentIcon(a())} />
            )}
          </Show>
          <span class="vt-fleet-name">{props.row.name}</span>
          <Show when={isUnseenDone()}>
            <span class="vt-fleet-unseen-badge" aria-label="확인 안 함" title="확인 안 함" />
          </Show>
          <Show when={props.row.diffFiles != null && props.row.diffFiles! > 0}>
            <span class="vt-fleet-diff">+{props.row.diffFiles}</span>
          </Show>
        </div>
        <div class="vt-fleet-row-sub">{props.row.statusSentence}</div>
        <Show when={props.row.status === 'waiting' && props.row.question}>
          <div class="vt-fleet-question">? {props.row.question}</div>
        </Show>
        <Show when={props.row.status === 'waiting'}>
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
                  <button type="button" class="vt-btn sm" onClick={() => props.onAnswer(opt.key)}>
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
  const [agentDetails, setAgentDetails] = createSignal<Record<string, AgentDetail>>({});
  const [agentNames, setAgentNames] = createSignal<Record<string, string>>({});
  const [diffTick, setDiffTick] = createSignal(0);
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [hostMenuOpen, setHostMenuOpen] = createSignal(false);
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
  const refreshHosts = async () => {
    const data = await safeFetch<{ hosts?: HostEntry[] }>(props.deps, '/api/hosts');
    if (data?.hosts) setHosts(data.hosts);
  };

  refreshSessions();
  refreshAgent();
  refreshHosts();
  const t1 = setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_POLL_MS);
  const t2 = setInterval(() => { if (!document.hidden) refreshAgent(); }, STATUS_POLL_MS);
  const t3 = setInterval(() => { if (!document.hidden) refreshHosts(); }, HOSTS_POLL_MS);
  onCleanup(() => { clearInterval(t1); clearInterval(t2); clearInterval(t3); });

  // 레일에서 호스트를 바꿔도(창을 넓혔다 줄이는 동안 둘 다 살아 있다) 여기가
  // 따라오도록 설정 변경을 구독한다 — 안 하면 같은 화면의 두 스위처가 서로
  // 다른 호스트를 가리킨다.
  const unsubSettings = (window as any).vtSettingsSubscribe?.((all: any) => {
    const v = String(all?.[SETTINGS_HOST_KEY] || LOCAL_HOST);
    if (v !== activeHostId()) setActiveHostId(v);
  });
  onCleanup(() => unsubSettings?.());

  const effectiveHostId = createMemo(() =>
    hosts().length === 0 ? activeHostId() : resolveActiveHost(hosts(), activeHostId()));
  const isRemoteHost = createMemo(() => effectiveHostId() !== LOCAL_HOST);
  const activeHost = createMemo(() => hosts().find((h) => h.id === effectiveHostId()) || null);

  const selectHost = (id: string) => {
    setActiveHostId(id);
    (window as any).vtSettingsSet?.(SETTINGS_HOST_KEY, id);
    setHostMenuOpen(false);
  };

  const rows = createMemo<RailRowInput[]>(() => {
    sessionsVersion(); agentVersion();
    const w = window as any;

    // C1 — 원격 호스트를 고르면 그 호스트의 세션만 그린다(Rail.tsx와 같은
    // 규칙: 스위처는 필터일 뿐, 이미 열린 pane은 건드리지 않는다).
    if (isRemoteHost()) {
      const h = activeHost();
      return h ? remoteSessionRows(h) : [];
    }
    const all = w.allSessions ? w.allSessions() : {};
    const byName: Record<string, any> = {};
    for (const t of tmuxSessions()) byName[t.name] = t;

    const out: RailRowInput[] = [];
    for (const [sid, s] of Object.entries<any>(all)) {
      const tmuxName = s.tmuxName || s.tmux_name || null;
      const tmuxInfo = tmuxName ? byName[tmuxName] : null;
      const status = tmuxName && w.getStatus ? w.getStatus(tmuxName) : 'idle';
      const detail = tmuxName ? agentDetails()[tmuxName] : undefined;
      const cwd = tmuxInfo?.cwd || null;
      out.push({
        sessionId: sid,
        tmuxName,
        name: w.sessionDisplayName ? w.sessionDisplayName(sid) : (tmuxName || sid.slice(0, 8)),
        status,
        since: detail?.since ?? null,
        tool: detail?.tool ?? null,
        diffFiles: cwd ? (_gitCache.get(cwd)?.files ?? null) : null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
        agent: tmuxName ? (agentNames()[tmuxName] ?? null) : null,
      });
      if (cwd && (!_gitCache.has(cwd) || Date.now() - (_gitCache.get(cwd)?.at ?? 0) >= GIT_CACHE_MS)) {
        fetchDiffCount(props.deps, cwd).then(() => setDiffTick((n) => n + 1));
      }
    }
    return out;
  });

  const sections = createMemo(() => { diffTick(); return buildRailSections(rows()); });

  const openRow = async (row: RailRow) => {
    const w = window as any;
    // C1+3단계 — 원격 행은 프록시 경로로 연다(term/remote.js가 window에 건다).
    if ((row as any).remote && row.tmuxName) {
      if (typeof w.attachRemoteSession === 'function') {
        await w.attachRemoteSession(effectiveHostId(), row.tmuxName);
        props.deps.onOpenTerminal();
      }
      return;
    }
    w.switchTo?.(row.sessionId);
    props.deps.onOpenTerminal();
  };

  const answerRow = (row: RailRow, key: string) => {
    props.deps.vtFetch(`/api/sessions/${encodeURIComponent(row.sessionId)}/keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: `${key}\r` }),
    }).catch(() => {}); // 실패해도 화면은 다음 폴링(agent status)이 정정한다
  };

  const seenRow = (row: RailRow) => {
    if (row.tmuxName) (window as any).markSeen?.(row.tmuxName);
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
            class="vt-fleet-host"
            classList={{ offline: activeHost() ? !activeHost()!.online : false, remote: isRemoteHost(), open: hostMenuOpen() }}
            aria-haspopup="listbox"
            aria-expanded={hostMenuOpen()}
            onClick={() => setHostMenuOpen((v) => !v)}
          >
            <span class="vt-fleet-host-name">{activeHost()?.label || effectiveHostId()}</span>
            <span class="vt-fleet-host-detail">{activeHost() ? hostDetail(activeHost()!) : ''}</span>
            {/* 캐럿은 SVG 하나를 CSS로 뒤집는다(.open일 때 180도) — 글리프
                ▴▾는 폰트마다 중심이 달라 옆 글자와 광학 정렬이 안 맞았다. */}
            <span class="vt-fleet-host-caret" classList={{ open: hostMenuOpen() }}><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
          </button>
        </div>
        <Show when={hostMenuOpen()}>
          <div class="vt-fleet-hostmenu" role="listbox">
            <For each={buildHostMenu(hosts(), activeHostId())}>
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
      <Show when={isRemoteHost() && activeHost() && !activeHost()!.online}>
        <div class="vt-fleet-empty">{hostDetail(activeHost()!)}</div>
      </Show>
      <Show when={sections().length === 0 && !(isRemoteHost() && activeHost() && !activeHost()!.online)}>
        <div class="vt-fleet-empty">세션이 없습니다</div>
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
                  onAnswer={(key) => answerRow(row, key)}
                  onSeen={() => seenRow(row)}
                />
              )}
            </For>
          </div>
        )}
      </For>
    </div>
  );
}

export function mountFleet(root: HTMLElement, deps: FleetDeps) {
  return render(() => <Fleet deps={deps} />, root);
}
