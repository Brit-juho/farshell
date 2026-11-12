// 「새 세션」 메뉴 본체(세션 로직 점검 단계 6, 시안 5차 P5). core의 `showAddMenu`(term/session.js)는 스텁이고,
// 이 파일은 shell 지연 청크다 — 처음 열 때만 받는다.
//
//   새 세션
//   [🔍 저장소 찾기]
//   최근 저장소
//     [폴더] farshell        ⑂ dev                         farshell-3   ← 오른쪽 회색 = 열릴 tmux 이름
//     [갈래] farshell · 워크트리  feat/x                     farshell-x
//   ─
//     [집] 홈 폴더                                           home
//     [>_] 일반 터미널  tmux 아님 · 화면을 닫으면 끝남
//   ─
//     [사람] 이번만 다른 계정으로  기본: …
//   오른쪽 회색 글자가 새 세션 이름입니다.
//
// 이름은 만들 때와 같은 규칙의 미리보기다(GET /api/tmux/name-preview — 시작 폴더의 저장소 이름, 겹치면 -2·-3).
// ⚠ shell 청크라 core 모듈(store·settings·session)을 정적 import하지 않는다 — 만들기·계정 동작은 부르는 쪽(deps)이 준다.
import { For, Show, createEffect, createMemo, createSignal, onMount } from 'solid-js';
import { render } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';

export interface NewSessionDeps {
  anchor: HTMLElement | null;
  /** JSON GET(원격 맥을 보고 있으면 그 맥으로 중계되는 길). */
  getJson: (path: string) => Promise<any>;
  createTmux: (opts?: { cwd?: string; profile?: string }) => Promise<unknown> | unknown;
  createPlain: () => Promise<unknown> | unknown;
  /** 다른 맥을 보고 있으면 계정 줄을 숨긴다(그 맥의 계정이 아니다). */
  relayed: boolean;
  /** 계정 줄. line은 지금 캐시로 읽은 글(없으면 줄을 숨긴다), refresh는 낡은 목록을 다시 받는다 — 받으면 줄을 다시 그린다. */
  accounts?: {
    line: () => string | null;
    refresh?: () => Promise<unknown>;
    openOnce: (anchor: HTMLElement | null, onPick: (id: string) => void) => void;
  } | null;
}

export interface RepoChoice { key: string; path: string; label: string; branch: string | null; linked: boolean; sessions: number }

/** /api/repos 응답 → 고를 수 있는 작업 트리. 세션이 많은(최근에 쓴) 저장소가 먼저, 메인 작업 트리가 그 저장소의 워크트리보다 먼저. */
export function repoChoices(data: any): RepoChoice[] {
  const out: RepoChoice[] = [];
  for (const repo of Array.isArray(data?.repos) ? data.repos : []) {
    const name = typeof repo.name === 'string' ? repo.name : '';
    for (const wt of Array.isArray(repo.worktrees) ? repo.worktrees : []) {
      if (typeof wt.path !== 'string' || !wt.path) continue;
      out.push({
        key: wt.id || wt.path, path: wt.path, branch: typeof wt.branch === 'string' ? wt.branch : null,
        linked: !wt.isMain, label: wt.isMain ? (name || wt.repoName || wt.path.split('/').pop()) : `${name || wt.repoName} · ${t('워크트리')}`,
        sessions: Array.isArray(wt.sessions) ? wt.sessions.length : 0,
      });
    }
  }
  const repoWeight = new Map<string, number>();
  for (const c of out) { const r = c.label.split(' · ')[0]; repoWeight.set(r, (repoWeight.get(r) || 0) + c.sessions); }
  return out.sort((a, b) => {
    const ra = a.label.split(' · ')[0], rb = b.label.split(' · ')[0];
    return (repoWeight.get(rb) || 0) - (repoWeight.get(ra) || 0) || ra.localeCompare(rb)
      || (a.linked ? 1 : 0) - (b.linked ? 1 : 0) || b.sessions - a.sessions || a.path.localeCompare(b.path);
  });
}

export function filterChoices(list: RepoChoice[], q: string): RepoChoice[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return list;
  return list.filter((c) => [c.label, c.branch || '', c.path].some((v) => v.toLowerCase().includes(needle)));
}

const MAX_SHOWN = 8;

