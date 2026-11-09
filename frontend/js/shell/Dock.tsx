// N35 §6의 dock 본문. 리디자인 1단계(작업대)부터 dock은 **사이드바 안의 도구 보기**다 —
// 탭 줄·접기·폭 리사이저·마이크·MCP·설정 버튼은 활동 막대와 작업대(Workbench.tsx)로
// 옮겨 갔고, 여기 남은 책임은 셋이다:
//   1. 「펼친 도구 활동과 오른쪽에 고정한 도구의 패널만 DOM에 있다」 — 활동 ↔ 패널 렌더러 동기화
//   2. `window.vtDockHost` — panels/panel.js가 패널을 어디에 붙일지 묻는 계약
//   3. 패널 머리의 「오른쪽에 고정 / 고정 해제」 버튼(리디자인 9단계)
// 탭 "내용"은 여기서 새로 그리지 않는다 — 이미 있는 패널 렌더러(queue.js·ports.js·
// panels/usage.js·panels/viewer/scm.js …)를 그대로 쓰고, 그것들이 그리던 자리만 모달에서
// 이 본문으로 옮긴다(panels/panel.js의 dock 호스트). 같은 화면을 두 벌 만들면 반드시 어긋난다.
//
// 오른쪽 고정 사이드바(리디자인 9단계): 고정한 도구의 패널은 왼쪽 본문 대신 `rightBody`(Workbench.tsx의
// `#vt-side-right` 본문)에 붙는다. 패널 id는 전역 하나라 한 패널이 두 자리에 동시에 있을 수 없다 — 그래서
// 고정한 도구는 왼쪽 활동이 될 수 없고(Workbench가 거른다), 고정·해제 때는 패널을 닫았다 다시 열지 않고
// **노드를 옮긴다**(폴링·스크롤·편집 상태를 잃지 않는다).
//
// ⚠ Rail.tsx와 같은 제약: 이 파일은 지연 청크(shell.js)에 들어가므로
// core/store.js·term/session.js 같은 **상태를 가진 모듈을 정적 import 하지
// 않는다**(ADR-26/N35 — Vite lib 모드가 청크 안에 복제해 넣어 앱과 다른
// 객체가 된다).
import { batch, createEffect, onCleanup } from 'solid-js';
import { t } from '../core/i18n.js';
import { shellIcon } from '../ui/icons-shell.js';
import { TOOL_ACTIVITIES, activityByPanelId, type Activity } from './activity-data.js';

export interface DockDeps {
  vtFetch: (path: string) => Promise<any>;
  getAction: (name: string) => unknown;
}

export interface DockProps {
  deps: DockDeps;
  /** 지금 사이드바에 펼쳐진 도구 활동 id. 세션 활동이거나 접혀 있으면 null. */
  active: () => string | null;
  /** 지금 오른쪽에 고정해 보이는 도구 활동 id. 없거나 compact면 null. */
  pinned?: () => string | null;
  /** 오른쪽 고정 사이드바의 본문(Workbench가 그린다). */
  rightBody?: () => HTMLElement | undefined;
  /** 지금 보이는 활동 목록(capability 게이팅 뒤). 게이팅된 도구는 dock에 붙지 않는다. */
  available: () => Activity[];
  /** 밖(팔레트·키맵·레일 ⋯)에서 도구 패널을 열었다 — 그 활동으로 사이드바를 펼쳐 달라. */
  onExternalOpen: (id: string) => void;
  /** 패널 머리의 고정 버튼 — 왼쪽 패널이면 오른쪽에 고정, 고정한 패널이면 해제. */
  onTogglePin?: (id: string) => void;
  /** 고정한 패널이 닫혔다(닫기 X · 팔레트 토글) — 고정을 푼다. */
  onPinnedClosed?: (id: string) => void;
}

const PIN_BTN = 'vt-dock-pin';

