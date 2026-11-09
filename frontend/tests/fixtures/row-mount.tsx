// 레일 행(RailRow.tsx)을 DOM에 올려 보는 테스트용 마운트. 상호작용(우클릭=⋯ 버튼)과
// 행 3줄 구성을 jsdom에서 확인한다.
import { render } from 'solid-js/web';
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
