// N37 §4 — 헤더의 새 조각 둘: 좌측 워크스페이스 칩, 우측 「연결된 화면」 아이콘.
// 워크트리 탭(layout/tabbar.js)·드래그(layout/dnd.js)·+ 버튼은 그대로 둔다
// (10-shell-layout.md §4 "탭 드래그→페인 드롭은 기존 layout/dnd.js 유지") —
// 이미 잘 동작하는 걸 다시 쓰는 리스크를 이번 마일스톤에서 지지 않는다.
//
// Hud.tsx와 같은 이유로 core/*를 직접 import하지 않고 main.js가 인자로
// 넘긴다(지연 청크가 core/*를 복제해 액션 레지스트리가 갈라지는 문제 방지).
import { createSignal, onCleanup, Show } from 'solid-js';
import { render } from 'solid-js/web';

const POLL_MS = 30000;

export interface HeaderDeps {
  vtFetch: (path: string) => Promise<unknown>;
  activeTmuxName: () => string;
  getAction: (name: string) => unknown;
}

interface Caps {
  hostname?: string;
}

async function safeFetch<T>(deps: HeaderDeps, path: string): Promise<T | null> {
  try {
    return (await deps.vtFetch(path)) as T;
  } catch (_) {
    return null;
  }
}

// 워크스페이스 칩 — "farshell / <호스트>". 호스트는 재부팅 전엔 안 바뀌므로
// 한 번만 읽는다(HUD처럼 상태 폴링이 필요한 값이 아니다).
function WorkspaceChip(props: { deps: HeaderDeps }) {
  const [hostname, setHostname] = createSignal('');

  safeFetch<Caps>(props.deps, '/api/capabilities').then((c) => {
    if (c?.hostname) setHostname(c.hostname);
  });

  const onClick = () => {
    // §4: "클릭 → 팔레트 @ 모드". @ 접두사 자체는 60-settings-palette.md
    // §3(N5/N40/N46) 몫이라 아직 없다 — 그게 생기기 전까지는 팔레트를 그냥 연다.
    const fn = props.deps.getAction('quickopen.open');
    if (typeof fn === 'function') (fn as () => void)();
  };

  return (
    <button type="button" id="vt-workspace-chip" onClick={onClick} title="빠른 열기">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="5" /><path d="m7 9 3 3-3 3" /><path d="M12 15h5" /></svg>
      <span class="vt-workspace-name">farshell</span>
      <Show when={hostname()}>
        <span class="vt-workspace-host">/ {hostname()}</span>
      </Show>
    </button>
  );
}

// 「연결된 화면」 — 지금 활성 세션에 붙은 클라이언트 수. HUD의 같은 이름
// 칩과 데이터·의미가 동일하다(clients.js 기준 — tmux 세션 단위). 1개(나
// 혼자)면 아이콘만, 2개 이상이면 숫자를 얹는다 — 항상 떠 있는 요소라 평소엔
// 존재감을 줄이고 "볼 필요가 생겼을 때"만 도드라지게 한다.
function ScreensButton(props: { deps: HeaderDeps }) {
  const [count, setCount] = createSignal<number | null>(null);

  const refresh = async () => {
    const tmuxName = props.deps.activeTmuxName();
    if (!tmuxName) { setCount(null); return; }
    const data = await safeFetch<{ clients?: unknown[] }>(
      props.deps,
      `/api/tmux/clients?session=${encodeURIComponent(tmuxName)}`,
    );
    setCount(data?.clients?.length ?? null);
  };

  refresh();
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  onCleanup(() => clearInterval(timer));

  const onClick = () => {
    const fn = props.deps.getAction('clients.show');
    if (typeof fn === 'function') (fn as () => void)();
  };

  return (
    <Show when={count() != null}>
      <button
        type="button"
        id="vt-screens-btn"
        classList={{ 'vt-screens-multi': (count() ?? 0) > 1 }}
        onClick={onClick}
        title="연결된 화면"
        aria-label={`연결된 화면 ${count()}개`}
      >
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="8" x="5" y="2" rx="2" /><path d="M9 22V13" /><path d="M15 22V13" /><path d="M20 13H4" /></svg>
        <Show when={(count() ?? 0) > 1}><span class="vt-screens-count">{count()}</span></Show>
      </button>
    </Show>
  );
}

export function mountWorkspaceChip(root: HTMLElement, deps: HeaderDeps) {
  return render(() => <WorkspaceChip deps={deps} />, root);
}

export function mountScreensButton(root: HTMLElement, deps: HeaderDeps) {
  return render(() => <ScreensButton deps={deps} />, root);
}
