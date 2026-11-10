// N34 §7 — HUD. hud-data.ts가 만든 칩 배열을 그리기만 한다. 첫 Solid 소비처다(N17/N18 파이프라인 위).
// 2026-11 — 하단 상태바에서 맨 위 메뉴바(28px)로 옮겼다가, 리디자인 2단계에서 다시 **아래 상태 막대 24**가 됐다.
// 맨 위는 제목 막대(TitleBar.tsx)이고 FarShell 메뉴도 그리로 갔다. 에이전트 칩은 맨 왼쪽 고정(hud-data.ts),
// 그 오른쪽이 토스트의 상태 막대 메시지 칸(`#vt-hud-msg`, ui/toast.js가 이 막대에 붙인다).
//
// ⚠ **이 파일은 core/*를 직접 import하지 않는다.** main.js가 `import()`로 이걸
// 지연 로드하고(ADR-26 번들 게이트), 그 지연 청크가 core/api·core/store·
// core/dom을 직접 import하면 Rollup이 그 모듈들을 청크 안에 **복제**해 넣는다
// (실측: shell.js가 import 0개인 자족 번들로 나왔다). 그러면 앱의 액션
// 레지스트리와 HUD가 보는 레지스트리가 서로 다른 객체가 돼서, 칩을 눌러도
// 아무 일도 안 일어난다 — vite.config.mjs 상단이 app.js/voice.js에 대해 설명한
// 중복 문제와 정확히 같은 함정이다. 그래서 필요한 것들을 main.js가 인자로
// 넘겨준다(HudDeps).
import { createSignal, createEffect, onCleanup, For, Show } from 'solid-js';
import { render } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { buildHudChips, parseHudLayout, type AgentCounts, type HudChip, type HudInput, type HudLayout } from './hud-data.js';
// core/poll.js는 상태를 갖지 않는 순수 모듈이라 이 지연 청크에 복제돼도 문제없다(위 ⚠의 예외).
import { createPoll, isStale } from '../core/poll.js';

const POLL_MS = 30000;
const SCREEN_POLL_MS = 5000;
// CPU·네트워크는 초 단위로 변하는 값이라 다른 HUD 데이터(30초)와 따로 3초마다 갱신한다.
const STATS_POLL_MS = 3000;
// 푸시 구독 상태는 서비스 워커에 묻는다 — 워커가 멎어 있으면 끝나지 않을 수 있어
// 따로 마감을 둔다(서버 호출은 core/api.js가 마감을 건다).
const PUSH_STATE_TIMEOUT_MS = 5000;

function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    p.then((v) => { clearTimeout(timer); resolve(v); }, () => { clearTimeout(timer); resolve(fallback); });
  });
}

export interface HudDeps {
  vtFetch: (path: string) => Promise<unknown>;
  /** 활성 세션의 tmux 이름(없으면 빈 문자열) — 「연결된 화면」 조회에 쓴다. */
  activeTmuxName: () => string;
  activeClientsPath?: () => string;
  getAction: (name: string) => unknown;
  e2eEnabled: boolean;
  /** 저장된 배치(JSON 문자열, `ui.hud.layout`). 없으면 기본 배치. core/settings.js를 직접 못 읽으니 main.js가 넘긴다. */
  getLayout?: () => string | undefined;
  /** 배치가 바뀌면 부른다. 해제 함수를 돌려준다. */
  subscribeLayout?: (fn: () => void) => () => void;
  /** 에이전트 4상태 집계(agent/state.js). 지연 청크가 core/*를 직접 못 읽으니 main.js가 넘긴다. */
  agentCounts?: () => AgentCounts;
  /** 집계가 바뀔 수 있을 때 부른다. 해제 함수를 돌려준다. */
  subscribeAgents?: (fn: () => void) => () => void;
  /** 이 브라우저의 푸시 구독 상태. */
  pushState?: () => Promise<'on' | 'off' | 'unavailable'>;
  /** 시계 표기용 로케일(`ko-KR`/`en-US`). */
  locale?: () => string;
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
  const clientsPath = deps.activeClientsPath?.() ||
    (tmuxName ? `/api/tmux/clients?session=${encodeURIComponent(tmuxName)}` : '');

