// 레일의 **행 하나와 컨텍스트 메뉴**. 목록 조립·상태 관리는 Rail.tsx.
//
// 행은 3줄이다(docs/ref/terminal.md 「Rail」):
//   1줄  상태 아이콘(읽지 않음이면 둘레 링) · 에이전트 · 이름(제자리 편집)
//   2줄  메인/워크트리 아이콘 · 브랜치(main도 항상) · 변경량
//   3줄  상태 키워드 · 경과 시간 · 만든 곳 아이콘
// 해당할 때만 1줄 끝에 「열림」 표시, 4번째 줄에 묶음 뷰 이름·「다른 화면 N」.
// 상태 왼쪽 막대(옛 vt-wgrail-bar)와 tmux 플러그 아이콘은 없다 — 상태는 아이콘의
// 색+모양이 말하고, tmux라는 사실은 행마다 같아서 정보가 아니었다.
//
// ⚠ 여기도 `shell` 지연 청크다 — core/store.js 계열을 직접 import하지 않는다
// (Rail.tsx 머리말과 같은 이유, ADR-26/N35).
import { For, Show, createSignal, onCleanup } from 'solid-js';

import { agentLabel, icon, stateIcon } from '../ui/icons.js';
import { agentMark } from './agent-mark.js';
import {
  stateKeyword, formatRelativeTime, statusSentence, diffLabel, worktreeIconName,
  ORIGIN_ICON, ORIGIN_LABEL, type SessionRow, type Origin,
} from './rail-data.js';

/** 경과 시간을 행 DOM을 다시 만들지 않고 갱신하는 시계(30초). */
function createNow() {
  const [now, setNow] = createSignal(Date.now());
  const t = setInterval(() => setNow(Date.now()), 30000);
  onCleanup(() => clearInterval(t));
  return now;
}

/** 만든 곳 아이콘(local은 없다). 레일 행과 Fleet 행이 같이 쓴다. */
export function OriginMark(props: { origin: Origin }) {
  return (
    <Show when={props.origin !== 'local'}>
      <span
        class="vt-srow-origin"
        data-origin={props.origin}
        data-tip={ORIGIN_LABEL[props.origin]}
        data-tip-side="right"
        role="img"
        aria-label={ORIGIN_LABEL[props.origin]}
        innerHTML={icon(ORIGIN_ICON[props.origin as Exclude<Origin, 'local'>], 12, 2)}
      />
    </Show>
  );
}

/** 키워드 줄(상태 키워드 · 도구 · 배지 · 경과). 레일과 Fleet이 같이 쓴다. */
export function KeywordLine(props: { row: SessionRow; now: () => number }) {
  const kw = () => stateKeyword(props.row);
  const rel = () => formatRelativeTime(props.row.since, props.now());
  return (
    <>
      <span
        class="vt-srow-kw"
        data-state={props.row.status}
        data-tip={props.row.status === 'waiting' && props.row.question ? props.row.question : undefined}
        data-tip-side="right"
      >{kw().text}</span>
      <Show when={kw().tool}><span class="vt-srow-tool">{kw().tool}</span></Show>
      <Show when={kw().badge}><span class="vt-srow-badge">{kw().badge}</span></Show>
      <Show when={rel()}><span class="vt-srow-since">{rel()}</span></Show>
    </>
  );
}

