// 파일 내용 검색 화면(파일 개편 단계 6, 시안 F06 · F06-B · F06-C) — 팔레트 `?` 모드와 사이드바 「파일 › 내용 검색」이
// 같은 엔진(`GET /api/fs/grep`)과 같은 화면을 쓴다. 차이는 `variant`뿐이다.
//   - palette: 옵션 한 줄(토글 + 포함·제외 glob + 범위), 진행 줄, 파일별 결과, 넓으면 오른쪽 미리보기.
//   - side: 질의 칸(토글 내장) + 포함·제외 두 칸, 결과 머리(다시 찾기·모두 접기), 파일별로 접히는 결과.
//
// 이 모듈은 shell 지연 청크다. 상태 모듈을 import하지 않고 필요한 것은 deps로 받는다(Palette.tsx 머리 주석과 같은 이유).
// 색은 토큰만(계약 9) — 줄 미리보기는 터미널 색(`--color-term-*`), 일치는 `--color-term-hl`. styles/screens/97-grep.css.
import { For, Show, createEffect, createMemo, createSignal, onCleanup, untrack } from 'solid-js';
import { t } from '../core/i18n.js';
import { shellIcon } from '../ui/icons-shell.js';
import {
  type GrepHit, type GrepOptions, type GrepResponse, DEFAULT_OPTIONS, grepUrl, groupByFile, hitColumn, newSid,
  segments, skippedLine, summary, fmtSeconds,
} from './grep-data.js';

export interface GrepDeps {
  /** vtFetch — `signal`·`timeout`을 받는다. 2xx가 아니면 `.status`·`.data`가 붙은 Error를 던진다. */
  fetch: (path: string, opts?: Record<string, unknown>) => Promise<any>;
  /** 파일을 그 줄로 연다(viewer-lazy `openFile(path, {line, col, host})`). */
  openFile: (path: string, opts: { line: number; col: number; host?: string }) => void;
  openFileInPane?: (path: string, opts: { line: number; col: number }) => void;
  /** 지금 화면이 보는 호스트(`relayHost()`) — 결과를 그 호스트의 파일로 연다. */
  host: string;
}

type Status = 'idle' | 'running' | 'done' | 'error';

interface GrepError { code: string; reason: string; status: number; pos?: number; }

// 서버 기한(10초) + 중계·자식 정리 여유. 브라우저 쪽 마감은 이보다 길어야 서버의 「기한 초과 + 부분 결과」를 받는다.
const REQUEST_TIMEOUT_MS = 25000;
const DEBOUNCE_MS = 250;

export interface GrepController {
  query: () => string;
  setQuery: (q: string) => void;
  opts: () => GrepOptions;
  setOpt: <K extends keyof GrepOptions>(k: K, v: GrepOptions[K]) => void;
  scope: () => string;
  status: () => Status;
  data: () => GrepResponse | null;
  error: () => GrepError | null;
  elapsed: () => number;
  groups: () => ReturnType<typeof groupByFile>;
  selected: () => number;
  setSelected: (i: number) => void;
  move: (d: number) => void;
  open: (i?: number, newPane?: boolean) => void;
  runNow: () => void;
  cancel: () => void;
  dispose: () => void;
}

