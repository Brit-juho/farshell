// N35 §6 — dock 392px 프레임. 문서가 프레임의 책임을 네 가지로 못박았다:
// **탭 전환 · 접기 · 폭 리사이저(320~560) · 기기 스코프 저장**. 탭 "내용"은
// 각자의 문서(40 소스컨트롤 · 60 큐/포트/사용량) 몫이라 여기서 새로 그리지
// 않는다 — 이미 있는 패널 렌더러(queue.js·ports.js·panels/usage.js)를 그대로
// 쓰고, 그것들이 그리던 자리만 backdrop 모달에서 dock 본문으로 옮긴다
// (panels/panel.js의 dock 호스트). 같은 화면을 두 벌 만들면 반드시 어긋난다.
//
// ⚠ Rail.tsx와 같은 제약: 이 파일은 지연 청크(shell.js)에 들어가므로
// core/store.js·term/session.js 같은 **상태를 가진 모듈을 정적 import 하지
// 않는다**(ADR-26/N35 — Vite lib 모드가 청크 안에 복제해 넣어 앱과 다른
// 객체가 된다). 상수 모듈(breakpoints·resizer)만 예외다.
import { createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { wireRatioResizer } from '../layout/resizer.js';
import { WIDE_MAX } from '../layout/breakpoints.js';

const MIN_W = 320, MAX_W = 560, DEFAULT_W = 392, COLLAPSED_W = 36;
// N3(기기 스코프 설정)가 아직 없어 Rail.tsx와 같이 임시로 localStorage를 쓴다.
// localStorage는 원래 기기 스코프라 60 §1이 오면 그대로 이관하면 된다.
const W_KEY = 'vt-dock-w', TAB_KEY = 'vt-dock-tab', COLLAPSE_KEY = 'vt-dock-collapsed';
const BADGE_POLL_MS = 15000;

export interface DockDeps {
  vtFetch: (path: string) => Promise<any>;
  getAction: (name: string) => unknown;
}

interface DockTab {
  id: string;
  label: string;
  panelId: string | null;   // 기존 패널 렌더러의 루트 id. null이면 자체 빈 상태.
  action: string | null;    // 그 패널을 여는 액션 id(팔레트·키맵이 참조하는 그 id)
  cap?: 'ports' | 'usage';  // /api/capabilities에 없으면 탭 자체가 사라진다
  badge?: 'queue' | 'ports';
}

// 문서 §6의 탭 행 그대로: 소스컨트롤 | 큐 N | 포트 N | 사용량 | ›(접기).
// 스니펫은 dock 탭이 아니다(60 §4에서 큐 탭 안의 스코프로 들어간다) — 그때까지
// 레일 "⋯ 더보기"의 팝업으로 남는다.
const TABS: DockTab[] = [
  { id: 'scm',   label: '소스컨트롤', panelId: 'vt-dock-scm', action: 'scm.show' },
  { id: 'queue', label: '큐',        panelId: 'vt-queue', action: 'queue.show', badge: 'queue' },
  { id: 'ports', label: '포트',      panelId: 'vt-ports', action: 'ports.show', cap: 'ports', badge: 'ports' },
  { id: 'usage', label: '사용량',    panelId: 'vt-usage', action: 'usage.open', cap: 'usage' },
];

function Dock(props: { deps: DockDeps }) {
  const [caps, setCaps] = createSignal<Record<string, any> | null>(null);
  const [badges, setBadges] = createSignal<Record<string, number>>({});
  // 접힘 기본값은 티어를 따른다(§3 표): xwide(≥1600)만 열림 기본, 그 아래는
  // 접힘 36px. 저장된 값이 있으면 그게 이긴다 — 사용자가 직접 정한 것이므로.
  const stored = localStorage.getItem(COLLAPSE_KEY);
  const [collapsed, setCollapsed] = createSignal(
    stored === null ? window.innerWidth < WIDE_MAX : stored === '1',
  );
  const [activeTab, setActiveTab] = createSignal(localStorage.getItem(TAB_KEY) || 'scm');
  const [width, setWidth] = createSignal(
    Math.min(MAX_W, Math.max(MIN_W, parseInt(localStorage.getItem(W_KEY) || '', 10) || DEFAULT_W)),
  );

  let bodyRef: HTMLDivElement | undefined;

  const tabs = createMemo(() => {
    const c = caps();
    // capabilities 응답 전에는 게이팅 대상 탭을 그리지 않는다 — 나타났다
    // 사라지는 것보다 늦게 나타나는 쪽이 낫다(2.0 needs-* 규칙과 같은 방향).
    return TABS.filter((t) => !t.cap || (c ? !!c[t.cap] : false));
  });

  // 게이팅으로 활성 탭이 사라지면(사용량 소스 없음 등) 첫 탭으로 물러난다.
  createEffect(() => {
    const list = tabs();
    if (list.length && !list.some((t) => t.id === activeTab())) setActiveTab(list[0].id);
  });

  // ── 폭·접힘을 CSS 변수 하나로 합친다 ─────────────────────────────────────
  // Rail.tsx가 실브라우저에서 잡았던 버그와 같은 함정: body의 padding-right가
  // 같은 변수를 읽으므로, 접었을 때 이 값이 안 따라오면 dock은 36px로 줄었는데
  // 본문은 392px만큼 밀린 채 빈 틈이 남는다.
  createEffect(() => {
    document.documentElement.style.setProperty(
      '--vt-dock-w', `${collapsed() ? COLLAPSED_W : width()}px`,
    );
  });
  onCleanup(() => document.documentElement.style.removeProperty('--vt-dock-w'));

  // ── 탭 ↔ 패널 렌더러 동기화 ─────────────────────────────────────────────
  // 단일 방향: "활성 탭의 패널만 DOM에 있다". 액션을 밖에서 부르는 경로
  // (팔레트·키맵·레일 ⋯)도 결국 dock 호스트를 거쳐 activeTab을 바꾸므로,
  // 열고 닫는 판단은 여기 한 곳에만 있다.
  const act = (name: string) => (props.deps.getAction(name) as (() => void) | undefined)?.();
  createEffect(() => {
    const active = activeTab();
    const open = !collapsed();
    for (const t of TABS) {
      if (!t.panelId || !t.action) continue;
      const el = document.getElementById(t.panelId);
      const want = open && t.id === active;
      if (want && !el) act(t.action);
      else if (!want && el) (window as any).closePanel?.(t.panelId);
    }
  });

  // 패널 모듈이 openPanel()을 부를 때 panel.js가 물어보는 호스트. 여기서
  // 탭을 바꾸면 위 effect와 재진입이 되므로(호스트 요청 → 탭 변경 → 다시
  // 액션 호출) 탭 변경은 마이크로태스크로 미룬다 — 그때는 이미 패널
  // 엘리먼트가 DOM에 있어서 effect가 아무것도 하지 않는다.
  (window as any).vtDockHost = (panelId: string): HTMLElement | null => {
    const tab = TABS.find((t) => t.panelId === panelId);
    if (!tab || !bodyRef) return null;
    if (tab.cap && !tabs().some((t) => t.id === tab.id)) return null; // 게이팅된 탭은 dock에 안 붙는다
    queueMicrotask(() => { setCollapsed(false); selectTab(tab.id); });
    return bodyRef;
  };
  onCleanup(() => { delete (window as any).vtDockHost; });

  const selectTab = (id: string) => {
    setActiveTab(id);
    localStorage.setItem(TAB_KEY, id);
  };
  const onTabClick = (id: string) => {
    if (collapsed()) { setCollapsedPersist(false); selectTab(id); return; }
    if (activeTab() === id) { setCollapsedPersist(true); return; }  // 같은 탭 재클릭 = 접기
    selectTab(id);
  };
  const setCollapsedPersist = (v: boolean) => {
    setCollapsed(v);
    localStorage.setItem(COLLAPSE_KEY, v ? '1' : '0');
  };

  // ── 배지(큐 대기 수 · 포트 수) ───────────────────────────────────────────
  // rail.js의 배지 폴링과 같은 값·같은 주기다. 그 파일을 import 해서 쓸 수는
  // 없어(지연 청크 제약) 호출만 같은 모양으로 반복한다 — 응답 두 개, 15초.
  const refreshBadges = async () => {
    if (document.hidden) return;
    const next: Record<string, number> = { ...badges() };
    try { next.queue = ((await props.deps.vtFetch('/api/queue'))?.items || []).length; } catch (_) {}
    if (caps()?.ports) {
      try {
        const p = await props.deps.vtFetch('/api/ports');
        next.ports = (p?.ports || []).filter((x: any) => !x.protected).length;
      } catch (_) {}
    }
    setBadges(next);
  };
  props.deps.vtFetch('/api/capabilities').then((c) => setCaps(c || {})).catch(() => setCaps({}));
  refreshBadges();
  const badgeTimer = setInterval(refreshBadges, BADGE_POLL_MS);
  onCleanup(() => clearInterval(badgeTimer));

  const wireResizer = (el: HTMLDivElement) => {
    wireRatioResizer(el, {
      dir: 'row',
      getContainerSize: () => 1,
      // 오른쪽 레일이라 드래그 방향이 폭과 **반대**다: 왼쪽으로 끌면 넓어진다.
      getStartRatio: () => -width(),
      onRatio: (r: number) => {
        if (collapsed()) return;
        const w = Math.min(MAX_W, Math.max(MIN_W, Math.round(-r)));
        setWidth(w);
        localStorage.setItem(W_KEY, String(w));
      },
      onStart: () => {},
      onEnd: () => {},
    });
  };

  const badgeOf = (t: DockTab) => (t.badge ? badges()[t.badge] || 0 : 0);

  return (
    <aside id="vt-dock" classList={{ collapsed: collapsed() }} aria-label="dock">
      <div class="vt-dock-tabs" role="tablist">
        <For each={tabs()}>
          {(t) => (
            <button
              type="button"
              class="vt-dock-tab"
              classList={{ active: !collapsed() && activeTab() === t.id }}
              role="tab"
              aria-selected={!collapsed() && activeTab() === t.id}
              title={t.label}
              onClick={() => onTabClick(t.id)}
            >
              {/* 접힘 36px에서는 CSS(writing-mode)가 이 라벨을 세로로 세운다 —
                  아이콘 세트를 새로 만들지 않고도 "무슨 탭인지"가 남는다. */}
              <span class="vt-dock-tab-label">{t.label}</span>
              <Show when={badgeOf(t) > 0}>
                <span class="vt-dock-badge">{badgeOf(t) > 99 ? '99+' : badgeOf(t)}</span>
              </Show>
            </button>
          )}
        </For>
        <Show when={!collapsed()}>
          <button type="button" class="vt-dock-collapse" onClick={() => setCollapsedPersist(true)} aria-label="dock 접기" title="접기">›</button>
        </Show>
      </div>
      <div class="vt-dock-body" ref={bodyRef}>
        <Show when={!collapsed() && activeTab() === 'scm'}>
          <div class="vt-dock-empty">소스컨트롤</div>
        </Show>
      </div>
      <div ref={wireResizer} class="vt-dock-resizer" />
    </aside>
  );
}

export function mountDock(root: HTMLElement, deps: DockDeps) {
  return render(() => <Dock deps={deps} />, root);
}