export function Dock(props: DockProps) {
  let bodyRef: HTMLDivElement | undefined;
  const pinned = () => props.pinned?.() ?? null;
  const rightBody = () => props.rightBody?.();

  // ── 활동 ↔ 패널 렌더러 동기화 ─────────────────────────────────────────────
  // 단일 방향: "펼친 도구 활동과 고정한 도구의 패널만 DOM에 있다". 액션을 밖에서 부르는 경로
  // (팔레트·키맵·레일 ⋯)도 결국 dock 호스트를 거쳐 활동을 바꾸므로,
  // 열고 닫는 판단은 여기 한 곳에만 있다.
  const act = (name: string) => (props.deps.getAction(name) as (() => void) | undefined)?.();
  // 우리가 방금 열라고 시킨 활동들. 액션이 **비동기**일 수 있어서 필요하다(소스컨트롤
  // 패널은 지연 청크를 먼저 받아온다) — 청크가 도착하기 전에 사용자가 다른 활동을
  // 누르면, 뒤늦게 도착한 패널이 이미 바뀐 활동 위에 겹쳐 붙는다(실브라우저에서
  // 재현: 큐를 눌렀는데 잠시 뒤 소스컨트롤로 되돌아갔다).
  const pendingSelf = new Set<string>();
  /** 활동 id가 지금 붙어야 할 본문. 왼쪽도 오른쪽도 아니면 null. */
  const hostOf = (id: string): HTMLElement | null => {
    if (id === pinned()) return rightBody() || null;
    if (id === props.active()) return bodyRef || null;
    return null;
  };
  createEffect(() => {
    // 두 시그널을 모두 읽어 둘 중 하나만 바뀌어도 다시 돈다.
    const active = props.active();
    const pin = pinned();
    for (const a of TOOL_ACTIVITIES) {
      if (!a.panelId || !a.action) continue;
      const el = document.getElementById(a.panelId);
      const want = a.id === active || a.id === pin;
      if (want && !el) { pendingSelf.add(a.id); act(a.action); }
      else if (want && el) {
        // 고정·해제 — 이미 열린 패널을 반대편 본문으로 옮긴다(닫았다 다시 열지 않는다).
        const host = hostOf(a.id);
        if (host && el.parentElement !== host) host.appendChild(el);
        decorate(el, a.id);
      } else if (!want && el) (window as any).closePanel?.(a.panelId);
    }
  });

  // 패널 모듈이 openPanel()을 부를 때 panel.js가 물어보는 호스트.
  // 반환값 계약(panels/panel.js가 읽는다):
  //   HTMLElement — 여기에 붙여라
  //   null        — dock 밖(모달)으로 열어라
  //   'stale'     — **열지 마라**. 우리가 시킨 열기인데 그 사이 활동이 바뀌었다.
  (window as any).vtDockHost = (panelId: string): HTMLElement | null | 'stale' => {
    const a = activityByPanelId(panelId);
    if (!a || !bodyRef) return null;
    if (a.cap && !props.available().some((x) => x.id === a.id)) return null; // 게이팅된 도구는 dock에 안 붙는다
    if (pendingSelf.has(a.id)) {
      pendingSelf.delete(a.id);
      return hostOf(a.id) || 'stale';
    }
    // 고정한 도구를 밖에서 열었다 — 오른쪽 그 자리에 연다(활동은 바꾸지 않는다).
    if (a.id === pinned()) return rightBody() || null;
    // 밖에서 온 요청(팔레트·키맵·레일 ⋯)은 사이드바를 그 활동으로 펼치는 게 맞다.
    // 재진입(요청 → 활동 변경 → effect가 같은 액션을 또 호출)을 피하려고
    // 변경은 마이크로태스크로 미룬다 — 그때는 이미 패널이 DOM에 있다.
    queueMicrotask(() => batch(() => props.onExternalOpen(a.id)));
    return bodyRef;
  };
  onCleanup(() => { delete (window as any).vtDockHost; });

  // ── 패널 머리의 고정 버튼 ───────────────────────────────────────────────
  // panel.js가 그리는 머리(.vt-viewer-head)의 닫기 X 바로 앞에 끼운다. 패널은 비동기로 붙으므로 두 본문의
  // 자식 추가를 지켜본다. 버튼 모양(고정 ↔ 해제)은 그 패널이 지금 어느 쪽에 있는지로 정한다.
  function decorate(el: Element, id: string) {
    const head = el.querySelector(':scope > .vt-viewer-card > .vt-viewer-head');
    if (!head || !props.onTogglePin) return;
    const isPinned = id === pinned();
    let btn = head.querySelector<HTMLButtonElement>(`:scope > .${PIN_BTN}`);
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `vt-icon-btn ${PIN_BTN}`;
      btn.addEventListener('click', () => props.onTogglePin?.(btn!.dataset.activity || ''));
      const close = head.querySelector(':scope > .vt-vw-x');
      head.insertBefore(btn, close);
    }
    if (btn.dataset.pinned === String(isPinned) && btn.dataset.activity === id) return;
    btn.dataset.activity = id;
    btn.dataset.pinned = String(isPinned);
    const label = isPinned ? t('오른쪽 고정 해제') : t('오른쪽에 고정(나란히 보기)');
    btn.setAttribute('aria-label', label);
    btn.setAttribute('data-tip', label);
    btn.setAttribute('data-tip-side', 'bottom');
    btn.setAttribute('aria-pressed', String(isPinned));
    btn.innerHTML = shellIcon(isPinned ? 'panel-left' : 'panel-right', 14, 2);
  }
  const decorateAll = () => {
    for (const host of [bodyRef, rightBody()]) {
      if (!host) continue;
      for (const el of Array.from(host.children)) {
        const a = activityByPanelId(el.id);
        if (a && el.classList.contains('vt-dock-panel')) decorate(el, a.id);
      }
    }
  };
  const mo = typeof MutationObserver === 'function' ? new MutationObserver(decorateAll) : null;
  createEffect(() => {
    pinned();
    const rb = rightBody();
    if (mo) {
      mo.disconnect();
      if (bodyRef) mo.observe(bodyRef, { childList: true });
      if (rb) mo.observe(rb, { childList: true });
    }
    decorateAll();
  });
  onCleanup(() => mo?.disconnect());

  // 고정한 패널이 닫혔다(닫기 X, 팔레트에서 같은 명령을 다시 눌러 토글) — 빈 오른쪽 사이드바를 남기지 않는다.
  // 우리 effect가 고정을 풀면서 닫는 경우는 그때 이미 pinned()가 바뀌어 있어 여기 걸리지 않는다.
  const onPanelClose = (e: Event) => {
    const id = (e as CustomEvent).detail?.id;
    const a = id ? activityByPanelId(id) : undefined;
    if (a && a.id === pinned() && !document.getElementById(id)) props.onPinnedClosed?.(a.id);
  };
  document.addEventListener('vt:panel-close', onPanelClose);
  onCleanup(() => document.removeEventListener('vt:panel-close', onPanelClose));

  return (
    <aside id="vt-dock" classList={{ collapsed: !props.active() }} aria-label="dock">
      {/* 패널 내용은 전부 패널 렌더러가 여기 붙인다(panels/panel.js의 dock 호스트). */}
      <div class="vt-dock-body" ref={bodyRef} />
    </aside>
  );
}