function Menu(p: { deps: NewSessionDeps; close: () => void }) {
  const [choices, setChoices] = createSignal<RepoChoice[] | null>(null);
  const [q, setQ] = createSignal('');
  const [names, setNames] = createSignal<Record<string, string>>({});
  const [error, setError] = createSignal('');
  let root: HTMLDivElement | undefined;
  let search: HTMLInputElement | undefined;
  const shown = createMemo(() => filterChoices(choices() || [], q()).slice(0, MAX_SHOWN));
  const asked = new Set<string>();
  // 다른 맥을 보고 있으면 묻지 않는다 — 미리보기 경로는 중계 허용 목록에 넣지 않았다(이름만 비고 만들기는 된다).
  const preview = (key: string, cwd: string | null) => {
    if (p.deps.relayed || asked.has(key)) return;
    asked.add(key);
    p.deps.getJson('/api/tmux/name-preview' + (cwd ? '?cwd=' + encodeURIComponent(cwd) : ''))
      .then((d) => { if (d && typeof d.name === 'string') setNames({ ...names(), [key]: d.name }); })
      .catch(() => { /* 미리보기는 덤이다 — 실패해도 만들기는 된다 */ });
  };
  // 계정 목록은 캐시(15초)라 처음 열 때 비어 있을 수 있다 — 받아 오면 줄을 다시 그린다(예전에는 다음에 열 때야 보였다).
  const [accountLine, setAccountLine] = createSignal<string | null>(p.deps.relayed ? null : p.deps.accounts?.line() ?? null);
  onMount(() => {
    search?.focus();
    preview('__home__', null);
    if (!p.deps.relayed && p.deps.accounts?.refresh) {
      p.deps.accounts.refresh().then(() => setAccountLine(p.deps.accounts?.line() ?? null)).catch(() => { /* 계정 줄은 덤이다 */ });
    }
    p.deps.getJson('/api/repos').then((d) => setChoices(repoChoices(d))).catch((e) => { setChoices([]); setError(e?.message || ''); });
  });
  // 보이는 저장소만 이름을 묻는다(검색으로 바뀌면 새로 보이는 것만).
  const ensureNames = () => { for (const c of shown()) preview(c.key, c.path); };
  createEffect(ensureNames);

  const run = (fn: () => unknown) => { p.close(); void Promise.resolve().then(fn); };
  const items = () => Array.from(root?.querySelectorAll<HTMLElement>('.vt-menu-item') || []);
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); p.close(); p.deps.anchor?.focus?.(); return; }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const all = items();
    const i = all.indexOf(document.activeElement as HTMLElement);
    all[(i + (e.key === 'ArrowDown' ? 1 : -1) + all.length + (i < 0 && e.key === 'ArrowUp' ? 1 : 0)) % all.length]?.focus();
  };
  const onSearchKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); const first = shown()[0]; if (first) run(() => p.deps.createTmux({ cwd: first.path })); }
  };
  const Item = (x: { iconHtml: string; label: string; sub?: string | null; mono?: boolean; name?: string | null; onRun: () => void; caret?: boolean }) => (
    <div class="vt-menu-item two vt-nsm-item" role="menuitem" tabindex="-1"
      onClick={x.onRun} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); x.onRun(); } }}>
      <span class="vt-menu-item-ico" innerHTML={x.iconHtml} />
      <span class="vt-menu-item-body">
        <span class="vt-menu-item-label">{x.label}</span>
        <Show when={x.sub}><span class="vt-menu-item-sub" classList={{ mono: !!x.mono }}>{x.sub}</span></Show>
      </span>
      <Show when={x.name}><span class="vt-nsm-name" aria-label={t('새 세션 이름 {name}', { name: x.name! })}>{x.name}</span></Show>
      <Show when={x.caret}><span class="vt-nsm-caret" innerHTML={icon('chevron-right', 13, 2)} /></Show>
    </div>
  );
  return (
    <div ref={root} id="add-menu" class="vt-menu vt-nsm" role="menu" aria-label={t('새 세션')} onKeyDown={onKey}>
      <div class="vt-nsm-head">{t('새 세션')}</div>
      <div class="vt-nsm-search">
        <span innerHTML={icon('search', 14, 2)} />
        <input ref={search} type="search" class="vt-input" placeholder={t('저장소 찾기')} aria-label={t('저장소 찾기')}
          value={q()} onInput={(e) => setQ(e.currentTarget.value)} onKeyDown={onSearchKey} />
      </div>
      <Show when={choices() === null}><div class="vt-nsm-note">{t('불러오는 중…')}</div></Show>
      <Show when={(choices() || []).length > 0}>
        <div class="vt-nsm-sec">{q().trim() ? t('찾은 저장소') : t('최근 저장소')}</div>
        <For each={shown()}>{(c) => (
          <Item iconHtml={shellIcon(c.linked ? 'git-fork' : 'folder', 15, 2)} label={c.label} sub={c.branch} mono
            name={names()[c.key] || null} onRun={() => run(() => p.deps.createTmux({ cwd: c.path }))} />
        )}</For>
        <Show when={q().trim() && shown().length === 0}><div class="vt-nsm-note">{t('맞는 저장소가 없습니다.')}</div></Show>
      </Show>
      <div class="vt-menu-sep" role="separator" />
      <Item iconHtml={shellIcon('home', 15, 2)} label={t('홈 폴더')} name={names().__home__ || null} onRun={() => run(() => p.deps.createTmux())} />
      <Item iconHtml={icon('terminal', 15, 2)} label={t('일반 터미널')} sub={t('tmux 아님 · 화면을 닫으면 끝남')} onRun={() => run(() => p.deps.createPlain())} />
      <Show when={!p.deps.relayed && accountLine()}>
        <div class="vt-menu-sep" role="separator" />
        <Item iconHtml={icon('user', 15, 2)} label={t('이번만 다른 계정으로')} sub={accountLine()!} caret
          onRun={() => { const anchor = p.deps.anchor; p.close(); p.deps.accounts!.openOnce(anchor, (id) => { void p.deps.createTmux({ profile: id }); }); }} />
      </Show>
      <Show when={!p.deps.relayed}><div class="vt-nsm-foot">{t('오른쪽 회색 글자가 새 세션 이름입니다.')}</div></Show>
      <Show when={error()}><div class="vt-nsm-note" role="status">{error()}</div></Show>
    </div>
  );
}