/** 검색 상태와 요청. `getScope`는 검색 범위(빈 문자열이면 서버의 시작 루트 전부)를 매번 읽는다. */
export function createGrep(deps: GrepDeps, getScope: () => string, initial?: { q?: string; opts?: Partial<GrepOptions> }): GrepController {
  const [query, setQuery] = createSignal(initial?.q || '');
  const [opts, setOpts] = createSignal<GrepOptions>({ ...DEFAULT_OPTIONS, ...(initial?.opts || {}) });
  const [status, setStatus] = createSignal<Status>('idle');
  const [data, setData] = createSignal<GrepResponse | null>(null);
  const [error, setError] = createSignal<GrepError | null>(null);
  const [elapsed, setElapsed] = createSignal(0);
  const [selected, setSelected] = createSignal(0);
  const [scope, setScope] = createSignal(getScope());
  const sid = newSid();
  let ctl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let seq = 0;

  const groups = createMemo(() => groupByFile(data()?.results || []));

  function stopTicker() { if (ticker) { clearInterval(ticker); ticker = null; } }

  function cancel() {
    if (timer) { clearTimeout(timer); timer = null; }
    seq += 1;
    if (ctl) { ctl.abort(); ctl = null; }
    stopTicker();
    if (status() === 'running') setStatus(data() ? 'done' : 'idle');
  }

  async function run(fallback = true) {
    if (timer) { clearTimeout(timer); timer = null; }
    const q = query().trim() ? query() : '';
    if (ctl) { ctl.abort(); ctl = null; }
    stopTicker();
    const mine = ++seq;
    if (!q.trim()) { setStatus('idle'); setData(null); setError(null); return; }
    const path = untrack(scope);
    const started = Date.now();
    ctl = new AbortController();
    setStatus('running');
    setError(null);
    setElapsed(0);
    ticker = setInterval(() => setElapsed(Date.now() - started), 100);
    try {
      const d = await deps.fetch(grepUrl(q, untrack(opts), path, sid), { signal: ctl.signal, timeout: REQUEST_TIMEOUT_MS });
      if (mine !== seq) return;
      setData(d || { results: [] });
      setSelected(0);
      setStatus('done');
    } catch (e: any) {
      if (mine !== seq) return;
      if (e?.name === 'AbortError') return;
      // 범위(지금 세션의 저장소 등)가 열람 경계 밖이면 시작 루트 전체로 한 번 다시 찾는다.
      if (fallback && e?.status === 403 && path) {
        setScope('');
        stopTicker();
        void run(false);
        return;
      }
      if (e?.status === 409) return;   // 같은 화면의 더 새 검색이 이 검색을 밀어냈다
      const body = e?.data || {};
      setError({ code: body.error || (e?.timeout ? 'timeout' : 'failed'), reason: body.reason || e?.message || t('검색에 실패했습니다.'), status: e?.status || 0, pos: body.pos });
      setData(null);
      setStatus('error');
    } finally {
      if (mine === seq) { stopTicker(); ctl = null; setElapsed(Date.now() - started); }
    }
  }

  // 질의·옵션이 바뀌면 잠깐 기다렸다가 찾는다. 바뀐 순간 이전 요청은 끊는다(서버도 같은 sid의 이전 검색을 취소한다).
  createEffect(() => {
    query(); opts();
    if (timer) clearTimeout(timer);
    if (ctl) { ctl.abort(); ctl = null; }
    timer = setTimeout(() => { setScope(getScope()); void run(); }, DEBOUNCE_MS);
  });

  function move(d: number) {
    const n = groups().flat.length;
    if (!n) return;
    setSelected((i) => Math.max(0, Math.min(n - 1, i + d)));
  }

  function open(i?: number, newPane = false) {
    const hit = groups().flat[i ?? selected()];
    if (!hit) return;
    const at = { line: hit.line, col: hitColumn(hit) };
    if (newPane && deps.openFileInPane && (!deps.host || deps.host === 'local')) deps.openFileInPane(hit.path, at);
    else deps.openFile(hit.path, { ...at, host: deps.host });
  }

  const ctlApi: GrepController = {
    query, setQuery, opts, scope,
    setOpt: (k, v) => setOpts((o) => ({ ...o, [k]: v })),
    status, data, error, elapsed, groups, selected, setSelected, move, open,
    runNow: () => { setScope(getScope()); void run(); },
    cancel,
    dispose: () => { cancel(); },
  };
  onCleanup(() => ctlApi.dispose());
  return ctlApi;
}

// ── 조각 ─────────────────────────────────────────────────────────────────────────────────

function Line(props: { hit: GrepHit; trim?: boolean }) {
  return (
    <span class="vt-gr-tx">
      <For each={segments(props.hit.text, props.hit.ranges, props.trim)}>
        {(s) => (s.hit ? <mark>{s.text}</mark> : <>{s.text}</>)}
      </For>
    </span>
  );
}

function fileIcon(name: string) {
  return shellIcon(/\.(md|txt|rst)$/i.test(name) ? 'file-text' : 'file-code', 14);
}

function Toggles(props: { ctl: GrepController; small?: boolean }) {
  const c = props.ctl;
  const items: { k: 'case' | 'word' | 'regex'; icon: string; label: string; key: string }[] = [
    { k: 'case', icon: 'case-sensitive', label: t('대소문자 구분'), key: '⌥C' },
    { k: 'word', icon: 'whole-word', label: t('단어 단위'), key: '⌥W' },
    { k: 'regex', icon: 'regex', label: t('정규식'), key: '⌥R' },
  ];
  return (
    <For each={items}>
      {(it) => (
        <button type="button" class="vt-gr-opt" classList={{ small: !!props.small }}
          aria-pressed={c.opts()[it.k]} title={`${it.label} ${it.key}`} aria-label={it.label}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => c.setOpt(it.k, !c.opts()[it.k])}
          innerHTML={shellIcon(it.icon, 14)} />
      )}
    </For>
  );
}

