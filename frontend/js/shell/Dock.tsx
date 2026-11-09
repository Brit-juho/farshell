// N35 §6의 dock 본문. 리디자인 1단계(작업대)부터 dock은 **사이드바 안의 도구 보기**다 —
// 탭 줄·접기·폭 리사이저·마이크·MCP·설정 버튼은 활동 막대와 작업대(Workbench.tsx)로
// 옮겨 갔고, 여기 남은 책임은 둘이다:
//   1. 「지금 펼친 도구 활동의 패널만 본문에 있다」 — 활동 ↔ 패널 렌더러 동기화
//   2. `window.vtDockHost` — panels/panel.js가 패널을 어디에 붙일지 묻는 계약
// 탭 "내용"은 여기서 새로 그리지 않는다 — 이미 있는 패널 렌더러(queue.js·ports.js·
// panels/usage.js·panels/viewer/scm.js …)를 그대로 쓰고, 그것들이 그리던 자리만 모달에서
// 이 본문으로 옮긴다(panels/panel.js의 dock 호스트). 같은 화면을 두 벌 만들면 반드시 어긋난다.
//
// ⚠ Rail.tsx와 같은 제약: 이 파일은 지연 청크(shell.js)에 들어가므로
// core/store.js·term/session.js 같은 **상태를 가진 모듈을 정적 import 하지
// 않는다**(ADR-26/N35 — Vite lib 모드가 청크 안에 복제해 넣어 앱과 다른
// 객체가 된다).
import { batch, createEffect, onCleanup } from 'solid-js';
import { TOOL_ACTIVITIES, activityByPanelId, type Activity } from './activity-data.js';

export interface DockDeps {
  vtFetch: (path: string) => Promise<any>;
  getAction: (name: string) => unknown;
}

export interface DockProps {
  deps: DockDeps;
  /** 지금 사이드바에 펼쳐진 도구 활동 id. 세션 활동이거나 접혀 있으면 null. */
  active: () => string | null;
  /** 지금 보이는 활동 목록(capability 게이팅 뒤). 게이팅된 도구는 dock에 붙지 않는다. */
  available: () => Activity[];
  /** 밖(팔레트·키맵·레일 ⋯)에서 도구 패널을 열었다 — 그 활동으로 사이드바를 펼쳐 달라. */
  onExternalOpen: (id: string) => void;
}

export function Dock(props: DockProps) {
  let bodyRef: HTMLDivElement | undefined;

  // ── 활동 ↔ 패널 렌더러 동기화 ─────────────────────────────────────────────
  // 단일 방향: "펼친 도구 활동의 패널만 DOM에 있다". 액션을 밖에서 부르는 경로
  // (팔레트·키맵·레일 ⋯)도 결국 dock 호스트를 거쳐 활동을 바꾸므로,
  // 열고 닫는 판단은 여기 한 곳에만 있다.
  const act = (name: string) => (props.deps.getAction(name) as (() => void) | undefined)?.();
  // 우리가 방금 열라고 시킨 활동. 액션이 **비동기**일 수 있어서 필요하다(소스컨트롤
  // 패널은 지연 청크를 먼저 받아온다) — 청크가 도착하기 전에 사용자가 다른 활동을
  // 누르면, 뒤늦게 도착한 패널이 이미 바뀐 활동 위에 겹쳐 붙는다(실브라우저에서
  // 재현: 큐를 눌렀는데 잠시 뒤 소스컨트롤로 되돌아갔다).
  let pendingSelf: string | null = null;
  createEffect(() => {
    const active = props.active();
    for (const a of TOOL_ACTIVITIES) {
      if (!a.panelId || !a.action) continue;
      const el = document.getElementById(a.panelId);
      const want = a.id === active;
      if (want && !el) { pendingSelf = a.id; act(a.action); }
      else if (!want && el) (window as any).closePanel?.(a.panelId);
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
    if (pendingSelf === a.id) {
      pendingSelf = null;
      if (props.active() !== a.id) return 'stale';
      return bodyRef;
    }
    // 밖에서 온 요청(팔레트·키맵·레일 ⋯)은 사이드바를 그 활동으로 펼치는 게 맞다.
    // 재진입(요청 → 활동 변경 → effect가 같은 액션을 또 호출)을 피하려고
    // 변경은 마이크로태스크로 미룬다 — 그때는 이미 패널이 DOM에 있다.
    queueMicrotask(() => batch(() => props.onExternalOpen(a.id)));
    return bodyRef;
  };
  onCleanup(() => { delete (window as any).vtDockHost; });

  return (
    <aside id="vt-dock" classList={{ collapsed: !props.active() }} aria-label="dock">
      {/* 패널 내용은 전부 패널 렌더러가 여기 붙인다(panels/panel.js의 dock 호스트). */}
      <div class="vt-dock-body" ref={bodyRef} />
    </aside>
  );
}
