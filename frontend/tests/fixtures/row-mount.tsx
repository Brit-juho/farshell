// 레일 행(RailRow.tsx)을 DOM에 올려 보는 테스트용 마운트. 상호작용(우클릭=⋯ 버튼)과
// 행 3줄 구성을 jsdom에서 확인한다.
import { render } from 'solid-js/web';
import { NativeChildren } from '../../js/shell/NativeChildren.js';
import { Row, Menu } from '../../js/shell/RailRow.js';

export function mountRow(root: HTMLElement, props: Parameters<typeof Row>[0]) {
  return render(() => <Row {...props} />, root);
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