function Progress(props: { ctl: GrepController }) {
  const c = props.ctl;
  const done = createMemo(() => (c.status() === 'done' && c.data() ? summary(c.data()!) : null));
  return (
    <Show when={c.status() !== 'idle'}>
      <div class="vt-gr-prog" classList={{ warn: done()?.tone === 'warn', err: c.status() === 'error' }} role="status" aria-live="polite">
        <Show when={c.status() === 'running'}>
          <span class="vt-gr-prog-ic" innerHTML={shellIcon('loader', 14)} />
          <span>{t('검색 중 · {secs}', { secs: fmtSeconds(c.elapsed()) })}</span>
          <span class="vt-gr-sp" />
          <button type="button" class="vt-btn quiet vt-gr-cancel" onClick={() => c.cancel()}>
            <span innerHTML={shellIcon('x', 12)} />{t('취소')}
          </button>
          <span class="vt-gr-bar" aria-hidden="true" />
        </Show>
        <Show when={done()}>
          {(s) => (
            <>
              <span class="vt-gr-prog-ic" innerHTML={shellIcon(s().tone === 'warn' ? (c.data()?.timeout ? 'clock' : 'alert-triangle') : 'check', 14)} />
              <span class="vt-gr-prog-tx">{s().text}</span>
            </>
          )}
        </Show>
        <Show when={c.status() === 'error' && c.error()}>
          {(e) => (
            <>
              <span class="vt-gr-prog-ic" innerHTML={shellIcon(e().status === 503 ? 'circle-x' : 'alert-triangle', 14)} />
              <span class="vt-gr-prog-tx">
                <Show when={e().status === 503} fallback={e().reason}>
                  <b>{t('지금은 검색할 수 없습니다.')}</b> {e().reason}
                </Show>
              </span>
              <Show when={e().status === 503 || e().code === 'timeout' || e().code === 'failed'}>
                <span class="vt-gr-sp" />
                <button type="button" class="vt-btn quiet vt-gr-cancel" onClick={() => c.runNow()}>{t('다시 시도')}</button>
              </Show>
            </>
          )}
        </Show>
      </div>
    </Show>
  );
}

function Empty(props: { ctl: GrepController; variant: 'palette' | 'side' }) {
  const c = props.ctl;
  return (
    <div class="vt-gr-empty">
      <Show when={c.status() === 'idle'}>
        <span>{props.variant === 'palette'
          ? t('「?」 뒤에 쓴 글자를 파일 안에서 찾습니다. 파일 이름은 「/」.')
          : t('찾을 글자를 쓰면 이 폴더 아래 파일 안에서 찾습니다.')}</span>
      </Show>
      <Show when={c.status() === 'done' && c.data() && !c.data()!.results.length}>
        <b>{t('「{q}」가 들어 있는 파일이 없습니다', { q: c.query().trim() })}</b>
        <Show when={c.opts().exclude.trim()}>
          <span>{t('제외 패턴 때문에 빠진 파일이 있을 수 있습니다.')}</span>
          <button type="button" class="vt-btn quiet" onClick={() => c.setOpt('exclude', '')}>{t('제외 없이 다시 찾기')}</button>
        </Show>
      </Show>
      <Show when={c.status() === 'error' && c.error()?.code === 'bad_regex'}>
        <b>{t('검색하지 않았습니다')}</b>
        <span>{t('정규식을 고치거나 ⌥R로 글자 그대로 찾기로 바꾸세요.')}</span>
      </Show>
      <Show when={c.status() === 'error' && c.error()?.status === 503 && props.variant === 'palette'}>
        <span>{t('파일 이름 검색(/)은 계속 쓸 수 있습니다')}</span>
      </Show>
    </div>
  );
}

