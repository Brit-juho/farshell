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