  // allSettled — 한 항목이 거부돼도 나머지로 스냅샷을 만든다. 각 서버 호출은 마감이
  // 있고(core/api.js) 푸시 상태도 마감을 둬서, 어느 하나가 매달려도 HUD 전체가 멈추지 않는다.
  const settled = await Promise.allSettled([
    safeFetch<HudInput['caps']>(deps, '/api/capabilities'),
    safeFetch<HudInput['safeMode']>(deps, '/api/safe-mode'),
    safeFetch<HudInput['usage']>(deps, '/api/usage'),
    clientsPath
      ? safeFetch<{ clients?: unknown[] }>(deps, clientsPath)
      : Promise.resolve(null),
    safeFetch<HudInput['stats']>(deps, '/api/system/stats'),
    deps.pushState
      ? withTimeout(deps.pushState(), PUSH_STATE_TIMEOUT_MS, 'unavailable' as const)
      : Promise.resolve(null),
  ] as const);
  const val = <T,>(r: PromiseSettledResult<T>): T | null => (r.status === 'fulfilled' ? r.value : null);
  const caps = val(settled[0]);
  const safeMode = val(settled[1]);
  const usage = val(settled[2]);
  const clients = val(settled[3]);
  const stats = val(settled[4]);
  const push = val(settled[5]);