function Results(props: { ctl: GrepController; variant: 'palette' | 'side'; collapsed?: () => Set<string>; toggle?: (p: string) => void }) {
  const c = props.ctl;
  let box: HTMLDivElement | undefined;
  // 고른 줄을 화면 안으로.
  createEffect(() => {
    const i = c.selected();
    queueMicrotask(() => box?.querySelector<HTMLElement>(`[data-i="${i}"]`)?.scrollIntoView?.({ block: 'nearest' }));
  });
  const skipped = createMemo(() => (c.data() ? skippedLine(c.data()!) : ''));
  return (
    <div class="vt-gr-res" ref={box} role="listbox" aria-label={t('내용 검색 결과')}>
      <For each={c.groups().groups}>
        {(g) => {
          const shut = () => !!props.collapsed?.().has(g.path);
          return (
            <>
              <div class="vt-gr-file" title={g.path}
                onClick={() => (props.toggle ? props.toggle(g.path) : c.open(g.start))}>
                <Show when={props.variant === 'side'}>
                  <span class="vt-gr-cv" innerHTML={shellIcon(shut() ? 'chevron-right' : 'chevron-down', 12)} />
                </Show>
                <span class="vt-gr-fic" innerHTML={fileIcon(g.name)} />
                <span class="vt-gr-fn">{g.name}</span>
                <span class="vt-gr-fd">{g.dir}</span>
                <span class="vt-gr-n">{g.hits.length}</span>
              </div>
              <Show when={!shut()}>
                <For each={g.hits}>
                  {(hit, j) => {
                    const i = () => g.start + j();
                    return (
                      <div class="vt-gr-row" classList={{ on: c.selected() === i() }} data-i={i()} role="option"
                        aria-selected={c.selected() === i()}
                        onMouseEnter={() => props.variant === 'palette' && c.setSelected(i())}
                        onClick={(e) => { c.setSelected(i()); c.open(i(), e.metaKey || e.ctrlKey); }}>
                        <span class="vt-gr-ln">{hit.line}</span>
                        <Line hit={hit} trim={props.variant === 'side'} />
                      </div>
                    );
                  }}
                </For>
              </Show>
            </>
          );
        }}
      </For>
      <Show when={skipped()}>
        <div class="vt-gr-skip"><span innerHTML={shellIcon('info', 14)} />{skipped()}</div>
      </Show>
      <Show when={!c.groups().flat.length}>
        <Empty ctl={c} variant={props.variant} />
      </Show>
    </div>
  );
}

// 오른쪽 미리보기 — 고른 줄 둘레를 보여 주고 그 줄을 강조한다(시안 F06). 같은 파일은 다시 받지 않는다.
const PREVIEW_CONTEXT = 12;

function Preview(props: { ctl: GrepController; fetch: GrepDeps['fetch'] }) {
  const c = props.ctl;
  const cache = new Map<string, Promise<string[] | null>>();
  const [lines, setLines] = createSignal<{ path: string; rel: string; line: number; rows: { n: number; text: string }[] } | null>(null);
  let box: HTMLDivElement | undefined;
  createEffect(() => {
    const hit = c.groups().flat[c.selected()];
    if (!hit) { setLines(null); return; }
    let p = cache.get(hit.path);
    if (!p) {
      p = props.fetch(`/api/fs/file?path=${encodeURIComponent(hit.path)}`, { timeout: 10000 })
        .then((d: any) => (d && !d.binary ? String(d.content || '').split('\n') : null))
        .catch(() => null);
      cache.set(hit.path, p);
    }
    p.then((all) => {
      if (c.groups().flat[c.selected()] !== hit) return;
      const from = Math.max(1, hit.line - PREVIEW_CONTEXT);
      const to = all ? Math.min(all.length, hit.line + PREVIEW_CONTEXT) : 0;
      const rows = [];
      for (let n = from; n <= to; n += 1) rows.push({ n, text: all![n - 1] ?? '' });
      setLines({ path: hit.path, rel: hit.rel, line: hit.line, rows });
      queueMicrotask(() => box?.querySelector<HTMLElement>('.vt-gr-pv-row.on')?.scrollIntoView?.({ block: 'center' }));
    });
  });
  return (
    <div class="vt-gr-pv">
      <Show when={lines()} fallback={<div class="vt-gr-pv-empty">{t('미리보기 없음')}</div>}>
        {(l) => {
          const cut = l().rel.lastIndexOf('/');
          return (
            <>
              <div class="vt-gr-pv-head">
                <span innerHTML={fileIcon(l().rel)} />
                <span class="vt-gr-fd">{cut < 0 ? '' : l().rel.slice(0, cut + 1)}</span>
                <span class="vt-gr-fn">{cut < 0 ? l().rel : l().rel.slice(cut + 1)}</span>
                <span class="vt-gr-fd">:{l().line}</span>
              </div>
              <div class="vt-gr-pv-body" ref={box}>
                <Show when={l().rows.length} fallback={<div class="vt-gr-pv-empty">{t('미리보기 범위 밖입니다')}</div>}>
                  <For each={l().rows}>
                    {(r) => (
                      <div class="vt-gr-pv-row" classList={{ on: r.n === l().line }}>
                        <span class="vt-gr-ln">{r.n}</span>
                        <Show when={r.n === l().line} fallback={<span class="vt-gr-tx">{r.text}</span>}>
                          <Line hit={c.groups().flat[c.selected()] || { text: r.text, ranges: [] } as any} />
                        </Show>
                      </div>
                    )}
                  </For>
                </Show>
              </div>
            </>
          );
        }}
      </Show>
    </div>
  );
}

