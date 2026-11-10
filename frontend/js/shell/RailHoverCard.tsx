// 접힌 세션 띠(48px) 칸의 상세 카드 — 칸에 마우스를 올리거나 키보드로 포커스하면 오른쪽에 뜬다.
// 내용 구성은 rail-hover-card.ts(순수 함수), 여기는 **열고 닫는 규칙 · 자리 잡기 · 그리기**다.
// 공용 툴팁(ui/tooltip.js, data-tip)은 건드리지 않는다 — 접힌 칸만 이 카드를 쓰고 data-tip은 달지 않는다.
//
// 열고 닫는 규칙(WCAG 1.4.13)
//   - 올림(hover) 250ms 뒤에 뜬다(빠르게 훑을 때 깜빡임 방지). 이미 떠 있거나 막 닫혔으면(WARM) 지연 없이 옮겨 뜬다.
//   - 키보드 포커스(:focus-visible)는 지연 없이 뜬다. 마우스 클릭으로 들어온 포커스에는 안 뜬다.
//   - hoverable   칸 → 카드로 포인터가 건너가도 닫히지 않는다(닫기 유예 160ms + 카드 왼쪽의 투명 다리).
//   - dismissable Esc로 닫는다. 같은 칸 위에 포인터가 그대로여도 다시 안 뜬다(칸을 떠났다 돌아오거나 포커스가 옮겨야 다시 뜬다).
//   - persistent  카드는 포인터/포커스가 떠나거나 Esc·클릭·스크롤이 있을 때만 닫힌다(시간으로 사라지지 않는다).
//   - 터치 포인터에는 안 뜬다(폰은 접힌 띠가 없다). 칸의 이름은 aria-label, 카드는 열린 동안 aria-describedby로 이어진다.
//
// 내용은 열린 동안 살아 있지만 **도구 이름은 열릴 때 한 번 찍은 값**이고 상태가 바뀔 때만 갱신한다(깜빡임 방지).
// 크기는 고정(폭 300, 최대 높이 380)이라 메뉴 크기 설정(ui.side.scale)과 무관하다.
//
// ⚠ shell 지연 청크 — core/store.js 계열을 직접 import하지 않는다(Rail.tsx 머리말과 같은 이유).
import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from 'solid-js';
import { Portal } from 'solid-js/web';

import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { agentMark } from './agent-mark.js';
import { buildHoverCard, type HcGlyph, type HcHost, type HcModel, type HcRow } from './rail-hover-card.js';
import type { SessionRow } from './rail-data.js';

export const CARD_ID = 'vt-rail-card';
/** 칸 요소가 다는 속성 — 카드가 어느 세션인지 찾는 열쇠(Rail의 rowKey와 같다). */
export const CARD_KEY_ATTR = 'data-card-key';
export const OPEN_DELAY = 250;
export const CLOSE_GRACE = 160;
export const WARM_MS = 400;
const GAP = 8;      // 띠 오른쪽 가장자리와 카드 사이
const EDGE = 8;     // 뷰포트 가장자리 여백
const HEAD_MID = 22; // 카드 맨 위에서 머리 줄 가운데까지 — 칸 가운데와 맞춘다

export interface HoverCardSource {
  row: SessionRow;
  /** 이 세션이 부모인 위임 하위 세션 행(진행 중·보고됨). */
  kids: SessionRow[];
  host: HcHost | null;
  shareable: boolean;
}

const GLYPH_ICON: Record<Exclude<HcGlyph, 'unseen'>, string> = {
  idle: 'state-idle', working: 'state-working', waiting: 'state-waiting', done: 'state-done', failed: 'x',
};

function Glyph(props: { name: HcGlyph; size?: number }) {
  return (
    <Show when={props.name !== 'unseen'} fallback={<span class="vt-hc-unseen" aria-hidden="true" />}>
      <span class="vt-hc-glyph" data-state={props.name} aria-hidden="true" innerHTML={icon(GLYPH_ICON[props.name as Exclude<HcGlyph, 'unseen'>], props.size ?? 14, 2)} />
    </Show>
  );
}

