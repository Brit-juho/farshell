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
import { batch, createSignal, createMemo, createEffect, onCleanup, onMount, For, Show } from 'solid-js';
import { icon } from '../ui/icons.js';
import { t } from '../core/i18n.js';
import { render } from 'solid-js/web';
import { wireRatioResizer } from '../layout/resizer.js';
import { WIDE_MAX } from '../layout/breakpoints.js';
// 마이크 노드를 이 탭 줄 밑으로 옮긴다. 자리를 정하는 곳이 keybar.js 하나뿐이어야
// 데스크톱/터치가 갈리는 판정이 두 군데로 흩어지지 않는다(L7, Rail.tsx에 있던
// 것과 같은 이유 — 2026-09-18(2차)에 그 레일에서 여기로 옮겨왔다).
import { placeMicButton } from '../term/keybar.js';

const MIN_W = 320, MAX_W = 560, DEFAULT_W = 392, COLLAPSED_W = 36;
// N3(60-settings-palette.md §1) — device-settings 정식 스토어. Rail.tsx와
// 같은 이유로 core/settings.js를 정적 import하지 않고 window 브리지로만 쓴다.
const SETTINGS_W_KEY = 'ui.dock.width', SETTINGS_TAB_KEY = 'ui.dock.tab', SETTINGS_COLLAPSE_KEY = 'ui.dock.collapsed';
const _sGet = (k: string) => (window as any).vtSettingsGet?.(k);
const _sSet = (k: string, v: unknown) => (window as any).vtSettingsSet?.(k, v);
const _sHas = (k: string) => Boolean((window as any).vtSettingsHas?.(k));
const BADGE_POLL_MS = 15000;

// MCP 버튼(사용자 요청, 2026-09-28) — 'mcp.show'라는 이름의 core 액션을 새로
// 만드는 대신 여기서 직접 지연 import 한다. core.js(상시 초기 페이로드)의
// 300KiB 상한이 이미 거의 다 찬 상태라, registerAction 방식으로 만들었더니
// 몇백 바이트 차이로 그 상한을 넘겼다(실측) — 이 파일(shell.js)은 그 상한
// 대상이 아니고, panels/mcp-open.js는 vite.config.js가 'settings' 청크로
// 못박아서 어차피 core에 안 들어간다. 재진입 가드는 ports-lazy.js와 같은
// 이유(청크 도착 전 두 번 눌리면 토글이 겹친다).
let _mcpOpening = false;
function showMcp() {
  if (_mcpOpening) return;
  _mcpOpening = true;
  import('../panels/mcp-open.js').then(
    (m) => { _mcpOpening = false; m.showMcp(); },
    (e) => { _mcpOpening = false; console.error('[FarShell mcp]', e); },
  );
}

export interface DockDeps {
  vtFetch: (path: string) => Promise<any>;
  getAction: (name: string) => unknown;
}

interface DockTab {
  id: string;
  label: string;
  /** 접힘 36px에서 라벨 대신 보이는 아이콘(ui/icons.js 이름). */
  icon: string;
  panelId: string | null;   // 기존 패널 렌더러의 루트 id. null이면 자체 빈 상태.
  action: string | null;    // 그 패널을 여는 액션 id(팔레트·키맵이 참조하는 그 id)
  cap?: 'ports' | 'usage';  // /api/capabilities에 없으면 탭 자체가 사라진다
  badge?: 'queue' | 'ports';
}