// ── 팔레트 변형 ─────────────────────────────────────────────────────────────────────────────

export interface GrepKeyApi {
  /** 처리했으면 true(팔레트가 기본 동작을 막는다). */
  key: (ev: KeyboardEvent) => boolean;
}

/** 팔레트 `?` 모드 본문. 질의는 팔레트 입력칸(접두어 뒤)이 준다. */
export function GrepPalette(props: {
  deps: GrepDeps;
  query: () => string;
  scope: () => string;
  scopeLabel: () => string;
  wide: () => boolean;
  onClose: () => void;
  /** ⌥↵ — 사이드바 「내용 검색」으로 옮긴다(같은 질의·옵션). */
  toSidebar?: (q: string, opts: GrepOptions) => void;
  onApi: (api: GrepKeyApi) => void;
}) {
  const c = createGrep(props.deps, props.scope, { q: props.query() });
  createEffect(() => c.setQuery(props.query()));
  const open = (i?: number, newPane = false) => { if (!c.groups().flat.length) return; c.open(i, newPane); props.onClose(); };
  const ctl: GrepController = { ...c, open };
  props.onApi({
    key(ev: KeyboardEvent) {
      if (ev.key === 'ArrowDown') { c.move(1); return true; }
      if (ev.key === 'ArrowUp') { c.move(-1); return true; }
      if (ev.key === 'Enter' && ev.altKey) {
        if (props.toSidebar) { props.toSidebar(c.query(), c.opts()); props.onClose(); }
        return true;
      }
      if (ev.key === 'Enter') {
        if (c.status() !== 'done' && c.status() !== 'running' && c.query().trim()) { c.runNow(); return true; }
        open(undefined, ev.metaKey || ev.ctrlKey);
        return true;
      }
      if (ev.altKey && !ev.metaKey && !ev.ctrlKey) {
        const k = ev.code === 'KeyC' ? 'case' : ev.code === 'KeyW' ? 'word' : ev.code === 'KeyR' ? 'regex' : null;
        if (k) { c.setOpt(k, !c.opts()[k]); return true; }
      }
      return false;
    },
  });
  return (
    <div class="vt-gr palette">
      <div class="vt-gr-opts">
        <Toggles ctl={c} />
        <span class="vt-gr-sep" aria-hidden="true" />
        <label class="vt-gr-glob">
          <span class="vt-gr-gl">{t('포함')}</span>
          <input type="text" spellcheck={false} autocapitalize="off" autocomplete="off" placeholder="src/**, *.ts"
            value={c.opts().include} onInput={(e) => c.setOpt('include', e.currentTarget.value)} />
        </label>
        <label class="vt-gr-glob">
          <span class="vt-gr-gl">{t('제외')}</span>
          <input type="text" spellcheck={false} autocapitalize="off" autocomplete="off" placeholder="**/*.test.*"
            value={c.opts().exclude} onInput={(e) => c.setOpt('exclude', e.currentTarget.value)} />
        </label>
        <span class="vt-gr-sp" />
        <span class="vt-gr-scope" title={c.scope() || props.scopeLabel()} innerHTML={shellIcon('folder', 14)} />
        <span class="vt-gr-scope-tx" title={c.scope()}>{c.scope() ? c.scope().split('/').filter(Boolean).pop() : props.scopeLabel()}</span>
      </div>
      <Progress ctl={c} />
      <div class="vt-gr-main">
        <Results ctl={ctl} variant="palette" />
        <Show when={props.wide() && c.groups().flat.length}>
          <Preview ctl={c} fetch={props.deps.fetch} />
        </Show>
      </div>
    </div>
  );
}

// ── 사이드바 변형 ─────────────────────────────────────────────────────────────────────────────

export interface GrepSideApi {
  focus: () => void;
  apply: (q?: string, opts?: Partial<GrepOptions>) => void;
}

