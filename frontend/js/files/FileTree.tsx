// 파일 트리 화면 — 사이드바 「파일」 활동(`vt-dir` 패널)의 본문(파일 개편 단계 5, 시안 F01 · F01-B · M-F1 · M-F1-B).
//
// - 행은 평탄화 목록(tree-model `flatten`)의 보이는 구간 + 위아래 10행만 DOM에 있다(가상 스크롤).
// - 행 이벤트는 스크롤 상자 하나가 받는다(위임). 행 DOM에는 리스너가 없다.
// - 같은 키·같은 내용의 행은 같은 객체라(`reuseRows`) `<For>`가 그 DOM을 그대로 둔다.
// - 키보드는 WAI-ARIA 트리 규칙(tree-model `treeKey`). 초점은 `aria-activedescendant`로 — 가상 행이라 초점 행이
//   DOM에서 빠질 수 있어, 스크롤 상자 자체가 초점을 갖고 초점 행을 화면 안으로 끌어온다.
// - 폰(<720px)은 행 44px, 길게 누르면 행 메뉴가 아래 시트로 열린다.
//
// 색은 토큰만 쓴다(계약 9, styles/screens/96-files-tree.css). 바뀜 점은 `--color-st-done`(사용자 선택 3 「가」).
import { For, Show, createMemo, createSignal, onCleanup, onMount, batch } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import {
  type Row, FlattenCache, compileFilter, flatten, focusable, fmtSize, parentOf, reuseRows, rowHeight, scrollToReveal,
  treeKey, typeahead, windowRange,
} from './tree-model.js';
import type { TreeStore } from './tree-store.js';

export interface FileTreeDeps {
  store: TreeStore;
  /** 호스트 표시 이름(로컬이면 이 맥 이름). */
  hostLabel: () => string;
  remote: boolean;
  openFile: (path: string) => void;
  insertPath: (path: string) => boolean;
  copyPath: (path: string) => Promise<boolean>;
  sharePath: (path: string) => void;
  /** 내려받기(파일 개편 단계 7, `/api/fs/download`). 원격 호스트면 부르지 않고 이유를 알린다(결정 D7). */
  downloadPath?: (path: string, size: number) => void;
  /** 지금 터미널 세션의 cwd. 모르면 null(이유는 deps가 토스트로 말한다). */
  terminalCwd: () => Promise<string | null>;
  /** 지금 터미널 세션 이름(행 메뉴 「경로 삽입」 옆에 적는다). */
  sessionName: () => string;
  recent: () => string[];
  clearRecent: () => void;
  toast: (msg: string, kind?: string) => void;
  dense: () => boolean;
  mobile: () => boolean;
  /** 루트가 바뀌었다(소스컨트롤 탭의 폴백 위치 등). */
  onRoot?: (root: string) => void;
}

interface MenuState {
  row: Row;
  x: number;
  y: number;
  sheet: boolean;
}

interface MenuItem {
  id: string;
  label: string;
  icon: string;
  hint?: string;
  run: () => void;
}

// 키 → 행 위치. 클릭·초점 때만 필요하므로 처음 물을 때 만든다(행이 수천 개면 매 갱신마다 만들기엔 아깝다).
function lazyIndex(rows: Row[]): { get: (key: string) => number | undefined } {
  let map: Map<string, number> | null = null;
  return {
    get(key: string) {
      if (!map) { map = new Map(); rows.forEach((r, i) => map!.set(r.key, i)); }
      return map.get(key);
    },
  };
}

// 앞을 말줄임하는 경로(direction:rtl)의 문장부호가 반대쪽으로 튀지 않게 감싸는 표시(왼쪽→오른쪽 표시 문자).
const LRM = '\u200e';

let _uid = 0;
const LONG_PRESS_MS = 500;
const TYPEAHEAD_MS = 700;

