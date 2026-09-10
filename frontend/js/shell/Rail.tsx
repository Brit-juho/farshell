// N36 §5 — 워크트리 레일 252px. 2.1.0은 워크트리 모델이 아직 없어(2.1.1
// 몫, ADR-10) 세션을 워크트리 자리에 임시로 그린다: 세션 하나 = 행 하나.
//
// ⚠ 이 파일은 core/store.js·layout/store.js·agent/state.js·term/session.js를
// 직접 import하지 않는다(panels/viewer-lazy.js·shell/Hud.tsx와 같은 이유 —
// 지연 청크가 이들을 정적 import하면 Vite lib 모드가 상태를 복제한다,
// ADR-26/N35 커밋 참고). 전부 그 파일들이 이미 노출해 둔 window 브리지로만
// 읽는다. vtFetch·getAction만 main.js가 인자로 넘긴다(Hud.tsx와 같은 이유).
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { buildRailSections, type RailRowInput, type RailRow } from './rail-data.js';
import { wireRatioResizer } from '../layout/resizer.js';

const SESSIONS_POLL_MS = 5000;   // tmux 목록(attached·cwd) — 자주 안 바뀌어도 짧게, 값싸다.
const STATUS_POLL_MS = 4000;     // since/tool 보강 — 서버가 아직 질문 텍스트를 안 줘서(2.1.0 gap) 상태 문장 갱신용.
const GIT_CACHE_MS = 60000;      // §5 원문: "60초 캐시".
const MIN_W = 240, MAX_W = 480, DEFAULT_W = 252;
// N3(60-settings-palette.md §1)가 생겨 device-settings 정식 스토어로
// 옮겼다 — 이전엔 여기 주석이 "N3 전이라 임시로 localStorage"였다. core/
// settings.js도 지연 청크가 정적 import 못 하는 모듈이라(파일 상단 주석과
// 같은 이유) window 브리지(vtSettingsGet/Set)로만 읽는다.
const SETTINGS_W_KEY = 'ui.rail.width';
const SETTINGS_COLLAPSE_KEY = 'ui.rail.collapsed';