  return {
    push,
    port: location.port,
    caps,
    e2e: deps.e2eEnabled,
    safeMode,
    usage,
    screens: clients?.clients?.length ?? null,
    stats,
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
      data-tip={props.chip.tooltip}
      aria-label={props.chip.tooltip ? `${props.chip.label} ${props.chip.value ?? ""} ${props.chip.tooltip}` : undefined}
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
  let snapshot: HudInput | null = null;
  // 배치는 설정에서 바뀐다(즉시 반영 — 새로고침 없음). 폴링 결과와 배치가 만나는 자리는
  // publish() 하나다: 어느 쪽이 바뀌어도 같은 함수로 칩을 다시 만든다.
  let layout: HudLayout = parseHudLayout(props.deps.getLayout?.());
  // 폴링 결과(snapshot)와 별개로 흐르는 값들 — 에이전트 집계(상태 이벤트마다), 시계(분마다), 푸시(클릭 뒤).
  // 이 셋은 서버 조회가 아니라서 snapshot에 섞지 않고 publish가 합친다.
  let agents: AgentCounts | null = props.deps.agentCounts?.() ?? null;
  let now = Date.now();
  let push: HudInput['push'] = null;
  // 갱신 지연 — 서버 호출이 하나라도 성공한 마지막 시각. 오래되면 표식을 띄운다.
  const mountedAt = Date.now();
  let lastOkAt = 0;
  const [stale, setStale] = createSignal(false);
  const checkStale = () => setStale(isStale(lastOkAt, POLL_MS, Date.now(), mountedAt));
  // 이 HUD가 부르는 서버 호출을 한 곳에서 센다. ok는 "이번 실행에서 하나라도 답을 받았나".
  const tracked = (): { deps: HudDeps; ok: () => boolean } => {
    let ok = false;
    return {
      deps: {
        ...props.deps,
        vtFetch: async (path: string) => {
          const v = await props.deps.vtFetch(path);
          ok = true; lastOkAt = Date.now(); checkStale();
          return v;
        },
      },
      ok: () => ok,
    };
  };
  const publish = () => {
    if (!snapshot) return;
    setChips(buildHudChips({
      ...snapshot,
      agents,
      now,
      locale: props.deps.locale?.(),
      push: push ?? snapshot.push,
    }, layout));
  };
  const unsubscribe = props.deps.subscribeLayout?.(() => {
    layout = parseHudLayout(props.deps.getLayout?.());
    publish();
  });
  onCleanup(() => unsubscribe?.());

  // 갱신: 30초 폴링(§7). 간격이 긴 이유는 여기 실리는 값이 전부 "천천히 바뀌는
  // 것"(터널 모드·세이프모드·사용량)이기 때문이다 — 빠르게 바뀌는 에이전트
  // 상태는 탭·레일이 이미 실시간으로 보여준다. 백그라운드 탭에서는 쉰다.
  // 응답이 하나도 없었으면 거부해서 폴러가 간격을 늘리게 한다(core/poll.js). 그래도
  // 스냅샷은 갱신한다 — 예전처럼 "값 없음"으로 떨어진 칩을 보여 준다.
  const refresh = async () => {
    const tr = tracked();
    snapshot = await collect(tr.deps);
    push = null;   // 방금 조회한 값이 최신이다
    publish();
    if (!tr.ok()) throw new Error('hud: no response');
  };

  // 에이전트 상태는 이벤트로 오고 짧은 시간에 여러 번 바뀐다 — 한 프레임에 한 번만 다시 만든다.
  let agentRaf = 0;
  const unsubAgents = props.deps.subscribeAgents?.(() => {
    if (agentRaf) return;
    agentRaf = requestAnimationFrame(() => {
      agentRaf = 0;
      agents = props.deps.agentCounts?.() ?? null;
      publish();
    });
  });
  onCleanup(() => { unsubAgents?.(); if (agentRaf) cancelAnimationFrame(agentRaf); });

  // 시계: 분이 바뀔 때만 칩을 다시 만든다(5초마다 보되 같은 분이면 아무것도 안 한다).
  let lastMinute = Math.floor(now / 60000);
  const clockTimer = setInterval(() => {
    now = Date.now();
    const m = Math.floor(now / 60000);
    checkStale();
    if (m === lastMinute) return;
    lastMinute = m;
    publish();
  }, 5000);
  onCleanup(() => clearInterval(clockTimer));

  // 푸시 칩을 눌러 구독이 바뀌면 30초를 기다리지 않고 바로 다시 읽는다. 구독/해지는 권한 창과
  // 서버 왕복이 끼어 즉시 끝나지 않으므로 잠깐 간격을 두고 두 번 본다.
  const onPushClick = (e: Event) => {
    if (!(e.target as HTMLElement | null)?.closest?.('[data-chip="push"]')) return;
    for (const ms of [600, 2500]) {
      setTimeout(async () => {
        const st = await props.deps.pushState?.().catch(() => null);
        if (st) { push = st; publish(); }
      }, ms);
    }
  };
  props.root.addEventListener('click', onPushClick);
  onCleanup(() => props.root.removeEventListener('click', onPushClick));

  // attach/detach는 사용량보다 빨리 변한다. 다른 HUD 데이터의 30초 주기는
  // 유지하고 화면 수만 목록 패널과 같은 5초 간격으로 갱신한다.
  const refreshScreens = async () => {
    if (!snapshot) return;
    const tmuxName = props.deps.activeTmuxName();
    const path = props.deps.activeClientsPath?.() ||
      (tmuxName ? `/api/tmux/clients?session=${encodeURIComponent(tmuxName)}` : '');
    if (!path) { snapshot = { ...snapshot, screens: null }; publish(); return; }
    const tr = tracked();
    const data = await safeFetch<{ clients?: unknown[] }>(tr.deps, path);
    if (!snapshot) return;
    snapshot = { ...snapshot, screens: data?.clients?.length ?? null };
    publish();
    if (!tr.ok()) throw new Error('hud: no response');
  };

  const refreshStats = async () => {
    if (!snapshot) return;
    const tr = tracked();
    const stats = await safeFetch<HudInput['stats']>(tr.deps, '/api/system/stats');
    if (!snapshot) return;
    snapshot = { ...snapshot, stats };
    publish();
    if (!tr.ok()) throw new Error('hud: no response');
  };

  // 세 폴러 모두 core/poll.js — 이전 조회가 안 끝났으면 건너뛰고, 실패하면 간격을 늘리고,
  // 숨은 탭에서는 쉰다. 첫 조회(refresh)만 즉시 한다(예전과 같다).
  const polls = [
    createPoll(refresh, { interval: POLL_MS, immediate: true }),
    createPoll(refreshStats, { interval: STATS_POLL_MS }),
    createPoll(refreshScreens, { interval: SCREEN_POLL_MS }),
  ];
  for (const p of polls) p.start();
  onCleanup(() => { for (const p of polls) p.stop(); });

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
      <Show when={stale()}>
        <span class="vt-stale-mark vt-hud-stale" role="status" title={t('서버에서 새 값을 한동안 받지 못했습니다. 보이는 값은 마지막으로 받은 것입니다.')}>{t('갱신 지연')}</span>
      </Show>
      <For each={side('right')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
    </>
  );
}


export function mountHud(root: HTMLElement, deps: HudDeps) {
  return render(() => <Hud deps={deps} root={root} />, root);
}
