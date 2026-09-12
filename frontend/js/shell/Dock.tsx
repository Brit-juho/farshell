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
import { batch, createSignal, createMemo, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { wireRatioResizer } from '../layout/resizer.js';
import { WIDE_MAX } from '../layout/breakpoints.js';

const MIN_W = 320, MAX_W = 560, DEFAULT_W = 392, COLLAPSED_W = 36;
// N3(60-settings-palette.md §1) — device-settings 정식 스토어. Rail.tsx와
// 같은 이유로 core/settings.js를 정적 import하지 않고 window 브리지로만 쓴다.
const SETTINGS_W_KEY = 'ui.dock.width', SETTINGS_TAB_KEY = 'ui.dock.tab', SETTINGS_COLLAPSE_KEY = 'ui.dock.collapsed';
const _sGet = (k: string) => (window as any).vtSettingsGet?.(k);
const _sSet = (k: string, v: unknown) => (window as any).vtSettingsSet?.(k, v);
const _sHas = (k: string) => Boolean((window as any).vtSettingsHas?.(k));
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
  { id: 'scm',   label: '소스컨트롤', panelId: 'vt-dock-scm', action: 'scm.show' },   // 40 §3 (2.1.0은 읽기 전용)
  { id: 'queue', label: '큐',        panelId: 'vt-queue', action: 'queue.show', badge: 'queue' },
  { id: 'files', label: '파일',      panelId: 'vt-files', action: 'files.show' },  // 50 §4
  { id: 'ports', label: '포트',      panelId: 'vt-ports', action: 'ports.show', cap: 'ports', badge: 'ports' },
  { id: 'usage', label: '사용량',    panelId: 'vt-usage', action: 'usage.open', cap: 'usage' },
];

function Dock(props: { deps: DockDeps }) {
  const [caps, setCaps] = createSignal<Record<string, any> | null>(null);
  const [badges, setBadges] = createSignal<Record<string, number>>({});
  // 접힘 기본값은 티어를 따른다(§3 표): xwide(≥1600)만 열림 기본, 그 아래는
  // 접힘 36px. 저장된 값이 있으면 그게 이긴다 — 사용자가 직접 정한 것이므로.
  const [collapsed, setCollapsed] = createSignal(
    _sHas(SETTINGS_COLLAPSE_KEY) ? Boolean(_sGet(SETTINGS_COLLAPSE_KEY)) : window.innerWidth < WIDE_MAX,
  );
  const [activeTab, setActiveTab] = createSignal(String(_sGet(SETTINGS_TAB_KEY) || 'scm'));
  const [width, setWidth] = createSignal(
    Math.min(MAX_W, Math.max(MIN_W, Number(_sGet(SETTINGS_W_KEY)) || DEFAULT_W)),
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
  // 우리가 방금 열라고 시킨 탭. 액션이 **비동기**일 수 있어서 필요하다(소스컨트롤
  // 탭은 지연 청크를 먼저 받아온다) — 청크가 도착하기 전에 사용자가 다른 탭을
  // 누르면, 뒤늦게 도착한 패널이 이미 바뀐 탭 위에 겹쳐 붙는다(실브라우저에서
  // 재현: 큐를 눌렀는데 잠시 뒤 소스컨트롤로 되돌아갔다).
  let pendingSelf: string | null = null;
  createEffect(() => {
    const active = activeTab();
    const open = !collapsed();
    for (const t of TABS) {
      if (!t.panelId || !t.action) continue;
      const el = document.getElementById(t.panelId);
      const want = open && t.id === active;
      if (want && !el) { pendingSelf = t.id; act(t.action); }
      else if (!want && el) (window as any).closePanel?.(t.panelId);
    }
  });

  // 패널 모듈이 openPanel()을 부를 때 panel.js가 물어보는 호스트. 여기서
  // 탭을 바꾸면 위 effect와 재진입이 되므로(호스트 요청 → 탭 변경 → 다시
  // 액션 호출) 탭 변경은 마이크로태스크로 미룬다 — 그때는 이미 패널
  // 엘리먼트가 DOM에 있어서 effect가 아무것도 하지 않는다.
  // 반환값 계약(panels/panel.js가 읽는다):
  //   HTMLElement — 여기에 붙여라
  //   null        — dock 밖(모달)으로 열어라
  //   'stale'     — **열지 마라**. 우리가 시킨 열기인데 그 사이 탭이 바뀌었다.
  (window as any).vtDockHost = (panelId: string): HTMLElement | null | 'stale' => {
    const tab = TABS.find((t) => t.panelId === panelId);
    if (!tab || !bodyRef) return null;
    if (tab.cap && !tabs().some((t) => t.id === tab.id)) return null; // 게이팅된 탭은 dock에 안 붙는다
    if (pendingSelf === tab.id) {
      pendingSelf = null;
      if (activeTab() !== tab.id || collapsed()) return 'stale';
      return bodyRef;
    }
    // 밖에서 온 요청(팔레트·키맵·레일 ⋯)은 탭을 그쪽으로 옮기는 게 맞다.
    // 재진입(요청 → 탭 변경 → effect가 같은 액션을 또 호출)을 피하려고
    // 탭 변경은 마이크로태스크로 미룬다 — 그때는 이미 패널이 DOM에 있다.
    queueMicrotask(() => batch(() => { setCollapsed(false); selectTab(tab.id); }));
    return bodyRef;
  };
  onCleanup(() => { delete (window as any).vtDockHost; });

  const selectTab = (id: string) => {
    setActiveTab(id);
    _sSet(SETTINGS_TAB_KEY, id);
  };
  const onTabClick = (id: string) => {
    // batch: 두 시그널을 따로 쓰면 effect가 **중간 상태**(펼침 + 옛 탭)로 한 번
    // 돌아 엉뚱한 탭의 패널을 연다(위 pendingSelf 주석의 그 버그).
    if (collapsed()) { batch(() => { setCollapsedPersist(false); selectTab(id); }); return; }
    if (activeTab() === id) { setCollapsedPersist(true); return; }  // 같은 탭 재클릭 = 접기
    selectTab(id);
  };
  const setCollapsedPersist = (v: boolean) => {
    setCollapsed(v);
    _sSet(SETTINGS_COLLAPSE_KEY, v);
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
        _sSet(SETTINGS_W_KEY, w);
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
      {/* 탭 내용은 전부 패널 렌더러가 여기 붙인다(panels/panel.js의 dock 호스트). */}
      <div class="vt-dock-body" ref={bodyRef} />
      <div ref={wireResizer} class="vt-dock-resizer" />
    </aside>
  );
}

export function mountDock(root: HTMLElement, deps: DockDeps) {
  return render(() => <Dock deps={deps} />, root);
}
