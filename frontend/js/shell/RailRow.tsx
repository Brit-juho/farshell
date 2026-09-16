// 워크트리 레일의 **행 하나와 컨텍스트 메뉴**. 목록 조립·상태 관리는 Rail.tsx.
//
// Rail.tsx에서 떼어낸 이유: 두 컴포넌트 다 자기 props만 보고 그리는 순수
// 표현부라, 400줄짜리 Rail 본체와 섞여 있을 이유가 없다. 색·배지·상태 문장을
// 고칠 때 폴링·리사이저 코드를 지나치지 않아도 된다.
//
// ⚠ 여기도 `shell` 지연 청크다 — core/store.js 계열을 직접 import하지 않는다
// (Rail.tsx 머리말과 같은 이유, ADR-26/N35).
import { For, Show, onCleanup } from 'solid-js';

import { GROUP_LABEL, hashRepoColorIndex, type WorktreeRailRowInput } from './rail-data.js';
import { actionSessionId, type DesktopRailRow } from './rail-fetch.js';

export function Row(props: { row: DesktopRailRow; active: boolean; onOpen: (e: MouseEvent) => void; onContext: (e: MouseEvent) => void }) {
  const isWt = () => props.row.kind === 'worktree';
  const isRemote = () => props.row.kind === 'session' && !!props.row.remote;
  // 원격 행은 이제 열 수 있으므로 흐리게 그리지 않는다(3단계 전에는 못 열어서
  // no-session으로 뒀다).
  const noSession = () =>
    isWt() && !actionSessionId(props.row) && !(props.row as WorktreeRailRowInput).primaryTmuxName;
  const diffLabel = () => {
    if (props.row.kind === 'worktree') {
      const c = props.row.changed;
      return c && c.files > 0 ? `+${c.add} −${c.del}` : null;
    }
    return props.row.diffFiles != null && props.row.diffFiles > 0 ? `파일 ${props.row.diffFiles}` : null;
  };
  const rowName = () => (props.row.kind === 'worktree' ? props.row.label : props.row.name);

  return (
    <div
      class="vt-wgrail-row"
      classList={{ active: props.active, 'no-session': noSession() }}
      onClick={props.onOpen}
      onContextMenu={props.onContext}
      role="button"
      tabindex="0"
    >
      {/* 20-design-system.md §5(O2): 레일 행 왼쪽 끝 세로 막대는 저장소 해시
          색점(원형 dot과 헷갈리지 않는 "막대") — 상태 5색·acc와는 별개 램프
          (--color-hash-1..8). 그 오른쪽의 기존 막대가 상태색(30-worktree.md
          §4/10-shell-layout.md §5)을 그대로 맡는다. 「기타」 세션 행은 저장소가
          없어 둘 다 "색점 없음"(kind-session이 CSS에서 투명 처리). */}
      <span class={`vt-wgrail-hash ${isWt() ? `hash-${hashRepoColorIndex((props.row as WorktreeRailRowInput).repoName)}` : 'kind-session'}`} />
      <span class={`vt-wgrail-bar ${isWt() ? `tone-${props.row.status}` : 'kind-session'}`} />
      <div class="vt-wgrail-row-main">
        <div class="vt-wgrail-row-top">
          <span class="vt-wgrail-name">{rowName()}</span>
          <Show when={diffLabel()}>
            <span class="vt-wgrail-diff">{diffLabel()}</span>
          </Show>
        </div>
        <div class="vt-wgrail-row-sub">
          {props.row.statusSentence}
          <Show when={isRemote()}><span class="vt-wgrail-remote-note"> · 원격</span></Show>
        </div>
        <Show when={props.row.status === 'waiting' && props.row.question}>
          <div class="vt-wgrail-question">? {props.row.question}</div>
        </Show>
      </div>
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
            classList={{ dim: !!it.dim, checked: !!it.checked }}
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
