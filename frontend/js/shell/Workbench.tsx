// 작업대 — 활동 막대 48 + 사이드바 하나(리디자인 1단계, 시안 desktop.html D01/D04).
//
// 사이드바 폭은 **활동 전체가 하나를 공유한다**(결정 1, shell/side-prefs.js 머리말). 그래서
// 활동을 바꿔도 칸 크기가 변하지 않고 터미널 refit·SIGWINCH도 없다. 내용은 새로 그리지 않는다:
//   - 「세션」 활동 = 지금 레일(Rail.tsx, `#vt-wgrail`)
//   - 도구 활동     = 지금 dock 본문(Dock.tsx, `#vt-dock .vt-dock-body` + `window.vtDockHost`)
// 둘 다 **늘 마운트**해 두고 `hidden`만 바꾼다 — 레일의 폴링·이름 편집 상태를 잃지 않는다.
//
// 접기: 펼친 상태에서 지금 활동을 다시 누르거나 ⌘B(`railToggle`, window.vtSideToggle).
// 세션은 48px 띠(레일의 접힘 모양), 도구는 0(활동 막대만)으로 접힌다.
//
// 폭을 끄는 동안에는 터미널 fit을 미룬다(window.vtSurfaceDrag — layout/surface.js의 refit
// 게이트). 예전 레일·dock 리사이저는 `onStart: () => {}`라 매 프레임 refit·WS resize가 나갔다.
//
// 오른쪽 고정 사이드바(리디자인 9단계, 시안 D01 · D04): 도구 패널 하나를 오른쪽 300에 고정해 왼쪽 사이드바와
// 나란히 본다(`ui.side.pinned` = 패널 id). wide는 칸 옆에 자리를 차지하고(body padding-right), regular는 칸 위에
// 뜬 서랍이다(칸 크기 그대로). 고정한 도구는 왼쪽 활동이 될 수 없다 — 패널 id가 전역 하나라서다. 고정을 켜고 끌
// 때는 칸 폭이 한 번 바뀌므로 refit 게이트로 감싸 칸마다 fit 한 번이 되게 한다.
//
// ⚠ 지연 청크(shell) — core/store.js·layout/surface.js·core/settings.js 같은 상태를 가진
// 모듈을 정적 import 하지 않는다(ADR-26). 설정은 window 브리지(vtSettingsGet/Has/Set)로만.
import { batch, createEffect, createMemo, createSignal, onCleanup, onMount } from 'solid-js';
import { render } from 'solid-js/web';
import { Rail, type RailDeps } from './Rail.js';
import { Dock } from './Dock.js';
import { ActivityBar } from './ActivityBar.js';
import { availableActivities, activityById } from './activity-data.js';
import {
  SESSIONS_ACTIVITY, SIDE_WIDTH_KEY, SIDE_ACTIVITY_KEY, SIDE_COLLAPSED_KEY, SIDE_PINNED_KEY,
  readSideWidth, readSideActivity, readSideCollapsed, readSidePinned, effectiveActivity, effectivePinned, sideMode,
  nextOnActivityClick, clampSideWidth,
} from './side-prefs.js';
import { t } from '../core/i18n.js';
// 상태 없는 순수 모듈 — 지연 청크에 복제돼도 된다.
import { createPoll } from '../core/poll.js';
import { wireRatioResizer } from '../layout/resizer.js';
// 마이크 노드를 활동 막대 아래 무리로 옮긴다. 자리를 정하는 곳이 keybar.js 하나뿐이어야
// 데스크톱/터치가 갈리는 판정이 두 군데로 흩어지지 않는다(옛 Dock.tsx와 같은 import).
import { placeMicButton } from '../term/keybar.js';

const BADGE_POLL_MS = 15000;

export interface WorkbenchDeps extends RailDeps {
  /** 키맵 id → 표시용 조합(main.js가 core/keymap.js로 계산해 넘긴다). */
  keyHint?: (id: string) => string;
}

const _sGet = (k: string) => (window as any).vtSettingsGet?.(k);
const _sHas = (k: string) => Boolean((window as any).vtSettingsHas?.(k));
const _sSet = (k: string, v: unknown) => (window as any).vtSettingsSet?.(k, v);

// MCP 버튼 — 'mcp.show'라는 core 액션을 만들지 않고 여기서 직접 지연 import 한다(core 예산).
// panels/mcp-open.js는 vite.config.js가 'settings' 청크로 못박아 core에 안 들어간다.
// 재진입 가드는 청크 도착 전 두 번 눌리면 토글이 겹치기 때문이다.
let _mcpOpening = false;
function showMcp() {
  if (_mcpOpening) return;
  _mcpOpening = true;
  import('../panels/mcp-open.js').then(
    (m) => { _mcpOpening = false; m.showMcp(); },
    (e) => { _mcpOpening = false; console.error('[FarShell mcp]', e); },
  );
}