export function Row(props: {
  row: SessionRow; active: boolean; compact?: boolean;
  /** 열린 행만 끌 수 있다(Rail.tsx의 onRowDragStart가 조건을 본다) — 터미널 칸에 놓으면 분할(묶음)이 생긴다. */
  draggable?: boolean; onDragStart?: (e: DragEvent) => void;
  /** 이름 제자리 편집 — window.prompt 안 씀. */
  editing?: boolean; editingValue?: string;
  onEditInput?: (v: string) => void; onEditCommit?: () => void; onEditCancel?: () => void;
  onOpen: (e: MouseEvent) => void;
  /** ⋯ 버튼(좌클릭)과 우클릭이 같은 함수를 부른다 — 같은 메뉴가 같은 항목으로 열린다. */
  onContext: (e: MouseEvent) => void;
}) {
  const now = createNow();
  const rowName = () => props.row.name;
  const diff = () => diffLabel(props.row);
  const branch = () => props.row.branch || '';
  const unseen = () => props.row.unseen && props.row.status !== 'idle';

  // 48px 접힘에서는 이름·상태 문장이 숨는다. 그 정보는 툴팁(이름 / 브랜치 · 키워드)이 맡는다.
  const compactSub = () => {
    if (!props.compact) return undefined;
    return [branch(), statusSentence(props.row, now())].filter(Boolean).join(' · ') || undefined;
  };

  // div에 role=button만 붙이면 Enter·Space가 아무 일도 안 한다 — 직접 붙인다.
  // Space는 preventDefault가 필수(안 하면 활성화와 동시에 페이지가 스크롤된다).
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;   // 이름 입력창의 키 입력을 가로채지 않는다
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    if (e.key !== 'Enter') e.preventDefault();
    props.onOpen(e as unknown as MouseEvent);
  };

  return (
    <div
      class="vt-srow vt-wgrail-row"
      classList={{ active: props.active, closed: !props.row.open }}
      onClick={props.onOpen}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => { e.preventDefault(); props.onContext(e); }}
      draggable={!!props.draggable}
      onDragStart={props.onDragStart}
      role="button"
      tabindex="0"
      aria-current={props.active ? 'true' : undefined}
      // agent/paint.js의 전역 클릭 위임이 `[data-session-id]`로 "봤다"를 판정한다.
      // 열려 있지 않은 행은 sessionId가 없어 자연히 제외된다.
      data-session-id={props.row.sessionId || undefined}
      data-tip={props.compact ? rowName() : undefined}
      data-tip-sub={compactSub()}
      data-tip-side="right"
    >
      {/* 접힘 전용: 상태 아이콘 + 에이전트 마크. 펼친 상태의 둘은 아래 row-main 안에 있고
          그 블록이 접히면 통째로 숨는다(둘이 동시에 보이는 일은 없다). */}
      <Show when={props.compact}>
        <span class="vt-srow-state" innerHTML={stateIcon(props.row.status, 14, unseen())} />
        <Show when={props.row.agent}>
          <span
            class="vt-srow-agent vt-wgrail-agent vt-wgrail-agent-compact"
            data-state={props.row.status}
            innerHTML={agentMark(props.row.agent!)}
          />
        </Show>
      </Show>
      <div class="vt-srow-main vt-wgrail-row-main">
        <div class="vt-srow-top vt-wgrail-row-top">
          <span class="vt-srow-state" innerHTML={stateIcon(props.row.status, 14, unseen())} />
          {/* 에이전트를 아직 모르면(agent == null) 아무것도 안 그린다 — "셸이다"와 "모른다"는 다르다. */}
          <Show when={props.row.agent}>
            {(a) => (
              <span
                class="vt-srow-agent vt-wgrail-agent"
                data-tip={agentLabel(a())}
                data-tip-side="right"
                data-state={props.row.status}
                innerHTML={agentMark(a())}
              />
            )}
          </Show>
          <Show
            when={props.editing}
            fallback={<span class="vt-srow-name vt-wgrail-name">{rowName()}</span>}
          >
            <input
              type="text"
              class="vt-input vt-wgrail-name-edit"
              value={props.editingValue ?? ''}
              ref={(el) => { queueMicrotask(() => { el.focus(); el.select(); }); }}
              onInput={(e) => props.onEditInput?.(e.currentTarget.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); props.onEditCommit?.(); }
                else if (e.key === 'Escape') { e.preventDefault(); props.onEditCancel?.(); }
              }}
              onBlur={() => props.onEditCommit?.()}
            />
          </Show>
          <Show when={props.row.open}>
            <span
              class="vt-srow-open"
              data-tip="이 화면에 열려 있음"
              data-tip-side="right"
              role="img"
              aria-label="열려 있음"
              innerHTML={icon('app-window', 12, 2)}
            />
          </Show>
        </div>
        <Show when={branch() || diff()}>
          <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line2">
            <Show when={branch()}>
              <span
                class="vt-srow-wt"
                data-tip={props.row.isMainWorktree ? '메인 워크트리' : '워크트리'}
                data-tip-side="right"
                role="img"
                aria-label={props.row.isMainWorktree ? '메인 워크트리' : '워크트리'}
                innerHTML={icon(worktreeIconName(props.row), 12, 2)}
              />
              <span class="vt-srow-branch">{branch()}</span>
            </Show>
            <Show when={diff()}><span class="vt-srow-meta vt-wgrail-diff">{diff()}</span></Show>
          </div>
        </Show>
        <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line3">
          <KeywordLine row={props.row} now={now} />
          <OriginMark origin={props.row.origin} />
        </div>
        <Show when={props.row.viewLabel || props.row.otherScreens > 0}>
          <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line4">
            <Show when={props.row.viewLabel}>
              <span class="vt-srow-view" data-tip="묶음 뷰" data-tip-side="right">{props.row.viewLabel}</span>
            </Show>
            <Show when={props.row.otherScreens > 0}>
              <span class="vt-srow-screens" data-tip="다른 브라우저 탭·기기·맥 터미널에서도 붙어 있음" data-tip-side="right">
                다른 화면 {props.row.otherScreens}
              </span>
            </Show>
          </div>
        </Show>
      </div>
      {/* ⋯ 버튼은 우클릭과 같은 메뉴를 연다(행의 좌클릭은 열기/전환이 쓰고 있다).
          compact(48px)에서는 숨는다 — 좁은 행에 18px가 더 끼어 가로 스크롤이 생긴
          회귀(2026-09-22)가 있었다. 접힘에서는 우클릭이 메뉴로 가는 길이다. */}
      <Show when={!props.compact}>
        <button
          type="button"
          class="vt-icon-btn xs vt-srow-menu"
          aria-label={`${rowName()} 메뉴`}
          data-tip="메뉴"
          data-tip-side="left"
          onClick={(e) => { e.stopPropagation(); props.onContext(e); }}
          innerHTML={icon('more-horizontal', 14, 2)}
        />
      </Show>
    </div>
  );
}

