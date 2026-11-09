// 리디자인 7단계 — 집중 모드의 떠 있는 부분(시안 desktop.html D05 · D05-B · D05-C, spec.html 「집중 모드 규칙」).
//
// 켜고 끄는 스위치는 core(layout/focus.js — `<html class="vt-focus">` + 숨은 막대의 `inert`)이고, 이 파일은
// 처음 켤 때 한 번 마운트돼 다음을 그린다:
//
//   - **가장자리 감지 띠** 8px — 왼쪽 · 위 · 아래(오른쪽은 스크롤 막대와 겹쳐 쓰지 않는다). 포인터가 띠에
//     `ui.focus.edgeDelay`(0/250/500ms) 머무르면 `<html>`에 `vt-reveal-left|top|bottom`을 붙인다. 막대들은 fixed라
//     body padding이 그대로다 — **칸 크기가 바뀌지 않는다**(refit 없음, WS resize 없음). 막대는 transform·opacity로만
//     나타난다(98-focus.css). 한 번에 한쪽만. 드러난 막대에서만 `inert`를 푼다.
//     벗어나고 400ms 뒤 닫힌다. 왼쪽은 세션을 고르거나 뒤 막(scrim)을 누르거나 Esc면 바로 닫힌다.
//   - **개입 알약** — waiting이 1개 이상이면 오른쪽 위. 왼쪽 「개입 N · 세션 · 종류」는 상태 막대 칩과 같은
//     `agents.jump`, 오른쪽 「⌘. 끝내기」는 `focus.exit`. 설정 `ui.focus.pill`로 끈다.
//   - **들어올 때 안내** — 아래 가운데 3초. 같은 기기에서 세 번 본 뒤에는 생략(`ui.focus.hintCount`).
//   - **터치용 끝내기 버튼** — 마우스가 없는 기기(hover: none)는 가장자리를 쓸 수 없으니 왼쪽 아래에 작은 버튼을 둔다.
//
// 토스트 · 승인 창 · 팔레트 · 설정은 이것들보다 z-index가 높아 집중 모드와 상관없이 그대로 뜬다.
//
// ⚠ 지연 청크(shell) — core/*(settings·store·keymap)를 직접 import하지 않는다(Hud.tsx 머리말과 같은 이유).
// 필요한 것은 main.js가 deps로 넘긴다.
import { createSignal, createEffect, onCleanup, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { stateIcon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { KIND_LABEL, normalizeKind } from './rail-data.js';

export type FocusEdge = 'left' | 'top' | 'bottom';

export interface FocusWaiting {
  tmuxName: string;
  sessionId: string | null;
  name: string;
  kind: string | null;
}

export interface FocusDeps {
  getAction: (name: string) => unknown;
  /** 키맵 id → 표시용 조합('⌘.'). 바인딩이 없거나 못 쓰면 ''. */
  keyHint?: (id: string) => string;
  agentCounts?: () => { waiting: number; working: number; done: number };
  subscribeAgents?: (fn: () => void) => () => void;
  /** 가장 오래 기다린 waiting 세션(상태 막대 칩의 agents.jump가 가는 곳). */
  oldestWaiting?: () => FocusWaiting | null;
  active: () => boolean;
  focusEvent: string;
  getSetting: (key: string) => unknown;
  setSetting: (key: string, value: unknown) => unknown;
  subscribeSettings: (fn: (changed: unknown) => void) => () => void;
}

export const EDGE_KEY = 'ui.focus.edgeReveal';
export const PILL_KEY = 'ui.focus.pill';
export const DELAY_KEY = 'ui.focus.edgeDelay';
export const HINT_KEY = 'ui.focus.hintCount';
export const HINT_MAX = 3;
export const HINT_MS = 3000;
export const HIDE_MS = 400;

/** 각 가장자리가 드러내는 크롬. 크롬 id는 layout/focus.js의 FOCUS_CHROME_IDS와 같다. */
export const EDGE_CHROME: Record<FocusEdge, string> = {
  left: 'vt-workbench-slot',
  top: 'vt-titlebar',
  bottom: 'vt-hud',
};
const REVEAL_CLASSES = ['vt-reveal-left', 'vt-reveal-top', 'vt-reveal-bottom'];

/** 설정값(문자열 '0'|'250'|'500') → ms. 모르는 값이면 기본 250. */
export function edgeDelayMs(v: unknown): number {
  const n = Number(v);
  return n === 0 || n === 250 || n === 500 ? n : 250;
}

/** 들어올 때 안내를 띄울지 — 본 횟수가 HINT_MAX 미만일 때만. */
export function shouldShowHint(count: unknown): boolean {
  const n = Number(count);
  return !Number.isFinite(n) || n < HINT_MAX;
}

/** 포인터가 드러난 막대(또는 그 막대가 띄운 메뉴·툴팁) 안에 있는가. 밖이면 닫기 타이머를 건다. */
export function insideReveal(target: EventTarget | null, chrome: Element | null): boolean {
  const el = target as Element | null;
  if (!el || typeof el.closest !== 'function') return false;
  if (chrome && chrome.contains(el)) return true;
  // 제목 막대의 FarShell 메뉴 · 호스트 메뉴, 레일의 행 메뉴는 body에 Portal로 그려진다 — 그 안도 「안」이다.
  return !!el.closest('.vt-mb-pop, [role="menu"], .vt-menu, .vt-ctx-menu, .vt-tooltip, [role="tooltip"]');
}

function hoverCapable(): boolean {
  return typeof window.matchMedia === 'function' ? window.matchMedia('(hover: hover)').matches : true;
}

function FocusLayer(props: { deps: FocusDeps }) {
  const { deps } = props;
  const [active, setActive] = createSignal(deps.active());
  const [reveal, setReveal] = createSignal<FocusEdge | null>(null);
  const [edgeOn, setEdgeOn] = createSignal(deps.getSetting(EDGE_KEY) !== false);
  const [pillOn, setPillOn] = createSignal(deps.getSetting(PILL_KEY) !== false);
  const [hint, setHint] = createSignal(false);
  const [hover, setHover] = createSignal(hoverCapable());
  const [waiting, setWaiting] = createSignal(deps.agentCounts?.().waiting ?? 0);
  const [first, setFirst] = createSignal<FocusWaiting | null>(deps.oldestWaiting?.() ?? null);
  const keyLabel = () => deps.keyHint?.('focusMode') || '';

  const act = (name: string) => {
    const fn = deps.getAction(name);
    if (typeof fn === 'function') (fn as () => void)();
  };

  // ── 켜짐 상태 ──────────────────────────────────────────────────────────
  let hintTimer = 0;
  const onFocusChange = (e: Event) => {
    const on = !!(e as CustomEvent).detail?.on;
    setActive(on);
    if (!on) { closeReveal(); setHint(false); return; }
    maybeHint();
  };
  window.addEventListener(deps.focusEvent, onFocusChange);
  onCleanup(() => window.removeEventListener(deps.focusEvent, onFocusChange));

  const maybeHint = () => {
    const desktop = typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 720px)').matches : true;
    const seen = deps.getSetting(HINT_KEY);
    if (!desktop || !shouldShowHint(seen)) return;
    deps.setSetting(HINT_KEY, Math.min(HINT_MAX, (Number(seen) || 0) + 1));
    setHint(true);
    clearTimeout(hintTimer);
    hintTimer = window.setTimeout(() => setHint(false), HINT_MS);
  };
  onCleanup(() => clearTimeout(hintTimer));
  // 처음 켤 때 이 층이 그 뒤에 마운트된다 — 켜짐 이벤트를 이미 지나쳤으므로 안내는 여기서 한 번.
  if (active()) maybeHint();

  const offSettings = deps.subscribeSettings((changed) => {
    if (!changed || typeof changed !== 'object') return;
    const c = changed as Record<string, unknown>;
    if (EDGE_KEY in c) { setEdgeOn(deps.getSetting(EDGE_KEY) !== false); if (!edgeOn()) closeReveal(); }
    if (PILL_KEY in c) setPillOn(deps.getSetting(PILL_KEY) !== false);
  });
  onCleanup(() => offSettings?.());

  const hoverMq = typeof window.matchMedia === 'function' ? window.matchMedia('(hover: hover)') : null;
  const onHoverMq = () => setHover(hoverCapable());
  hoverMq?.addEventListener?.('change', onHoverMq);
  onCleanup(() => hoverMq?.removeEventListener?.('change', onHoverMq));

  // ── 개입 수 — 상태 막대 칩과 같은 구독. 한 프레임에 한 번만 다시 센다. ──────────────
  let agentRaf = 0;
  const offAgents = deps.subscribeAgents?.(() => {
    if (agentRaf) return;
    agentRaf = requestAnimationFrame(() => {
      agentRaf = 0;
      setWaiting(deps.agentCounts?.().waiting ?? 0);
      setFirst(deps.oldestWaiting?.() ?? null);
    });
  });
  onCleanup(() => { offAgents?.(); if (agentRaf) cancelAnimationFrame(agentRaf); });

  // ── 가장자리 노출 ───────────────────────────────────────────────────────
  let showTimer = 0;
  let hideTimer = 0;
  let pendingEdge: FocusEdge | null = null;
  const chromeOf = (edge: FocusEdge | null) => (edge ? document.getElementById(EDGE_CHROME[edge]) : null);

  function closeReveal() {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    showTimer = 0; hideTimer = 0; pendingEdge = null;
    setReveal(null);
  }

  // 드러난 쪽만 inert를 풀고 클래스를 붙인다. 집중 모드가 꺼지면 core가 inert를 이미 다 풀었으므로 건드리지 않는다.
  createEffect(() => {
    const edge = reveal();
    const on = active();
    const root = document.documentElement;
    for (const c of REVEAL_CLASSES) root.classList.remove(c);
    for (const [side, id] of Object.entries(EDGE_CHROME) as Array<[FocusEdge, string]>) {
      const el = document.getElementById(id);
      if (!el || !on) continue;
      const open = side === edge;
      el.inert = !open;
      if (open) el.removeAttribute('inert'); else el.setAttribute('inert', '');
    }
    if (on && edge) root.classList.add(`vt-reveal-${edge}`);
  });

  const onEdgeEnter = (edge: FocusEdge) => {
    if (!active() || !edgeOn()) return;
    clearTimeout(hideTimer); hideTimer = 0;
    if (reveal() === edge) return;
    clearTimeout(showTimer);
    pendingEdge = edge;
    const delay = edgeDelayMs(deps.getSetting(DELAY_KEY));
    const show = () => { showTimer = 0; if (pendingEdge === edge && active()) setReveal(edge); };
    if (delay === 0) show(); else showTimer = window.setTimeout(show, delay);
  };
  const onEdgeLeave = (edge: FocusEdge) => {
    if (pendingEdge === edge && reveal() !== edge) { clearTimeout(showTimer); showTimer = 0; pendingEdge = null; }
  };

  // 드러난 동안 포인터가 막대 밖으로 나가면 400ms 뒤 닫는다. 다시 들어오면 취소.
  const onPointerMove = (e: PointerEvent) => {
    const edge = reveal();
    if (!edge) return;
    if (insideReveal(e.target, chromeOf(edge))) { clearTimeout(hideTimer); hideTimer = 0; return; }
    if (!hideTimer) hideTimer = window.setTimeout(() => { hideTimer = 0; if (!holdOpen()) setReveal(null); }, HIDE_MS);
  };
  // 메뉴가 열려 있거나 막대 안 입력란에서 글을 쓰는 중이면 닫지 않는다(이름 바꾸기 도중에 사라지지 않게).
  const holdOpen = () => {
    if (document.querySelector('.vt-mb-pop, [role="menu"]')) return true;
    const a = document.activeElement;
    const chrome = chromeOf(reveal());
    return !!(a && chrome && chrome.contains(a) && a.matches('input, textarea, [contenteditable="true"]'));
  };
  const onLeaveWindow = (e: PointerEvent) => {
    if (!reveal() || (e as any).relatedTarget) return;
    if (!hideTimer) hideTimer = window.setTimeout(() => { hideTimer = 0; if (!holdOpen()) setReveal(null); }, HIDE_MS);
  };
  // Esc — 메뉴가 열려 있으면 메뉴가 먼저 닫는다. 드러난 막대가 있을 때만 키를 먹는다(평소 Esc는 터미널 몫).
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || !reveal()) return;
    if (document.querySelector('.vt-mb-pop, [role="menu"]')) return;
    e.preventDefault();
    e.stopPropagation();
    closeReveal();
  };
  // 왼쪽 노출에서 세션 행을 고르면 바로 닫는다(행 안의 ⋯ 같은 버튼은 메뉴를 여는 것이라 제외).
  const onClick = (e: MouseEvent) => {
    if (reveal() !== 'left') return;
    const el = e.target as Element | null;
    if (!el?.closest) return;
    if (el.closest('#vt-wgrail [role="button"][data-state]') && !el.closest('button')) {
      // 행의 클릭 처리(세션 열기)가 먼저 돌도록 다음 틱에.
      setTimeout(closeReveal, 0);
    }
  };
  document.addEventListener('pointermove', onPointerMove, { passive: true });
  document.documentElement.addEventListener('pointerleave', onLeaveWindow as EventListener);
  window.addEventListener('keydown', onKey, true);
  document.addEventListener('click', onClick);
  onCleanup(() => {
    document.removeEventListener('pointermove', onPointerMove);
    document.documentElement.removeEventListener('pointerleave', onLeaveWindow as EventListener);
    window.removeEventListener('keydown', onKey, true);
    document.removeEventListener('click', onClick);
    closeReveal();
  });

  const showEdges = () => active() && edgeOn() && hover();
  const showPill = () => active() && pillOn() && waiting() > 0;
  const kindLabel = () => {
    const f = first();
    return f ? KIND_LABEL[normalizeKind(f.kind)] : '';
  };
  const exitLabel = () => t('집중 모드 끝내기');

  return (
    <>
      <Show when={showEdges()}>
        <div class="vt-focus-edge" data-edge="left" aria-hidden="true"
          onPointerEnter={() => onEdgeEnter('left')} onPointerLeave={() => onEdgeLeave('left')} />
        <div class="vt-focus-edge" data-edge="top" aria-hidden="true"
          onPointerEnter={() => onEdgeEnter('top')} onPointerLeave={() => onEdgeLeave('top')} />
        <div class="vt-focus-edge" data-edge="bottom" aria-hidden="true"
          onPointerEnter={() => onEdgeEnter('bottom')} onPointerLeave={() => onEdgeLeave('bottom')} />
      </Show>
      <Show when={active() && reveal() === 'left'}>
        <div class="vt-focus-scrim" aria-hidden="true" onClick={closeReveal} />
      </Show>
      <Show when={showPill()}>
        <div class="vt-focus-pill" role="group" aria-label={t('집중 모드 개입 표시')}>
          <button
            type="button"
            class="vt-focus-pill-go"
            data-tip={t('누르면 그 세션으로')}
            data-tip-side="bottom"
            onClick={() => act('agents.jump')}
          >
            <span class="vt-focus-pill-ico" innerHTML={stateIcon('waiting', 14)} />
            <b class="vt-focus-pill-count">{t('개입 {n}', { n: waiting() })}</b>
            <Show when={first()}>
              <span class="vt-focus-pill-sep" aria-hidden="true">·</span>
              <span class="vt-focus-pill-name">{first()!.name}</span>
              <span class="vt-focus-pill-kind">{kindLabel()}</span>
            </Show>
            <span class="vt-focus-pill-chev" innerHTML={shellIcon('chevron-right', 12, 2)} />
          </button>
          <button type="button" class="vt-focus-pill-exit" aria-label={exitLabel()} data-tip={exitLabel()} data-tip-side="bottom" onClick={() => act('focus.exit')}>
            <Show when={keyLabel()}><kbd class="vt-focus-kbd">{keyLabel()}</kbd></Show>
            {t('끝내기')}
          </button>
        </div>
      </Show>
      <Show when={active() && hint()}>
        <div class="vt-focus-hint" role="status">
          <span class="vt-focus-hint-ico" innerHTML={shellIcon('maximize', 14, 2)} />
          <span>{t('집중 모드')}</span>
          <Show when={edgeOn() && hover()}>
            <span class="vt-focus-hint-sep" aria-hidden="true">·</span>
            <span>{t('화면 가장자리에 마우스를 대면 막대가 나타납니다')}</span>
          </Show>
          <span class="vt-focus-hint-sep" aria-hidden="true">·</span>
          <Show when={hover() && keyLabel()} fallback={<span>{t('왼쪽 아래 버튼으로 끝냅니다')}</span>}>
            <span><kbd class="vt-focus-kbd">{keyLabel()}</kbd>{t('끝내기')}</span>
          </Show>
        </div>
      </Show>
      <Show when={active() && !hover()}>
        <button type="button" class="vt-focus-exit" aria-label={exitLabel()} data-tip={exitLabel()} onClick={() => act('focus.exit')}>
          <span innerHTML={shellIcon('minimize', 16, 2)} />
        </button>
      </Show>
    </>
  );
}

export function mountFocus(root: HTMLElement, deps: FocusDeps) {
  return render(() => <FocusLayer deps={deps} />, root);
}