function Chevron(props: { open: boolean }) {
  return <span class="vt-ft-cv" classList={{ open: props.open }} innerHTML={icon('chevron-right', 12)} />;
}

function NameText(props: { row: Row }) {
  return (
    <Show when={props.row.match && props.row.match[1] > props.row.match[0]} fallback={<span class="vt-ft-nm">{props.row.name}</span>}>
      <span class="vt-ft-nm">
        {props.row.name.slice(0, props.row.match![0])}
        <mark>{props.row.name.slice(props.row.match![0], props.row.match![1])}</mark>
        {props.row.name.slice(props.row.match![1])}
      </span>
    </Show>
  );
}

export function FileTree(props: { deps: FileTreeDeps }) {
  const deps = props.deps;
  const store = deps.store;
  const uid = `vt-ft-${++_uid}`;
  const [version, setVersion] = createSignal(0);
  const [scrollTop, setScrollTop] = createSignal(0);
  const [viewport, setViewport] = createSignal(600);
  const [focusKey, setFocusKey] = createSignal<string | null>(null);
  const [selectedKey, setSelectedKey] = createSignal<string | null>(null);
  const [treeFocused, setTreeFocused] = createSignal(false);
  const [filterText, setFilterText] = createSignal('');
  const [menu, setMenu] = createSignal<MenuState | null>(null);
  const [pathDraft, setPathDraft] = createSignal(store.root || '');
  const [recentOpen, setRecentOpen] = createSignal(false);
  let scrollRef: HTMLDivElement | undefined;
  let filterRef: HTMLInputElement | undefined;
  let pathRef: HTMLInputElement | undefined;

  const offStore = store.subscribe(() => {
    batch(() => {
      setVersion((v) => v + 1);
      if (document.activeElement !== pathRef) setPathDraft(store.root || '');
    });
  });
  onCleanup(offStore);

  // store는 시그널이 아니다 — 화면에 쓰는 루트는 version()을 거쳐 읽는다.
  const root = createMemo(() => { version(); return store.root || ''; });
  const rowH = createMemo(() => rowHeight(deps.dense(), deps.mobile()));
  const matcher = createMemo(() => compileFilter(filterText()));

  // 평탄화 — 필터가 없으면 폴더별 조각 캐시가 바뀌지 않은 폴더의 행 객체를 그대로 돌려준다. 필터 중에는 매번 새로
  // 펴고 경로 키 비교(reuseRows)로 같은 행 객체를 되찾는다. 어느 쪽이든 `<For>`는 바뀐 행만 다시 그린다.
  const cache = new FlattenCache();
  let prevByKey = new Map<string, Row>();
  const flat = createMemo(() => {
    version();
    const root = store.root;
    const f = matcher();
    if (!root) return { rows: [] as Row[], matches: -1, index: lazyIndex([]) };
    const out = flatten({
      host: store.host, root, dirs: store.dirs, expanded: store.expanded,
      changed: store.changed, changedDirs: store.changedDirs, filter: f, cache, marks: store.marks,
      expandVer: store.expandVer,
    });
    if (!f) return { rows: out.rows, matches: out.matches, index: lazyIndex(out.rows) };
    const r = reuseRows(prevByKey, out.rows);
    prevByKey = r.byKey;
    return { rows: r.rows, matches: out.matches, index: lazyIndex(r.rows) };
  });
  const rows = () => flat().rows;
  const win = createMemo(() => windowRange(scrollTop(), viewport(), rowH(), rows().length));
  const visible = createMemo(() => {
    const w = win();
    return rows().slice(w.start, w.end);
  });
  const focusIndex = createMemo(() => {
    const k = focusKey();
    return k == null ? -1 : (flat().index.get(k) ?? -1);
  });

  // --- 스크롤·크기 ------------------------------------------------------------------------
  let rafPending = false;
  const onScroll = () => {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      if (scrollRef) setScrollTop(scrollRef.scrollTop);
    });
  };
  onMount(() => {
    if (!scrollRef) return;
    setViewport(scrollRef.clientHeight || 600);
    const RO = (window as any).ResizeObserver;
    if (RO) {
      const ro = new RO(() => { if (scrollRef) setViewport(scrollRef.clientHeight || 600); });
      ro.observe(scrollRef);
      onCleanup(() => ro.disconnect());
    }
  });

  const reveal = (index: number) => {
    if (!scrollRef || index < 0) return;
    const next = scrollToReveal(index, scrollRef.scrollTop, viewport(), rowH());
    if (next !== scrollRef.scrollTop) {
      scrollRef.scrollTop = next;
      setScrollTop(next);
    }
  };

  const focusRow = (index: number) => {
    const r = rows()[index];
    if (!r || !focusable(r)) return;
    setFocusKey(r.key);
    reveal(index);
  };

  // --- 동작 -------------------------------------------------------------------------------
  const openRoot = async (path: string): Promise<boolean> => {
    const v = path.trim();
    if (!v) return false;
    try {
      await store.open(v);
    } catch (e: any) {
      deps.toast(t('이동할 수 없습니다: {msg}', { msg: e?.message || String(e) }));
      setPathDraft(store.root || '');
      return false;
    }
    batch(() => {
      setFocusKey(null);
      setSelectedKey(null);
      setPathDraft(v);
    });
    if (scrollRef) { scrollRef.scrollTop = 0; setScrollTop(0); }
    deps.onRoot?.(v);
    return true;
  };

  const goUp = () => {
    const root = store.root;
    if (!root || root === '/') return;
    void openRoot(parentOf(root));
  };

  const goTerminal = async () => {
    const cwd = await deps.terminalCwd();
    if (cwd) await openRoot(cwd);
  };

  const openFile = (r: Row) => {
    setSelectedKey(r.key);
    store.markSeen(r.path);
    deps.openFile(r.path);
  };

  const activate = (r: Row) => {
    setFocusKey(r.kind === 'dir' || r.kind === 'file' || r.kind === 'more' ? r.key : focusKey());
    if (r.kind === 'dir') { if (!matcher()) void store.toggle(r.path); }
    else if (r.kind === 'file') openFile(r);
    else if (r.kind === 'more') void store.loadMore(r.path);
    else if (r.kind === 'error') void store.refreshDir(r.path);
  };

  const refreshRow = (r: Row) => {
    void store.refreshDir(r.kind === 'dir' ? r.path : parentOf(r.path));
  };

  const menuItems = (r: Row): MenuItem[] => {
    const items: MenuItem[] = [];
    // 폰 시트에는 키 안내를 적지 않는다(키보드가 없다). 경로 삽입 옆에는 지금 터미널 세션 이름을 적는다(시안 M-F1-B).
    const keys = !deps.mobile();
    if (r.kind === 'file') items.push({ id: 'open', label: t('열기'), icon: 'file', hint: keys ? '↵' : undefined, run: () => openFile(r) });
    else items.push({ id: 'toggle', label: r.open ? t('접기') : t('펼치기'), icon: 'folder', hint: keys ? (r.open ? '←' : '→') : undefined, run: () => { void store.toggle(r.path); } });
    items.push({ id: 'insert', label: t('터미널에 경로 삽입'), icon: 'corner-down-left',
      hint: keys ? '⌥↵' : (deps.sessionName() || undefined), run: () => { deps.insertPath(r.path); } });
    items.push({ id: 'copy', label: t('경로 복사'), icon: 'clipboard-copy', run: () => {
      void deps.copyPath(r.path).then((ok) => { if (ok) deps.toast(t('경로를 복사했습니다')); });
    } });
    // 다운로드는 이 컴퓨터의 파일만(결정 D7) — 원격에서는 숨기지 않고 이유를 붙인다(시안 F01-C).
    if (r.kind === 'file' && deps.downloadPath) items.push({ id: 'download', label: t('다운로드'), icon: 'download',
      hint: deps.remote ? t('이 컴퓨터에서만') : fmtSize(r.size),
      run: () => {
        if (deps.remote) deps.toast(t('다운로드는 이 컴퓨터의 파일만 됩니다 — 원격 호스트 파일은 받을 수 없습니다'), 'warn');
        else deps.downloadPath!(r.path, r.size);
      } });
    if (r.kind === 'file') items.push({ id: 'share', label: t('공유 링크 만들기…'), icon: 'link', run: () => deps.sharePath(r.path) });
    items.push({ id: 'refresh', label: r.kind === 'dir' ? t('이 폴더 새로고침') : t('새로고침'), icon: 'refresh-cw', run: () => refreshRow(r) });
    return items;
  };

  const openMenu = (r: Row, x: number, y: number) => {
    if (r.kind !== 'dir' && r.kind !== 'file') return;
    setFocusKey(r.key);
    setMenu({ row: r, x, y, sheet: deps.mobile() });
  };

  const openMenuForIndex = (index: number) => {
    const r = rows()[index];
    if (!r || !scrollRef) return;
    reveal(index);
    const box = scrollRef.getBoundingClientRect();
    const top = box.top + index * rowH() - scrollRef.scrollTop + rowH();
    openMenu(r, box.left + 24, top);
  };

  // --- 키보드 -----------------------------------------------------------------------------
  let typeBuf = '';
  let typeTimer: ReturnType<typeof setTimeout> | null = null;
  const onKeyDown = (ev: KeyboardEvent) => {
    if (ev.isComposing) return;
    const list = rows();
    let idx = focusIndex();
    if (idx < 0 && list.length && ev.key !== '/') {
      // 초점 행이 아직 없으면 첫 이동 키가 첫 행(또는 선택 행)에 초점을 둔다.
      const sel = selectedKey();
      const si = sel != null ? (flat().index.get(sel) ?? -1) : -1;
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(ev.key) && si >= 0) { ev.preventDefault(); focusRow(si); return; }
    }
    const act = treeKey(list, idx, ev);
    if (act.type === 'none') {
      if (ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey && ev.key !== ' ') {
        typeBuf += ev.key;
        if (typeTimer) clearTimeout(typeTimer);
        typeTimer = setTimeout(() => { typeBuf = ''; }, TYPEAHEAD_MS);
        const hit = typeahead(list, idx, typeBuf);
        if (hit >= 0) { ev.preventDefault(); focusRow(hit); }
      }
      return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    switch (act.type) {
      case 'focus': focusRow(act.index); break;
      case 'expand': void store.expand(list[act.index].path); break;
      case 'collapse': store.collapse(list[act.index].path); break;
      case 'activate': activate(list[act.index]); break;
      case 'insert': deps.insertPath(list[act.index].path); break;
      case 'menu': openMenuForIndex(act.index); break;
      case 'filter': filterRef?.focus(); break;
    }
  };

  // --- 포인터(위임) -----------------------------------------------------------------------
  const rowFromEvent = (ev: Event): Row | null => {
    const el = (ev.target as HTMLElement | null)?.closest?.('[data-key]') as HTMLElement | null;
    if (!el || !scrollRef?.contains(el)) return null;
    const i = flat().index.get(el.dataset.key || '');
    return i == null ? null : rows()[i] || null;
  };

  let pressTimer: ReturnType<typeof setTimeout> | null = null;
  let pressAt: { x: number; y: number } | null = null;
  let suppressClick = false;
  const cancelPress = () => {
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = null;
    pressAt = null;
  };
  const onPointerDown = (ev: PointerEvent) => {
    if (ev.pointerType === 'mouse') return;
    const r = rowFromEvent(ev);
    if (!r || (r.kind !== 'dir' && r.kind !== 'file')) return;
    pressAt = { x: ev.clientX, y: ev.clientY };
    pressTimer = setTimeout(() => {
      pressTimer = null;
      suppressClick = true;
      openMenu(r, pressAt?.x ?? 0, pressAt?.y ?? 0);
    }, LONG_PRESS_MS);
  };
  const onPointerMove = (ev: PointerEvent) => {
    if (!pressAt) return;
    if (Math.abs(ev.clientX - pressAt.x) > 10 || Math.abs(ev.clientY - pressAt.y) > 10) cancelPress();
  };
  const onClick = (ev: MouseEvent) => {
    if (suppressClick) { suppressClick = false; ev.preventDefault(); return; }
    const r = rowFromEvent(ev);
    if (!r) return;
    const act = (ev.target as HTMLElement).closest?.('[data-act]') as HTMLElement | null;
    if (act) {
      ev.stopPropagation();
      const a = act.dataset.act;
      if (a === 'insert') deps.insertPath(r.path);
      else if (a === 'share') deps.sharePath(r.path);
      else if (a === 'menu') {
        const b = act.getBoundingClientRect();
        openMenu(r, b.left, b.bottom);
      }
      return;
    }
    activate(r);
  };
  const onContextMenu = (ev: MouseEvent) => {
    const r = rowFromEvent(ev);
    if (!r || (r.kind !== 'dir' && r.kind !== 'file')) return;
    ev.preventDefault();
    if (menu()) return;   // 길게 누름이 이미 열었다(안드로이드 Chrome은 길게 누름에도 contextmenu를 보낸다)
    cancelPress();
    openMenu(r, ev.clientX, ev.clientY);
  };
  onCleanup(() => { cancelPress(); if (typeTimer) clearTimeout(typeTimer); });

  const rowLabel = (r: Row): string => {
    if (r.kind === 'loading') return t('불러오는 중…');
    if (r.kind === 'empty') return t('빈 디렉토리');
    if (r.kind === 'error') return t('목록을 불러오지 못했습니다: {msg}', { msg: r.detail });
    if (r.kind === 'capped') return t('{n}개까지만 받았습니다 — 경로 입력으로 하위 폴더를 여세요', { n: r.loaded.toLocaleString() });
    if (r.kind === 'more') {
      return r.open ? t('다음 항목 받는 중…') : t('더 불러오기 · {n}개 더', { n: Math.min(1000, Math.max(0, r.total - r.loaded)).toLocaleString() });
    }
    return r.name;
  };

  const indent = (r: Row) => (deps.mobile() ? 12 + r.depth * 16 : 6 + r.depth * 12);

  return (
    <div class="vt-ft" classList={{ dense: deps.dense() && !deps.mobile(), touch: deps.mobile() }}
      style={{ '--ft-row': `${rowH()}px` }}>
      <div class="vt-ft-host" classList={{ remote: deps.remote }} title={deps.remote ? t('원격 호스트의 파일입니다') : ''}>
        <span class="vt-ft-host-ic" innerHTML={icon('laptop', 14)} />
        <span class="vt-ft-host-nm">{deps.hostLabel()}</span>
        <span class="vt-ft-host-p">{LRM + root() + LRM}</span>
      </div>
      <div class="vt-ft-tools">
        <div class="vt-ft-tool-row">
          <input ref={pathRef} class="vt-ft-inp vt-ft-path" type="text" spellcheck={false} autocapitalize="off" autocomplete="off"
            aria-label={t('트리 최상단 경로')} title={t('경로를 입력하고 Enter — 이 위치를 최상단으로 엽니다')}
            value={pathDraft()} onInput={(e) => setPathDraft(e.currentTarget.value)}
            onFocus={(e) => e.currentTarget.select()}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { setPathDraft(store.root || ''); e.currentTarget.blur(); return; }
              if (e.key !== 'Enter' || e.isComposing) return;
              e.preventDefault();
              const el = e.currentTarget;
              void openRoot(pathDraft()).then(() => el.blur());
            }} />
          <button type="button" class="vt-icon-btn vt-ft-btn" title={t('현재 터미널 위치로 열기')} aria-label={t('현재 터미널 위치로 열기')}
            innerHTML={icon('terminal', 14)} onClick={() => { void goTerminal(); }} />
          <button type="button" class="vt-icon-btn vt-ft-btn" title={t('위로')} aria-label={t('위로')}
            disabled={!root() || root() === '/'} innerHTML={icon('arrow-up', 14)} onClick={goUp} />
        </div>
        <div class="vt-ft-tool-row vt-ft-filter">
          <span class="vt-ft-filter-ic" innerHTML={icon('search', 14)} />
          <input ref={filterRef} class="vt-ft-inp" type="search" spellcheck={false} autocapitalize="off" autocomplete="off"
            aria-label={t('이름 필터')} placeholder={t('이름 필터 (예: rail, *.tsx)')}
            value={filterText()} onInput={(e) => { setFilterText(e.currentTarget.value); if (scrollRef) { scrollRef.scrollTop = 0; setScrollTop(0); } }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setFilterText(''); scrollRef?.focus(); }
              else if (e.key === 'ArrowDown' || e.key === 'Enter') { e.preventDefault(); scrollRef?.focus(); focusRow(Math.max(0, rows().findIndex(focusable))); }
            }} />
          <Show when={matcher()}>
            <span class="vt-ft-cnt">{t('{n}개', { n: Math.max(0, flat().matches) })}</span>
          </Show>
        </div>
      </div>
      <Show when={!matcher() && deps.recent().length > 0}>
        <div class="vt-ft-recent">
          <button type="button" class="vt-ft-sec" aria-expanded={recentOpen()} onClick={() => setRecentOpen(!recentOpen())}>
            <Chevron open={recentOpen()} />
            <span>{t('최근')}</span>
            <span class="vt-ft-sec-n">{deps.recent().length}</span>
          </button>
          <Show when={recentOpen()}>
            <div class="vt-ft-recent-list">
              <For each={deps.recent()}>{(p) => (
                <button type="button" class="vt-ft-recent-row" title={p} onClick={() => { store.markSeen(p); deps.openFile(p); }}>
                  <span class="vt-ft-nm">{p.split('/').pop() || p}</span>
                  <span class="vt-ft-recent-dir">{(() => {
                    const base = root().replace(/\/$/, '') + '/';
                    const rel = p.startsWith(base) ? p.slice(base.length) : p;
                    return rel.split('/').slice(0, -1).join('/') || '.';
                  })()}</span>
                </button>
              )}</For>
              <button type="button" class="vt-ft-recent-clear" onClick={() => { deps.clearRecent(); setRecentOpen(false); }}>{t('지우기')}</button>
            </div>
          </Show>
        </div>
      </Show>
      <div ref={scrollRef} class="vt-ft-scroll" role="tree" tabindex="0" aria-label={t('파일 트리')}
        aria-activedescendant={treeFocused() && focusIndex() >= 0 ? `${uid}-focus` : undefined}
        onScroll={onScroll} onKeyDown={onKeyDown} onClick={onClick} onContextMenu={onContextMenu}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={cancelPress} onPointerCancel={cancelPress}
        onFocus={() => {
          setTreeFocused(true);
          if (focusIndex() < 0) {
            const sel = selectedKey();
            const si = sel != null ? (flat().index.get(sel) ?? -1) : -1;
            const first = rows().findIndex(focusable);
            const target = si >= 0 ? si : first;
            if (target >= 0) setFocusKey(rows()[target].key);
          }
        }}
        onBlur={() => setTreeFocused(false)}>
        <div class="vt-ft-spacer" style={{ height: `${rows().length * rowH()}px` }}>
          <For each={visible()}>{(r, i) => {
            const idx = () => win().start + i();
            const isItem = r.kind === 'dir' || r.kind === 'file';
            const isFocus = () => focusKey() === r.key;
            return (
              <div class={`vt-ft-row k-${r.kind}`}
                classList={{ sel: selectedKey() === r.key, foc: isFocus() && treeFocused(), open: r.open }}
                data-key={r.key}
                id={isFocus() ? `${uid}-focus` : undefined}
                role={isItem || r.kind === 'more' ? 'treeitem' : 'none'}
                aria-level={r.depth + 1}
                aria-expanded={r.kind === 'dir' ? r.open : undefined}
                aria-selected={isItem ? selectedKey() === r.key : undefined}
                aria-setsize={isItem ? r.setSize : undefined}
                aria-posinset={isItem ? r.pos : undefined}
                aria-busy={r.kind === 'loading' || (r.kind === 'more' && r.open) ? true : undefined}
                style={{ transform: `translateY(${idx() * rowH()}px)`, 'padding-left': `${indent(r)}px` }}>
                <Show when={r.kind === 'dir'} fallback={<span class="vt-ft-cv" />}>
                  <Chevron open={r.open} />
                </Show>
                <Show when={isItem} fallback={
                  <span class="vt-ft-ic" classList={{ spin: r.kind === 'loading' || (r.kind === 'more' && r.open) }}
                    innerHTML={r.kind === 'more' ? icon('chevron-down', 14) : r.kind === 'error' ? icon('alert-triangle', 14) : ''} />
                }>
                  <span class="vt-ft-ic" innerHTML={icon(r.kind === 'dir' ? 'folder' : 'file', 14)} />
                </Show>
                <Show when={isItem} fallback={<span class="vt-ft-nm vt-ft-note">{rowLabel(r)}</span>}>
                  <NameText row={r} />
                </Show>
                <Show when={r.changed}>
                  <span class="vt-ft-chg" classList={{ ring: r.changed === 'ring' }}
                    title={r.changed === 'ring' ? t('안쪽에 바뀐 파일이 있습니다') : t('마지막으로 본 뒤 바뀜')} />
                </Show>
                <span class="vt-ft-r">
                  <Show when={r.kind === 'file'}><span class="vt-ft-sz">{fmtSize(r.size)}</span></Show>
                  <Show when={r.kind === 'more'}>
                    <span class="vt-ft-sz">{t('{total}개 중 {n}', { total: r.total.toLocaleString(), n: r.loaded.toLocaleString() })}</span>
                  </Show>
                  <Show when={isItem && !deps.mobile()}>
                    <span class="vt-ft-acts">
                      <button type="button" tabindex="-1" class="vt-icon-btn xs" data-act="insert" title={t('터미널에 경로 삽입')}
                        aria-label={t('터미널에 경로 삽입')} innerHTML={icon('corner-down-left', 12)} />
                      <Show when={r.kind === 'file'}>
                        <button type="button" tabindex="-1" class="vt-icon-btn xs" data-act="share" title={t('공유 링크 만들기')}
                          aria-label={t('공유 링크 만들기')} innerHTML={icon('link', 12)} />
                      </Show>
                      <button type="button" tabindex="-1" class="vt-icon-btn xs" data-act="menu" title={t('더 보기')}
                        aria-label={t('더 보기')} innerHTML={icon('more-horizontal', 12)} />
                    </span>
                  </Show>
                </span>
              </div>
            );
          }}</For>
        </div>
      </div>
      <Show when={matcher() && flat().matches === 0}>
        <div class="vt-ft-empty">
          <b>{t('이름이 맞는 항목이 없습니다')}</b>
          <span>{t('필터는 받아 둔 항목의 이름만 봅니다. 폴더를 펼치거나 빠른 열기(/)로 찾으세요.')}</span>
        </div>
      </Show>
      <Show when={!deps.mobile()}>
        <div class="vt-ft-keys" aria-hidden="true">
          <span><kbd>↑↓</kbd> {t('이동')}</span><span><kbd>←→</kbd> {t('접기·펼치기')}</span>
          <span><kbd>↵</kbd> {t('열기')}</span><span><kbd>⌥↵</kbd> {t('경로 삽입')}</span>
        </div>
      </Show>
      <Show when={menu()}>{(m) => (
        <RowMenu state={m()} items={menuItems(m().row)} onClose={() => { setMenu(null); scrollRef?.focus(); }} />
      )}</Show>
    </div>
  );
}

