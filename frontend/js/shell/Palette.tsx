import { pushScope } from '../core/keyboard-scopes.js';
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
import { t } from '../core/i18n.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';
import {
  parseQuery, fuzzyMatch, PLACEHOLDER, MODE_HINT, MODE_PREFIX, DEFAULT_COMMANDS, SETTINGS_COMMANDS,
  type PaletteMode, type CommandDescriptor,
} from './palette-data.js';
import { GrepPalette, type GrepKeyApi } from './GrepView.js';
import { renderAnsi, fetchPreview } from '../lib/ansi-spans.js';
import type { GrepOptions } from './grep-data.js';
import {
  SECTION_META, buildSettingEntries, matchSettingEntry, stepSettingValue, formatSettingValue,
} from '../panels/settings/index-data.js';

export interface SessionTab { id: string; name: string; }
export interface KeymapBinding { id: string; label: string; combo: string; passthrough: boolean; unavailable: boolean; }

export interface PaletteDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<any>;
  getAction: (name: string) => ((...args: any[]) => void) | undefined;
  gateOk: (gate?: string) => boolean;
  listSessions: () => SessionTab[];
  getSession: (id: string) => any;
  switchTo: (id: string) => void;
  loadViewer: () => Promise<{ _loadRecent: () => string[] }>;
  openFile: (path: string, opts?: { line?: number; col?: number; host?: string }) => void;
  openFileInPane: (path: string, opts?: { line?: number; col?: number }) => void;
  /** 지금 화면이 보는 호스트(`relayHost()`) — 내용 검색 결과를 그 호스트의 파일로 연다. */
  host?: string;
  /** ⌥↵ — 내용 검색을 사이드바 「파일 › 내용 검색」으로 옮긴다. */
  openSidebarSearch?: (q: string, opts: GrepOptions) => void;
  splitActivePane: (dir: string, sessionId?: string | null) => string | null;
  setPaneSession: (sessionId: string, paneId?: string) => void;
  buildSessionCard: (sess: any, onSelect: () => void) => HTMLElement;
  updateSessionCard: (card: HTMLElement, sess: any, agentInfo: any) => void;
  setVtSkin: (skin: string) => void;
  vtSkins: () => string[];
  vtSkinLabel: (skin: string) => string;
  listKeymapActions: () => KeymapBinding[];
  invokeKeymapAction: (id: string) => void;
  displayCombo: (combo: string) => string;
  displayComboHtml?: (combo: string) => string;
  showToast: (msg: string, type?: string, opts?: any) => void;
  /** 계정(핫스왑 H1) — 없으면 계정 줄을 그리지 않는다. */
  loadAccounts?: () => Promise<{ ok: boolean; profiles: { id: string; name: string; tool: string; selectable: boolean }[] }>;
  newSessionWithAccount?: (profile: string) => void;
}

interface Row {
  key: string;
  kind: 'session' | 'file' | 'command' | 'queue' | 'port' | 'scrollback' | 'keymap' | 'setting' | 'empty' | 'recent';
  label: string;
  hint?: string;
  hintHtml?: string;            // 단축키 — SVG 기호가 섞인 마크업(core/keymap.js displayComboHtml)
  sub?: string;                // 이름 아래 한 줄(설정의 경로 «터미널 › 표시»)
  /** 줄 안에서 바로 바꾸는 값. bool은 스위치, 나머지는 ‹ 값 › 로 그린다. */
  ctl?: { kind: 'bool' | 'select' | 'range'; on: boolean; text: string };
  /** ←/→(dir ±1)로 값을 한 칸 옮긴다. 없으면 방향키는 입력창의 캐럿을 그대로 움직인다. */
  onAdjust?: (dir: number) => void;
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
  recent:     { label: t('최근 사용'), icon: 'list' },
  session:    { label: t('세션'),     icon: 'agent-shell' },
  file:       { label: t('파일'),     icon: 'file' },
  command:    { label: t('명령'),     icon: 'list' },
  keymap:     { label: t('키맵'),     icon: 'keyboard' },
  setting:    { label: t('설정'),     icon: 'settings' },
  queue:      { label: t('큐'),       icon: 'list' },
  port:       { label: t('포트'),     icon: 'plug' },
  scrollback: { label: t('스크롤백'), icon: 'search' },
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
  onApi: (api: { selectAndFire: (d: number) => void; openSelected: (newPane: boolean) => void; queueSelected: () => void; adjustSelected: (dir: number) => boolean; grepKey: (ev: KeyboardEvent) => boolean }) => void;
}

