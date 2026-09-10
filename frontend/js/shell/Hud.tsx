// N34 §7 — 상태바 HUD 24px. hud-data.ts가 만든 칩 배열을 그리기만 한다.
// 첫 Solid 소비처다(N17/N18 파이프라인 위).
//
// ⚠ **이 파일은 core/*를 직접 import하지 않는다.** main.js가 `import()`로 이걸
// 지연 로드하고(ADR-26 번들 게이트), 그 지연 청크가 core/api·core/store·
// core/dom을 직접 import하면 Rollup이 그 모듈들을 청크 안에 **복제**해 넣는다
// (실측: shell.js가 import 0개인 자족 번들로 나왔다). 그러면 앱의 액션
// 레지스트리와 HUD가 보는 레지스트리가 서로 다른 객체가 돼서, 칩을 눌러도
// 아무 일도 안 일어난다 — vite.config.js 상단이 app.js/voice.js에 대해 설명한
// 중복 문제와 정확히 같은 함정이다. 그래서 필요한 것들을 main.js가 인자로
// 넘겨준다(HudDeps).
import { createSignal, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { buildHudChips, type HudChip, type HudInput } from './hud-data.js';

const POLL_MS = 30000;

export interface HudDeps {
  vtFetch: (path: string) => Promise<unknown>;
  /** 활성 세션의 tmux 이름(없으면 빈 문자열) — 「연결된 화면」 조회에 쓴다. */
  activeTmuxName: () => string;
  getAction: (name: string) => unknown;
  e2eEnabled: boolean;
}

// 실패한 조회는 조용히 "값 없음"으로 떨어뜨린다 — HUD는 부가 정보라, 한
// 항목이 안 되는 걸 에러로 알리면 소음만 는다(clients.js와 같은 판단).
async function safeFetch<T>(deps: HudDeps, path: string): Promise<T | null> {
  try {
    return (await deps.vtFetch(path)) as T;
  } catch (_) {
    return null;
  }
}

async function collect(deps: HudDeps): Promise<HudInput> {
  const tmuxName = deps.activeTmuxName();

  const [caps, tunnel, safeMode, usage, clients] = await Promise.all([
    safeFetch<HudInput['caps']>(deps, '/api/capabilities'),
    safeFetch<HudInput['tunnel']>(deps, '/api/tunnel/status'),
    safeFetch<HudInput['safeMode']>(deps, '/api/safe-mode'),
    safeFetch<HudInput['usage']>(deps, '/api/usage'),
    tmuxName
      ? safeFetch<{ clients?: unknown[] }>(deps, `/api/tmux/clients?session=${encodeURIComponent(tmuxName)}`)
      : Promise.resolve(null),
  ]);

  return {
    port: location.port,
    caps,
    tunnel,
    e2e: deps.e2eEnabled,
    safeMode,
    usage,
    screens: clients?.clients?.length ?? null,
  };
}

function Chip(props: { chip: HudChip; deps: HudDeps }) {
  const clickable = () => !!props.chip.action;
  const fire = () => {
    const id = props.chip.action;
    if (!id) return;
    const fn = props.deps.getAction(id);
    if (typeof fn === 'function') (fn as () => void)();
  };

  return (
    <span
      class="vt-hud-chip"
      classList={{ [`tone-${props.chip.tone}`]: true, clickable: clickable() }}
      data-chip={props.chip.id}
      onClick={clickable() ? fire : undefined}
      onKeyDown={clickable() ? (e: KeyboardEvent) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fire(); }
      } : undefined}
      role={clickable() ? 'button' : undefined}
      tabindex={clickable() ? 0 : undefined}
    >
      <Show when={props.chip.dot}>
        <span class="vt-hud-dot" />
      </Show>
      <Show when={props.chip.label}>
        <span class="vt-hud-label">{props.chip.label}</span>
      </Show>
      <Show when={props.chip.value}>
        <span class="vt-hud-value">{props.chip.value}</span>
      </Show>
      <Show when={props.chip.hint}>
        <span class="vt-hud-hint">{props.chip.hint}</span>
      </Show>
    </span>
  );
}

function Hud(props: { deps: HudDeps }) {
  const [chips, setChips] = createSignal<HudChip[]>([]);

  // 갱신: 30초 폴링(§7). 간격이 긴 이유는 여기 실리는 값이 전부 "천천히 바뀌는
  // 것"(터널 모드·세이프모드·사용량)이기 때문이다 — 빠르게 바뀌는 에이전트
  // 상태는 탭·레일이 이미 실시간으로 보여준다. 백그라운드 탭에서는 쉰다.
  const refresh = async () => setChips(buildHudChips(await collect(props.deps)));

  refresh();
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  onCleanup(() => clearInterval(timer));

  const side = (s: 'left' | 'right') => chips().filter((c) => c.side === s);

  return (
    <>
      <For each={side('left')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
      <div class="vt-hud-spacer" />
      <For each={side('right')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
    </>
  );
}

export function mountHud(root: HTMLElement, deps: HudDeps) {
  return render(() => <Hud deps={deps} />, root);
}
