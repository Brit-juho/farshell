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
import { icon } from '../ui/icons.js';
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

// 목업(docs/design_sample, 화면 4a)의 팔레트는 결과를 **종류별 구획**으로 끊고
// 각 줄 앞에 그 종류의 마크를 둔다. 구현은 한 덩어리 목록이라, 세션·파일·명령이
// 섞여 나올 때 무엇을 보고 있는지가 글자를 읽어야만 구분됐다.
//
// 헤더는 "직전 행과 kind가 다를 때"만 끼워 넣는다 — rows()의 순서를 바꾸지
// 않으므로 선택 인덱스(selected)와 키보드 이동은 그대로다. 헤더에 .vt-qo-row를
// 붙이지 않는 것도 같은 이유다(행 수를 세는 곳이 헤더를 행으로 세면 안 된다).
const KIND_META: Record<string, { label: string; icon: string }> = {
  session:    { label: '세션',     icon: 'agent-shell' },
  file:       { label: '파일',     icon: 'file' },
  command:    { label: '명령',     icon: 'list' },
  keymap:     { label: '키맵',     icon: 'keyboard' },
  queue:      { label: '큐',       icon: 'list' },
  port:       { label: '포트',     icon: 'plug' },
  scrollback: { label: '스크롤백', icon: 'search' },
  empty:      { label: '',         icon: 'list' },
};

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
  // 검색 요청이 실패했는데 결과 0건으로만 그리면 "그런 파일이 없다"로 읽힌다 —
  // 루트 밖 경로·권한·서버 오류가 전부 "일치하는 항목이 없습니다"로 뭉개졌다.
  // dock 소스컨트롤은 같은 상황에서 이유를 말해주는데 여기만 삼키고 있었다.
  const [searchError, setSearchError] = createSignal('');
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
    if (p.mode !== 'file' || !p.query) { setFileResults([]); setSearchError(''); return; }
    const rawSnapshot = p.raw;
    setFileLoading(true);
    setSearchError('');
    deps.vtFetch(`/api/fs/search?q=${encodeURIComponent(p.query)}`)
      .then((data) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setFileResults(data.results || []);
      })
      .catch((e) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setFileResults([]);
        setSearchError(e?.message ? `검색에 실패했습니다 — ${e.message}` : '검색에 실패했습니다.');
      })
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
    if (p.mode !== 'scrollback' || !p.query) { setScrollbackResults([]); setSearchError(''); return; }
    const rawSnapshot = p.raw;
    setScrollbackLoading(true);
    setSearchError('');
    // 2.1.3 — 호스트 스위처가 원격을 가리키고 있으면 **그 호스트의** 과거 출력을
    // 찾는다. 로컬만 뒤지면 원격 탭에서 검색이 늘 빈손이라, "기능이 고장났나"로
    // 읽힌다. 모든 호스트를 동시에 뒤지지 않는 건 타자마다 왕복이 늘기 때문이고,
    // "지금 이 호스트를 보고 있다"는 스위처의 의미와도 어긋난다.
    const host = String((window as any).vtSettingsGet?.('ui.activeHostId') || 'local');
    const url = host && host !== 'local'
      ? `/api/hosts/${encodeURIComponent(host)}/search?q=${encodeURIComponent(p.query)}`
      : `/api/search/scrollback?q=${encodeURIComponent(p.query)}&sessions=all`;
    deps.vtFetch(url)
      .then((data) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setScrollbackResults(data.results || []);
      })
      .catch((e) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setScrollbackResults([]);
        setSearchError(e?.message ? `검색에 실패했습니다 — ${e.message}` : '검색에 실패했습니다.');
      })
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
    // 원격 결과에는 session_id가 없다 — 그건 상대 서버 안에서만 뜻이 있는 값이라
    // peer 응답에서 지운다(routes/peer.py). 대신 세션 **이름**으로 그 호스트의
    // 세션을 연다(로컬의 switchTo와 같은 자리를 attachRemoteSession이 맡는다).
    const remoteHost: string | null = r.host || null;
    const open = () => {
      if (remoteHost) (window as any).attachRemoteSession?.(remoteHost, r.session_name);
      else deps.switchTo(r.session_id);
      props.onRequestClose();
    };
    return {
      key: `sb:${remoteHost || ''}:${r.session_id || r.session_name}:${r.line_no}:${i}`,
      kind: 'scrollback',
      // 2.1.2 — `source: "log"`는 **끝난 세션이나 재시작 이전의 출력**일 수 있다.
      // 그 표시가 없으면 클릭했는데 전환할 세션이 없는 이유를 알 수 없다.
      label: `${remoteHost ? `${r.host_label || remoteHost} / ` : ''}${r.session_name}`
        + `${r.source === 'log' ? ' (기록)' : ''} · ${r.line.trim()}`,
      onOpen: open,
      onOpenNewPane: () => {
        if (remoteHost) { open(); return; }
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

  // i번째 행에서 구획이 시작되면 {label,count}, 아니면 null.
  const sectionHeadAt = (i: number) => {
    const list = rows();
    const row = list[i];
    if (!row) return null;
    const meta = KIND_META[row.kind];
    if (!meta || !meta.label) return null;
    if (i > 0 && list[i - 1]?.kind === row.kind) return null;
    // 한 종류만 나오는 모드(예: `~` 스크롤백)에서도 머리를 보여준다 — 그
    // 한 줄이 "지금 무엇을 검색한 결과인가"를 말해준다.
    return { label: meta.label, count: list.filter((r) => r.kind === row.kind).length };
  };

  const markFor = (row: Row) => icon(KIND_META[row.kind]?.icon || 'list', 13, 1.75);

  // 미리보기 칸은 **보여줄 게 있을 때만** 연다. 넓은 화면이면 무조건 2단으로
  // 열던 탓에, 세션·명령처럼 미리보기가 없는 모드에서도 팔레트가 880px로
  // 벌어지고 오른쪽 절반이 "미리보기 없음"으로 비어 있었다. 파일(`/`)과
  // 스크롤백(`~`)만 실제로 내용을 채운다 — 세션은 결과 줄 자체가 이미 라이브
  // 프리뷰 카드라 오른쪽에 또 그릴 것이 없다.
  const showPreview = () => props.wide() && (parsed().mode === 'file' || parsed().mode === 'scrollback');

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
    <div class={`vt-qo-body ${showPreview() ? 'two-col' : ''}`} id="vt-qo-body">
      <div class="vt-qo-results-pane">
        <Show when={rows().length === 0}>
          <div class="vt-vw-empty">
            {parsed().mode === 'port' && !deps.gateOk('ports')
              ? '포트 대시보드를 사용할 수 없는 환경입니다.'
              : (parsed().mode === 'file' && fileLoading()) || (parsed().mode === 'scrollback' && scrollbackLoading()) || (parsed().mode === 'port' && portsLoading())
                ? '불러오는 중…'
                : searchError()
                  ? searchError()
                  : '일치하는 항목이 없습니다.'}
          </div>
        </Show>
        <For each={rows()}>
          {(row, i) => (
            <>
              {/* 종류가 바뀌는 첫 줄에만 구획 머리를 끼운다. 행이 아니므로
                  .vt-qo-row를 붙이지 않는다 — 행을 세는 쪽이 이걸 행으로
                  세면 키보드 이동과 어긋난다. */}
              <Show when={sectionHeadAt(i())}>
                {(head) => (
                  <div class="vt-qo-sec" aria-hidden="true">
                    <span class="vt-qo-sec-label">{head().label}</span>
                    <span class="vt-qo-sec-count">{head().count}</span>
                  </div>
                )}
              </Show>
              <Show when={row.el} fallback={
                <div
                  class="vt-vw-row vt-qo-row"
                  classList={{ selected: i() === selected() }}
                  onMouseEnter={() => setSelected(i())}
                  onClick={() => row.onOpen?.()}
                >
                  <span class="vt-qo-mark" aria-hidden="true" innerHTML={markFor(row)} />
                  <div class="vt-vw-name">{row.label}</div>
                  <Show when={row.hint}><div class="vt-qo-hint">{row.hint}</div></Show>
                  {/* 선택된 줄의 ↵ 표시는 CSS ::after로 그린다(.vt-qo-row.selected).
                      DOM에 글자로 넣으면 행의 textContent가 "alpha↵"가 되어,
                      행 이름으로 찾는 쪽(테스트·접근성 이름)이 전부 어긋난다 —
                      실제로 테스트가 이 문제를 잡았다. */}
                </div>
              }>
                <div class="vt-qo-card-slot" onMouseEnter={() => setSelected(i())} ref={(el) => { if (row.el) el.appendChild(row.el); }} />
              </Show>
            </>
          )}
        </For>
      </div>
      <Show when={showPreview()}>
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
        <div class="vt-viewer-backdrop vt-qo-backdrop" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
          {/* vt-qo 스코프: 이 카드는 코드 뷰어와 껍데기(.vt-viewer-card)를
              공유하는데, 그쪽 치수(1100px)를 그대로 물려받아 팔레트가 화면
              절반을 덮고 있었다. 폭·행 높이·머리글은 이 클래스 안에서만
              다시 정한다(components.css). */}
          <div class="vt-viewer-card vt-qo-card" role="dialog" aria-modal="true" aria-label="빠른 열기">
            <div class="vt-viewer-head vt-qo-head">
              <span class="vt-qo-head-mark" aria-hidden="true" innerHTML={icon('search', 14, 2)} />
              <div class="vt-vw-title">빠른 열기</div>
              <span class="vt-qo-head-esc" aria-hidden="true">esc 닫기</span>
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
            {/* 이 팔레트는 접두사(/ @ : # ! ~)로 모드가 갈리는데, 그걸 아는
                방법이 placeholder 한 줄뿐이었다. 목업 4a처럼 아래에 상시
                고정한다 — 키 힌트와 모드 힌트를 한 줄에 같이 둔다. */}
            <div class="vt-qo-foot" aria-hidden="true">
              <span>↑↓ 이동</span>
              <span>↵ 열기</span>
              <span>⌘↵ 새 pane</span>
              <span class="vt-qo-foot-modes">/ 파일 · @ 세션 · : 명령 · # 큐 · ! 포트 · ~ 스크롤백</span>
            </div>
          </div>
        </div>
      </Show>
    );
  }

  render(Overlay, root);
  return api;
}
