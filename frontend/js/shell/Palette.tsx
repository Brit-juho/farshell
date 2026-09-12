// N5/N40/N46(60-settings-palette.md §3) — 커맨드 팔레트 전면 통합. 구
// quickopen.js(vanilla) + search.js(인페인 검색바 자체는 term/search-bar.js로
// 남고, 팔레트로 여는 진입점만 여기로 옮겨왔다)를 하나로.
//
// ⚠ 이 파일은 core/store.js·layout/store.js·core/settings.js(설정 값은
// window.vtSettingsGet/Set 브리지로 예외)·agent/state.js·term/session.js 등을
// **직접 import하지 않는다.** Hud.tsx 상단 주석과 같은 이유(ADR-26) — 지연
// 청크(shell.js)가 그 모듈들을 정적 import하면 Rollup이 청크 안에 복제해
// 넣어서, 앱의 실제 스토어/액션 레지스트리와 팔레트가 보는 것이 다른 객체가
// 된다. 필요한 것은 전부 palette-lazy.js(app.js 쪽 엔트리 그래프)가 인자로
// 넘긴다 — Hud/Rail/Dock과 동일한 관행. 유일한 예외는 설정값 읽기/쓰기로,
// core/settings.js가 이미 공개한 window.vtSettingsGet/Set/Subscribe 브리지를
// 그대로 쓴다(과제 브리핑이 명시한 경로 — 이 모듈만 deps 대신 브리지를 쓴다).
import { createSignal, createEffect, onCleanup, onMount, For, Show, createMemo } from 'solid-js';
import { render } from 'solid-js/web';
import {
  parseQuery, fuzzyMatch, PLACEHOLDER, DEFAULT_COMMANDS, SETTINGS_COMMANDS,
  type PaletteMode, type CommandDescriptor,
} from './palette-data.js';

export interface SessionTab { id: string; name: string; }
export interface KeymapBinding { id: string; label: string; combo: string; passthrough: boolean; unavailable: boolean; }

export interface PaletteDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<any>;
  getAction: (name: string) => ((...args: any[]) => void) | undefined;
  gateOk: (gate?: string) => boolean;
  listSessions: () => SessionTab[];
  getSession: (id: string) => any;
  switchTo: (id: string) => void;
  loadViewer: () => Promise<{ _loadRecent: () => string[]; _hl: (text: string, lang?: string | null) => string }>;
  openFileInPane: (path: string) => void;
  splitActivePane: (dir: string, sessionId?: string | null) => string | null;
  setPaneSession: (sessionId: string, paneId?: string) => void;
  buildSessionCard: (sess: any, onSelect: () => void) => HTMLElement;
  updateSessionCard: (card: HTMLElement, sess: any, agentInfo: any) => void;
  ensurePreviewWs: (name: string) => void;
  setVtSkin: (skin: string) => void;
  listKeymapActions: () => KeymapBinding[];
  invokeKeymapAction: (id: string) => void;
  displayCombo: (combo: string) => string;
  showToast: (msg: string, type?: string, opts?: any) => void;
}

interface Row {
  key: string;
  kind: 'session' | 'file' | 'command' | 'queue' | 'port' | 'scrollback' | 'keymap' | 'empty';
  label: string;
  hint?: string;
  el?: HTMLElement;            // 이미 만들어진 DOM(세션 라이브 프리뷰 카드)
  onOpen?: () => void;
  onOpenNewPane?: () => void;
  onSendToQueue?: () => void;
  preview?: { kind: 'file'; path: string } | { kind: 'session'; sess: any } | { kind: 'scrollback'; before: string[]; line: string; after: string[] } | null;
}

const DEBOUNCE_MS = 200;

function useDebounced<T>(fn: () => void, ms: number, dep: () => T) {
  let handle: ReturnType<typeof setTimeout> | null = null;
  createEffect((prevDep: T | undefined) => {
    const d = dep();
    if (prevDep !== undefined && d !== prevDep) {
      if (handle) clearTimeout(handle);
      handle = setTimeout(fn, ms);
    }
    return d;
  });
  onCleanup(() => { if (handle) clearTimeout(handle); });
}