function CardRow(props: { row: HcRow }) {
  const r = () => props.row;
  return (
    <div class="vt-hc-row" classList={{ muted: !!r().muted, dim: !!r().dim, item: !!r().glyph }} data-key={r().key}>
      <span class="vt-hc-ico" aria-hidden={r().glyph ? undefined : 'true'}>
        <Show when={r().glyph} fallback={<Show when={r().icon}><span innerHTML={shellIcon(r().icon!, 14, 2)} /></Show>}>
          <Glyph name={r().glyph!} />
        </Show>
      </span>
      <span class="vt-hc-val" title={r().title}>
        <span class="vt-hc-text" classList={{ mono: !!r().mono }}>{r().text}</span>
        <Show when={r().tag}><span class="vt-hc-tag">{r().tag}</span></Show>
      </span>
      <Show when={r().diff || r().extra}>
        <span class="vt-hc-extra">
          <Show when={r().diff} fallback={r().extra}>
            {(d) => <>
              <Show when={d().add > 0}><span class="vt-diff-add">+{d().add}</span></Show>
              {d().add > 0 && d().del > 0 ? ' ' : ''}
              <Show when={d().del > 0}><span class="vt-diff-del">−{d().del}</span></Show>
            </>}
          </Show>
        </span>
      </Show>
    </div>
  );
}

