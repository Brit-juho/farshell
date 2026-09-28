// Solid용 agentIcon — 브랜드 로고 청크(ui/icons.js)가 늦게 와도 다시 그리도록
// 도착 이벤트를 신호로 바꿔 준다. innerHTML={agentMark(name)}로 쓰면 추적된다.
import { createRoot, createSignal } from 'solid-js';
import { agentIcon } from '../ui/icons.js';

const [brandTick, setBrandTick] = createRoot(() => createSignal(0));
window.addEventListener('vt:brand-icons', () => setBrandTick((n) => n + 1));

export function agentMark(agentName: string): string {
  brandTick();
  return agentIcon(agentName);
}