function Workbench(props: { deps: WorkbenchDeps }) {
  const [caps, setCaps] = createSignal<Record<string, any> | null>(null);
  const [badges, setBadges] = createSignal<Record<string, number>>({});
  // 「세션」 배지(waiting 수)는 레일이 이미 세는 값을 받는다 — 폴링하는 큐·포트와 따로 둬야 비동기
  // 갱신이 서로의 값을 덮지 않는다.
  const [waitingCount, setWaitingCount] = createSignal(0);
  const allBadges = createMemo(() => ({ ...badges(), waiting: waitingCount() }));
  // 펼쳤을 때 쓰고 싶은 폭(사용자 의도). 화면에 그리는 폭은 CSS가 창 폭으로 한 번 더 누른다.
  const [storedWidth, setStoredWidth] = createSignal(readSideWidth(_sGet, _sHas));
  const [activity, setActivity] = createSignal(readSideActivity(_sGet, _sHas));
  const [collapsed, setCollapsed] = createSignal(readSideCollapsed(_sGet, _sHas, window.innerWidth));

  const [pinnedPanel, setPinnedPanel] = createSignal(readSidePinned(_sGet, _sHas));

  const available = createMemo(() => availableActivities(caps()));
  // compact(<720)에는 작업대가 없다(CSS로 숨는다 — 하단 내비가 대신한다). 그 동안 도구 패널을
  // 열면 panels/panel.js가 dock 대신 풀스크린 모달로 띄워 폰 화면을 덮으므로, 열린 도구를 없는
  // 것으로 친다. 레일은 compact에서 스스로 폴링을 쉰다(Rail.tsx).
  const desktopMq = typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 720px)') : null;
  const [desktop, setDesktop] = createSignal(desktopMq ? desktopMq.matches : window.innerWidth >= 720);
  const onMq = () => setDesktop(desktopMq ? desktopMq.matches : window.innerWidth >= 720);
  desktopMq?.addEventListener?.('change', onMq);
  onCleanup(() => desktopMq?.removeEventListener?.('change', onMq));
  /** 오른쪽에 고정해 보이는 도구 활동. 없거나 지금 쓸 수 없거나 compact면 null. */
  const pinned = createMemo(() => (desktop() ? effectivePinned(pinnedPanel(), available()) : null));
  // 고정한 도구는 왼쪽 활동 후보에서 뺀다 — 저장된 왼쪽 활동이 그것이면 세션으로 물러난다(저장값은 그대로).
  const current = createMemo(() => {
    const pin = pinned();
    return effectiveActivity(activity(), available().map((a) => a.id).filter((id) => id !== pin));
  });
  const mode = createMemo(() => sideMode(collapsed(), current()));
  /** 사이드바에 펼쳐진 도구 활동. 세션이거나 접혀 있거나 compact면 null — Dock.tsx가 이것만 본다. */
  const openTool = createMemo(() => (desktop() && mode() === 'open' && current() !== SESSIONS_ACTIVITY ? current() : null));

  // 같은 값이면 core/settings.js의 set()이 저장을 건너뛴다.
  const setActivityPersist = (id: string) => { setActivity(id); _sSet(SIDE_ACTIVITY_KEY, id); };
  const setCollapsedPersist = (v: boolean) => { setCollapsed(v); _sSet(SIDE_COLLAPSED_KEY, v); };
  // batch: 두 시그널을 따로 쓰면 Dock의 effect가 **중간 상태**(펼침 + 옛 활동)로 한 번 돌아
  // 엉뚱한 패널을 연다(옛 Dock.tsx pendingSelf 주석의 그 버그).
  const select = (id: string) => {
    // 고정한 도구의 활동 버튼은 왼쪽을 바꾸지 않고 오른쪽 그 패널로 초점을 보낸다.
    if (id === pinned()) { focusPinned(); return; }
    const next = nextOnActivityClick({ activity: current(), collapsed: collapsed() }, id);
    batch(() => { setActivityPersist(next.activity); setCollapsedPersist(next.collapsed); });
  };
  const toggle = () => setCollapsedPersist(!collapsed());
  const openActivity = (id: string) => batch(() => { setActivityPersist(id); setCollapsedPersist(false); });

  (window as any).vtSideToggle = toggle;
  onCleanup(() => { delete (window as any).vtSideToggle; });

  // ── 오른쪽 고정 ─────────────────────────────────────────────────────────
  const [rightBody, setRightBody] = createSignal<HTMLElement | undefined>(undefined);
  const surfaceDrag = (phase: 'begin' | 'end') => (window as any).vtSurfaceDrag?.(phase);
  // 칸 폭이 한 번 바뀌는 일(고정 켜기·끄기)을 refit 게이트로 감싼다 — 바뀐 폭을 ResizeObserver가 다음 프레임에
  // 알리므로 두 프레임 뒤에 닫는다(아래 폭 리사이저와 같은 이유).
  const withRefitGate = (fn: () => void) => {
    surfaceDrag('begin');
    const raf = typeof window.requestAnimationFrame === 'function'
      ? (f: () => void) => window.requestAnimationFrame(f) : (f: () => void) => setTimeout(f, 16);
    try { fn(); } finally { raf(() => raf(() => surfaceDrag('end'))); }
  };
  const setPinnedPersist = (panelId: string) => { setPinnedPanel(panelId); _sSet(SIDE_PINNED_KEY, panelId); };
  /** 패널 머리 버튼: 왼쪽 패널이면 오른쪽에 고정(왼쪽은 세션으로), 고정한 패널이면 해제(왼쪽 그 활동으로 돌려놓는다). */
  const togglePin = (id: string) => {
    const a = activityById(id);
    if (!a || !a.panelId) return;
    withRefitGate(() => batch(() => {
      if (pinned() === id) {
        setPinnedPersist('');
        setActivityPersist(id);
        setCollapsedPersist(false);
      } else {
        setPinnedPersist(a.panelId!);
        if (current() === id) setActivityPersist(SESSIONS_ACTIVITY);
      }
    }));
  };
  const unpinClosed = (id: string) => { if (pinned() === id) withRefitGate(() => setPinnedPersist('')); };
  function focusPinned() {
    const el = rightBody()?.querySelector<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    el?.focus();
  }
  // 넓이 판단(docked/drawer)은 CSS 미디어쿼리 몫이다. 여기서는 「고정한 것이 있다」만 알린다.
  createEffect(() => {
    const root = document.documentElement;
    if (pinned()) root.dataset.vtPin = pinned()!;
    else delete root.dataset.vtPin;
  });
  onCleanup(() => { delete document.documentElement.dataset.vtPin; });

  // ── 폭·표시 상태를 CSS로 ────────────────────────────────────────────────
  // `--vt-side-w`·`--vt-activity-w`는 90-workbench.css가 이 두 값으로 계산한다(티어별 상한은
  // 미디어쿼리 몫이라 창 크기를 바꿀 때 JS가 다시 돌 필요가 없다). 폭·padding에 transition 없음.
  createEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--vt-side-stored', `${storedWidth()}px`);
    root.dataset.vtSide = mode();
  });
  onCleanup(() => {
    const root = document.documentElement;
    root.style.removeProperty('--vt-side-stored');
    delete root.dataset.vtSide;
  });

  // ── 도구 활동을 칸으로 끌어내기(N4 3단계) ─────────────────────────────────
  // 배선은 core(layout/dnd.js)에 있고 여기서는 브리지로 부른다. 끌어낸 도구가 지금 사이드바에
  // 펼쳐진 그 도구면 사이드바를 접는다 — 같은 패널이 두 자리에 떠 있으면 어느 쪽이 "그"
  // 화면인지 알 수 없다. 안 보이던 도구였다면 할 일이 없다.
  const wireDrag = (el: HTMLElement, id: string) => (window as any).vtWirePanelDragSource?.(el, id);
  const onPanelDropped = (e: Event) => {
    const panel = (e as CustomEvent).detail?.panel;
    if (panel && panel === openTool()) setCollapsedPersist(true);
  };
  window.addEventListener('vt-panel-pane-drop', onPanelDropped);
  onCleanup(() => window.removeEventListener('vt-panel-pane-drop', onPanelDropped));

  // ── 배지(큐 대기 수 · 포트 수) ───────────────────────────────────────────
  // 옛 dock 탭과 같은 값·같은 주기. 폴링은 core/poll.js — 이전 조회가 안 끝났으면 건너뛰고,
  // 둘 다 실패하면 간격을 늘린다. 실패한 쪽 배지는 직전 값을 그대로 둔다.
  const refreshBadges = async () => {
    const next: Record<string, number> = { ...badges() };
    let ok = false;
    try { next.queue = (((await props.deps.vtFetch('/api/queue')) as any)?.items || []).length; ok = true; } catch (_) {}
    if (caps()?.ports) {
      try {
        const p = (await props.deps.vtFetch('/api/ports')) as any;
        next.ports = (p?.ports || []).filter((x: any) => !x.protected).length;
        ok = true;
      } catch (_) {}
    }
    setBadges(next);
    if (!ok) throw new Error('activity badges: no response');
  };
  props.deps.vtFetch('/api/capabilities').then((c) => setCaps((c as any) || {})).catch(() => setCaps({}));
  const badgePoll = createPoll(refreshBadges, { interval: BADGE_POLL_MS, immediate: true });
  badgePoll.start();
  onCleanup(() => badgePoll.stop());

  // keybar.js는 모듈 평가 시점에 한 번 자리를 잡는데, 그때 활동 막대는 아직 없다(지연 로드).
  // 마운트된 뒤 한 번 더 부른다 — 이미 제자리면 아무 일도 안 한다.
  onMount(() => placeMicButton());

  // ── 폭 리사이저 ─────────────────────────────────────────────────────────
  // 끄는 동안 저장 폭(시그널)만 바꾸고, 기기 설정 저장은 놓을 때 한 번 한다(매 프레임 PUT 금지).
  // fit은 놓은 뒤 한 번: begin으로 refit 게이트를 세우고, 놓은 다음 **두 프레임 뒤**에 end —
  // 마지막 pointermove의 폭 변화를 ResizeObserver가 다음 프레임에 알리므로, 바로 end를 부르면
  // 그 보고가 게이트 밖에서 한 번 더 fit을 내 칸마다 WS resize가 두 번 나갈 수 있다.
  let sideRef: HTMLDivElement | undefined;
  const wireResizer = (el: HTMLDivElement) => {
    wireRatioResizer(el, {
      dir: 'row',
      getContainerSize: () => 1, // 컨테이너=1로 두고 비율을 그대로 픽셀 델타로 쓴다
      // 실제로 그려진 폭에서 출발한다 — CSS 변수는 min() 식이라 px로 파싱되지 않는다.
      getStartRatio: () => sideRef?.getBoundingClientRect().width || storedWidth(),
      onRatio: (r: number) => { if (mode() === 'open') setStoredWidth(clampSideWidth(r)); },
      onStart: () => surfaceDrag('begin'),
      onEnd: () => {
        requestAnimationFrame(() => requestAnimationFrame(() => surfaceDrag('end')));
        _sSet(SIDE_WIDTH_KEY, storedWidth());
      },
    });
  };

  const act = (name: string) => (props.deps.getAction(name) as (() => void) | undefined)?.();
  const keyHint = (id: string) => props.deps.keyHint?.(id) || '';
  const sideLabel = () => activityById(current())?.label || '';
  const pinnedLabel = () => activityById(pinned() || '')?.label || '';

  return (
    <>
    <div id="vt-workbench" class="vt-sidepanel left" data-mode={mode()}>
      <ActivityBar
        items={available}
        current={current}
        pinned={pinned}
        shown={() => mode() !== 'closed'}
        badges={allBadges}
        onSelect={select}
        onMcp={showMcp}
        onSettings={() => act('settings.show')}
        keyHint={keyHint}
        wireDrag={wireDrag}
      />
      <div id="vt-side" class="vt-side" ref={sideRef} role="tabpanel" aria-label={sideLabel()} hidden={mode() === 'closed'}>
        <div class="vt-side-view vt-side-sessions" hidden={current() !== SESSIONS_ACTIVITY}>
          <Rail deps={props.deps} collapsed={() => mode() === 'strip'} onToggleCollapse={toggle} onWaitingCount={setWaitingCount} />
        </div>
        <div class="vt-side-view vt-side-tools" hidden={current() === SESSIONS_ACTIVITY}>
          <Dock
            deps={props.deps as any}
            active={openTool}
            pinned={pinned}
            rightBody={rightBody}
            available={available}
            onExternalOpen={openActivity}
            onTogglePin={togglePin}
            onPinnedClosed={unpinClosed}
          />
        </div>
        <div ref={wireResizer} class="vt-side-resizer" hidden={mode() !== 'open'} />
      </div>
    </div>
    {/* 오른쪽 고정 사이드바 — 늘 마운트하고 hidden만 바꾼다. 고정을 풀 때 Dock이 패널 노드를 왼쪽으로 옮기기
        전에 이 상자가 DOM에서 빠지면 패널이 함께 떨어져 나가 다시 열어야 한다(상태를 잃는다). */}
    <aside id="vt-side-right" class="vt-sidepanel right vt-side-pinned" hidden={!pinned()}
      aria-label={pinned() ? t('오른쪽 고정: {name}', { name: pinnedLabel() }) : undefined}>
      <div class="vt-side-pinned-body" ref={setRightBody} />
    </aside>
    </>
  );
}

export function mountWorkbench(root: HTMLElement, deps: WorkbenchDeps) {
  return render(() => <Workbench deps={deps} />, root);
}