// 문서 §6의 탭 행 그대로: 소스컨트롤 | 큐 N | 포트 N | 사용량 | ›(접기).
// 스니펫은 2026-10-23(사용자 요청)부터 dock 탭이다. 이전 판단(「60 §4에서 큐 탭 안의
// 스코프로 들어간다」 → 레일 ⋯ 메뉴의 팝업)을 뒤집은 것 — 자주 쓰는 명령을 한 번에
// 꺼내 쓰는 도구라 메뉴 두 단계 안쪽에 두면 안 쓰게 된다. compact에서는 dock이 없어
// 예전처럼 시트로 뜬다.
// 「파일」(디렉토리 트리)은 N35 §6에서 지운 폴더 트리를 되살린 것(2026-09-22,
// 사용자 확인 — N35 번복). 업로드 파일을 관리하던 옛 「파일」 탭은 그 이름을
// 내주고 「첨부」로 물러났다 — 둘 다 "파일"이면 구분이 안 된다.
const TABS: DockTab[] = [
  { id: 'scm',   label: t('소스컨트롤'), icon: 'git-branch', panelId: 'vt-dock-scm', action: 'scm.show' },   // 40 §3 (2.1.0은 읽기 전용)
  { id: 'dir',   label: t('파일'), icon: 'folder',    panelId: 'vt-dir', action: 'dir.show' },
  { id: 'queue', label: t('큐'), icon: 'list',        panelId: 'vt-queue', action: 'queue.show', badge: 'queue' },
  { id: 'files', label: t('첨부'), icon: 'paperclip',   panelId: 'vt-files', action: 'files.show' },  // 50 §4
  { id: 'snippets', label: t('스니펫'), icon: 'braces', panelId: 'vt-snippets', action: 'snippets.show' },
  { id: 'ports', label: t('포트'), icon: 'plug',      panelId: 'vt-ports', action: 'ports.show', cap: 'ports', badge: 'ports' },
  { id: 'usage', label: t('사용량'), icon: 'gauge',    panelId: 'vt-usage', action: 'usage.open', cap: 'usage' },
  { id: 'system', label: t('시스템'), icon: 'bar-chart', panelId: 'vt-system', action: 'system.open' },   // CPU·RAM·네트워크
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

  // keybar.js는 모듈 평가 시점에 한 번 자리를 잡는데, 그때 이 dock은 아직 없다
  // (지연 로드). 마운트된 뒤 한 번 더 부른다 — 이미 제자리면 아무 일도 안 한다.
  onMount(() => placeMicButton());

  // ── N4 3단계: 탭을 pane으로 끌어내기 ────────────────────────────────────
  // 배선 자체는 core(layout/dnd.js)에 있고 여기서는 브리지로 부른다 — 이
  // 파일은 지연 청크라 dnd.js를 정적 import 하면 그쪽이 들고 있는
  // layout/store.js가 복제된다(ADR-26, 파일 머리말의 그 제약).
  const wireDrag = (el: HTMLElement, id: string) =>
    (window as any).vtWirePanelDragSource?.(el, id);

  // 끌어낸 탭이 지금 dock에 펼쳐져 있던 그 탭이면 dock을 접는다. 같은 패널이
  // dock과 pane에 동시에 떠 있으면(1단계 인스턴스 계약상 기술적으로는 가능하다)
  // 어느 쪽이 "그" 화면인지 알 수 없고, 무엇보다 끌어낸다는 동작과 화면 결과가
  // 어긋난다. 활성 탭이 아니었다면 애초에 안 보이던 것이라 할 일이 없다.
  const onPanelDropped = (e: Event) => {
    const panel = (e as CustomEvent).detail?.panel;
    if (panel && panel === activeTab() && !collapsed()) setCollapsedPersist(true);
  };
  window.addEventListener('vt-panel-pane-drop', onPanelDropped);
  onCleanup(() => window.removeEventListener('vt-panel-pane-drop', onPanelDropped));

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
  // 수치는 탭에서 내려가고 툴팁으로 올라간다(아래 vt-dock-tab-dot 주석).
  // 단위를 붙이는 이유: 툴팁에는 자리가 있고, "14"보다 "14개 열림"이 한 번에
  // 읽힌다. 배지 칸에서는 못 하던 일이다.
  const tipSub = (tab: DockTab) => {
    const n = badgeOf(tab);
    if (!n) return undefined;
    return tab.badge === 'queue' ? t('{n}개 대기', { n }) : t('{n}개 열림', { n });
  };

  return (
    <aside id="vt-dock" class="vt-sidepanel right" classList={{ collapsed: collapsed() }} aria-label="dock">
      <div class="vt-dock-tabs" role="tablist">
        {/* 2026-09-28(사용자 지적) — 탭과 설정·마이크·접기가 서로 다른 부모
            (스크롤 래퍼 안/밖)에 있어서 같은 줄인데도 셋이 제각각 높이·테두리
            규칙을 따로 들고 있었다(80-dock.css가 :empty·:last-child로 서로
            보정해야 했던 그 얽힘). 전부 이 한 줄의 **형제**로 편다 — 탭이든
            액션이든 `.vt-dock-tab` 하나만 공유하면 높이는 부모의
            align-items:stretch가 채워주므로 개별 px 지정이 필요 없어진다.
            줄 전체가 넘치면(좁은 dock) 통째로 가로 스크롤된다(아래 CSS) —
            탭 개수가 늘어도 액션 버튼이 시야 밖으로 사라지는 일은 여전히
            없다: 다만 이제는 "액션만 항상 보이게"가 아니라 "필요하면
            사용자가 옆으로 스크롤해서 본다" 쪽으로 트레이드오프를 바꿨다
            (같은 부모라는 요구가 더 우선이라는 판단, 사용자 확인). */}
        <For each={tabs()}>
          {(t) => (
            <button
              type="button"
              class="vt-dock-tab"
              classList={{ active: !collapsed() && activeTab() === t.id }}
              role="tab"
              aria-selected={!collapsed() && activeTab() === t.id}
              aria-label={t.label}
              data-tip={t.label}
              data-tip-sub={tipSub(t)}
              data-tip-side="left"
              ref={(el) => wireDrag(el, t.id)}
              onClick={() => onTabClick(t.id)}
            >
              {/* 접힘 36px에서는 **아이콘만** 보인다. 이전에는 CSS의
                  writing-mode:vertical-rl로 이 한글 라벨을 세로로 세웠는데,
                  한글은 세로쓰기에서 음절이 한 자씩 쌓여 "소/스/컨/트/롤"이
                  되고 모노스페이스까지 겹쳐 읽을 수 없었다. 세로 라벨은
                  영문을 전제한 패턴이라 한글에는 성립하지 않는다.
                  전체 이름은 data-tip이 들고 있다(ui/tooltip.js). dock은 화면
                  오른쪽 끝이라 툴팁은 왼쪽으로 나온다. */}
              <span class="vt-dock-tab-ico" innerHTML={icon(t.icon, 16)} />
              {/* 2026-09-18 — 배지가 수치(.vt-badge)에서 점으로 내려왔던 이유:
                  접힘 36px에서 탭의 clientWidth 35에 scrollWidth가 38이 되어
                  (아이콘 16 + gap 4 + 배지 min-width 18) .vt-dock-tabs의
                  overflow:hidden에 잘렸다. 점은 absolute라 폭에 0px 기여해서
                  안 밀린다 — 접힘은 지금도 이 이유가 그대로라 점을 유지한다.
                  2026-09-19(7차) — 펼침은 사정이 다르다: 그 폭 제약이
                  없고(392px+, 게다가 탭 목록이 이제 자체 스크롤이라 넘쳐도
                  옆 버튼을 안 밀어낸다), touch 기기는 hover가 없어 점 뒤의
                  수치를 tooltip으로도 영영 못 본다 — 열어보기 전까진 "몇
                  개인지" 화면 어디에도 없었다. 펼침엔 수치 배지(.vt-badge,
                  DESIGN.md §10 — 테두리만·무채움)를 되돌린다. */}
              <Show when={badgeOf(t) > 0}>
                <Show when={collapsed()} fallback={<span class="vt-badge vt-dock-tab-badge">{badgeOf(t)}</span>}>
                  <span class="vt-dock-tab-dot" aria-hidden="true" />
                </Show>
              </Show>
            </button>
          )}
        </For>
        {/* 2026-09-18(2차) — 설정·마이크(사용자 요청으로 왼쪽 레일 바닥에서
            이식). 탭이 아니라서 role="tab"도, activeTab 동기화도 없다.
            2026-09-28 — MCP를 마이크·설정 사이에 새로 추가(독립 팝업으로
            띄운다, 위 showMcp()). 셋 다
            이제 `.vt-dock-tab .vt-dock-action`을 같이 받는다 — 탭과 같은
            크기 규칙을 쓰되 활성 표시·배지 없이 아이콘만 보인다(아래 CSS).
            순서는 마이크 → MCP → 설정: 마이크가 탭 무리와 가장 가까운
            자리라는 6차 결정을 그대로 잇는다.
            아이콘은 `link`(사용자 지적 — 바로 옆 줄의 「포트」 탭과 같은
            `plug`를 썼더니 구분이 안 됐다). 새 SVG를 icons.js에 추가하는
            대신 이미 있는 `link`를 고른 이유는 core.js 예산이다: icons.js는
            core 전체가 공유해서 core 청크에 들어가는데, 300KiB 상한이 이미
            거의 다 차 있어 path 하나만 새로 추가해도 실측으로 넘겼다(MCP
            팝업 자체를 core 밖으로 뺀 것과 같은 제약) — 그래서 이미 정의돼
            있고 실제로는 아무 데도 안 쓰이던 `link`를 재사용해 비용 0으로
            해결했다. */}
        <div id="vt-dock-mic-home" class="vt-dock-tab vt-dock-action vt-dock-mic-home" />
        <button
          type="button"
          class="vt-dock-tab vt-dock-action vt-dock-mcp-btn"
          aria-label={t('MCP · 스킬 관리')}
          data-tip={t('MCP · 스킬 관리')}
          data-tip-side="left"
          onClick={showMcp}
          innerHTML={icon('link', 16, 2)}
        />
        <button
          type="button"
          class="vt-dock-tab vt-dock-action vt-dock-settings-btn"
          aria-label={t('설정')}
          data-tip={t('설정')}
          data-tip-keys="Mod+,"
          data-tip-side="left"
          onClick={() => (props.deps.getAction('settings.show') as (() => void) | undefined)?.()}
          innerHTML={icon('settings', 16, 2)}
        />
        <Show when={!collapsed()}>
          {/* 2026-09-18(6차) — 손으로 쓴 SVG를 아이콘 레지스트리 호출로 바꾸고
              (DESIGN.md §아이콘: 아이콘 소스는 icons.js 하나뿐), 방향을 고친다.
              chevron-down을 그대로 쓰면 "접기"가 아래쪽으로 접히는 것처럼
              읽히는데, dock은 오른쪽 가장자리로 접히는 좌우 패널이다. 같은
              상황(좌우로 접히는 패널)의 기존 관용구는 Rail.tsx의
              `.vt-wgrail-collapse`(70-worktree-rail.css) — chevron-down을
              회전만 시켜 방향을 맞춘다. 그 왼쪽 레일은 펼침 상태에서 왼쪽
              (접히는 쪽)을 가리키는 90deg를 썼다. dock은 오른쪽으로 접히므로
              거울상인 -90deg(오른쪽을 가리킴). */}
          <button type="button" class="vt-dock-tab vt-dock-action vt-dock-collapse" onClick={() => setCollapsedPersist(true)} aria-label={t('dock 접기')} data-tip={t('dock 접기')} data-tip-side="bottom" innerHTML={icon('chevron-down', 14, 2)} />
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