export interface MenuItem {
  label: string;
  run: () => void;
  /** C1 호스트 메뉴의 둘째 줄("세션 3 · 12ms" 또는 "응답 없음"). 없으면 안 그린다. */
  detail?: string;
  /** 흐리게(오프라인 호스트). 선택 자체는 막지 않는다 — 꺼진 호스트를 고르면
   * 이유를 보여주는 게 목적이다. */
  dim?: boolean;
  checked?: boolean;
  /** 되돌릴 수 없는 항목(종료). */
  danger?: boolean;
}

export function Menu(props: { x: number; y: number; onClose: () => void; items: MenuItem[] }) {
  let ref: HTMLDivElement | undefined;
  const onDocClick = (e: MouseEvent) => { if (ref && !ref.contains(e.target as Node)) props.onClose(); };
  document.addEventListener('mousedown', onDocClick, true);
  onCleanup(() => document.removeEventListener('mousedown', onDocClick, true));

  return (
    <div ref={ref} class="vt-menu" style={{ left: `${props.x}px`, top: `${props.y}px`, right: 'auto' }}>
      <For each={props.items}>
        {(it) => (
          <div
            class="vt-menu-item"
            classList={{ dim: !!it.dim, checked: !!it.checked, danger: !!it.danger }}
            onClick={() => { props.onClose(); it.run(); }}
          >
            <span class="vt-menu-item-label">{it.checked ? '✓ ' : ''}{it.label}</span>
            <Show when={it.detail}><span class="vt-menu-item-detail">{it.detail}</span></Show>
          </div>
        )}
      </For>
    </div>
  );
}
