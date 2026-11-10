// 리디자인 2단계 — 제목 막대 36(`#vt-titlebar`, regular 이상). 시안: docs/local의 redesign-2026-11 specimen
// (desktop.html D01/D04, overlays.html O07, spec.html 「제목 막대」).
//
//   [신호등] FarShell ▾ · 호스트 ▾ · 저장소 › 브랜치 › 세션 ── [ 검색하거나 명령 실행  ⌘K ] ── [집중 모드] [키바] [캡션]
//
// - **FarShell 메뉴**(`MenuButton`)는 옛 메뉴바(Hud.tsx)에서 그대로 옮겼다. 승인 대기 배지(share-lazy.js의
//   vtAuthzBadge)는 `.vt-mb-menu` 클래스로 이 버튼을 찾으므로 클래스 이름을 바꾸지 않는다.
// - **호스트 · 경로**는 옛 워크스페이스 칩(HeaderExtras.tsx, `#vt-workspace-chip`)을 대신한다. 호스트는 활성 칸이
//   보여 주는 맥을 따라간다(term/host-view.js가 판정, window.vtActiveViewHost — 2026-09-28 사용자 지적). 맥이 둘
//   이상이면 눌러서 화면 전체를 그 맥으로 전환한다(core/host-route.js, 레일 머리의 스위처와 같은 동작).
//   경로는 활성 칸 세션의 저장소 › 브랜치 › 세션 이름이다(layout/branch-chips.js — 칸 머리 브랜치 칩과 같은 출처).
// - **팔레트 입력**은 진짜 입력란이 아니라 팔레트를 여는 버튼이다(누르면 기존 팔레트, `quickopen.open`). 가운데에
//   두려고 막대를 세 칸 격자(1fr auto 1fr)로 나눈다 — 양옆이 좁아지면 양옆이 먼저 줄고 입력은 가운데에 남는다.
// - **오른쪽 도구**: 집중 모드 버튼(7단계, `#vt-tb-focus-slot` 안), 그리고 `#vt-mb-tools`(키바 버튼 ·
//   윈도 캡션, layout/menubar-slots.js가 #topbar에서 옮겨 온다). 왼쪽 끝 `#vt-mb-lead`(macOS 신호등)도 같다.
//   이 둘은 index.html의 정적 노드라 Solid가 소유하지 않는다 — 마운트 뒤 이 막대의 빈 자리(자식이 없는 요소)로
//   **옮겨** 둔다. 그 자리는 JSX 자식이 없어서 Solid가 그 안을 다시 그릴 일이 없다.
//
// ⚠ 지연 청크(shell) — core/*를 직접 import하지 않는다(Hud.tsx 머리말과 같은 이유: Rollup이 청크 안에 복제해
// 앱의 액션 레지스트리와 갈라진다). 필요한 것은 main.js가 인자로 넘기거나 window 브리지로 읽는다.
import { createSignal, createEffect, onCleanup, onMount, For, Show } from 'solid-js';
import { render, Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { shellIcon } from '../ui/icons-shell.js';
import { Menu, type MenuItem } from './RailRow.js';
import { buildHostMenu, hostDetail, LOCAL_HOST, type HostEntry } from './host-data.js';

export interface TitleCrumb {
  repo?: string | null;
  branch?: string | null;
  session?: string | null;
}

export interface TitleBarDeps {
  vtFetch: (path: string) => Promise<unknown>;
  getAction: (name: string) => unknown;
  /** 맥 키보드인가 — 단축키 표기(⌘/Ctrl)에 쓴다. */
  isMac?: boolean;
  /** 클립보드 복사(term/clipboard.js). 성공 여부를 돌려준다. */
  copyText?: (text: string) => Promise<boolean>;
  /** 토스트(ui/toast.js). */
  toast?: (msg: string, type?: string) => void;
  /** 활성 칸의 저장소 › 브랜치 › 세션. */
  crumb?: () => TitleCrumb;
  /** 경로가 바뀔 수 있을 때 부른다(세션 전환·칸 이동·브랜치 지도 갱신). 해제 함수를 돌려준다. */
  subscribeCrumb?: (fn: () => void) => () => void;
  /** 키맵 id → 표시용 조합(사용자가 바꿨으면 바꾼 조합). 못 쓰는 조합이면 ''. */
  keyHint?: (id: string) => string;
  /** 집중 모드가 켜져 있는가(layout/focus.js). */
  focusActive?: () => boolean;
  /** 집중 모드를 켜고 끌 때 window에 오는 이벤트 이름. */
  focusEvent?: string;
}

interface Caps { hostname?: string; tunnel?: unknown }

async function safeFetch<T>(deps: TitleBarDeps, path: string): Promise<T | null> {
  try {
    return (await deps.vtFetch(path)) as T;
  } catch (_) {
    return null;
  }
}

const svg = (name: string, size: number, sw = 2) => <span class="vt-tb-ico" innerHTML={shellIcon(name, size, sw)} />;

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
export function accessUrl(caps: unknown): string {
  const tun = (caps as { tunnel?: { running?: boolean; url?: unknown } } | null | undefined)?.tunnel;
  if (tun && tun.running !== false && typeof tun.url === 'string' && /^https:\/\//.test(tun.url)) return tun.url;
  return location.origin;
}

/**
 * 제목 막대 왼쪽 첫 칸의 FarShell 메뉴. 새 세션 · 빠른 열기 · 집중 모드 · 접속 주소 복사 · 설정 · 상태 막대 설정 · 캐시 지우기 · 로그아웃.
 * 드롭다운은 Portal로 body에 그린다 — 막대가 overflow를 자르고 OS 흉내 스킨은 backdrop-filter를 쓰는데, 그게
 * fixed의 기준 상자를 바꿔서 안에 두면 잘린다. 닫기: 바깥 클릭 · Esc · 항목 실행 · 창 크기 변경/포커스 잃음.
 * 키보드: ↑↓ Home End.
 */
function MenuButton(props: { deps: TitleBarDeps; getCaps: () => unknown }) {
  const [open, setOpen] = createSignal(false);
  const [pos, setPos] = createSignal({ top: 0, left: 0 });
  let btn: HTMLButtonElement | undefined;
  let pop: HTMLDivElement | undefined;

  // 승인 대기 배지(share-lazy.js의 vtAuthzBadge)는 이 버튼에 붙는다. 이 버튼은 지연 청크라 배지 호출이
  // 먼저 왔을 수 있으니, 그려진 직후 마지막 건수를 한 번 다시 적용한다.
  onMount(() => {
    const w = window as unknown as { vtAuthzBadge?: (n: number) => void; vtAuthzPending?: number };
    if ((w.vtAuthzPending ?? 0) > 0) w.vtAuthzBadge?.(w.vtAuthzPending as number);
  });

  const act = (name: string, ...args: unknown[]) => {
    const fn = props.deps.getAction(name);
    if (typeof fn === 'function') (fn as (...a: unknown[]) => void)(...args);
  };
  const mod = props.deps.isMac ? '⌘' : 'Ctrl+';

  const entries = (): MenuEntry[] => [
    { id: 'new', iconName: 'plus', label: t('새 세션'), run: (el) => act('session.add-menu', el) },
    { id: 'quick', iconName: 'search', label: t('빠른 열기'), keys: `${mod}K`, run: () => act('quickopen.open') },
    // 리디자인 7단계(시안 O07) — 집중 모드. 표기는 키맵의 지금 조합(사용자가 바꿨으면 그 조합)이다.
    { id: 'focus', iconName: 'maximize', label: t('집중 모드'), keys: props.deps.keyHint?.('focusMode') || undefined, run: () => act('focus.toggle') },
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
    { id: 'statusbar-settings', iconName: 'panel-top', label: t('상태 막대 설정'), run: () => act('settings.section', 'hud') },
    // 캐시 지우기는 서비스 워커·Cache Storage만 지운다(설정은 그대로). 로그아웃은 웹 로그인 세션만 끝낸다(기기 등록은 유지).
    // 둘 다 core/app-session-lazy.js가 등록한 액션이고, 본체는 같은 shell 청크의 app-session.ts다(명령 팔레트도 같은 액션).
    { id: 'clear-cache', iconName: 'refresh-cw', label: t('캐시 지우기'), sepBefore: true, run: () => act('app.clear-cache') },
    { id: 'logout', iconName: 'log-out', label: t('로그아웃'), run: () => act('session.logout') },
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
        {svg('square-terminal', 16)}
        <span class="vt-mb-name">FarShell</span>
        {svg('chevron-down', 12)}
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
                  <Show when={it.iconName}><span innerHTML={shellIcon(it.iconName!, 14, 2)} style="display:contents" /></Show>
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

/**
 * 호스트 — 활성 칸이 보여 주는 맥. 2026-09-28(사용자 지적): 칩·스위처·터미널 칸 셋이 서로 다른 호스트를 말하지
 * 않도록 활성 칸을 따라간다. 레일 스위처로 다른 맥으로 전환해 있으면 앱 전체가 그 맥의 API로 가므로
 * (core/host-route.js) `/api/capabilities`의 hostname이 곧 그 맥이다.
 * 등록된 맥이 하나뿐이면 누를 것이 없으므로 이름만 보인다(고를 게 없는 드롭다운은 잡음 — 레일 스위처와 같은 판단).
 */
function HostSwitcher(props: { deps: TitleBarDeps; hostname: () => string }) {
  const [viewHostId, setViewHostId] = createSignal(LOCAL_HOST);
  const [viewLabel, setViewLabel] = createSignal('');
  const [hosts, setHosts] = createSignal<HostEntry[]>([]);
  const [menu, setMenu] = createSignal<{ x: number; y: number } | null>(null);
  const relayed = String((window as any).vtRelayHost?.() || LOCAL_HOST);

  let seq = 0;
  const syncView = async () => {
    const w = window as any;
    const id = relayed !== LOCAL_HOST ? relayed : String(w.vtActiveViewHost?.() || LOCAL_HOST);
    if (id === viewHostId() && (id === LOCAL_HOST || viewLabel())) return;
    const mine = ++seq;
    setViewHostId(id);
    if (id === LOCAL_HOST) { setViewLabel(''); return; }
    setViewLabel(w.vtHostLabelSync?.(id) || id);
    const label = await w.vtHostLabel?.(id);
    if (mine === seq && label) setViewLabel(label);   // 그 사이 다른 칸으로 옮겼으면 버린다
  };
  const offStore = (window as any).storeSubscribe?.(() => { void syncView(); });
  const offLayout = (window as any).onLayoutChange?.(() => { void syncView(); });
  onCleanup(() => { offStore?.(); offLayout?.(); });
  void syncView();

  const loadHosts = async () => {
    const data = await safeFetch<{ hosts?: HostEntry[] }>(props.deps, '/api/hosts');
    if (data?.hosts) setHosts(data.hosts);
  };
  void loadHosts();
  const onHostsChanged = () => { void loadHosts(); };
  window.addEventListener('vt:hosts-changed', onHostsChanged);
  onCleanup(() => window.removeEventListener('vt:hosts-changed', onHostsChanged));

  const remote = () => viewHostId() !== LOCAL_HOST;
  const label = () => (remote() ? viewLabel() : props.hostname());
  const switchable = () => hosts().length > 1;
  const active = () => hosts().find((h) => h.id === (relayed !== LOCAL_HOST ? relayed : LOCAL_HOST)) || null;

  const openMenu = (e: MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    void loadHosts();   // 열 때 한 번 더 — 목록이 오래됐을 수 있다.
    setMenu({ x: r.left, y: r.bottom + 4 });
  };
  // 고르면 화면 **전체**가 그 맥으로 간다(같은 주소에서 새로고침, core/host-route.js 머리말).
  const items = (): MenuItem[] =>
    buildHostMenu(hosts(), relayed).map((h) => ({
      label: h.label,
      detail: h.detail,
      dim: !h.online,
      checked: h.active,
      run: () => { if (h.id !== relayed) (window as any).vtSwitchHost?.(h.id); },
    }));

  return (
    <div id="vt-workspace-chip" class="vt-tb-host-wrap">
      <Show when={label()}>
        <Show
          when={switchable()}
          fallback={
            <span class="vt-tb-host" classList={{ remote: remote() }} data-tip={t('이 화면이 보고 있는 맥')} data-tip-side="bottom">
              {svg(remote() ? 'server' : 'laptop', 14)}
              <span class="vt-workspace-host" classList={{ remote: remote() }}>{label()}</span>
            </span>
          }
        >
          <button
            type="button"
            class="vt-tb-host"
            classList={{ remote: remote(), open: !!menu() }}
            aria-haspopup="menu"
            aria-expanded={!!menu()}
            aria-label={t('호스트 선택: {name}', { name: label() })}
            data-tip={t('호스트 선택')}
            data-tip-sub={active() ? hostDetail(active()!) : undefined}
            data-tip-side="bottom"
            onClick={openMenu}
          >
            {svg(remote() ? 'server' : 'laptop', 14)}
            <span class="vt-workspace-host" classList={{ remote: remote() }}>{label()}</span>
            {svg('chevron-down', 12)}
          </button>
        </Show>
      </Show>
      <Show when={menu()}>
        {(m) => (
          <Portal>
            <Menu x={m().x} y={m().y} onClose={() => setMenu(null)} items={items()} />
          </Portal>
        )}
      </Show>
    </div>
  );
}

/**
 * 집중 모드 버튼(리디자인 7단계, 시안 D01 오른쪽 · D05-C). 평소엔 `maximize`(켜기), 집중 모드에서 위 가장자리로
 * 제목 막대를 띄웠을 때는 `minimize`(끝내기) + 눌림 표시. 상태는 core가 쏘는 이벤트로만 따라간다.
 */
function FocusButton(props: { deps: TitleBarDeps }) {
  const [on, setOn] = createSignal(!!props.deps.focusActive?.());
  const evt = props.deps.focusEvent || 'vt:focus-change';
  const onChange = (e: Event) => setOn(!!(e as CustomEvent).detail?.on);
  window.addEventListener(evt, onChange);
  onCleanup(() => window.removeEventListener(evt, onChange));
  const label = () => (on() ? t('집중 모드 끝내기') : t('집중 모드'));
  const keys = () => props.deps.keyHint?.('focusMode') || '';
  return (
    <button
      type="button"
      id="vt-tb-focus"
      class="vt-tb-focus"
      classList={{ on: on() }}
      aria-pressed={on()}
      aria-label={label()}
      data-tip={label()}
      data-tip-sub={keys() || undefined}
      data-tip-side="bottom"
      onClick={() => {
        const fn = props.deps.getAction('focus.toggle');
        if (typeof fn === 'function') (fn as () => void)();
      }}
    >
      {svg(on() ? 'minimize' : 'maximize', 16)}
    </button>
  );
}

/** 저장소 › 브랜치 › 세션 — 활성 칸의 세션. 모르는 조각은 빼고, 셋 다 없으면 아무것도 그리지 않는다. */
function Breadcrumb(props: { deps: TitleBarDeps }) {
  const read = (): TitleCrumb => props.deps.crumb?.() || {};
  const [crumb, setCrumb] = createSignal<TitleCrumb>(read());
  // 같은 값이면 신호를 건드리지 않는다 — 세션 스토어는 자주 알리는데 경로는 드물게 바뀐다.
  const same = (a: TitleCrumb, b: TitleCrumb) => a.repo === b.repo && a.branch === b.branch && a.session === b.session;
  let raf = 0;
  const sync = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const next = read();
      if (!same(next, crumb())) setCrumb(next);
    });
  };
  const off = props.deps.subscribeCrumb?.(sync);
  onCleanup(() => { off?.(); if (raf) cancelAnimationFrame(raf); });

  const parts = () => {
    const c = crumb();
    return [
      c.repo ? { kind: 'repo', text: c.repo } : null,
      c.branch ? { kind: 'branch', text: c.branch } : null,
      c.session ? { kind: 'session', text: c.session } : null,
    ].filter(Boolean) as Array<{ kind: string; text: string }>;
  };
  const whole = () => parts().map((p) => p.text).join(' › ');

  return (
    <Show when={parts().length}>
      <nav class="vt-tb-crumb" aria-label={t('현재 위치')} data-tip={whole()} data-tip-side="bottom">
        <Show when={crumb().branch}>{svg('git-branch', 12)}</Show>
        <For each={parts()}>{(p, i) => (
          <>
            <Show when={i() > 0}><span class="vt-tb-crumb-sep" aria-hidden="true">{svg('chevron-right', 12)}</span></Show>
            <span class={`vt-tb-crumb-part ${p.kind}`} classList={{ cur: i() === parts().length - 1 }}>{p.text}</span>
          </>
        )}</For>
      </nav>
    </Show>
  );
}

function TitleBar(props: { deps: TitleBarDeps; root: HTMLElement }) {
  const [caps, setCaps] = createSignal<Caps | null>(null);
  const loadCaps = async () => {
    const c = await safeFetch<Caps>(props.deps, '/api/capabilities');
    if (c) setCaps(c);
  };
  void loadCaps();
  // 접속 주소(터널 URL)는 바뀔 수 있다 — 메뉴를 열 때마다 다시 읽어 두면 복사가 늘 최신 값을 쓴다.
  // (복사 자체는 클릭과 같은 틱에서 해야 하므로 — 사용자 활성화가 필요한 브라우저가 있다 — 그때 기다리지 않는다.)
  const onMenuOpen = (e: Event) => {
    if ((e.target as HTMLElement | null)?.closest?.('.vt-mb-menu')) void loadCaps();
  };
  props.root.addEventListener('pointerdown', onMenuOpen);
  onCleanup(() => props.root.removeEventListener('pointerdown', onMenuOpen));

  const mod = props.deps.isMac ? '⌘' : 'Ctrl+';
  const openPalette = () => {
    const fn = props.deps.getAction('quickopen.open');
    if (typeof fn === 'function') (fn as () => void)();
  };

  let leadHome: HTMLDivElement | undefined;
  let toolsHome: HTMLDivElement | undefined;
  onMount(() => {
    // 정적 자리(#vt-mb-lead · #vt-mb-tools)를 이 막대의 빈 자리로 옮긴다 — 안의 신호등·키바 버튼·캡션은 그대로
    // 따라오고(id·리스너 유지), menubar-slots.js가 나중에 넣고 빼는 것도 같은 노드라 그대로 맞는다.
    const lead = document.getElementById('vt-mb-lead');
    const tools = document.getElementById('vt-mb-tools');
    if (lead && leadHome) leadHome.append(lead);
    if (tools && toolsHome) toolsHome.append(tools);
  });

  return (
    <>
      <div class="vt-tb-left">
        <div class="vt-tb-home" ref={leadHome} />
        <MenuButton deps={props.deps} getCaps={() => caps()} />
        <HostSwitcher deps={props.deps} hostname={() => caps()?.hostname || ''} />
        <Breadcrumb deps={props.deps} />
      </div>
      <button
        type="button"
        id="vt-tb-palette"
        class="vt-tb-palette"
        aria-label={t('빠른 열기')}
        aria-keyshortcuts={props.deps.isMac ? 'Meta+K' : 'Control+K'}
        onClick={openPalette}
      >
        {svg('search', 14)}
        <span class="vt-tb-palette-ph">{t('검색하거나 명령 실행')}</span>
        <kbd class="vt-tb-kbd">{`${mod}K`}</kbd>
      </button>
      <div class="vt-tb-right">
        {/* 집중 모드 버튼(리디자인 7단계). 자리 요소는 2단계 계약 그대로 두고 그 안에 그린다. */}
        <div id="vt-tb-focus-slot" class="vt-tb-home"><FocusButton deps={props.deps} /></div>
        <div class="vt-tb-home" ref={toolsHome} />
      </div>
    </>
  );
}

export function mountTitleBar(root: HTMLElement, deps: TitleBarDeps) {
  return render(() => <TitleBar deps={deps} root={root} />, root);
}
