// N37 §4 — 헤더 좌측 워크스페이스 칩.
//
// 우측에 있던 「연결된 화면」 아이콘 버튼은 2026-09-17에 **제거했다**. 이유 둘:
//  1. 순수 중복이었다 — 같은 `clients.show`를 HUD 칩(hud-data.ts)이 「연결된
//     화면 1」이라고 **글자로** 띄우고 레일 행 컨텍스트 메뉴에도 있다.
//  2. 그 14px 모니터 아이콘이 한글 **「유」로 읽혔다.** 둥근 사각형(ㅇ) 아래
//     가로줄(ㅡ)과 다리 둘(ㅠ)이라 stroke 2px로 줄면 획이 뭉쳐 글자가 된다.
//     제품이 쓰는 언어의 글자와 헷갈리는 아이콘은 아이콘 역할을 못 한다.
//     사용자가 실제로 "우측 상단 유 버튼이 뭐냐"고 물어서 드러났다.
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

// 워크스페이스 칩 — "farshell / <호스트>".
//
// 2026-09-28(사용자 지적) — 예전엔 로그인한 서버의 이름을 부팅 때 한 번 읽어
// 고정했다. 레일 스위처로 M5를 고르고 M5 원격 칸을 보고 있어도 칩은 계속 M1이라,
// 세 곳(칩·스위처·터미널 칸)이 서로 다른 호스트를 말했다. 이제 **활성 칸이
// 보여주는 호스트**를 따라간다(term/host-view.js가 판정, window.vtActiveViewHost).
// 원격 칸이면 이 화면이 어느 서버를 거쳐 그 호스트에 닿았는지도 같이 적는다 —
// 로그인한 서버는 여전히 이 맥이고, 원격 칸은 이 맥의 프록시를 지난다.
function WorkspaceChip(props: { deps: HeaderDeps }) {
  const [hostname, setHostname] = createSignal('');
  const [viewHostId, setViewHostId] = createSignal('local');
  const [viewLabel, setViewLabel] = createSignal('');

  safeFetch<Caps>(props.deps, '/api/capabilities').then((c) => {
    if (c?.hostname) setHostname(c.hostname);
  });

  let seq = 0;
  const syncView = async () => {
    const w = window as any;
    const id = String(w.vtActiveViewHost?.() || 'local');
    if (id === viewHostId() && (id === 'local' || viewLabel())) return;
    const mine = ++seq;
    setViewHostId(id);
    if (id === 'local') { setViewLabel(''); return; }
    setViewLabel(w.vtHostLabelSync?.(id) || id);
    const label = await w.vtHostLabel?.(id);
    if (mine === seq && label) setViewLabel(label);   // 그 사이 다른 칸으로 옮겼으면 버린다
  };
  // 세션 추가·삭제·전환(store)과 칸·탭 이동(layout) 둘 다 활성 칸을 바꾼다.
  const offStore = (window as any).storeSubscribe?.(() => { syncView(); });
  const offLayout = (window as any).onLayoutChange?.(() => { syncView(); });
  onCleanup(() => { offStore?.(); offLayout?.(); });
  syncView();

  // 2026-09-21: 클릭(팔레트 @ 모드)을 뗐다 — 이 칩을 호스트 전환 진입점으로
  // 착각하기 쉬운데, @ 모드는 세션 이름 검색일 뿐이고 실제 호스트 전환은
  // Rail/Fleet의 호스트 스위처(80-multihost-agents.md C1)에 따로 있다.
  // getAction 배선은 다른 용도로 재활용할 수 있어 남겨둔다.
  const onClick = () => {
    const fn = props.deps.getAction('quickopen.open');
    if (typeof fn === 'function') (fn as () => void)();
  };

  return (
    <div id="vt-workspace-chip">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="5" /><path d="m7 9 3 3-3 3" /><path d="M12 15h5" /></svg>
      <span class="vt-workspace-name">farshell</span>
      <Show
        when={viewHostId() !== 'local'}
        fallback={<Show when={hostname()}><span class="vt-workspace-host">/ {hostname()}</span></Show>}
      >
        <span class="vt-workspace-host remote">/ {viewLabel()}</span>
        <Show when={hostname()}>
          <span class="vt-workspace-via">· {hostname()} 경유</span>
        </Show>
      </Show>
    </div>
  );
}

export function mountWorkspaceChip(root: HTMLElement, deps: HeaderDeps) {
  return render(() => <WorkspaceChip deps={deps} />, root);
}
