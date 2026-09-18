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
import { createSignal, createEffect, onCleanup, For, Show } from 'solid-js';
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
      data-priority={props.chip.priority}
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

/**
 * 폭이 모자라면 순위가 낮은 칩부터 덜어낸다.
 *
 * 칩 너비는 글자 수·폰트·스킨에 따라 달라서 **그려보기 전에는 모른다.** 그래서
 * 다 그려놓고 재는 수밖에 없다: 넘치면 최하위 하나를 숨기고 다시 재기를 반복한다.
 * 칩 수가 한 자리라 반복은 몇 번이면 끝난다.
 *
 * DOM에서 빼지 않고 `hidden` 클래스만 토글하는 이유 — Solid의 <For>가 관리하는
 * 노드를 여기서 직접 없애면 다음 갱신 때 서로 다른 것을 실제 DOM으로 믿게 된다.
 * 클래스 토글은 Solid의 소유권을 건드리지 않는다.
 */
function fitChips(host: HTMLElement) {
  const els = [...host.querySelectorAll<HTMLElement>('.vt-hud-chip')];
  if (!els.length) return;

  // 매번 전부 되살리고 시작한다 — 넓어졌을 때 되돌아와야 하므로.
  for (const el of els) el.classList.remove('overflow-hidden');

  // 순위가 낮은(숫자가 큰) 것부터 후보. 같은 순위면 오른쪽 것을 먼저 버린다.
  const byDrop = els
    .map((el, i) => ({ el, i, p: Number(el.dataset.priority) || 0 }))
    .sort((a, b) => (b.p - a.p) || (b.i - a.i));

  let cursor = 0;
  // scrollWidth가 clientWidth를 넘으면 넘친 것이다(#vt-hud는 overflow-x:auto).
  while (host.scrollWidth > host.clientWidth && cursor < byDrop.length) {
    byDrop[cursor].el.classList.add('overflow-hidden');
    cursor += 1;
  }

  // 전부 숨겨도 안 들어가면(레일·dock이 극단적으로 넓은 경우) 마지막으로 숨긴
  // 것을 되살린다 — 빈 띠보다는 스크롤되는 칩 하나가 낫다.
  if (cursor === byDrop.length && cursor > 0) {
    byDrop[cursor - 1].el.classList.remove('overflow-hidden');
  }
}

function Hud(props: { deps: HudDeps; root: HTMLElement }) {
  const [chips, setChips] = createSignal<HudChip[]>([]);

  // 갱신: 30초 폴링(§7). 간격이 긴 이유는 여기 실리는 값이 전부 "천천히 바뀌는
  // 것"(터널 모드·세이프모드·사용량)이기 때문이다 — 빠르게 바뀌는 에이전트
  // 상태는 탭·레일이 이미 실시간으로 보여준다. 백그라운드 탭에서는 쉰다.
  const refresh = async () => setChips(buildHudChips(await collect(props.deps)));

  refresh();
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  onCleanup(() => clearInterval(timer));

  const side = (s: 'left' | 'right') => chips().filter((c) => c.side === s);

  // 다시 재야 하는 때는 둘뿐이다 — 칩 내용이 바뀌었을 때(값이 길어지면 폭도
  // 는다), 그리고 띠 자체의 폭이 바뀌었을 때(창 크기·레일/dock 리사이즈).
  // 레일 드래그는 프레임마다 이벤트가 오므로 rAF로 한 프레임에 한 번만 잰다.
  //
  // 컨테이너는 mountHud가 받은 root를 그대로 쓴다. 마커 엘리먼트를 두고
  // `ref`에서 parentElement를 타고 올라가려 했다가 한 번 헛짚었다 — Solid의
  // ref는 **노드가 부모에 꽂히기 전에** 불려서 그 시점 parentElement가 null이다.
  // 그래서 옵저버가 아예 안 붙고 칩이 하나도 안 접혔다(실브라우저에서 재현).
  let raf = 0;
  const scheduleFit = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; fitChips(props.root); });
  };

  createEffect(() => {
    chips();            // 내용 변경 구독
    scheduleFit();
  });

  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(scheduleFit);
    ro.observe(props.root);
    onCleanup(() => ro.disconnect());
  }
  onCleanup(() => { if (raf) cancelAnimationFrame(raf); });

  return (
    <>
      <For each={side('left')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
      <div class="vt-hud-spacer" />
      <For each={side('right')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
    </>
  );
}


export function mountHud(root: HTMLElement, deps: HudDeps) {
  return render(() => <Hud deps={deps} root={root} />, root);
}