let _dispose: (() => void) | null = null;

/** 메뉴를 연다(이미 열려 있으면 닫는다 — 같은 버튼 두 번 누르기). 누른 버튼 옆에 뜨고 뷰포트 안으로 당긴다. */
export function openNewSessionMenu(deps: NewSessionDeps): void {
  if (_dispose) { _dispose(); return; }
  const host = document.createElement('div');
  document.body.appendChild(host);
  let disposeRender: () => void = () => {};
  const close = () => {
    if (!_dispose) return;
    _dispose = null;
    document.removeEventListener('mousedown', onDoc, true);
    disposeRender();
    host.remove();
  };
  const onDoc = (e: MouseEvent) => { const m = host.firstElementChild; if (m && !m.contains(e.target as Node) && !deps.anchor?.contains(e.target as Node)) close(); };
  _dispose = close;
  disposeRender = render(() => <Menu deps={deps} close={close} />, host);
  document.addEventListener('mousedown', onDoc, true);
  place(host.firstElementChild as HTMLElement, deps.anchor);
}

function place(menu: HTMLElement | null, el: HTMLElement | null) {
  if (!menu) return;
  const r = el ? el.getBoundingClientRect() : { left: 8, right: 8, top: 44, bottom: 44 } as DOMRect;
  // 사이드바 머리의 「+」는 그 아래 목록을 덮지 않게 사이드바 바깥(오른쪽)으로 연다.
  const inHead = !!el?.closest?.('.vt-wgrail-head');
  const panelRight = inHead ? (el!.closest('.vt-sidepanel') || el!).getBoundingClientRect().right : 0;
  menu.style.right = 'auto';
  menu.style.left = `${Math.round(inHead ? panelRight + 6 : r.left)}px`;
  menu.style.top = `${Math.round(inHead ? r.top : r.bottom + 6)}px`;
  const fit = () => {
    const h = menu.offsetHeight;
    if (!inHead && r.bottom + 6 + h > window.innerHeight) menu.style.top = `${Math.round(r.top - h - 6)}px`;
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) menu.style.left = `${Math.max(8, Math.round(window.innerWidth - 8 - rect.width))}px`;
    if (rect.bottom > window.innerHeight - 8) menu.style.top = `${Math.max(8, Math.round(window.innerHeight - 8 - rect.height))}px`;
  };
  fit();
  // 저장소 목록이 도착해 메뉴가 커지면 다시 당긴다.
  if (typeof ResizeObserver === 'function') {
    const ro = new ResizeObserver(fit);
    ro.observe(menu);
    const stop = new MutationObserver(() => { if (!menu.isConnected) { ro.disconnect(); stop.disconnect(); } });
    stop.observe(document.body, { childList: true });
  }
}