interface PaletteBodyProps {
  deps: PaletteDeps;
  visible: () => boolean;
  query: () => string;
  wide: () => boolean;
  onRequestClose: () => void;
  onApi: (api: { selectAndFire: (d: number) => void; openSelected: (newPane: boolean) => void; queueSelected: () => void }) => void;
}

function PaletteBody(props: PaletteBodyProps) {
  const { deps } = props;
  const parsed = createMemo(() => parseQuery(props.query()));

  const [recentFiles, setRecentFiles] = createSignal<string[]>([]);
  const [fileResults, setFileResults] = createSignal<{ path: string; name: string }[]>([]);
  const [fileLoading, setFileLoading] = createSignal(false);
  const [tmuxByWebId, setTmuxByWebId] = createSignal<Record<string, any>>({});
  const [agents, setAgents] = createSignal<Record<string, any>>({});
  const [ports, setPorts] = createSignal<{ port: number; cmd?: string }[]>([]);
  const [portsLoading, setPortsLoading] = createSignal(false);
  const [queueItems, setQueueItems] = createSignal<any[]>([]);
  const [scrollbackResults, setScrollbackResults] = createSignal<any[]>([]);
  const [scrollbackLoading, setScrollbackLoading] = createSignal(false);
  const [selected, setSelected] = createSignal(0);
  const [previewText, setPreviewText] = createSignal<string[] | null>(null);

  onMount(() => {
    deps.loadViewer().then((v) => setRecentFiles(v._loadRecent())).catch(() => {});
    refreshTmux();
  });

  function refreshTmux() {
    Promise.all([
      deps.vtFetch('/api/tmux/sessions').catch(() => []),
      deps.vtFetch('/api/agents').catch(() => ({})),
    ]).then(([sessRes, agentsRes]) => {
      const byWebId: Record<string, any> = {};
      for (const s of (sessRes || [])) if (s.web_session_id) byWebId[s.web_session_id] = s;
      setTmuxByWebId(byWebId);
      setAgents(agentsRes || {});
    }).catch(() => {});
  }

  // 팔레트가 열릴 때마다 큐/세션 스냅샷을 새로 받는다 — 열려 있는 동안 다른
  // 화면에서 바뀐 걸 반영한다.
  createEffect(() => {
    if (!props.visible()) return;
    refreshTmux();
  });

  // `/` 파일 검색 — 서버 fuzzy(GET /api/fs/search), 200ms 디바운스.
  useDebounced(() => {
    const p = parsed();
    if (p.mode !== 'file' || !p.query) { setFileResults([]); return; }
    const rawSnapshot = p.raw;
    setFileLoading(true);
    deps.vtFetch(`/api/fs/search?q=${encodeURIComponent(p.query)}`)
      .then((data) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setFileResults(data.results || []);
      })
      .catch(() => setFileResults([]))
      .finally(() => setFileLoading(false));
  }, DEBOUNCE_MS, () => parsed().mode === 'file' ? parsed().raw : '');

  // `!` 포트 — 킬은 여기서 하지 않는다(오조작 방지), 포트 대시보드로 유도한다.
  createEffect(() => {
    const p = parsed();
    if (p.mode !== 'port') return;
    if (!deps.gateOk('ports')) return;
    setPortsLoading(true);
    deps.vtFetch('/api/ports')
      .then((d) => setPorts(d.ports || []))
      .catch(() => setPorts([]))
      .finally(() => setPortsLoading(false));
  });

  // `#` 큐 — 현재 큐 목록.
  createEffect(() => {
    const p = parsed();
    if (p.mode !== 'queue') return;
    deps.vtFetch('/api/queue').then((d) => setQueueItems(d.items || [])).catch(() => setQueueItems([]));
  });

  // `~` 스크롤백 검색 — 200ms 디바운스, 서버가 세션 scrollback 링버퍼를 grep.
  useDebounced(() => {
    const p = parsed();
    if (p.mode !== 'scrollback' || !p.query) { setScrollbackResults([]); return; }
    const rawSnapshot = p.raw;
    setScrollbackLoading(true);
    deps.vtFetch(`/api/search/scrollback?q=${encodeURIComponent(p.query)}&sessions=all`)
      .then((data) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setScrollbackResults(data.results || []);
      })
      .catch(() => setScrollbackResults([]))
      .finally(() => setScrollbackLoading(false));
  }, DEBOUNCE_MS, () => parsed().mode === 'scrollback' ? parsed().raw : '');

  function sendToQueue(text: string) {
    deps.vtFetch('/api/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).then(() => deps.showToast('큐에 추가됨')).catch((e: any) => deps.showToast(`추가 실패: ${e.message}`));
  }

  function openSessionRow(sess: SessionTab): Row {
    const tmuxSess = tmuxByWebId()[sess.id];
    if (tmuxSess) {
      deps.ensurePreviewWs(tmuxSess.name);
      return {
        key: `session:${sess.id}`,
        kind: 'session',
        label: tmuxSess.name,
        el: (() => {
          const card = deps.buildSessionCard(tmuxSess, () => { deps.switchTo(sess.id); props.onRequestClose(); });
          card.classList.add('vt-qo-row', 'vt-qo-session-card');
          deps.updateSessionCard(card, tmuxSess, agents()[tmuxSess.name]);
          return card;
        })(),
        onOpen: () => { deps.switchTo(sess.id); props.onRequestClose(); },
        onOpenNewPane: () => {
          const paneId = deps.splitActivePane('right');
          if (paneId) deps.setPaneSession(sess.id, paneId);
          props.onRequestClose();
        },
        preview: { kind: 'session', sess: tmuxSess },
      };
    }
    return {
      key: `session:${sess.id}`,
      kind: 'session',
      label: sess.name,
      onOpen: () => { deps.switchTo(sess.id); props.onRequestClose(); },
      onOpenNewPane: () => {
        const paneId = deps.splitActivePane('right');
        if (paneId) deps.setPaneSession(sess.id, paneId);
        props.onRequestClose();
      },
    };
  }

  function fileRow(path: string): Row {
    const name = path.split('/').pop() || path;
    const dir = path.split('/').slice(0, -1).join('/') || '.';
    return {
      key: `file:${path}`,
      kind: 'file',
      label: `${name}  ${dir}`,
      onOpen: () => { deps.openFileInPane(path); props.onRequestClose(); },
      onOpenNewPane: () => {
        const paneId = deps.splitActivePane('right');
        if (paneId) deps.openFileInPane(path);
        props.onRequestClose();
      },
      onSendToQueue: () => sendToQueue(path),
      preview: { kind: 'file', path },
    };
  }

  function commandRow(c: CommandDescriptor): Row | null {
    if (!deps.gateOk(c.gate)) return null;
    const run = () => {
      props.onRequestClose();
      if (c.action?.startsWith('__toggle:')) {
        const key = c.action.slice('__toggle:'.length);
        const w = window as any;
        if (typeof w.vtSettingsGet === 'function' && typeof w.vtSettingsSet === 'function') {
          w.vtSettingsSet(key, !w.vtSettingsGet(key));
        }
        return;
      }
      const fn = c.action ? deps.getAction(c.action) : undefined;
      if (typeof fn === 'function') fn();
    };
    if (c.action && !c.action.startsWith('__toggle:') && typeof deps.getAction(c.action) !== 'function') return null;
    return { key: `cmd:${c.label}`, kind: 'command', label: c.label, onOpen: run };
  }

  function themeRows(): Row[] {
    return Array.from(document.querySelectorAll<HTMLElement>('.theme-chip')).map((chip) => ({
      key: `theme:${chip.dataset.skin}`,
      kind: 'command',
      label: `테마 · ${(chip.textContent || '').trim()}`,
      onOpen: () => { props.onRequestClose(); deps.setVtSkin(chip.dataset.skin || ''); },
    }));
  }

  function keymapRow(b: KeymapBinding): Row {
    return {
      key: `keymap:${b.id}`,
      kind: 'keymap',
      label: b.label,
      hint: b.unavailable ? '' : deps.displayCombo(b.combo),
      onOpen: () => { props.onRequestClose(); deps.invokeKeymapAction(b.id); },
    };
  }

  function queueRow(item: any): Row {
    return {
      key: `queue:${item.id}`,
      kind: 'queue',
      label: item.text,
      hint: item.status,
      onOpen: () => { props.onRequestClose(); deps.getAction('queue.show')?.(); },
    };
  }

  function portRow(p: { port: number; cmd?: string }): Row {
    return {
      key: `port:${p.port}`,
      kind: 'port',
      label: `:${p.port} · ${p.cmd || ''}`,
      onOpen: () => { props.onRequestClose(); deps.getAction('ports.show')?.(); },
    };
  }

  function scrollbackRow(r: any, i: number): Row {
    return {
      key: `sb:${r.session_id}:${r.line_no}:${i}`,
      kind: 'scrollback',
      // 2.1.2 — `source: "log"`는 **끝난 세션이나 재시작 이전의 출력**일 수 있다.
      // 그 표시가 없으면 클릭했는데 전환할 세션이 없는 이유를 알 수 없다.
      label: `${r.session_name}${r.source === 'log' ? ' (기록)' : ''} · ${r.line.trim()}`,
      onOpen: () => { deps.switchTo(r.session_id); props.onRequestClose(); },
      onOpenNewPane: () => {
        const paneId = deps.splitActivePane('right');
        if (paneId) deps.setPaneSession(r.session_id, paneId);
        props.onRequestClose();
      },
      onSendToQueue: () => sendToQueue(r.line.trim()),
      preview: { kind: 'scrollback', before: r.context_before || [], line: r.line, after: r.context_after || [] },
    };
  }

  const rows = createMemo<Row[]>(() => {
    const p = parsed();
    const q = p.query;
    switch (p.mode) {
      case 'file': {
        const recentMatches = recentFiles().filter((f) => fuzzyMatch(f, q));
        const recentSet = new Set(recentMatches);
        const serverMatches = fileResults().filter((r) => !recentSet.has(r.path));
        return [...recentMatches.map(fileRow), ...serverMatches.map((r) => fileRow(r.path))];
      }
      case 'session':
        return deps.listSessions().filter((s) => fuzzyMatch(s.name, q)).map(openSessionRow);
      case 'keymap':
        return deps.listKeymapActions().filter((b) => fuzzyMatch(b.label, q)).map(keymapRow);
      case 'queue':
        return queueItems().filter((it) => fuzzyMatch(it.text || '', q)).map(queueRow);
      case 'port':
        return ports().filter((p2) => fuzzyMatch(`${p2.port} ${p2.cmd || ''}`, q)).map(portRow);
      case 'scrollback':
        return scrollbackResults().map((r, i) => scrollbackRow(r, i));
      case 'settings':
        return [...themeRows(), ...SETTINGS_COMMANDS.filter((c) => fuzzyMatch(c.label, q)).map(commandRow).filter(Boolean) as Row[]]
          .filter((r) => fuzzyMatch(r.label, q));
      default: {
        const sessionRows = deps.listSessions().filter((s) => fuzzyMatch(s.name, q)).map(openSessionRow);
        const fileRows = recentFiles().filter((f) => fuzzyMatch(f, q)).map(fileRow);
        const cmdRows = DEFAULT_COMMANDS.map(commandRow).filter(Boolean) as Row[];
        // 파일 업로드 — registerAction 대상이 아니라(quickopen.js 시절부터
        // #file-input을 직접 클릭하는 run 트리거였다) DEFAULT_COMMANDS(action
        // 기반, rail-palette-parity 대상)에는 안 넣는다.
        const uploadRow: Row = {
          key: 'cmd:upload',
          kind: 'command',
          label: '파일 업로드',
          onOpen: () => { props.onRequestClose(); document.getElementById('file-input')?.click(); },
        };
        const filteredCmdRows = [...cmdRows, uploadRow].filter((r) => fuzzyMatch(r.label, q));
        return [...sessionRows, ...fileRows, ...filteredCmdRows];
      }
    }
  });

  // 선택 인덱스가 목록 범위를 벗어나면 되돌린다(모드 전환·필터링으로 목록이
  // 줄어들 때).
  createEffect(() => {
    const n = rows().length;
    if (selected() >= n) setSelected(Math.max(0, n - 1));
  });

  createEffect(() => {
    const r = rows()[selected()];
    if (!r || !r.preview) { setPreviewText(null); return; }
    if (r.preview.kind === 'file') {
      const path = r.preview.path;
      deps.vtFetch(`/api/fs/file?path=${encodeURIComponent(path)}`).then(async (d) => {
        if (d.binary) { setPreviewText([`바이너리 파일`]); return; }
        const lines = String(d.content || '').split('\n').slice(0, 40);
        setPreviewText(lines);
      }).catch((e: any) => setPreviewText([e.message]));
    } else if (r.preview.kind === 'session') {
      // 라이브 프리뷰 카드가 이미 마지막 출력을 보여주므로, 별도 fetch 없이
      // 상태 문구만 보여준다 — 카드 자체가 결과 목록에도 이미 나온다.
      const sess = r.preview.sess;
      setPreviewText([`세션 · ${sess.name}`, sess.command || '']);
    } else if (r.preview.kind === 'scrollback') {
      setPreviewText(null); // 스크롤백은 행 자체가 컨텍스트를 담아 별도 렌더(아래)
    }
  });

  function selectAndFire(delta: number) {
    const n = rows().length;
    if (!n) return;
    setSelected((i) => Math.max(0, Math.min(n - 1, i + delta)));
  }
  function openSelected(newPane: boolean) {
    const r = rows()[selected()];
    if (!r) return;
    if (newPane && r.onOpenNewPane) r.onOpenNewPane();
    else if (r.onOpen) r.onOpen();
  }
  function queueSelected() {
    const r = rows()[selected()];
    r?.onSendToQueue?.();
  }

  // 키보드는 상위(Overlay)의 document keydown 리스너가 이 API로 위임한다.
  props.onApi({ selectAndFire, openSelected, queueSelected });

  return (
    <div class={`vt-qo-body ${props.wide() ? 'two-col' : ''}`} id="vt-qo-body">
      <div class="vt-qo-results-pane">
        <Show when={rows().length === 0}>
          <div class="vt-vw-empty">
            {parsed().mode === 'port' && !deps.gateOk('ports')
              ? '포트 대시보드를 사용할 수 없는 환경입니다.'
              : (parsed().mode === 'file' && fileLoading()) || (parsed().mode === 'scrollback' && scrollbackLoading()) || (parsed().mode === 'port' && portsLoading())
                ? '불러오는 중…'
                : '일치하는 항목이 없습니다.'}
          </div>
        </Show>
        <For each={rows()}>
          {(row, i) => (
            <Show when={row.el} fallback={
              <div
                class="vt-vw-row vt-qo-row"
                classList={{ selected: i() === selected() }}
                onMouseEnter={() => setSelected(i())}
                onClick={() => row.onOpen?.()}
              >
                <div class="vt-vw-name">{row.label}</div>
                <Show when={row.hint}><div class="vt-qo-hint">{row.hint}</div></Show>
              </div>
            }>
              <div class="vt-qo-card-slot" onMouseEnter={() => setSelected(i())} ref={(el) => { if (row.el) el.appendChild(row.el); }} />
            </Show>
          )}
        </For>
      </div>
      <Show when={props.wide()}>
        <div class="vt-qo-preview-pane">
          <Show when={parsed().mode === 'scrollback' && rows()[selected()]?.preview?.kind === 'scrollback'}
                fallback={
                  <Show when={previewText()} fallback={<div class="vt-vw-empty">미리보기 없음</div>}>
                    <For each={previewText() || []}>{(line) => <div class="vt-qo-preview-line">{line}</div>}</For>
                  </Show>
                }>
            {(() => {
              const p = rows()[selected()]?.preview as { kind: 'scrollback'; before: string[]; line: string; after: string[] } | undefined;
              if (!p) return null;
              return (
                <>
                  <For each={p.before}>{(l) => <div class="vt-qo-preview-line">{l}</div>}</For>
                  <div class="vt-qo-preview-line match">{p.line}</div>
                  <For each={p.after}>{(l) => <div class="vt-qo-preview-line">{l}</div>}</For>
                </>
              );
            })()}
          </Show>
        </div>
      </Show>
    </div>
  );
}

