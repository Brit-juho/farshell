// 파일 트리 마운트 — panels/viewer/tree.js(`vt-dir` 패널, `dir.show`)가 패널 본문에 붙인다.
//
// 이 모듈은 `files` 지연 청크다(vite.config.js `chunkOf`). 패널 뼈대·경로 삽입·공유는 부르는 쪽(panels 청크)이
// 함수로 넘긴다 — 이 청크는 그 모듈들을 정적으로 끌어오지 않는다.
//
// 파일 개편 단계 6(시안 F06-C) — 트리 위에 「트리 | 내용 검색」 전환을 둔다. 내용 검색은 팔레트 `?` 모드와 같은 화면
// (shell/GrepView.tsx)이고 범위는 트리의 지금 루트다. 단축키(mac ⌘⇧F, palette-lazy.js `contentSearch`)는
// `vt:files-search` 이벤트나 `window.__vtFilesSearchPending`(패널이 아직 없을 때)으로 이 전환을 연다.
import { Show, createSignal, onCleanup } from 'solid-js';
import { render } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { shellIcon } from '../ui/icons-shell.js';
import { FileTree, type FileTreeDeps } from './FileTree.js';
import { TreeStore, type TreeStoreDeps } from './tree-store.js';
import { GrepSide, type GrepSideApi } from '../shell/GrepView.js';
import type { GrepOptions } from '../shell/grep-data.js';

export interface MountDeps extends Omit<FileTreeDeps, 'store' | 'dense' | 'mobile' | 'hostLabel'>, TreeStoreDeps {
  /** 호스트 표시 이름 — 처음엔 id를 보이고, 받아 오면 바꾼다(`/api/hosts`). */
  fetchHostLabel: () => Promise<string | null>;
  /** `files.tree.dense` 설정 읽기와 바뀜 구독. */
  getDense: () => boolean;
  subscribeSettings: (fn: (changed: Record<string, unknown> | null) => void) => () => void;
}

export interface MountedTree {
  store: TreeStore;
  dispose: () => void;
}

const MOBILE_MQ = '(max-width:719px)';

interface SearchRequest { q?: string; opts?: Partial<GrepOptions>; focus?: boolean; }

function takePending(): SearchRequest | null {
  const w = window as any;
  const req = w.__vtFilesSearchPending || null;
  w.__vtFilesSearchPending = null;
  return req;
}

// 내용 검색은 줄 번호로 열어야 한다 — 트리의 `openFile(path)`는 줄을 모른다. 파일 열기 브리지(viewer-lazy.js
// `window.openFile(path, {line, col, host})`, ADR-44 표면 설정)와 요청 함수(`window.vtFetch` — 마감·중계·signal)를 쓴다.
function grepDeps(host: string, watch?: TreeStoreDeps['watch']) {
  const w = window as any;
  return {
    host,
    // 트리와 같은 감시(`tree` 구독) — 사이드바 검색이 「결과가 오래됨」을 띄운다(GrepView.tsx GrepSide).
    watch,
    fetch: (p: string, o?: Record<string, unknown>) => w.vtFetch(p, o),
    openFile: (path: string, o: { line: number; col: number; host?: string }) => w.openFile?.(path, o),
    openFileInPane: (path: string, o: { line: number; col: number }) => w.openFileInPane?.(path, o),
  };
}

function FilesSide(props: { deps: FileTreeDeps; host: string; watch?: TreeStoreDeps['watch'] }) {
  const pending = takePending();
  const [view, setView] = createSignal<'tree' | 'grep'>(pending ? 'grep' : 'tree');
  let grepApi: GrepSideApi | null = null;
  // 트리 루트는 처음 열 때(비동기)와 루트를 바꿀 때 정해진다 — 검색 범위와 감시가 따라가게 신호로 둔다.
  const [root, setRoot] = createSignal(props.deps.store.root || '');
  onCleanup(props.deps.store.subscribe(() => setRoot(props.deps.store.root || '')));
  function onRequest(ev: Event) {
    const req = ((ev as CustomEvent).detail || {}) as SearchRequest;
    setView('grep');
    grepApi?.apply(req.q, req.opts);
    if (req.focus !== false) grepApi?.focus();
  }
  window.addEventListener('vt:files-search', onRequest);
  onCleanup(() => window.removeEventListener('vt:files-search', onRequest));
  const seg = (id: 'tree' | 'grep', icon: string, label: string, hint?: string) => (
    <button type="button" class="vt-segment" role="tab" aria-selected={view() === id}
      onClick={() => { setView(id); if (id === 'grep') grepApi?.focus(); }}>
      <span class="vt-fs-seg-ic" innerHTML={shellIcon(icon, 14)} />{label}
      <Show when={hint}><span class="vt-fs-seg-key">{hint}</span></Show>
    </button>
  );
  const comboHint = () => {
    try { return (window as any).vtContentSearchHint?.() || ''; } catch (_) { return ''; }
  };
  return (
    <div class="vt-fs-side">
      <div class="vt-fs-seg" role="tablist" aria-label={t('파일 보기 전환')}>
        {seg('tree', 'list-tree', t('트리'))}
        {seg('grep', 'search', t('내용 검색'), comboHint())}
      </div>
      <div class="vt-fs-pane" hidden={view() !== 'tree'}>
        <FileTree deps={props.deps} />
      </div>
      <div class="vt-fs-pane" hidden={view() !== 'grep'}>
        <GrepSide deps={grepDeps(props.host, props.watch)} scope={root} initial={pending || undefined}
          onApi={(a) => { grepApi = a; if (pending?.focus !== false && pending) a.focus(); }} />
      </div>
    </div>
  );
}

export function mountFileTree(el: HTMLElement, deps: MountDeps): MountedTree {
  const store = new TreeStore({ host: deps.host, fetchJSON: deps.fetchJSON, watch: deps.watch });
  const [dense, setDense] = createSignal(!!deps.getDense());
  const mq = typeof window.matchMedia === 'function' ? window.matchMedia(MOBILE_MQ) : null;
  const [mobile, setMobile] = createSignal(!!mq?.matches);
  const onMq = () => setMobile(!!mq?.matches);
  mq?.addEventListener?.('change', onMq);
  const offSettings = deps.subscribeSettings((changed) => {
    if (!changed || 'files.tree.dense' in changed) setDense(!!deps.getDense());
  });
  const [hostLabel, setHostLabel] = createSignal(deps.host);
  deps.fetchHostLabel().then((l) => { if (l) setHostLabel(l); }).catch(() => {});
  const disposeView = render(() => <FilesSide deps={{ ...deps, store, dense, mobile, hostLabel }} host={deps.host} watch={deps.watch} />, el);
  let disposed = false;
  return {
    store,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      mq?.removeEventListener?.('change', onMq);
      offSettings();
      store.dispose();
      disposeView();
    },
  };
}
