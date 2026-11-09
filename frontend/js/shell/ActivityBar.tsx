// 활동 막대 48px — 작업대 맨 왼쪽 세로 막대(리디자인 1단계, 시안 spec.html 「활동 막대」).
//
// 위 무리: 사이드바를 바꾸는 활동 9개(capability 게이팅 뒤). role="tablist" +
// aria-orientation="vertical", 「수동 활성화」 — ↑↓·Home·End는 초점만 옮기고 Enter·Space
// (버튼 클릭)가 연다. 펼친 상태에서 지금 활동을 다시 누르면 사이드바를 접는다(Workbench.tsx).
// 아래 무리: 사이드바를 바꾸지 않는 동작(MCP · 스킬 관리 · 음성 · 설정) — 탭이 아니다.
// 위 무리의 순서·숨김은 부르는 쪽(Workbench.tsx)이 `items`로 넘기고, 아래 무리의 순서는 `bottomOrder`(CSS order)다 —
// 마이크 자리는 voice.js가 캐시한 노드를 옮겨 오는 곳이라 DOM 순서를 바꾸지 않는다(shell/activity-layout.js).
//
// 이름은 아이콘 칸이라 글자가 없다: aria-label과 공용 툴팁(data-tip, ui/tooltip.js)이 말한다.
// 툴팁 둘째 줄은 배지 수치와 단축키. 활성 표시는 왼쪽 2px 강조선(CSS ::before)이다.
//
// ⚠ 지연 청크(shell) — 상태를 가진 core 모듈을 정적 import 하지 않는다(ADR-26).
import { For, Show } from 'solid-js';
import { t } from '../core/i18n.js';
import { shellIcon } from '../ui/icons-shell.js';
import { badgeTip, stepActivityIndex, type Activity } from './activity-data.js';
import { SESSIONS_ACTIVITY } from './side-prefs.js';

export interface ActivityBarProps {
  items: () => Activity[];
  /** 지금 활동(펼침·접힘과 무관). */
  current: () => string;
  /** 오른쪽에 고정한 도구 활동 id(없으면 null) — 모서리에 강조 점 4px(시안 `.pinr`). */
  pinned?: () => string | null;
  /** 사이드바가 보이는가(펼침 또는 세션 48px 띠). 도구를 접어 0이면 false — 활성 표시를 끈다. */
  shown: () => boolean;
  badges: () => Record<string, number>;
  onSelect: (id: string) => void;
  onMcp: () => void;
  onSettings: () => void;
  /** 키맵 id → 표시용 조합(예: ⌘B). 없거나 브라우저가 가로채는 조합이면 ''. */
  keyHint: (id: string) => string;
  /** 도구 활동 버튼을 칸으로 끌어낼 수 있게 한다(layout/dnd.js의 브리지). */
  wireDrag?: (el: HTMLElement, id: string) => void;
  /** 아래 무리(mcp·mic·settings)의 자리(CSS order). 없으면 마크업 순서. 설정 → 상태 막대 · 활동 막대가 정한다. */
  bottomOrder?: (id: string) => number;
}

const ICON = 20;
const STROKE = 1.75;

export function ActivityBar(props: ActivityBarProps) {
  let listRef: HTMLDivElement | undefined;

  const badgeOf = (a: Activity) => (a.badge ? props.badges()[a.badge] || 0 : 0);
  const tipSub = (a: Activity) => {
    const parts: string[] = [];
    const b = badgeTip(a, badgeOf(a));
    if (b) parts.push(b);
    if (props.pinned?.() === a.id) parts.push(t('오른쪽에 고정됨'));
    // 지금 활동의 툴팁만 ⌘B를 알린다 — 그 키가 접고 펴는 대상이 지금 활동이다.
    if (a.id === props.current()) {
      const k = props.keyHint('railToggle');
      if (k) parts.push(t('{key} 사이드바 토글', { key: k }));
    }
    return parts.length ? parts.join(' · ') : undefined;
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    const tabs = Array.from(listRef?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || []);
    if (!tabs.length) return;
    const i = tabs.indexOf(document.activeElement as HTMLButtonElement);
    const next = stepActivityIndex(i, e.key, tabs.length);
    if (next < 0) return;
    e.preventDefault();
    tabs[next].focus();
  };

  const settingsHint = () => {
    const k = props.keyHint('settings');
    return k || undefined;
  };

  return (
    <nav id="vt-activitybar" class="vt-activitybar" aria-label={t('활동 막대')}>
      <div
        class="vt-act-list"
        role="tablist"
        aria-orientation="vertical"
        aria-label={t('사이드바 보기')}
        ref={listRef}
        onKeyDown={onKeyDown}
      >
        <For each={props.items()}>
          {(a) => {
            const selected = () => props.current() === a.id;
            return (
              <button
                type="button"
                class="vt-act"
                classList={{ active: selected() && props.shown(), pinned: props.pinned?.() === a.id }}
                role="tab"
                id={`vt-act-${a.id}`}
                data-activity={a.id}
                aria-selected={selected()}
                aria-controls="vt-side"
                tabindex={selected() ? 0 : -1}
                aria-label={a.label}
                data-tip={a.label}
                data-tip-sub={tipSub(a)}
                data-tip-side="right"
                ref={(el) => { if (a.id !== SESSIONS_ACTIVITY) props.wireDrag?.(el, a.id); }}
                onClick={() => props.onSelect(a.id)}
              >
                <span class="vt-act-ico" innerHTML={shellIcon(a.icon, ICON, STROKE)} />
                <Show when={badgeOf(a) > 0}>
                  {/* 「세션」 배지는 waiting 세션 수라 호박색(개입 필요와 같은 색), 나머지는 중립. */}
                  <span class="vt-badge vt-act-badge" data-state={a.badge === 'waiting' ? 'waiting' : undefined} aria-hidden="true">{badgeOf(a) > 99 ? '99+' : badgeOf(a)}</span>
                </Show>
              </button>
            );
          }}
        </For>
      </div>
      <div class="vt-act-gap" />
      <div class="vt-act-bottom" role="group" aria-label={t('도구')}>
        <button
          type="button"
          class="vt-act vt-act-mcp"
          style={{ order: props.bottomOrder?.('mcp') }}
          aria-label={t('MCP · 스킬 관리')}
          data-tip={t('MCP · 스킬 관리')}
          data-tip-side="right"
          onClick={() => props.onMcp()}
          innerHTML={shellIcon('box', ICON, STROKE)}
        />
        {/* 마이크는 버튼을 새로 만들지 않고 index.html의 `#mic-btn-wrap` 노드 자체를 여기로
            옮겨 온다(term/keybar.js의 placeMicButton) — voice.js가 그 id를 캐시해 상태를
            그리므로 같은 노드여야 한다. 음성 미설치면 비어(:empty) 사라진다. */}
        <div id="vt-activity-mic-home" class="vt-act vt-act-mic-home" style={{ order: props.bottomOrder?.('mic') }} />
        <button
          type="button"
          class="vt-act vt-act-settings"
          style={{ order: props.bottomOrder?.('settings') }}
          aria-label={t('설정')}
          data-tip={t('설정')}
          data-tip-sub={settingsHint()}
          data-tip-side="right"
          onClick={() => props.onSettings()}
          innerHTML={shellIcon('settings', ICON, STROKE)}
        />
      </div>
    </nav>
  );
}
