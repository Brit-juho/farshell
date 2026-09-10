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
import { buildRailSections, GROUP_LABEL, type RailRowInput, type RailRow } from './rail-data.js';

const SESSIONS_POLL_MS = 5000;
const STATUS_POLL_MS = 4000;
const GIT_CACHE_MS = 60000;

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
                <button type="button" class="vt-fleet-answer-btn vt-fleet-answer-fallback" onClick={props.onOpen}>
                  터미널로
                </button>
              }
            >
              <For each={props.row.options}>
                {(opt) => (
                  <button type="button" class="vt-fleet-answer-btn" onClick={() => props.onAnswer(opt.key)}>
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
  const [diffTick, setDiffTick] = createSignal(0);

  const refreshSessions = async () => {
    const list = await safeFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (list) setTmuxSessions(list);
  };
  const refreshAgent = async () => setAgentDetails(await fetchAgentDetails(props.deps));

  refreshSessions();
  refreshAgent();
  const t1 = setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_POLL_MS);
  const t2 = setInterval(() => { if (!document.hidden) refreshAgent(); }, STATUS_POLL_MS);
  onCleanup(() => { clearInterval(t1); clearInterval(t2); });

  const rows = createMemo<RailRowInput[]>(() => {
    sessionsVersion(); agentVersion();
    const w = window as any;
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
        name: s.tabEl?.querySelector('.tab-name')?.textContent || tmuxName || sid.slice(0, 8),
        status,
        since: detail?.since ?? null,
        tool: detail?.tool ?? null,
        diffFiles: cwd ? (_gitCache.get(cwd)?.files ?? null) : null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
      });
      if (cwd && (!_gitCache.has(cwd) || Date.now() - (_gitCache.get(cwd)?.at ?? 0) >= GIT_CACHE_MS)) {
        fetchDiffCount(props.deps, cwd).then(() => setDiffTick((n) => n + 1));
      }
    }
    return out;
  });

  const sections = createMemo(() => { diffTick(); return buildRailSections(rows()); });

  const openRow = (sessionId: string) => {
    (window as any).switchTo?.(sessionId);
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
      <Show when={sections().length === 0}>
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
                  onOpen={() => openRow(row.sessionId)}
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
