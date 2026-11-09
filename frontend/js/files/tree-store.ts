// 파일 트리의 데이터 계층 — 받기·페이지·감시(파일 개편 단계 5, 계획 4.4·4.5·3.5). 화면(FileTree.tsx)과 분리했다.
//
// 받기 규칙
// - 처음 열 때(루트를 바꿀 때)만 `depth=2`로 두 단계를 미리 받는다.
// - 아직 받지 않은 폴더를 펼치면 `depth=1`(그 폴더 + 바로 아래 폴더들). 미리 받은 폴더는 요청 없이 펼친다.
// - 「더 불러오기」는 `after=<커서>`로 다음 1000개.
// - 변경 알림(`tree` 감시)은 **바뀐 폴더 목록**을 준다. 그중 지금 보이는(루트부터 펼쳐진) 폴더만 `depth=0`으로
//   다시 받는다 — 받아 둔 페이지 수만큼. 보이지 않는 폴더는 받아 둔 것을 버리고 「안이 바뀜」 표시만 남긴다(다시
//   펼칠 때 받는다). 목록이 넘치면(`overflow`)·다시 붙으면 보이는 폴더를 전부 다시 받는다.
//
// 모든 상태 키는 `(host, path)`다(tree-model `keyOf`). 루트를 바꾸면 세대(epoch)가 올라가고, 그 전에 나간 요청의
// 응답은 버린다 — 늦게 도착한 옛 루트·옛 호스트의 목록이 섞이지 않는다.
import {
  type DirState, type Listing, appendPage, changedNames, dirFromListing, emptyDir, flattenPrefetch, isUnder, joinPath,
  keyOf, parentOf, relToAbs, removedDirs,
} from './tree-model.js';

export interface WatchDetail {
  dirs?: string[];
  overflow?: boolean;
  reconnect?: boolean;
}

export interface TreeStoreDeps {
  /** 이 화면이 보는 호스트 id(core/host-route.js `relayHost()`). */
  host: string;
  /** `vtFetch` — 2xx가 아니면 던진다. */
  fetchJSON: (path: string) => Promise<any>;
  /** `shell/fs-watch.js` `watchPath`. 해제 함수를 돌려준다. */
  watch: (kind: string, path: string, fn: (detail?: WatchDetail) => void) => () => void;
}

/** 바뀔 때마다 `bump`를 부르는 Set — 바뀜 표시의 판 번호(`TreeStore.marks`)를 올린다. */
class MarkSet extends Set<string> {
  private bump: () => void;
  constructor(bump: () => void) {
    super();
    this.bump = bump;
  }
  add(v: string): this {
    if (!this.has(v)) { super.add(v); this.bump?.(); }
    return this;
  }
  delete(v: string): boolean {
    const r = super.delete(v);
    if (r) this.bump();
    return r;
  }
  clear(): void {
    if (this.size) { super.clear(); this.bump(); }
  }
}

export class TreeStore {
  readonly host: string;
  root: string | null = null;
  readonly dirs = new Map<string, DirState>();
  readonly expanded = new Set<string>();
  /** 바뀜 표시(`changed`·`changedDirs`)의 판 번호 — 화면의 평탄화 캐시가 표시를 다시 그릴지 이걸로 정한다. */
  marks = 0;
  /** 마지막으로 연 뒤 바뀐 파일. */
  readonly changed: Set<string> = new MarkSet(() => { this.marks += 1; });
  /** 안이 바뀌었는데 받아 두지 않은 폴더. */
  readonly changedDirs: Set<string> = new MarkSet(() => { this.marks += 1; });
  /** 보낸 목록 요청 수(측정·시험용). */
  requests = 0;
  /** 폴더 키 → 바로 아래 펼침이 바뀐 횟수(tree-model `flatten`의 `expandVer`). */
  private ever = new Map<string, number>();
  readonly expandVer = (dirKey: string): number => this.ever.get(dirKey) || 0;
  private bumpParent(path: string): void {
    const k = this.key(parentOf(path));
    this.ever.set(k, (this.ever.get(k) || 0) + 1);
  }

  private deps: TreeStoreDeps;
  private epoch = 0;
  private unwatch: (() => void) | null = null;
  private listeners = new Set<() => void>();
  private notifyQueued = false;
  private inflight = new Map<string, Promise<void>>();
  private again = new Set<string>();
  private disposed = false;

  constructor(deps: TreeStoreDeps) {
    this.deps = deps;
    this.host = deps.host;
  }