export function RailHoverCard(props: {
  /** 이벤트를 받는 띠 요소(칸들의 공통 조상). 카드는 body에 그려지므로 그 안에 있지 않다. */
  root: () => HTMLElement | undefined;
  /** 접힌 띠일 때만 true — 펼치면 닫는다. */
  enabled: () => boolean;
  resolve: (key: string) => HoverCardSource | null;
  openDelay?: number;
  closeGrace?: number;
}) {
  const [key, setKey] = createSignal<string | null>(null);
  const [open, setOpen] = createSignal(false);
  const [now, setNow] = createSignal(Date.now());
  let card: HTMLDivElement | undefined;
  let anchor: HTMLElement | null = null;
  let cause: 'pointer' | 'focus' = 'pointer';
  let showT = 0;
  let hideT = 0;
  let lastHideAt = 0;
  let dismissed: string | null = null;
  const delay = () => props.openDelay ?? OPEN_DELAY;
  const grace = () => props.closeGrace ?? CLOSE_GRACE;

  const rowOf = (target: EventTarget | null): HTMLElement | null =>
    target instanceof Element ? target.closest<HTMLElement>(`[${CARD_KEY_ATTR}]`) : null;
  const cancelShow = () => { if (showT) { clearTimeout(showT); showT = 0; } };
  const cancelHide = () => { if (hideT) { clearTimeout(hideT); hideT = 0; } };

  const describe = (el: HTMLElement | null, on: boolean) => {
    if (!el) return;
    if (on) { if (!el.hasAttribute('aria-describedby')) { el.setAttribute('aria-describedby', CARD_ID); (el as any)._vtCardAria = true; } }
    else if ((el as any)._vtCardAria) { el.removeAttribute('aria-describedby'); delete (el as any)._vtCardAria; }
  };

  const hideNow = () => {
    cancelShow(); cancelHide();
    if (!open()) return;
    describe(anchor, false);
    anchor = null;
    lastHideAt = Date.now();
    setOpen(false);
    setKey(null);
  };

  const show = (el: HTMLElement, why: 'pointer' | 'focus') => {
    showT = 0;
    const k = el.getAttribute(CARD_KEY_ATTR);
    if (!k || !el.isConnected || !props.enabled() || dismissed === k || !props.resolve(k)) return;
    if (anchor && anchor !== el) describe(anchor, false);
    anchor = el;
    cause = why;
    describe(el, true);
    setNow(Date.now());
    setKey(k);
    setOpen(true);
  };

  const scheduleHide = () => {
    cancelShow(); cancelHide();
    if (!open()) return;
    hideT = window.setTimeout(() => {
      hideT = 0;
      // 키보드 포커스로 열렸으면 포인터가 스쳐 갔다고 닫지 않는다(포커스가 떠날 때 닫힌다).
      if (cause === 'focus' && anchor && document.activeElement === anchor) return;
      hideNow();
    }, grace());
  };

  // 칸을 다시 그려 앵커 요소가 바뀌어도 같은 키의 칸을 다시 찾는다.
  const currentAnchor = (): HTMLElement | null => {
    if (anchor?.isConnected) return anchor;
    const k = key();
    const root = props.root();
    if (!k || !root) return null;
    anchor = Array.from(root.querySelectorAll<HTMLElement>(`[${CARD_KEY_ATTR}]`)).find((e) => e.getAttribute(CARD_KEY_ATTR) === k) || null;
    describe(anchor, true);
    return anchor;
  };

  const place = () => {
    if (!card) return;
    const el = currentAnchor();
    if (!el) return;
    const r = el.getBoundingClientRect();
    const strip = props.root()?.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = card.offsetWidth, h = card.offsetHeight;
    // 모든 칸이 같은 x(띠 오른쪽 가장자리 + 간격)에 뜬다 — 하위 칸(32px)도 같다.
    const left = Math.max(EDGE, Math.min(Math.max(r.right, strip?.right ?? r.right) + GAP, vw - EDGE - w));
    // 머리 줄 가운데를 칸 가운데에 맞추고, 뷰포트 안(위·아래 8px)으로 당긴다.
    const top = Math.max(EDGE, Math.min(r.top + r.height / 2 - HEAD_MID, vh - EDGE - h));
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  };

  const source = createMemo(() => { const k = key(); return k ? props.resolve(k) : null; });
  // 도구 이름은 열릴 때 한 번 찍고 상태가 바뀔 때만 갱신한다(호출마다 바뀌는 값이라 그대로 따라가면 깜빡인다).
  let frozen: { key: string; status: string; tool: string | null } | null = null;
  const model = createMemo<HcModel | null>(() => {
    const src = source();
    const k = key();
    if (!src || !k) { frozen = null; return null; }
    if (!frozen || frozen.key !== k || frozen.status !== src.row.status) frozen = { key: k, status: src.row.status, tool: src.row.tool };
    return buildHoverCard(src.row, { now: now(), tool: frozen.tool, kids: src.kids, host: src.host, shareable: src.shareable });
  }, null, { equals: (a, b) => JSON.stringify(a) === JSON.stringify(b) });

  // 내용이 바뀌거나 칸이 바뀌면 다시 자리를 잡는다. 열린 동안 경과 시간만 30초마다 갱신한다.
  createEffect(() => { if (model() && open()) place(); });
  createEffect(() => {
    if (!open()) return;
    const id = window.setInterval(() => setNow(Date.now()), 30000);
    onCleanup(() => clearInterval(id));
  });
  // 세션이 사라지면(종료) 카드도 닫는다. 띠가 펼쳐져도 닫는다.
  createEffect(() => { if (open() && !source()) hideNow(); });
  createEffect(() => { if (!props.enabled()) hideNow(); });

  onMount(() => {
    const root = props.root();
    if (!root) return;
    const onOver = (e: PointerEvent) => {
      if (e.pointerType === 'touch' || !props.enabled()) return;
      const el = rowOf(e.target);
      if (!el) { dismissed = null; scheduleHide(); return; }
      const k = el.getAttribute(CARD_KEY_ATTR);
      if (dismissed && dismissed !== k) dismissed = null;
      cancelHide();
      if (open() && anchor === el) return;
      cancelShow();
      if (open() || Date.now() - lastHideAt < WARM_MS) show(el, 'pointer');   // 이미 떠 있으면 지연 없이 옮긴다
      else showT = window.setTimeout(() => show(el, 'pointer'), delay());
    };
    const onLeave = (e: PointerEvent) => { if (e.pointerType !== 'touch') { dismissed = null; scheduleHide(); } };
    const onFocusIn = (e: FocusEvent) => {
      const el = rowOf(e.target);
      if (!el || !props.enabled()) return;
      let visible = true;
      try { visible = el.matches(':focus-visible'); } catch { /* 옛 엔진 — 보이는 포커스로 본다 */ }
      if (!visible) return;
      const k = el.getAttribute(CARD_KEY_ATTR);
      if (dismissed && dismissed !== k) dismissed = null;
      cancelShow(); cancelHide();
      show(el, 'focus');
    };
    const onFocusOut = (e: FocusEvent) => {
      // 다른 칸으로 옮기면 focusin이 곧 바꿔 띄운다 — 깜빡이지 않게 여기서는 닫지 않는다.
      if (rowOf(e.relatedTarget)) return;
      dismissed = null;
      if (cause === 'focus') hideNow();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || !open()) return;
      dismissed = key();
      hideNow();
    };
    const onDown = (e: Event) => { if (open() && !(e.target instanceof Node && card?.contains(e.target))) { dismissed = key(); hideNow(); } };
    const onScroll = (e: Event) => { if (open() && !(e.target instanceof Node && card?.contains(e.target))) hideNow(); };
    const onGone = () => hideNow();
    root.addEventListener('pointerover', onOver);
    root.addEventListener('pointerleave', onLeave);
    root.addEventListener('focusin', onFocusIn);
    root.addEventListener('focusout', onFocusOut);
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown, true);
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    window.addEventListener('resize', onGone);
    window.addEventListener('blur', onGone);
    onCleanup(() => {
      hideNow();
      root.removeEventListener('pointerover', onOver);
      root.removeEventListener('pointerleave', onLeave);
      root.removeEventListener('focusin', onFocusIn);
      root.removeEventListener('focusout', onFocusOut);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onGone);
      window.removeEventListener('blur', onGone);
    });
  });

  return (
    <Portal>
      <div
        ref={card}
        id={CARD_ID}
        class="vt-hc"
        classList={{ open: open() }}
        role="tooltip"
        aria-hidden={open() ? 'false' : 'true'}
        data-state={model()?.state}
        onPointerEnter={(e) => { if (e.pointerType !== 'touch') cancelHide(); }}
        onPointerLeave={(e) => { if (e.pointerType !== 'touch') scheduleHide(); }}
      >
        <Show when={model()}>
          {(m) => <>
            <div class="vt-hc-row vt-hc-head">
              <span class="vt-hc-ico vt-hc-mark" data-state={m().state} aria-hidden="true" innerHTML={agentMark(m().agent || 'shell')} />
              <span class="vt-hc-val vt-hc-name" title={m().name}>{m().name}</span>
              <Show when={m().since}><span class="vt-hc-extra">{m().since}</span></Show>
            </div>
            <div class="vt-hc-row vt-hc-status">
              <span class="vt-hc-ico" />
              <span class="vt-hc-val">
                <span class="vt-hc-chip" data-state={m().state}>
                  <span class="vt-hc-glyph" data-state={m().state} aria-hidden="true" innerHTML={icon(GLYPH_ICON[m().state], 12, 2)} />
                  {m().stateText}
                </span>
                <Show when={m().note}><span class="vt-hc-note">{m().note}</span></Show>
              </span>
            </div>
            <For each={m().sections}>
              {(s) => (
                <div class="vt-hc-sec" data-sec={s.id}>
                  <For each={s.rows}>{(r) => <CardRow row={r} />}</For>
                </div>
              )}
            </For>
          </>}
        </Show>
      </div>
    </Portal>
  );
}
