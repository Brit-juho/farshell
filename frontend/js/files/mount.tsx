// 파일 트리 마운트 — panels/viewer/tree.js(`vt-dir` 패널, `dir.show`)가 패널 본문에 붙인다.
//
// 이 모듈은 `files` 지연 청크다(vite.config.js `chunkOf`). 패널 뼈대·경로 삽입·공유는 부르는 쪽(panels 청크)이
// 함수로 넘긴다 — 이 청크는 그 모듈들을 정적으로 끌어오지 않는다.
import { createSignal } from 'solid-js';
import { render } from 'solid-js/web';
import { FileTree, type FileTreeDeps } from './FileTree.js';
import { TreeStore, type TreeStoreDeps } from './tree-store.js';

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
  const disposeView = render(() => <FileTree deps={{ ...deps, store, dense, mobile, hostLabel }} />, el);
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