function PaletteBody(props: PaletteBodyProps) {
  const { deps } = props;
  const parsed = createMemo(() => parseQuery(props.query()));

  const [recentFiles, setRecentFiles] = createSignal<string[]>([]);
  // 2026-09-23(2.1 D6) — 기본 화면의 "최근 사용 기능" 2개. 레이블이 아니라
  // action id(Row.key)로 기록한다 — 레이블은 그대로인 채 동작만 바뀔 수
  // 있는 __toggle: 계열도 있어서, 레이블 비교로는 같은 기능인지 못 믿는다.
  // 이 팔레트 모듈은 core/settings.js를 직접 import하지 않으므로(§ 위
  // 주석) 그 모듈이 공개한 window.vtSettingsGet/Set 브리지를 쓴다 —
  // __toggle: 처리(commandRow)가 이미 쓰는 것과 같은 경로다.
  const RECENT_KEY = 'palette.recentCommands';
  const RECENT_MAX = 10;
  function readRecentIds(): string[] {
    try {
      const raw = (window as any).vtSettingsGet?.(RECENT_KEY);
      const arr = JSON.parse(raw || '[]');
      return Array.isArray(arr) ? arr : [];
    } catch (_) { return []; }
  }
  const [recentCommandIds, setRecentCommandIds] = createSignal<string[]>(readRecentIds());
  function recordRecent(id: string) {
    const next = [id, ...recentCommandIds().filter((x) => x !== id)].slice(0, RECENT_MAX);
    setRecentCommandIds(next);
    (window as any).vtSettingsSet?.(RECENT_KEY, JSON.stringify(next));
  }
  function withRecent(row: Row): Row {
    if (!row.onOpen) return row;
    const orig = row.onOpen;
    return { ...row, onOpen: () => { recordRecent(row.key); orig(); } };
  }
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
  const [accountRows, setAccountRows] = createSignal<{ id: string; name: string }[] | null>(null);
  const [selected, setSelected] = createSignal(0);
  // 설정 값이 바뀌면(이 줄에서든 다른 화면에서든) 줄의 값 표시를 다시 읽는다.
  const [settingsRev, setSettingsRev] = createSignal(0);
  const [previewText, setPreviewText] = createSignal<string[] | null>(null);
  // 세션 미리보기(시안 O01 · O03) — 그 세션 화면의 마지막 줄들을 터미널 색으로. null = 아직/없음.
  const [sessionPreview, setSessionPreview] = createSignal<{ name: string; content: string | null } | null>(null);

  onMount(() => {
    const off = (window as any).vtSettingsSubscribe?.(() => setSettingsRev((n) => n + 1));
    if (typeof off === 'function') onCleanup(off);
    deps.loadViewer().then((v) => setRecentFiles(v._loadRecent())).catch(() => {});
    refreshTmux();
  });
  // 열릴 때마다 계정 목록을 새로 본다(등록·기본 변경이 다른 화면에서 일어날 수 있다). 계정이 없거나 읽지 못하면 줄이 없다.
  createEffect(() => {
    if (!props.visible() || !deps.loadAccounts) return;
    deps.loadAccounts().then((a) => setAccountRows(a.ok ? a.profiles.filter((p) => p.selectable).map((p) => ({ id: p.id, name: p.name })) : null)).catch(() => setAccountRows(null));
  });

  let tmuxRevision = 0;
  function applyTmux(sessRes: any[]) {
    const byWebId: Record<string, any> = {};
    const byName = new Map(sessRes.map((s: any) => [s.name, s]));
    const local = (window as any).allSessions?.() || {};
    for (const [id, session] of Object.entries<any>(local)) {
      const tmuxName = session.tmuxName || session.tmux_name;
      if (byName.has(tmuxName)) byWebId[id] = byName.get(tmuxName);
    }
    setTmuxByWebId(byWebId);
  }
  const offTmux = onWorkspaceEvent('tmux_sessions', (msg: any) => {
    tmuxRevision += 1;
    if (props.visible() && Array.isArray(msg.sessions)) applyTmux(msg.sessions);
  });
  onCleanup(offTmux);
  function refreshTmux() {
    const request = ++tmuxRevision;
    Promise.all([
      deps.vtFetch('/api/tmux/sessions').catch(() => null),
      deps.vtFetch('/api/agents').catch(() => null),
    ]).then(([sessRes, agentsRes]) => {
      if (request !== tmuxRevision) return;
      if (Array.isArray(sessRes)) applyTmux(sessRes);
      if (agentsRes) setAgents(agentsRes);
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
        setSearchError(e?.message ? t('검색에 실패했습니다 — {message}', { message: e.message }) : t('검색에 실패했습니다.'));
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
    // 2026-09-28 — 호스트 스위처로 다른 맥에 전환해 있으면 이 호출 자체가 그 맥으로
    // 중계된다(core/host-route.js). 예전처럼 호스트별 검색 경로를 따로 고르지 않는다.
    const url = `/api/search/scrollback?q=${encodeURIComponent(p.query)}&sessions=all`;
    deps.vtFetch(url)
      .then((data) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setScrollbackResults(data.results || []);
      })
      .catch((e) => {
        if (parseQuery(props.query()).raw !== rawSnapshot) return;
        setScrollbackResults([]);
        setSearchError(e?.message ? t('검색에 실패했습니다 — {message}', { message: e.message }) : t('검색에 실패했습니다.'));
      })
      .finally(() => setScrollbackLoading(false));
  }, DEBOUNCE_MS, () => parsed().mode === 'scrollback' ? parsed().raw : '');

  function sendToQueue(text: string) {
    deps.vtFetch('/api/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).then(() => deps.showToast(t('큐에 추가됨'))).catch((e: any) => deps.showToast(t('추가 실패: {message}', { message: e.message })));
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
      onOpen: () => { deps.openFile(path); props.onRequestClose(); },
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

  // 2026-09-18 — DOM의 `.theme-chip`을 읽던 옛 구현은 그 칩이 사는 설정
  // 「모양」 탭이 열려 있지 않으면(대부분의 시간) 결과가 0개였다 — 칩이
  // display:none 레일 안에만 있던 시절엔 항상 있어서 안 드러난 문제였다.
  // theme.js를 deps로 직접 받는다(ADR-26 — DOM 상태가 아니라 모듈 상태를 본다).
  function themeRows(): Row[] {
    return deps.vtSkins().map((skin) => ({
      key: `theme:${skin}`,
      kind: 'command',
      label: t('테마 · {skin}', { skin: deps.vtSkinLabel(skin) }),
      onOpen: () => { props.onRequestClose(); deps.setVtSkin(skin); },
    }));
  }

  // 설정 색인(panels/settings/index-data.js)의 줄. 값은 window.vtSettings* 브리지로만 읽고 쓴다
  // (이 모듈은 core/settings.js를 import하지 않는다 — 파일 머리 주석).
  function settingEntryRows(q: string): Row[] {
    settingsRev(); // 값이 바뀌면 다시 계산
    const w = window as any;
    const compact = window.matchMedia?.('(max-width:719px)').matches;
    const shown = SECTION_META.filter((s) => !(compact && s.desktopOnly)).map((s) => s.id);
    return buildSettingEntries(shown).filter((e) => matchSettingEntry(e, q)).map((e): Row => {
      if (e.kind === 'link') {
        return {
          key: `setting:${e.id}`, kind: 'setting', label: e.label, sub: e.path,
          onOpen: () => { props.onRequestClose(); deps.getAction('settings.section')?.(e.section); },
        };
      }
      const value = w.vtSettingsGet?.(e.key);
      const adjust = (dir: number) => {
        const next = stepSettingValue(e, w.vtSettingsGet?.(e.key), dir);
        if (next !== undefined) w.vtSettingsSet?.(e.key, next);
      };
      return {
        key: `setting:${e.key}`, kind: 'setting', label: e.label, sub: e.path,
        ctl: { kind: e.kind as 'bool' | 'select' | 'range', on: e.kind === 'bool' ? !!value : false, text: formatSettingValue(e, value) },
        // 줄을 눌러도, Enter를 눌러도 창은 닫지 않는다 — 바꾼 결과를 보면서 이어서 바꾸려는 줄이다.
        onOpen: () => adjust(1),
        onAdjust: adjust,
      };
    });
  }

  function keymapRow(b: KeymapBinding): Row {
    return {
      key: `keymap:${b.id}`,
      kind: 'keymap',
      label: b.label,
      hint: b.unavailable ? '' : deps.displayCombo(b.combo),
      hintHtml: b.unavailable || !deps.displayComboHtml ? undefined : deps.displayComboHtml(b.combo),
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
      // ADR-43(2.3.0) — 영속 로그 검색을 없애면서 `source`는 항상 "live"가
      // 됐다("(기록)" 표시가 가리키던 `"log"`는 더 이상 안 나온다). 필드
      // 자체는 원격 피어 호환을 위해 남아 있다(server/routes/search.py).
      label: `${remoteHost ? `${r.host_label || remoteHost} / ` : ''}${r.session_name}`
        + ` · ${r.line.trim()}`,
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
      case 'settings': {
        const entryRows = settingEntryRows(q);
        // 「드래그 시 자동 복사」처럼 색인에 줄 안 컨트롤로 이미 있는 토글은 명령 목록에서 뺀다 — 같은 항목이 두 줄로 나온다.
        const inlineKeys = new Set(entryRows.map((r) => r.key.slice('setting:'.length)));
        const commands = SETTINGS_COMMANDS
          .filter((c) => !(c.action?.startsWith('__toggle:') && inlineKeys.has(c.action.slice('__toggle:'.length))))
          .filter((c) => fuzzyMatch(c.label, q)).map(commandRow).filter(Boolean) as Row[];
        return [...entryRows, ...themeRows().filter((r) => fuzzyMatch(r.label, q)), ...commands];
      }
      default: {
        // 2026-09-23(2.1 D6) — 세션은 여기서 뺐다(사용자 요청 — 세션 이동·
        // 관리는 세션 탭에서 하고, 팔레트에서는 `@`로만 찾는다). 순서는
        // 최근 사용 기능 2개 → 최근 파일 2개 → 모든 기능(스크롤).
        const fileMatches = recentFiles().filter((f) => fuzzyMatch(f, q));
        const fileRows = (q ? fileMatches : fileMatches.slice(0, 2)).map(fileRow);

        const cmdRows = DEFAULT_COMMANDS.map(commandRow).filter(Boolean) as Row[];
        // 계정(핫스왑 H1) — 등록된 계정이 있을 때만. 「<이름> 계정으로 새 세션」은 쓸 수 있는 계정만(깨진 계정은 선택기에서 사유와 함께 본다).
        const accts = accountRows();
        if (accts && accts.length && deps.newSessionWithAccount) {
          const create = deps.newSessionWithAccount;
          // 기본 계정은 설정이다 — 세션을 만드는 메뉴가 아니라 설정 › 에이전트 계정을 연다.
          cmdRows.push({ key: 'cmd:account-default', kind: 'command', label: t('기본 계정 바꾸기'), hint: t('설정 › 에이전트 계정'),
            onOpen: () => { props.onRequestClose(); deps.getAction('settings.section')?.('accounts'); } });
          for (const a of accts) {
            cmdRows.push({ key: `cmd:account-new:${a.id}`, kind: 'command', label: t('{name} 계정으로 새 세션', { name: a.name }),
              onOpen: () => { props.onRequestClose(); create(a.id); } });
          }
        }
        // 파일 업로드 — registerAction 대상이 아니라(quickopen.js 시절부터
        // #file-input을 직접 클릭하는 run 트리거였다) DEFAULT_COMMANDS(action
        // 기반, rail-palette-parity 대상)에는 안 넣는다.
        const uploadRow: Row = {
          key: 'cmd:upload',
          kind: 'command',
          label: t('파일 업로드'),
          onOpen: () => { props.onRequestClose(); document.getElementById('file-input')?.click(); },
        };
        // 모바일에는 dock MCP 버튼이 없으므로 빠른 열기에서도 독립 팝업으로 연다.
        const mcpRow: Row = {
          key: 'cmd:mcp',
          kind: 'command',
          label: t('MCP · 스킬 관리'),
          onOpen: () => {
            props.onRequestClose();
            import('../panels/mcp-open.js')
              .then((m) => m.showMcp())
              .catch(() => deps.showToast(t('MCP 화면을 열지 못했습니다'), 'error'));
          },
        };
        // 키맵 명령도 「모든 기능」에 합류한다 — 예전엔 `:` 접두사로만
        // 닿았는데, "한곳에 모아서 찾을 수 있게" 해달라는 요청은 접두사를
        // 아는 사람만을 위한 게 아니었다. unavailable(일반 탭이 못 잡는
        // 조합)은 `:` 모드와 마찬가지로 뺀다 — 눌러도 안 되는 걸 보여주지
        // 않는다.
        const keymapRows = deps.listKeymapActions().filter((b) => !b.unavailable).map(keymapRow);
        const allCommandRows = [...cmdRows, uploadRow, mcpRow, ...keymapRows]
          .map(withRecent)
          .filter((r) => fuzzyMatch(r.label, q));

        // 검색어가 있으면 "최근" 구분이 오히려 결과를 가린다 — 매치되는 걸
        // 전부 평평하게 보여준다.
        if (q) return [...fileRows, ...allCommandRows];

        const byKey = new Map(allCommandRows.map((r) => [r.key, r] as const));
        const recentRows: Row[] = [];
        for (const id of recentCommandIds()) {
          const r = byKey.get(id);
          if (r) recentRows.push({ ...r, kind: 'recent' });
          if (recentRows.length >= 2) break;
        }
        const recentKeys = new Set(recentRows.map((r) => r.key));
        const restRows = allCommandRows.filter((r) => !recentKeys.has(r.key));
        return [...recentRows, ...fileRows, ...restRows];
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
        if (d.binary) { setPreviewText([t('바이너리 파일')]); return; }
        const lines = String(d.content || '').split('\n').slice(0, 40);
        setPreviewText(lines);
      }).catch((e: any) => setPreviewText([e.message]));
    } else if (r.preview.kind === 'session') {
      // 리디자인 6단계(시안 O01 · O03): 오른쪽에 그 세션 화면의 마지막 줄들을 터미널 색으로 그린다.
      // tmux capture-pane -e 원문을 받아 lib/ansi-spans.js가 글자는 textContent로, 색은 term 토큰 클래스로만 넣는다.
      // 위아래로 훑는 동안 요청이 몰리지 않게 120ms 머문 뒤에 묻고, 답이 늦게 오면 그때 고른 세션인지 다시 본다.
      const sess = r.preview.sess;
      const name = String(sess.name || '');
      setPreviewText([t('세션 · {name}', { name }), sess.command || '']);
      if (sessionPreview()?.name !== name) setSessionPreview({ name, content: null });
      const timer = setTimeout(() => {
        void fetchPreview(deps.vtFetch, name, { lines: 40, host: deps.host || '' }).then((content) => {
          const cur = rows()[selected()];
          if (cur?.preview?.kind === 'session' && String(cur.preview.sess?.name || '') === name) setSessionPreview({ name, content });
        });
      }, 120);
      onCleanup(() => clearTimeout(timer));
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
  // 리디자인 6단계(시안 O01 · O03) — 세션(`@`)과 기본 모드도 미리보기 칸을 연다: 고른 세션의 화면을 터미널 색으로 보여 준다.
  // 기본 모드는 세션 줄이 하나라도 있을 때만(명령·설정만 보이는 목록이 880으로 벌어지지 않게).
  const showPreview = () => props.wide() && (parsed().mode === 'file' || parsed().mode === 'scrollback'
    || parsed().mode === 'session' || (parsed().mode === 'default' && rows().some((r) => r.preview?.kind === 'session')));
  const selectedSession = () => { const p = rows()[selected()]?.preview; return p && p.kind === 'session' ? p.sess : null; };

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

  // ←/→ — 선택한 줄에 값이 있으면 그 값을 옮기고 true(방향키를 입력창 캐럿에 넘기지 않는다).
  function adjustSelected(dir: number): boolean {
    const r = rows()[selected()];
    if (!r?.onAdjust) return false;
    r.onAdjust(dir);
    return true;
  }

  // `?` 내용 검색(파일 개편 단계 6) — 목록·키보드를 GrepView가 따로 가진다. 범위는 지금 세션의 저장소
  // (없으면 그 세션 위치, 그것도 없으면 서버의 시작 루트 전부). 범위가 열람 경계 밖이면 GrepView가 전부로 다시 찾는다.
  let grepApi: GrepKeyApi | null = null;
  function grepKey(ev: KeyboardEvent): boolean {
    return parsed().mode === 'grep' && !!grepApi?.key(ev);
  }
  function grepScope(): string {
    const id = (window as any).activeSessionId?.();
    const s = id ? tmuxByWebId()[id] : null;
    return (s && (s.context?.root || s.contextCwd || s.cwd)) || '';
  }

  // 키보드는 상위(Overlay)의 document keydown 리스너가 이 API로 위임한다.
  props.onApi({ selectAndFire, openSelected, queueSelected, adjustSelected, grepKey });

  return (
    <Show when={parsed().mode !== 'grep'} fallback={
      <div class="vt-qo-body vt-qo-grep" id="vt-qo-body">
        <GrepPalette
          deps={{ fetch: deps.vtFetch, openFile: deps.openFile, openFileInPane: deps.openFileInPane, host: deps.host || 'local' }}
          query={() => parsed().query}
          scope={grepScope}
          scopeLabel={() => t('열람 루트 전체')}
          wide={props.wide}
          onClose={props.onRequestClose}
          toSidebar={deps.openSidebarSearch}
          onApi={(a) => { grepApi = a; }}
        />
      </div>
    }>
    <div class={`vt-qo-body ${showPreview() ? 'two-col' : ''}`} id="vt-qo-body">
      <div class="vt-qo-results-pane">
        <Show when={rows().length === 0}>
          <div class="vt-vw-empty">
            {parsed().mode === 'port' && !deps.gateOk('ports')
              ? t('포트 대시보드를 사용할 수 없는 환경입니다.')
              : (parsed().mode === 'file' && fileLoading()) || (parsed().mode === 'scrollback' && scrollbackLoading()) || (parsed().mode === 'port' && portsLoading())
                ? t('불러오는 중…')
                : searchError()
                  ? searchError()
                  : t('일치하는 항목이 없습니다.')}
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
                  classList={{ selected: i() === selected(), 'vt-qo-setting': row.kind === 'setting' }}
                  onMouseEnter={() => setSelected(i())}
                  onClick={() => row.onOpen?.()}
                >
                  <span class="vt-qo-mark" aria-hidden="true" innerHTML={markFor(row)} />
                  <div class="vt-vw-name">
                    <Show when={row.sub} fallback={row.label}>
                      <span class="vt-qo-name">{row.label}</span>
                      <span class="vt-qo-sub">{row.sub}</span>
                    </Show>
                  </div>
                  <Show when={row.ctl}>
                    {(ctl) => (
                      <Show when={ctl().kind === 'bool'} fallback={
                        <span class="vt-qo-val" role="status" aria-label={`${row.label}: ${ctl().text}`}>
                          <span class="vt-qo-val-arrow" aria-hidden="true">‹</span>{ctl().text}<span class="vt-qo-val-arrow" aria-hidden="true">›</span>
                        </span>
                      }>
                        <span class="vt-qo-switch" classList={{ on: ctl().on }} role="switch" aria-checked={ctl().on} aria-label={row.label} />
                      </Show>
                    )}
                  </Show>
                  <Show when={row.hint}>
                    <Show when={row.hintHtml} fallback={<div class="vt-qo-hint">{row.hint}</div>}>
                      <div class="vt-qo-hint" aria-label={row.hint} innerHTML={row.hintHtml} />
                    </Show>
                  </Show>
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
                  <Show when={selectedSession()} fallback={
                    <Show when={previewText()} fallback={<div class="vt-vw-empty">{t('미리보기 없음')}</div>}>
                      <For each={previewText() || []}>{(line) => <div class="vt-qo-preview-line">{line}</div>}</For>
                    </Show>
                  }>
                    {(sess) => (
                      <div class="vt-qo-term-wrap">
                        <div class="vt-qo-preview-head"><span class="vt-qo-term-name">{sess().name}</span><span class="vt-qo-term-cmd">{sess().command || ''}</span></div>
                        <Show when={sessionPreview()?.name === sess().name && sessionPreview()?.content != null}
                              fallback={<div class="vt-vw-empty">{sessionPreview()?.name === sess().name && sessionPreview()?.content === null ? t('불러오는 중…') : t('미리보기 없음')}</div>}>
                          <div class="vt-qo-term" ref={(el) => {
                            createEffect(() => { const p = sessionPreview(); if (p && p.content != null) renderAnsi(el, p.content, { maxLines: 40 }); });
                          }} />
                        </Show>
                      </div>
                    )}
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
    </Show>
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
  let disposeKeys: (() => void) | null = null;
  onCleanup(() => disposeKeys?.());

  function onResize() { setWide(window.innerWidth >= 900); }
  window.addEventListener('resize', onResize);
  onCleanup(() => window.removeEventListener('resize', onResize));

  function close() {
    setVisible(false);
    disposeKeys?.(); disposeKeys = null;
  }

  function onKeydown(ev: KeyboardEvent) {
    if ((ev.target as Element)?.closest?.('.vt-dialog, button.card-meta')) return;
    if (ev.key === 'Escape') { ev.preventDefault(); close(); return; }
    // `?` 내용 검색 모드는 결과 이동·열기·옵션 키(⌥C/⌥W/⌥R, ⌥↵)를 스스로 처리한다. 포함·제외 칸에 쓰는 중에도 같다.
    if (bodyApi?.grepKey(ev)) { ev.preventDefault(); return; }
    if ((ev.target as Element)?.closest?.('.vt-gr-glob')) return;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); bodyApi?.selectAndFire(1); return; }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); bodyApi?.selectAndFire(-1); return; }
    if ((ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !ev.shiftKey) {
      if (bodyApi?.adjustSelected(ev.key === 'ArrowRight' ? 1 : -1)) { ev.preventDefault(); return; }
    }
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
    if (mode && mode !== 'default') {
      // `?`는 뒤에 공백을 하나 둔다 — 시안처럼 「? 글자」로 읽히고, 「?」로 시작하는 글자를 바로 이어 쓸 수 있다.
      setQuery(MODE_PREFIX[mode] + (mode === 'grep' ? ' ' : ''));
    } else {
      setQuery('');
    }
    setVisible(true);
    disposeKeys?.();
    disposeKeys = pushScope({ id: 'palette', modal: true, onKey: onKeydown, allowActions: ['palette', 'search', 'contentSearch'] });
  }

  const api: PaletteApi = { open, close, isOpen: visible };

  function Overlay() {
    let inputEl!: HTMLInputElement;
    createEffect(() => {
      if (visible()) requestAnimationFrame(() => inputEl?.focus());
    });
    const grepMode = createMemo(() => parseQuery(query()).mode === 'grep');
    return (
      <Show when={visible()}>
        <div class="vt-viewer-backdrop vt-qo-backdrop" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
          {/* vt-qo 스코프: 이 카드는 코드 뷰어와 껍데기(.vt-viewer-card)를
              공유하는데, 그쪽 치수(1100px)를 그대로 물려받아 팔레트가 화면
              절반을 덮고 있었다. 폭·행 높이·머리글은 이 클래스 안에서만
              다시 정한다(components.css). */}
          <div class="vt-viewer-card vt-qo-card" classList={{ 'vt-qo-card-grep': grepMode() }} role="dialog" aria-modal="true"
            aria-label={grepMode() ? t('파일 내용 검색') : t('빠른 열기')}>
            <div class="vt-viewer-head vt-qo-head">
              <span class="vt-qo-head-mark" aria-hidden="true" innerHTML={icon('search', 14, 2)} />
              <div class="vt-vw-title">{grepMode() ? t('파일 내용 검색') : t('빠른 열기')}</div>
              <span class="vt-qo-head-esc" aria-hidden="true">{t('esc 닫기')}</span>
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
              <span><kbd class="vt-qo-key" innerHTML={icon('arrow-up', 13, 2) + icon('arrow-down', 13, 2)} /> {t('이동')}</span>
              <span><kbd class="vt-qo-key" innerHTML={icon('key-enter', 13, 2)} /> {grepMode() ? t('그 줄로 열기') : t('열기')}</span>
              <span><kbd class="vt-qo-key" innerHTML={deps.displayComboHtml ? deps.displayComboHtml('Mod+Enter') : '⌘↵'} /> {t('새 페인')}</span>
              <Show when={grepMode() && deps.openSidebarSearch}>
                <span><kbd class="vt-qo-key" innerHTML={deps.displayComboHtml ? deps.displayComboHtml('Alt+Enter') : '⌥↵'} /> {t('사이드바 검색으로 옮기기')}</span>
              </Show>
              <span class="vt-qo-foot-modes">{grepMode() ? t('기본은 글자 그대로 · 정규식은 ⌥R') : MODE_HINT}</span>
            </div>
          </div>
        </div>
      </Show>
    );
  }

  render(Overlay, root);
  return api;
}