export interface RailDeps {
  vtFetch: (path: string) => Promise<unknown>;
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

interface AgentDetail { since: number | null; tool: string | null; }

async function fetchAgentDetails(deps: RailDeps): Promise<Record<string, AgentDetail>> {
  const data = await safeFetch<{ all?: Record<string, any> }>(deps, '/api/agent/status');
  const out: Record<string, AgentDetail> = {};
  for (const entry of Object.values(data?.all || {})) {
    const name = (entry as any)?.tmux_session;
    if (!name) continue;
    out[name] = { since: (entry as any).since ?? null, tool: (entry as any).tool || (entry as any).last_tool || null };
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

function Row(props: { row: RailRow; active: boolean; onOpen: (e: MouseEvent) => void; onContext: (e: MouseEvent) => void }) {
  return (
    <div
      class="vt-wgrail-row"
      classList={{ active: props.active }}
      onClick={props.onOpen}
      onContextMenu={props.onContext}
      role="button"
      tabindex="0"
    >
      <span class={`vt-wgrail-bar tone-${props.row.status}`} />
      <div class="vt-wgrail-row-main">
        <div class="vt-wgrail-row-top">
          <span class="vt-wgrail-name">{props.row.name}</span>
          <Show when={props.row.diffFiles != null && props.row.diffFiles! > 0}>
            <span class="vt-wgrail-diff">파일 {props.row.diffFiles}</span>
          </Show>
        </div>
        <div class="vt-wgrail-row-sub">{props.row.statusSentence}</div>
      </div>
    </div>
  );
}

function Menu(props: { x: number; y: number; onClose: () => void; items: { label: string; run: () => void }[] }) {
  let ref: HTMLDivElement | undefined;
  const onDocClick = (e: MouseEvent) => { if (ref && !ref.contains(e.target as Node)) props.onClose(); };
  document.addEventListener('mousedown', onDocClick, true);
  onCleanup(() => document.removeEventListener('mousedown', onDocClick, true));

  return (
    <div ref={ref} class="vt-menu" style={{ left: `${props.x}px`, top: `${props.y}px`, right: 'auto' }}>
      <For each={props.items}>
        {(it) => <div class="vt-menu-item" onClick={() => { props.onClose(); it.run(); }}>{it.label}</div>}
      </For>
    </div>
  );
}

function Rail(props: { deps: RailDeps }) {
  const sessionsVersion = useSessionsVersion();
  const agentVersion = useAgentVersion();

  const [tmuxSessions, setTmuxSessions] = createSignal<any[]>([]);
  const [agentDetails, setAgentDetails] = createSignal<Record<string, AgentDetail>>({});
  const [diffTick, setDiffTick] = createSignal(0); // git 조회가 끝나면 다시 그리라는 신호
  const [collapsed, setCollapsed] = createSignal(Boolean((window as any).vtSettingsGet?.(SETTINGS_COLLAPSE_KEY)));
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; sessionId: string } | null>(null);
  const [moreMenu, setMoreMenu] = createSignal<{ x: number; y: number } | null>(null);

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

  // 세션 스토어(sessionsVersion)가 바뀔 때마다(탭 추가/삭제/전환) 실제 목록을
  // 다시 구성한다 — window.allSessions()가 진짜 웹 세션 맵의 단일 출처다.
  const rows = createMemo<RailRowInput[]>(() => {
    sessionsVersion(); agentVersion(); // 구독 트리거용 — 값 자체는 안 씀
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
      });
      // git status는 별도로 비동기 채운다(캐시 60초) — 도착하면 diffTick으로 재렌더.
      if (cwd && (!_gitCache.has(cwd) || Date.now() - (_gitCache.get(cwd)?.at ?? 0) >= GIT_CACHE_MS)) {
        fetchDiffCount(props.deps, cwd).then(() => setDiffTick((n) => n + 1));
      }
    }
    return out;
  });

  const sections = createMemo(() => { diffTick(); return buildRailSections(rows()); });

  const activeId = createMemo(() => { sessionsVersion(); return (window as any).activeSessionId?.() ?? null; });

  const openRow = (e: MouseEvent, sessionId: string) => {
    const w = window as any;
    if (e.metaKey || e.ctrlKey) w.splitActivePane?.('row', sessionId);
    else w.switchTo?.(sessionId);
  };

  const contextRow = (e: MouseEvent, row: RailRow) => {
    e.preventDefault();
    setCtxMenu({ x: e.clientX, y: e.clientY, sessionId: row.sessionId });
  };

  const ctxMenuItems = () => {
    const m = ctxMenu();
    if (!m) return [];
    const w = window as any;
    return [
      { label: '새 세션', run: () => w.createSession?.() },
      { label: '맥에서 열기', run: () => { w.switchTo?.(m.sessionId); (props.deps.getAction('session.open-on-mac') as (() => void) | undefined)?.(); } },
      { label: '연결된 화면', run: () => { w.switchTo?.(m.sessionId); (props.deps.getAction('clients.show') as (() => void) | undefined)?.(); } },
      { label: '닫기', run: () => w.removeSession?.(m.sessionId) },
    ];
  };

  // "⋯ 더보기" — §5가 기존 48px 아이콘 레일을 완전히 대체하기로 하면서 갈 곳을
  // 잃은 것들의 임시 정류장(§6 dock·§60 설정 화면이 생기기 전까지). 대부분은
  // getAction으로 바로 되지만, 마이크·테마는 아직 rail.js의 #vt-rail-panel
  // 플라이아웃 안에만 산다(desktop mic의 유일한 자리) — 그 DOM은 지우지 않고
  // CSS로만 숨겨 뒀으므로, 숨은 버튼을 그대로 다시 눌러 같은 경로를 그대로 쓴다.
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

  return (
    <aside id="vt-wgrail" ref={railRef} classList={{ collapsed: collapsed() }} aria-label="워크트리">
      <div class="vt-wgrail-head">
        <Show when={!collapsed()}>
          <span class="vt-wgrail-title">워크트리 · {rows().length}</span>
        </Show>
        <button type="button" class="vt-wgrail-collapse" onClick={toggleCollapse} aria-label={collapsed() ? '펼치기' : '접기'} title={collapsed() ? '펼치기' : '접기'}>
          {collapsed() ? '›' : '‹'}
        </button>
      </div>
      <div class="vt-wgrail-body">
        <For each={sections()}>
          {(section) => (
            <>
              <Show when={!collapsed()}>
                <div class="vt-wgrail-group-head">{section.group === 'attention' ? '개입 필요' : section.group === 'working' ? '작업 중' : '유휴'}</div>
              </Show>
              <For each={section.rows}>
                {(row) => (
                  <Row
                    row={row}
                    active={row.sessionId === activeId()}
                    onOpen={(e) => openRow(e, row.sessionId)}
                    onContext={(e) => contextRow(e, row)}
                  />
                )}
              </For>
            </>
          )}
        </For>
      </div>
      <div class="vt-wgrail-footer">
        <button type="button" class="vt-wgrail-new" onClick={() => (window as any).createSession?.()}>
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
      <Show when={moreMenu()}>
        {(m) => <Menu x={m().x} y={m().y} onClose={() => setMoreMenu(null)} items={moreMenuItems()} />}
      </Show>
    </aside>
  );
}

export function mountRail(root: HTMLElement, deps: RailDeps) {
  return render(() => <Rail deps={deps} />, root);
}