  key(path: string): string { return keyOf(this.host, path); }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }

  private notify(): void {
    if (this.notifyQueued) return;
    this.notifyQueued = true;
    queueMicrotask(() => {
      this.notifyQueued = false;
      for (const fn of Array.from(this.listeners)) {
        try { fn(); } catch (_) { /* 구독자 하나가 던져도 나머지는 받는다 */ }
      }
    });
  }

  private url(path: string, depth: number, after?: string | null): string {
    let u = `/api/fs/tree?path=${encodeURIComponent(path)}&depth=${depth}`;
    if (after) u += `&after=${encodeURIComponent(after)}`;
    return u;
  }

  private async get(path: string, depth: number, after?: string | null): Promise<Listing> {
    this.requests += 1;
    return this.deps.fetchJSON(this.url(path, depth, after));
  }

  /** 미리 받은 하위 목록까지 상태에 넣는다. 이미 받아 둔 하위 폴더는 덮지 않는다(그쪽이 더 새것일 수 있다). */
  private store(path: string, listing: Listing, overwriteChildren = false): void {
    for (const [p, l] of flattenPrefetch(path, listing)) {
      const k = this.key(p);
      if (p !== path && !overwriteChildren && this.dirs.has(k)) continue;
      this.dirs.set(k, dirFromListing(l));
      if (p === path) this.changedDirs.delete(k);
    }
  }

  /** 트리 최상단을 바꾼다. 실패하면 이전 상태 그대로 두고 던진다. */
  async open(root: string): Promise<void> {
    const epoch = ++this.epoch;
    const listing = await this.get(root, 2);
    if (epoch !== this.epoch || this.disposed) return;
    this.root = root;
    this.dirs.clear();
    this.expanded.clear();
    this.changed.clear();
    this.changedDirs.clear();
    this.ever.clear();
    this.inflight.clear();
    this.again.clear();
    this.store(root, listing, true);
    this.unwatch?.();
    this.unwatch = this.deps.watch('tree', root, (d) => this.onWatch(d));
    this.notify();
  }

  /** 지금 화면에 펼쳐져 보이는 폴더인가(루트부터 조상이 모두 펼쳐져 있다). */
  isVisible(path: string): boolean {
    const root = this.root;
    if (!root) return false;
    if (path === root) return true;
    if (!isUnder(path, root)) return false;
    for (let p = path; p !== root; p = parentOf(p)) {
      if (p === '/' || !this.expanded.has(this.key(p))) return false;
    }
    return true;
  }

  async toggle(path: string): Promise<void> {
    if (this.expanded.has(this.key(path))) this.collapse(path);
    else await this.expand(path);
  }

  collapse(path: string): void {
    const k = this.key(path);
    if (!this.expanded.delete(k)) return;
    this.bumpParent(path);
    // 접으면 그 아래 펼침도 접는다(예전 트리와 같다).
    const prefix = this.key(path === '/' ? '/' : path + '/');
    const hp = this.host.length + 1;
    for (const e of Array.from(this.expanded)) {
      if (e.startsWith(prefix)) { this.expanded.delete(e); this.bumpParent(e.slice(hp)); }
    }
    this.notify();
  }

  async expand(path: string): Promise<void> {
    const k = this.key(path);
    if (this.expanded.has(k)) return;
    this.expanded.add(k);
    this.bumpParent(path);
    const have = this.dirs.get(k);
    if (have && !have.error) { this.notify(); return; }
    const epoch = this.epoch;
    this.dirs.set(k, { ...(have || emptyDir()), loading: true, error: null });
    this.notify();
    try {
      const listing = await this.get(path, 1);
      if (epoch !== this.epoch) return;
      this.store(path, listing, false);
    } catch (e: any) {
      if (epoch !== this.epoch) return;
      this.dirs.set(k, { ...emptyDir(), error: String(e?.message || e) });
    }
    this.notify();
  }

  async loadMore(path: string): Promise<void> {
    const k = this.key(path);
    const d = this.dirs.get(k);
    if (!d || !d.next || d.loadingMore) return;
    const epoch = this.epoch;
    this.dirs.set(k, { ...d, loadingMore: true });
    this.notify();
    try {
      const listing = await this.get(path, 0, d.next);
      if (epoch !== this.epoch) return;
      const cur = this.dirs.get(k);
      if (!cur) return;
      this.dirs.set(k, appendPage(cur, listing));
    } catch (e: any) {
      if (epoch !== this.epoch) return;
      const cur = this.dirs.get(k);
      if (cur) this.dirs.set(k, { ...cur, loadingMore: false, error: String(e?.message || e) });
    }
    this.notify();
  }

  /**
   * 폴더 하나를 다시 받는다(`depth=0`, 받아 둔 페이지 수만큼). 같은 폴더에 대한 요청이 돌고 있으면 끝난 뒤 한 번만
   * 더 받는다. 새 목록과 옛 목록을 비교해 바뀐 파일에 표시를 남기고, 사라진 하위 폴더의 상태를 치운다.
   */
  refreshDir(path: string): Promise<void> {
    const k = this.key(path);
    const running = this.inflight.get(k);
    if (running) { this.again.add(k); return running; }
    const p = (async () => {
      try {
        do {
          this.again.delete(k);
          await this.refetch(path);
        } while (this.again.has(k));
      } finally {
        this.inflight.delete(k);
      }
    })();
    this.inflight.set(k, p);
    return p;
  }

  private async refetch(path: string): Promise<void> {
    const k = this.key(path);
    const prev = this.dirs.get(k);
    const epoch = this.epoch;
    const wantPages = Math.max(1, prev?.pages || 1);
    let next: DirState;
    try {
      next = dirFromListing(await this.get(path, 0));
      while (next.pages < wantPages && next.next) {
        if (epoch !== this.epoch) return;
        next = appendPage(next, await this.get(path, 0, next.next));
      }
    } catch (e: any) {
      if (epoch !== this.epoch) return;
      // 폴더가 지워졌거나 경계 밖이 됐다 — 받아 둔 목록을 지키되 오류를 보인다(부모 갱신이 곧 행을 치운다).
      if (prev) this.dirs.set(k, { ...prev, loading: false, error: String(e?.message || e) });
      this.notify();
      return;
    }
    if (epoch !== this.epoch) return;
    const cur = this.dirs.get(k);
    if (cur && prev) {
      for (const name of changedNames(prev.entries, next.entries)) {
        const e = next.entries.find((x) => x.name === name);
        const ck = this.key(joinPath(path, name));
        if (e && e.dir) this.changedDirs.add(ck);
        else this.changed.add(ck);
      }
      for (const name of removedDirs(prev.entries, next.entries)) this.forget(joinPath(path, name));
      const names = new Set(next.entries.map((e) => e.name));
      for (const e of prev.entries) if (!e.dir && !names.has(e.name)) this.changed.delete(this.key(joinPath(path, e.name)));
    }
    this.dirs.set(k, next);
    this.changedDirs.delete(k);
    this.notify();
  }

  /** 사라진 폴더와 그 아래에 받아 둔 모든 것을 잊는다. */
  private forget(path: string): void {
    const self = this.key(path);
    const prefix = this.key(path + '/');
    this.bumpParent(path);
    for (const set of [this.expanded, this.changed, this.changedDirs]) {
      for (const k of Array.from(set)) if (k === self || k.startsWith(prefix)) set.delete(k);
    }
    for (const k of Array.from(this.dirs.keys())) if (k === self || k.startsWith(prefix)) this.dirs.delete(k);
  }

  /** 지금 보이는 받아 둔 폴더 전부를 다시 받는다(새로고침 버튼·넘침·재연결). */
  async refreshVisible(): Promise<void> {
    const root = this.root;
    if (!root) return;
    const prefix = this.host + '\n';
    const paths = Array.from(this.dirs.keys())
      .filter((k) => k.startsWith(prefix))
      .map((k) => k.slice(prefix.length))
      .filter((p) => this.isVisible(p));
    // 보이지 않는 받아 둔 목록은 버린다 — 다시 펼칠 때 새로 받는다.
    for (const k of Array.from(this.dirs.keys())) {
      const p = k.slice(prefix.length);
      if (!paths.includes(p)) this.dirs.delete(k);
    }
    await Promise.all(paths.map((p) => this.refreshDir(p)));
  }

  /** 감시 알림. */
  onWatch(detail?: WatchDetail): void {
    const root = this.root;
    if (!root || this.disposed) return;
    if (!detail || detail.reconnect || detail.overflow || !Array.isArray(detail.dirs)) {
      void this.refreshVisible();
      return;
    }
    let marked = false;
    for (const rel of detail.dirs) {
      const abs = relToAbs(root, rel);
      if (!abs) continue;
      const k = this.key(abs);
      if (this.isVisible(abs) && this.dirs.has(k)) { void this.refreshDir(abs); continue; }
      // 보이지 않는 폴더 — 받아 둔 것이 있으면 낡았으니 버리고, 「안이 바뀜」만 남긴다.
      this.dirs.delete(k);
      this.changedDirs.add(k);
      marked = true;
    }
    if (marked) this.notify();
  }

  /** 파일을 열었다 — 바뀜 표시를 지운다. */
  markSeen(path: string): void {
    if (this.changed.delete(this.key(path))) this.notify();
  }

  dispose(): void {
    this.disposed = true;
    this.epoch += 1;
    this.unwatch?.();
    this.unwatch = null;
    this.listeners.clear();
  }
}
