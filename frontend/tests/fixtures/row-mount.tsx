// 레일 행(RailRow.tsx)을 DOM에 올려 보는 테스트용 마운트. 상호작용(우클릭=⋯ 버튼)과
// 행 3줄 구성을 jsdom에서 확인한다.
import { createSignal } from 'solid-js';
import { render } from 'solid-js/web';
import { RailHoverCard, type HoverCardSource } from '../../js/shell/RailHoverCard.js';
import { NativeChildren } from '../../js/shell/NativeChildren.js';
import { Row, Menu } from '../../js/shell/RailRow.js';
import { ViewGroup } from '../../js/shell/ViewGroup.js';

export function mountRow(root: HTMLElement, props: Parameters<typeof Row>[0]) {
  return render(() => <Row {...props} />, root);
}

/** 묶음 머리(ViewGroup) — 접힘 토글이 있으면 캐럿 버튼, 접히면 구성원 행을 그리지 않는다. */
export function mountViewGroup(
  root: HTMLElement,
  opts: { label: string; rows: string[]; collapsed?: () => boolean; onToggle?: () => void; onOpen?: () => void },
) {
  return render(() => (
    <ViewGroup group={{ id: 'v1', label: opts.label, rows: [] as any }} onOpen={opts.onOpen || (() => {})}
      collapsed={opts.collapsed?.()} onToggle={opts.onToggle}>
      {opts.rows.map((r) => <div class="member">{r}</div>)}
    </ViewGroup>
  ), root);
}

export function mountMenu(root: HTMLElement, props: Parameters<typeof Menu>[0]) {
  return render(() => <Menu {...props} />, root);
}

export function mountNativeChildren(root: HTMLElement, row: Parameters<typeof NativeChildren>[0]['row']) {
  return render(() => <NativeChildren row={row} />, root);
}

// 리디자인 8단계 — 폰 세션 카드(Fleet.tsx FleetRow). 두 줄 · 개입 필요 카드 · 길게 누르기.
import { FleetRow } from '../../js/shell/Fleet.js';
export function mountFleetRow(root: HTMLElement, props: Parameters<typeof FleetRow>[0]) {
  return render(() => <FleetRow {...props} />, root);
}

// 접힌 띠 호버 카드(RailHoverCard.tsx). 칸은 테스트가 `root`에 직접 만든다(`data-card-key`). 데이터는 키 → 소스 표이고
// `update`로 한 칸의 값을 바꿔 열린 카드가 따라가는지(상태 변경 · 도구 이름 고정) 본다.
export function mountHoverCard(
  root: HTMLElement,
  opts: { sources: Record<string, HoverCardSource>; enabled?: boolean; openDelay?: number; closeGrace?: number },
) {
  const [map, setMap] = createSignal(opts.sources);
  const [enabled, setEnabled] = createSignal(opts.enabled !== false);
  const dispose = render(() => (
    <RailHoverCard root={() => root} enabled={enabled} resolve={(k) => map()[k] ?? null}
      openDelay={opts.openDelay} closeGrace={opts.closeGrace} />
  ), root.ownerDocument.createElement('div'));
  return {
    dispose,
    setEnabled,
    update: (key: string, patch: Partial<HoverCardSource['row']>) =>
      setMap({ ...map(), [key]: { ...map()[key], row: { ...map()[key].row, ...patch } } }),
    remove: (key: string) => { const m = { ...map() }; delete m[key]; setMap(m); },
  };
}

// ── 저장소 가지 그래프(세션 로직 점검 단계 5) ─────────────────────────────────
import { SessionNode } from '../../js/shell/SessionNode.js';
import { RepoBranch } from '../../js/shell/RepoBranch.js';
import { EndedFold } from '../../js/shell/EndedFold.js';

export function mountSessionNode(root: HTMLElement, props: Parameters<typeof SessionNode>[0]) {
  return render(() => <SessionNode {...props} />, root);
}

/** 저장소 묶음 하나를 그린다. calls에 열기·메뉴·접기가 쌓인다. 돌려준 setGroup으로 새 묶음 객체를 넣어 볼 수 있다. */
export function mountRepoBranch(root: HTMLElement, group: any, opts: { collapsed?: boolean; ended?: any[]; endedOpen?: boolean; calls?: string[]; editing?: string } = {}) {
  const calls = opts.calls || [];
  const [g, setGroup] = createSignal<any>(group);
  render(() => (
    <div id="vt-wgrail">
      <RepoBranch
        group={g()}
        collapsed={!!opts.collapsed}
        onToggle={() => calls.push('toggle')}
        isActive={() => false}
        nodeProps={(row) => ({ onOpen: () => calls.push('open:' + row.name), onContext: () => calls.push('menu:' + row.name),
          editing: opts.editing === row.name, editingValue: row.name, onEditCommit: () => calls.push('commit:' + row.name) })}
        kidsCollapsed={() => false}
        hasEnded={(opts.ended || []).length > 0}
        ended={() => <EndedFold sessions={opts.ended || []} open={!!opts.endedOpen} onToggle={() => calls.push('ended')} onChanged={() => {}} />}
      />
    </div>
  ), root);
  return { setGroup };
}
