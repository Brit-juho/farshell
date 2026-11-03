// N34 §7 — HUD. hud-data.ts가 만든 칩 배열을 그리기만 한다. 첫 Solid 소비처다(N17/N18 파이프라인 위).
// 2026-11 — 하단 상태바에서 맨 위 메뉴바(28px)로 옮겼고, 맨 앞에 FarShell 메뉴(MenuButton)가 붙었다.
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
import { render, Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { buildHudChips, parseHudLayout, type AgentCounts, type HudChip, type HudInput, type HudLayout } from './hud-data.js';

const POLL_MS = 30000;
const SCREEN_POLL_MS = 5000;
// CPU·네트워크는 초 단위로 변하는 값이라 다른 HUD 데이터(30초)와 따로 3초마다 갱신한다.
const STATS_POLL_MS = 3000;

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
  /** 클립보드 복사(term/clipboard.js). 성공 여부를 돌려준다. */
  copyText?: (text: string) => Promise<boolean>;
  /** 토스트(ui/toast.js). */
  toast?: (msg: string, type?: string) => void;
  /** 시계 표기용 로케일(`ko-KR`/`en-US`). */
  locale?: () => string;
  /** 맥 키보드인가 — 메뉴의 단축키 표기(⌘/Ctrl)에 쓴다. */
  isMac?: boolean;
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

  const [caps, safeMode, usage, clients, stats, push] = await Promise.all([
    safeFetch<HudInput['caps']>(deps, '/api/capabilities'),
    safeFetch<HudInput['safeMode']>(deps, '/api/safe-mode'),
    safeFetch<HudInput['usage']>(deps, '/api/usage'),
    clientsPath
      ? safeFetch<{ clients?: unknown[] }>(deps, clientsPath)
      : Promise.resolve(null),
    safeFetch<HudInput['stats']>(deps, '/api/system/stats'),
    deps.pushState ? deps.pushState().catch(() => 'unavailable' as const) : Promise.resolve(null),
  ]);

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

interface MenuEntry {
  id: string;
  iconName?: string;
  label: string;
  keys?: string;
  disabled?: boolean;
  /** 이 항목 위에 구분선을 긋는다. */
  sepBefore?: boolean;
  run: (anchor: HTMLElement) => void;
}

// 접속 주소 — 터널이 떠 있으면 공개 주소, 아니면 지금 이 화면의 origin. 공개 주소 자체는 비밀이 아니다
// (들어가려면 로그인과 기기 등록이 따로 필요하다). 기기 등록용 QR·OTP는 여기서 만들지 않는다 — `fsh mobile`이 한다.
function accessUrl(caps: unknown): string {
  const tun = (caps as { tunnel?: { running?: boolean; url?: unknown } } | null | undefined)?.tunnel;
  if (tun && tun.running !== false && typeof tun.url === 'string' && /^https:\/\//.test(tun.url)) return tun.url;
  return location.origin;
}

/**
 * 맥 메뉴바의 첫 칸에 해당하는 FarShell 메뉴. 새 세션 · 빠른 열기 · 접속 주소 복사 · 설정 · 메뉴바 설정.
 * 드롭다운은 Portal로 body에 그린다 — #vt-hud가 overflow를 자르고 backdrop-filter가 fixed의 기준 상자를
 * 바꿔서 안에 두면 잘린다. 닫기: 바깥 클릭 · Esc · 항목 실행 · 스크롤/리사이즈. 키보드: ↑↓ Home End.
 */
function MenuButton(props: { deps: HudDeps; getCaps: () => unknown }) {
  const [open, setOpen] = createSignal(false);
  const [pos, setPos] = createSignal({ top: 0, left: 0 });
  let btn: HTMLButtonElement | undefined;
  let pop: HTMLDivElement | undefined;

  const act = (name: string, ...args: unknown[]) => {
    const fn = props.deps.getAction(name);
    if (typeof fn === 'function') (fn as (...a: unknown[]) => void)(...args);
  };
  const mod = props.deps.isMac ? '⌘' : 'Ctrl+';

  const entries = (): MenuEntry[] => [
    { id: 'new', iconName: 'plus', label: t('새 세션'), run: (el) => act('session.add-menu', el) },
    { id: 'quick', iconName: 'search', label: t('빠른 열기'), keys: `${mod}K`, run: () => act('quickopen.open') },
    {
      id: 'copy-url', iconName: 'link', label: t('접속 주소 복사'), sepBefore: true,
      run: () => {
        const url = accessUrl(props.getCaps());
        void (props.deps.copyText?.(url) ?? Promise.resolve(false)).then((ok) => {
          props.deps.toast?.(ok ? t('주소를 복사했습니다') : t('복사하지 못했습니다'), ok ? 'success' : 'error');
        });
      },
    },
    { id: 'settings', iconName: 'settings', label: t('설정'), keys: `${mod},`, sepBefore: true, run: () => act('settings.show') },
    { id: 'menubar-settings', iconName: 'settings', label: t('메뉴바 설정'), run: () => act('settings.section', 'hud') },
  ];

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) btn?.focus();
  };
  const toggle = () => {
    if (open()) { close(); return; }
    const r = btn!.getBoundingClientRect();
    setPos({ top: Math.round(r.bottom + 4), left: Math.max(8, Math.round(r.left)) });
    setOpen(true);
    // 첫 항목에 포커스 — Portal이 그린 다음 프레임에.
    requestAnimationFrame(() => pop?.querySelector<HTMLElement>('.vt-mb-item:not([disabled])')?.focus());
  };

  createEffect(() => {
    if (!open()) return;
    const onDown = (e: MouseEvent) => {
      const el = e.target as Node;
      if (pop?.contains(el) || btn?.contains(el)) return;
      close();
    };
    const onAway = () => close();
    document.addEventListener('mousedown', onDown, true);
    window.addEventListener('resize', onAway);
    window.addEventListener('blur', onAway);
    onCleanup(() => {
      document.removeEventListener('mousedown', onDown, true);
      window.removeEventListener('resize', onAway);
      window.removeEventListener('blur', onAway);
    });
  });

  const onPopKey = (e: KeyboardEvent) => {
    const items = [...(pop?.querySelectorAll<HTMLElement>('.vt-mb-item:not([disabled])') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); items[(n + items.length) % items.length]?.focus(); };
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown') go(i + 1);
    else if (e.key === 'ArrowUp') go(i < 0 ? items.length - 1 : i - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(items.length - 1);
    else if (e.key === 'Tab') close();
  };

  const runItem = (it: MenuEntry) => {
    close();
    // 닫는 것과 실행을 분리한다 — 실행이 앵커(버튼)를 기준으로 팝업을 열 수 있다(새 세션).
    it.run(btn!);
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        class="vt-mb-menu"
        aria-haspopup="menu"
        aria-expanded={open()}
        aria-label={t('FarShell 메뉴')}
        data-tip={t('FarShell 메뉴')}
        data-tip-side="bottom"
        onClick={toggle}
        onKeyDown={(e: KeyboardEvent) => {
          if (e.key === 'ArrowDown' && !open()) { e.preventDefault(); toggle(); }
        }}
      >
        <span innerHTML={icon('square-terminal', 14, 2)} style="display:contents" />
        <span>FarShell</span>
        <span innerHTML={icon('chevron-down', 12, 2)} style="display:contents" />
      </button>
      <Show when={open()}>
        <Portal>
          <div
            ref={pop}
            class="vt-mb-pop"
            role="menu"
            aria-label={t('FarShell 메뉴')}
            style={{ top: `${pos().top}px`, left: `${pos().left}px` }}
            onKeyDown={onPopKey}
          >
            <For each={entries()}>{(it) => (
              <>
                <Show when={it.sepBefore}><div class="vt-mb-sep" role="separator" /></Show>
                <button type="button" role="menuitem" class="vt-mb-item" disabled={it.disabled} onClick={() => runItem(it)}>
                  <Show when={it.iconName}><span innerHTML={icon(it.iconName!, 14, 2)} style="display:contents" /></Show>
                  <span class="vt-mb-label">{it.label}</span>
                  <Show when={it.keys}><span class="vt-mb-keys">{it.keys}</span></Show>
                </button>
              </>
            )}</For>
          </div>
        </Portal>
      </Show>
    </>
  );
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
  const refresh = async () => {
    snapshot = await collect(props.deps);
    push = null;   // 방금 조회한 값이 최신이다
    publish();
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
    const data = path ? await safeFetch<{ clients?: unknown[] }>(props.deps, path) : null;
    snapshot = { ...snapshot, screens: data?.clients?.length ?? null };
    publish();
  };

  const refreshStats = async () => {
    if (!snapshot) return;
    const stats = await safeFetch<HudInput['stats']>(props.deps, '/api/system/stats');
    snapshot = { ...snapshot, stats };
    publish();
  };

  refresh();
  const statsTimer = setInterval(() => { if (!document.hidden) refreshStats(); }, STATS_POLL_MS);
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  const screenTimer = setInterval(() => { if (!document.hidden) refreshScreens(); }, SCREEN_POLL_MS);
  onCleanup(() => { clearInterval(timer); clearInterval(screenTimer); clearInterval(statsTimer); });

  const publishedCaps = () => snapshot?.caps ?? null;
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
      <MenuButton deps={props.deps} getCaps={() => (publishedCaps())} />
      <For each={side('left')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
      <div class="vt-hud-spacer" />
      <For each={side('right')}>{(c) => <Chip chip={c} deps={props.deps} />}</For>
    </>
  );
}


export function mountHud(root: HTMLElement, deps: HudDeps) {
  return render(() => <Hud deps={deps} root={root} />, root);
}