export interface PaletteApi {
  open: (mode?: PaletteMode) => void;
  close: () => void;
  isOpen: () => boolean;
}

export function mountPalette(root: HTMLElement, deps: PaletteDeps): PaletteApi {
  const [visible, setVisible] = createSignal(false);
  const [query, setQuery] = createSignal('');
  const [wide, setWide] = createSignal(typeof window !== 'undefined' ? window.innerWidth >= 900 : true);
  const [currentMode, setCurrentMode] = createSignal<PaletteMode>('default');

  let bodyApi: any = null;

  function onResize() { setWide(window.innerWidth >= 900); }
  window.addEventListener('resize', onResize);
  onCleanup(() => window.removeEventListener('resize', onResize));

  function close() {
    setVisible(false);
    document.removeEventListener('keydown', onKeydown, true);
  }

  function onKeydown(ev: KeyboardEvent) {
    if (ev.key === 'Escape') { ev.preventDefault(); close(); return; }
    if (ev.key === 'ArrowDown') { ev.preventDefault(); bodyApi?.selectAndFire(1); return; }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); bodyApi?.selectAndFire(-1); return; }
    if (ev.key === 'Tab') { ev.preventDefault(); bodyApi?.queueSelected(); return; }
    if (ev.key === 'Enter') {
      ev.preventDefault();
      bodyApi?.openSelected(ev.metaKey || ev.ctrlKey);
    }
  }

  function open(mode?: PaletteMode) {
    const target: PaletteMode = mode || 'default';
    if (visible() && currentMode() === target) { close(); return; }
    setCurrentMode(target);
    if (mode) {
      const prefixMap: Record<string, string> = { file: '/', session: '@', keymap: ':', queue: '#', port: '!', scrollback: '~', settings: '>' };
      setQuery(prefixMap[mode] || '');
    } else {
      setQuery('');
    }
    setVisible(true);
    document.addEventListener('keydown', onKeydown, true);
  }

  const api: PaletteApi = { open, close, isOpen: visible };

  function Overlay() {
    let inputEl!: HTMLInputElement;
    createEffect(() => {
      if (visible()) requestAnimationFrame(() => inputEl?.focus());
    });
    return (
      <Show when={visible()}>
        <div class="vt-viewer-backdrop" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
          <div class="vt-viewer-card" role="dialog" aria-modal="true" aria-label="빠른 열기">
            <div class="vt-viewer-head">
              <div class="vt-vw-title">빠른 열기</div>
              <button class="vt-vw-x" aria-label="닫기" onClick={close}>×</button>
            </div>
            <input
              ref={inputEl}
              id="vt-qo-input"
              class="vt-vw-path"
              type="text"
              spellcheck={false}
              autocapitalize="off"
              autocomplete="off"
              placeholder={PLACEHOLDER}
              value={query()}
              onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
            />
            <PaletteBody deps={deps} visible={visible} query={query} wide={wide} onRequestClose={close} onApi={(a) => { bodyApi = a; }} />
          </div>
        </div>
      </Show>
    );
  }

  render(Overlay, root);
  return api;
}