/** 사이드바 「파일 › 내용 검색」 본문. 범위는 트리의 지금 루트. */
export function GrepSide(props: {
  deps: GrepDeps;
  scope: () => string;
  initial?: { q?: string; opts?: Partial<GrepOptions> };
  onApi?: (api: GrepSideApi) => void;
}) {
  const c = createGrep(props.deps, props.scope, props.initial);
  const [collapsed, setCollapsed] = createSignal<Set<string>>(new Set());
  let input: HTMLInputElement | undefined;
  props.onApi?.({
    focus: () => requestAnimationFrame(() => { input?.focus(); input?.select(); }),
    apply: (q, o) => {
      if (o) for (const [k, v] of Object.entries(o)) c.setOpt(k as keyof GrepOptions, v as never);
      if (q != null) c.setQuery(q);
    },
  });
  const toggle = (p: string) => setCollapsed((s) => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const allShut = () => c.groups().groups.length > 0 && c.groups().groups.every((g) => collapsed().has(g.path));
  function onKey(ev: KeyboardEvent) {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); c.move(1); return; }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); c.move(-1); return; }
    if (ev.key === 'Enter') {
      ev.preventDefault();
      if (c.status() === 'done' && c.groups().flat.length) c.open(undefined, ev.metaKey || ev.ctrlKey);
      else c.runNow();
      return;
    }
    if (ev.key === 'Escape' && c.status() === 'running') { ev.preventDefault(); c.cancel(); return; }
    if (ev.altKey && !ev.metaKey && !ev.ctrlKey) {
      const k = ev.code === 'KeyC' ? 'case' : ev.code === 'KeyW' ? 'word' : ev.code === 'KeyR' ? 'regex' : null;
      if (k) { ev.preventDefault(); c.setOpt(k, !c.opts()[k]); }
    }
  }
  return (
    <div class="vt-gr side">
      <div class="vt-gr-form">
        <div class="vt-gr-q">
          <span class="vt-gr-q-ic" innerHTML={shellIcon('search', 14)} />
          <input ref={input} type="text" spellcheck={false} autocapitalize="off" autocomplete="off"
            aria-label={t('파일 안에서 찾을 글자')} placeholder={t('파일 안에서 찾을 글자')}
            value={c.query()} onInput={(e) => c.setQuery(e.currentTarget.value)} onKeyDown={onKey} />
          <Toggles ctl={c} small />
        </div>
        <label class="vt-gr-glob row">
          <span class="vt-gr-gl">{t('포함')}</span>
          <input type="text" spellcheck={false} autocapitalize="off" autocomplete="off" placeholder="src/**, *.ts"
            value={c.opts().include} onInput={(e) => c.setOpt('include', e.currentTarget.value)} onKeyDown={onKey} />
        </label>
        <label class="vt-gr-glob row">
          <span class="vt-gr-gl">{t('제외')}</span>
          <input type="text" spellcheck={false} autocapitalize="off" autocomplete="off" placeholder="**/*.test.*"
            value={c.opts().exclude} onInput={(e) => c.setOpt('exclude', e.currentTarget.value)} onKeyDown={onKey} />
        </label>
        <Show when={c.scope()}>
          <div class="vt-gr-scope-line" title={c.scope()}>
            <span innerHTML={shellIcon('folder', 12)} /><span class="vt-gr-scope-p">{c.scope()}</span>
          </div>
        </Show>
      </div>
      <div class="vt-gr-head">
        <Progress ctl={c} />
        <Show when={c.status() !== 'running'}>
          <span class="vt-gr-acts">
            <button type="button" class="vt-icon-btn" title={t('다시 찾기')} aria-label={t('다시 찾기')}
              disabled={!c.query().trim()} onClick={() => c.runNow()} innerHTML={shellIcon('refresh-cw', 14)} />
            <button type="button" class="vt-icon-btn" title={allShut() ? t('모두 펼치기') : t('모두 접기')}
              aria-label={allShut() ? t('모두 펼치기') : t('모두 접기')} disabled={!c.groups().groups.length}
              onClick={() => setCollapsed(allShut() ? new Set<string>() : new Set(c.groups().groups.map((g) => g.path)))}
              innerHTML={shellIcon('chevrons-down-up', 14)} />
          </span>
        </Show>
      </div>
      <Results ctl={c} variant="side" collapsed={collapsed} toggle={toggle} />
    </div>
  );
}
