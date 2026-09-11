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
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import {
  buildRailSections, mostUrgentStatus, GROUP_LABEL,
  type WorktreeRailRowInput, type OtherRailRowInput, type DesktopRailRowInput,
} from './rail-data.js';
import { wireRatioResizer } from '../layout/resizer.js';
import { WorktreeDialog } from './WorktreeDialog.js';

const SESSIONS_POLL_MS = 5000;   // tmux 목록(attached·cwd) — 자주 안 바뀌어도 짧게, 값싸다.
const STATUS_POLL_MS = 4000;     // since/tool 보강 — 서버가 아직 질문 텍스트를 안 줘서(2.1.0 gap) 상태 문장 갱신용.
const GIT_CACHE_MS = 60000;      // §5 원문: "60초 캐시" — 「기타」 세션 행에만 쓴다(워크트리 행은 changed 요약을 서버가 준다).
const WORKTREES_POLL_MS = 8000;  // N8(30-worktree.md) — 서버가 이미 5초 캐시라 자주 불러도 싸다.
const MIN_W = 240, MAX_W = 480, DEFAULT_W = 252;
// N3(60-settings-palette.md §1)가 생겨 device-settings 정식 스토어로
// 옮겼다 — 이전엔 여기 주석이 "N3 전이라 임시로 localStorage"였다. core/
// settings.js도 지연 청크가 정적 import 못 하는 모듈이라(파일 상단 주석과
// 같은 이유) window 브리지(vtSettingsGet/Set)로만 읽는다.
const SETTINGS_W_KEY = 'ui.rail.width';
const SETTINGS_COLLAPSE_KEY = 'ui.rail.collapsed';

export interface RailDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
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

interface AgentDetail {
  since: number | null;
  tool: string | null;
  question: string | null;
  options: { key: string; label: string }[] | null;
}

