// 활동 막대(ActivityBar.tsx)를 DOM에 올려 보는 테스트용 마운트. 상태는 테스트가 시그널로 쥔다.
import { createSignal } from 'solid-js';
import { render } from 'solid-js/web';
import { ActivityBar, type ActivityBarProps } from '../../js/shell/ActivityBar.js';
import { availableActivities } from '../../js/shell/activity-data.js';

export function mountActivityBar(
  root: HTMLElement,
  opts: Partial<ActivityBarProps> & { caps?: Record<string, unknown> | null } = {},
) {
  const [current, setCurrent] = createSignal('sessions');
  const [shown, setShown] = createSignal(true);
  const [badges, setBadges] = createSignal<Record<string, number>>({});
  const calls: { select: string[]; mcp: number; settings: number; drag: string[] } = { select: [], mcp: 0, settings: 0, drag: [] };
  const dispose = render(() => (
    <ActivityBar
      items={() => availableActivities(opts.caps === undefined ? {} : opts.caps)}
      current={current}
      shown={shown}
      badges={badges}
      onSelect={(id) => { calls.select.push(id); }}
      onMcp={() => { calls.mcp += 1; }}
      onSettings={() => { calls.settings += 1; }}
      keyHint={opts.keyHint || ((id) => (id === 'railToggle' ? '⌘B' : id === 'settings' ? '⌘,' : ''))}
      wireDrag={(el, id) => { calls.drag.push(id); el.setAttribute('data-drag-wired', id); }}
    />
  ), root);
  return { dispose, calls, setCurrent, setShown, setBadges };
}
