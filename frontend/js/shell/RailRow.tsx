// 워크트리 레일의 **행 하나와 컨텍스트 메뉴**. 목록 조립·상태 관리는 Rail.tsx.
//
// Rail.tsx에서 떼어낸 이유: 두 컴포넌트 다 자기 props만 보고 그리는 순수
// 표현부라, 400줄짜리 Rail 본체와 섞여 있을 이유가 없다. 색·배지·상태 문장을
// 고칠 때 폴링·리사이저 코드를 지나치지 않아도 된다.
//
// ⚠ 여기도 `shell` 지연 청크다 — core/store.js 계열을 직접 import하지 않는다
// (Rail.tsx 머리말과 같은 이유, ADR-26/N35).
import { For, Show, onCleanup } from 'solid-js';

import { hashRepoColorIndex, repoColorKey } from './rail-data.js';
import { type DesktopRailRow } from './rail-fetch.js';
import { agentIcon, agentLabel } from '../ui/icons.js';

// ADR-29 B — 행은 이제 언제나 세션이다(워크트리 행이 없어졌다). `sleeping`은
// 이 행이 레일의 「잠자는 중」 구역에 그려지는 것인지를 부모(Rail.tsx)가
// 알려준다 — 잠든 행은 클릭하면 깨우고(openRow가 갈림), 시각적으로도
// 가라앉은 톤을 쓴다(옛 no-session의 후계자, 뜻은 "세션이 없다"에서
// "지금 화면에 없다"로 바뀌었다).
export function Row(props: {
  row: DesktopRailRow; active: boolean; compact?: boolean; sleeping?: boolean;
  /** ADR-29 E — 드래그로 그룹 재편성. tmux 이름이 있는(=재편성 대상이 될 수
   * 있는) 행만 끌 수 있다. */
  draggable?: boolean; onDragStart?: (e: DragEvent) => void;
  onOpen: (e: MouseEvent) => void; onContext: (e: MouseEvent) => void;
}) {
  const isRemote = () => !!props.row.remote;
  const diffLabel = () => {
    const c = props.row.changed;
    if (c && c.files > 0) return `+${c.add} −${c.del}`;
    return props.row.diffFiles != null && props.row.diffFiles > 0 ? `파일 ${props.row.diffFiles}` : null;
  };
  const rowName = () => props.row.name;
  // ADR-29 B — 브랜치 배지. 메인 워크트리 세션은 원래부터 브랜치를 안 보였다
  // (그룹 헤더가 이미 저장소를 말하므로, main은 "기본"이라 생략). 워크트리에
  // 안 속한 세션(순수 셸)은 branch 자체가 없다.
  const branchText = () => (props.row.branch && !props.row.isMainWorktree) ? props.row.branch : '';
  // 48px 접힘에서는 이름·상태 문장이 숨는다(10-shell-layout.md §5: "마크+색점만.
  // 호버 시 툴팁에 2줄"). 그 두 줄이 툴팁이다 — 없으면 접힌 레일은 정체를
  // 알 수 없는 색 막대 기둥이 된다. 펼친 상태에서는 달지 않는다(글자가 이미
  // 보이는 자리에 툴팁이 뜨면 잡음이다).
  // 2.1.6까지는 `이름\n소유자 · 상태` 한 덩어리를 title에 넣었다. 네이티브
  // 툴팁은 두 줄을 같은 크기·같은 색으로 그려서 무엇이 이름인지 안 보였다 —
  // 공용 툴팁(ui/tooltip.js)은 이름(ui)과 값(mono/sub)을 다른 단으로 그리므로
  // 여기서도 둘을 data-tip / data-tip-sub로 나눠 넘긴다.
  const compactSub = () => {
    if (!props.compact) return undefined;
    // 상태 문장이 빈 행(방금 만든 세션 등)은 보조 단을 아예 안 만든다 — 빈
    // 문자열을 넘기면 툴팁에 빈 칸이 한 칸 붙는다.
    return [branchText(), props.row.statusSentence].filter(Boolean).join(' · ') || undefined;
  };

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
      classList={{ active: props.active, sleeping: !!props.sleeping }}
      onClick={props.onOpen}
      onContextMenu={props.onContext}
      onKeyDown={onKeyDown}
      draggable={!!props.draggable}
      onDragStart={props.onDragStart}
      role="button"
      tabindex="0"
      // 지금 어느 것을 보고 있는지가 **클래스로만** 표시돼 있었다 — 눈으로는
      // 배경색으로 보이지만 스크린리더에는 아무 말도 안 했다. 목록에서 "현재
      // 것"을 가리키는 표준 표기가 aria-current다.
      aria-current={props.active ? 'true' : undefined}
      data-tip={props.compact ? rowName() : undefined}
      data-tip-sub={compactSub()}
      data-tip-side="right"
    >
      {/* 20-design-system.md §5(O2): 레일 행 왼쪽 끝 세로 막대는 저장소 해시
          색점(원형 dot과 헷갈리지 않는 "막대") — 상태 5색·acc와는 별개 램프
          (--color-hash-1..8). 그 오른쪽의 기존 막대가 상태색(30-worktree.md
          §4/10-shell-layout.md §5)을 그대로 맡는다. ADR-29 B — 워크트리에 안
          속한 세션(순수 셸)은 저장소가 없어 색점만 "없음"(kind-session이
          CSS에서 투명 처리); 상태색 막대는 이제 모든 세션 행에 그린다 —
          행 자체가 언제나 진짜 세션이므로 상태가 항상 의미 있다. */}
      {/* 98 §4 — 해시의 입력이 저장소 **이름**에서 **소유자**로 바뀌었다.
          같은 조직의 저장소가 같은 색이 되어, 이 막대가 처음으로 정보를 갖는다
          (이름 해시는 디자인 리뷰에서 S1 「의미 없는 장식」이었다). remote를
          못 읽은 저장소는 예전과 똑같이 이름 해시로 떨어진다. */}
      <span class={`vt-wgrail-hash ${props.row.repoName ? `hash-${hashRepoColorIndex(repoColorKey(props.row.repoName, props.row.gitRemote))}` : 'kind-session'}`} />
      <span class={`vt-srow-mark vt-wgrail-bar tone-${props.row.status}`} />
      {/* 접힘 전용 에이전트 마크. 펼친 상태의 마크는 아래 row-main 안에 있고
          그 블록이 접히면 통째로 숨으므로, 같은 마크를 이 자리에 한 번 더
          그린다(둘이 동시에 보이는 일은 없다). */}
      <Show when={props.compact && props.row.agent}>
        <span
          class="vt-srow-agent vt-wgrail-agent vt-wgrail-agent-compact"
          data-tip={agentLabel(props.row.agent!)}
          data-tip-side="right"
          innerHTML={agentIcon(props.row.agent!)}
        />
      </Show>
      <div class="vt-srow-main vt-wgrail-row-main">
        <div class="vt-srow-top vt-wgrail-row-top">
          {/* 2.1.6 — 헤더의 워크트리 탭(layout/tabbar.js)은 이 마크를 달고 있었고
              레일 행과 폰의 플릿 행은 안 달고 있었다. 같은 세션을 보는 세 화면이
              서로 다른 것을 말하던 것을 맞춘다. 에이전트를 아직 모르면
              (agent == null) 아무것도 안 그린다 — "셸이다"와 "모른다"는 다르다. */}
          <Show when={props.row.agent}>
            {(a) => (
              <span class="vt-srow-agent vt-wgrail-agent" data-tip={agentLabel(a())} data-tip-side="right" innerHTML={agentIcon(a())} />
            )}
          </Show>
          <span class="vt-srow-name vt-wgrail-name">{rowName()}</span>
          {/* ADR-29 B — 브랜치 배지. 그룹 헤더가 이미 저장소를 말하므로 여기는
              브랜치만(main은 "기본"이라 생략, branchText()가 그 규칙을 안다). */}
          <Show when={branchText()}>
            <span class="vt-srow-meta vt-wgrail-branch">{branchText()}</span>
          </Show>
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