async function fetchAgentDetails(deps: RailDeps): Promise<Record<string, AgentDetail>> {
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
async function fetchDiffCount(deps: RailDeps, cwd: string): Promise<number | null> {
  const hit = _gitCache.get(cwd);
  if (hit && Date.now() - hit.at < GIT_CACHE_MS) return hit.files;
  const data = await safeFetch<{ repo?: boolean; files?: unknown[] }>(deps, `/api/git/status?repo=${encodeURIComponent(cwd)}`);
  const files = data?.repo ? (data.files || []).length : null;
  _gitCache.set(cwd, { at: Date.now(), files });
  return files;
}

// 30-worktree.md §2: 세션이 하나도 없는 워크트리를 여는 API. 흐리게 표시된
// 행을 클릭했을 때만 탄다(대부분은 이미 tmux 세션이 있어 attachTmux로 충분).
async function openWorktree(deps: RailDeps, wtId: string): Promise<string | null> {
  try {
    const data = await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}/open`, { method: 'POST' }) as { tmux_session?: string };
    return data?.tmux_session || null;
  } catch (_) {
    return null;
  }
}

type DesktopRailRow =
  | (WorktreeRailRowInput & { statusSentence: string })
  | (OtherRailRowInput & { statusSentence: string });

// 행이 가리키는 "열 수 있는" 대상. 워크트리 행 중 세션이 전혀 없는(흐리게
// 표시된) 행은 null — 컨텍스트 메뉴(세션 대상 액션)를 못 연다, 클릭은 openRow가
// 별도로 open API로 처리한다.
function actionSessionId(row: DesktopRailRow): string | null {
  return row.kind === 'session' ? row.sessionId : row.primarySessionId;
}

function Row(props: { row: DesktopRailRow; active: boolean; onOpen: (e: MouseEvent) => void; onContext: (e: MouseEvent) => void }) {
  const isWt = () => props.row.kind === 'worktree';
  const noSession = () => isWt() && !actionSessionId(props.row) && !(props.row as WorktreeRailRowInput).primaryTmuxName;
  const diffLabel = () => {
    if (props.row.kind === 'worktree') {
      const c = props.row.changed;
      return c && c.files > 0 ? `+${c.add} −${c.del}` : null;
    }
    return props.row.diffFiles != null && props.row.diffFiles > 0 ? `파일 ${props.row.diffFiles}` : null;
  };
  const rowName = () => (props.row.kind === 'worktree' ? props.row.label : props.row.name);

  return (
    <div
      class="vt-wgrail-row"
      classList={{ active: props.active, 'no-session': noSession() }}
      onClick={props.onOpen}
      onContextMenu={props.onContext}
      role="button"
      tabindex="0"
    >
      {/* 30-worktree.md §4/10-shell-layout.md §5: 워크트리 행은 상태색 막대(§20의
          저장소 해시 색점 램프는 §2 6스킨 토큰이 아직 없어 보류 — 대신 상태색을
          재사용한다, 아래 result 보고 참고). 「기타」 세션 행은 "색점 없음"
          (kind-session이 CSS에서 투명 처리). */}
      <span class={`vt-wgrail-bar ${isWt() ? `tone-${props.row.status}` : 'kind-session'}`} />
      <div class="vt-wgrail-row-main">
        <div class="vt-wgrail-row-top">
          <span class="vt-wgrail-name">{rowName()}</span>
          <Show when={diffLabel()}>
            <span class="vt-wgrail-diff">{diffLabel()}</span>
          </Show>
        </div>
        <div class="vt-wgrail-row-sub">{props.row.statusSentence}</div>
        <Show when={props.row.status === 'waiting' && props.row.question}>
          <div class="vt-wgrail-question">? {props.row.question}</div>
        </Show>
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
  const [worktrees, setWorktrees] = createSignal<any[]>([]);
  const [diffTick, setDiffTick] = createSignal(0); // git 조회가 끝나면 다시 그리라는 신호
  const [collapsed, setCollapsed] = createSignal(Boolean((window as any).vtSettingsGet?.(SETTINGS_COLLAPSE_KEY)));
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; sessionId: string } | null>(null);
  const [moreMenu, setMoreMenu] = createSignal<{ x: number; y: number } | null>(null);
  const [dialogOpen, setDialogOpen] = createSignal(false);

  const refreshSessions = async () => {
    const list = await safeFetch<any[]>(props.deps, '/api/tmux/sessions');
    if (list) setTmuxSessions(list);
  };
  const refreshAgent = async () => setAgentDetails(await fetchAgentDetails(props.deps));
  const refreshWorktrees = async () => {
    const data = await safeFetch<{ worktrees?: any[] }>(props.deps, '/api/worktrees');
    setWorktrees(data?.worktrees || []);
  };

  refreshSessions();
  refreshAgent();
  refreshWorktrees();
  const t1 = setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_POLL_MS);
  const t2 = setInterval(() => { if (!document.hidden) refreshAgent(); }, STATUS_POLL_MS);
  const t3 = setInterval(() => { if (!document.hidden) refreshWorktrees(); }, WORKTREES_POLL_MS);
  onCleanup(() => { clearInterval(t1); clearInterval(t2); clearInterval(t3); });

  // 키맵(worktreeNew, core/keymap.js) · 팔레트 등 이 파일을 정적 import 못 하는
  // 곳(위 파일 상단 주석과 같은 이유)이 다이얼로그를 열 수 있도록 하는 브리지.
  (window as any).vtOpenWorktreeDialog = () => setDialogOpen(true);
  onCleanup(() => { if ((window as any).vtOpenWorktreeDialog) delete (window as any).vtOpenWorktreeDialog; });

  // 세션 스토어(sessionsVersion)가 바뀔 때마다(탭 추가/삭제/전환) 실제 목록을
  // 다시 구성한다 — window.allSessions()가 진짜 웹 세션 맵의 단일 출처다.
  const rows = createMemo<DesktopRailRowInput[]>(() => {
    sessionsVersion(); agentVersion(); // 구독 트리거용 — 값 자체는 안 씀
    const w = window as any;
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
        name: s.tabEl?.querySelector('.tab-name')?.textContent || tmuxName || sid.slice(0, 8),
        status,
        since: detail?.since ?? null,
        tool: detail?.tool ?? null,
        diffFiles: cwd ? (_gitCache.get(cwd)?.files ?? null) : null,
        question: detail?.question ?? null,
        options: detail?.options ?? null,
      });
      // git status는 별도로 비동기 채운다(캐시 60초) — 도착하면 diffTick으로 재렌더.
      if (cwd && (!_gitCache.has(cwd) || Date.now() - (_gitCache.get(cwd)?.at ?? 0) >= GIT_CACHE_MS)) {
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

  const defaultRepo = () => worktrees().find((x: any) => x.isMain)?.repo || worktrees()[0]?.repo || '';

  const onCreated = async (result: any) => {
    setDialogOpen(false);
    await refreshWorktrees();
    const tmuxName = result?.opened?.tmux_session || null;
    if (tmuxName) await (window as any).attachTmux?.(tmuxName);
  };

  return (
    <aside id="vt-wgrail" ref={railRef} classList={{ collapsed: collapsed() }} aria-label="워크트리">
      <div class="vt-wgrail-head">
        <Show when={!collapsed()}>
          <span class="vt-wgrail-title">워크트리 · {worktrees().length}</span>
        </Show>
        <button type="button" class="vt-wgrail-collapse" onClick={toggleCollapse} aria-label={collapsed() ? '펼치기' : '접기'} title={collapsed() ? '펼치기' : '접기'}>
          {collapsed() ? '›' : '‹'}
        </button>
      </div>
      <div class="vt-wgrail-body">
        <Show when={totalRows() === 0 && !collapsed()}>
          <div class="vt-wgrail-empty">
            아직 워크트리가 없습니다.
            <button type="button" class="vt-wgrail-empty-new" onClick={() => setDialogOpen(true)}>+ 워크트리 만들기</button>
          </div>
        </Show>
        <For each={sections()}>
          {(section) => (
            <>
              <Show when={!collapsed()}>
                <div class="vt-wgrail-group-head">{GROUP_LABEL[section.group]}</div>
              </Show>
              <For each={section.rows}>
                {(row) => (
                  <Row
                    row={row}
                    active={actionSessionId(row) === activeId()}
                    onOpen={(e) => openRow(e, row)}
                    onContext={(e) => contextRow(e, row)}
                  />
                )}
              </For>
            </>
          )}
        </For>
      </div>
      <div class="vt-wgrail-footer">
        <button type="button" class="vt-wgrail-new" onClick={() => setDialogOpen(true)}>
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
      <Show when={dialogOpen()}>
        <WorktreeDialog deps={props.deps} defaultRepo={defaultRepo()} onClose={() => setDialogOpen(false)} onCreated={onCreated} />
      </Show>
    </aside>
  );
}

export function mountRail(root: HTMLElement, deps: RailDeps) {
  return render(() => <Rail deps={deps} />, root);
}
