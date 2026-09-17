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
import { agentIcon, agentLabel } from '../ui/icons.js';

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

  // `role="button"` + `tabindex=0`으로 포커스는 갔지만 **Enter·Space가 아무
  // 일도 안 했다**(실브라우저 확인: 활성 행이 안 바뀜). div에 버튼 역할만
  // 붙이면 브라우저가 키보드 활성화를 대신 해주지 않는다 — 진짜 <button>이
  // 공짜로 주는 것을 직접 붙여야 한다. 레일은 세션을 고르는 주 경로이고
  // 재정렬이 키보드 전용(Mod+Alt+Shift+←/→)이라, 여기서 키보드가 끊기면
  // 그 경로 전체가 끊긴다.
  //
  // Space는 preventDefault가 필수다 — 안 하면 활성화와 동시에 페이지가
  // 스크롤된다(버튼의 기본 동작에는 그 억제가 이미 들어 있다).
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    if (e.key !== 'Enter') e.preventDefault();
    props.onOpen(e as unknown as MouseEvent);
  };

  return (
    <div
      class="vt-srow vt-wgrail-row"
      classList={{ active: props.active, 'no-session': noSession() }}
      onClick={props.onOpen}
      onContextMenu={props.onContext}
      onKeyDown={onKeyDown}
      role="button"
      tabindex="0"
      // 지금 어느 것을 보고 있는지가 **클래스로만** 표시돼 있었다 — 눈으로는
      // 배경색으로 보이지만 스크린리더에는 아무 말도 안 했다. 목록에서 "현재
      // 것"을 가리키는 표준 표기가 aria-current다.
      aria-current={props.active ? 'true' : undefined}
    >
      {/* 20-design-system.md §5(O2): 레일 행 왼쪽 끝 세로 막대는 저장소 해시
          색점(원형 dot과 헷갈리지 않는 "막대") — 상태 5색·acc와는 별개 램프
          (--color-hash-1..8). 그 오른쪽의 기존 막대가 상태색(30-worktree.md
          §4/10-shell-layout.md §5)을 그대로 맡는다. 「기타」 세션 행은 저장소가
          없어 둘 다 "색점 없음"(kind-session이 CSS에서 투명 처리). */}
      <span class={`vt-wgrail-hash ${isWt() ? `hash-${hashRepoColorIndex((props.row as WorktreeRailRowInput).repoName)}` : 'kind-session'}`} />
      <span class={`vt-srow-mark vt-wgrail-bar ${isWt() ? `tone-${props.row.status}` : 'kind-session'}`} />
      <div class="vt-srow-main vt-wgrail-row-main">
        <div class="vt-srow-top vt-wgrail-row-top">
          {/* 2.1.6 — 헤더의 워크트리 탭(layout/tabbar.js)은 이 마크를 달고 있었고
              레일 행과 폰의 플릿 행은 안 달고 있었다. 같은 세션을 보는 세 화면이
              서로 다른 것을 말하던 것을 맞춘다. 에이전트를 아직 모르면
              (agent == null) 아무것도 안 그린다 — "셸이다"와 "모른다"는 다르다. */}
          <Show when={props.row.agent}>
            {(a) => (
              <span class="vt-srow-agent vt-wgrail-agent" title={agentLabel(a())} innerHTML={agentIcon(a())} />
            )}
          </Show>
          <span class="vt-srow-name vt-wgrail-name">{rowName()}</span>
          <Show when={diffLabel()}>
            <span class="vt-srow-meta vt-wgrail-diff">{diffLabel()}</span>
          </Show>
        </div>
        <div class="vt-srow-sub vt-wgrail-row-sub">
          {props.row.statusSentence}
          <Show when={isRemote()}><span class="vt-wgrail-remote-note"> · 원격</span></Show>
        </div>
        <Show when={props.row.status === 'waiting' && props.row.question}>
          <div class="vt-srow-question vt-wgrail-question">? {props.row.question}</div>
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