function RowMenu(props: { state: MenuState; items: MenuItem[]; onClose: () => void }) {
  let ref: HTMLDivElement | undefined;
  const [active, setActive] = createSignal(0);
  const [pos, setPos] = createSignal({ left: props.state.x, top: props.state.y });
  const run = (it: MenuItem) => { props.onClose(); it.run(); };
  onMount(() => {
    if (!ref) return;
    if (!props.state.sheet) {
      // 화면 밖으로 나가지 않게 끌어온다.
      const r = ref.getBoundingClientRect();
      const w = window.innerWidth, h = window.innerHeight;
      setPos({
        left: Math.max(8, Math.min(props.state.x, w - r.width - 8)),
        top: Math.max(8, Math.min(props.state.y, h - r.height - 8)),
      });
    }
    (ref.querySelector('[role="menuitem"]') as HTMLElement | null)?.focus();
  });
  const onKey = (e: KeyboardEvent) => {
    const n = props.items.length;
    if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); props.onClose(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      const i = e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : (active() + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      setActive(i);
      (ref?.querySelectorAll('[role="menuitem"]')[i] as HTMLElement | undefined)?.focus();
    }
  };
  const row = props.state.row;
  // 문서 끝(body)에 띄운다 — 패널 안에 두면 폰 하단 내비(z 650)와 패널의 쌓임 맥락에 가린다.
  return (
    <Portal>
      <div class="vt-ft-scrim" classList={{ sheet: props.state.sheet }} onClick={props.onClose}
        onContextMenu={(e) => { e.preventDefault(); props.onClose(); }} />
      <div ref={ref} class="vt-ft-menu" classList={{ sheet: props.state.sheet }} role="menu" aria-label={row.name}
        style={props.state.sheet ? undefined : { left: `${pos().left}px`, top: `${pos().top}px` }} onKeyDown={onKey}>
        <Show when={props.state.sheet}>
          <div class="vt-ft-menu-grab" aria-hidden="true" />
          <div class="vt-ft-menu-head">
            <span class="vt-ft-ic" innerHTML={icon(row.kind === 'dir' ? 'folder' : 'file', 20)} />
            <span class="vt-ft-menu-title">
              <b>{row.name}</b>
              <span>{parentOf(row.path)}{row.kind === 'file' ? ` · ${fmtSize(row.size)}` : ''}</span>
            </span>
          </div>
        </Show>
        <Show when={!props.state.sheet}>
          <div class="vt-ft-menu-path">{LRM + row.path + LRM}</div>
        </Show>
        <For each={props.items}>{(it, i) => (
          <button type="button" role="menuitem" class="vt-ft-mi" tabindex={i() === active() ? 0 : -1}
            onClick={() => run(it)} onFocus={() => setActive(i())}>
            <span class="vt-ft-ic" innerHTML={icon(it.icon, props.state.sheet ? 20 : 14)} />
            <span class="vt-ft-mi-l">{it.label}</span>
            <Show when={it.hint}><span class="vt-ft-mi-k">{it.hint}</span></Show>
          </button>
        )}</For>
        <Show when={props.state.sheet}>
          <button type="button" class="vt-btn vt-ft-menu-cancel" onClick={props.onClose}>{t('취소')}</button>
        </Show>
      </div>
    </Portal>
  );
}
