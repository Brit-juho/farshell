// 보관함(세션 로직 점검 단계 6, 시안 5차 P3). 레일의 「보관함 N ›」을 누르면 같은 자리의 목록이 이 화면으로 바뀐다.
//
//   ‹ 세션   보관함 12                    [비우기]
//   마지막 활동 뒤 30일이 지난 종료 세션은 여기로 옮겨집니다. 자동으로 지우지는 않습니다.
//   [🔍 이름 · 대화 · 브랜치 · 지시 · 폴더 검색]
//   (전체 12) (farshell 8) (acme 3) (저장소 밖 1)      ← 저장소(루트 폴더) 칩
//   [폴더] farshell                         8
//     ◌ 제목 …                    35일 전  [해제] ⋯
//   … 더 보기
//
// 검색(`q`)과 저장소(`project`)는 서버가 거른다 — 200개 한 쪽 밖의 오래된 기록도 찾는다(order=activity, 커서로 넘김).
// 다른 맥을 보고 있으면 이 맥의 카탈로그가 아니므로 「이 맥에서만 볼 수 있음」.
import { For, Show, createMemo, createSignal, onCleanup } from 'solid-js';
import { t } from '../core/i18n.js';
import { confirmDialog } from '../core/dialog-lazy.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { historyApi } from '../history/api.js';
import { deleteMany } from '../history/session-actions.js';
import { EndedItem } from './EndedFold.js';

const PAGE = 50;

export interface CatalogSnapshot {
  host: string; revision: number; projects: any[]; workspaces: any[]; sessions: any[]; next: string | null; relations?: any[];
  auto_archive_days?: number; archived_count?: number; auto_name?: boolean; resume_ends_session?: boolean;
  project_counts?: Record<string, number>;
}

/**
 * 카탈로그 쪽 합치기(레일·폰 공용). 목록은 첫 쪽(최근 활동순 200개)만 폴링하고, 「종료 세션 더 불러오기」로 받은 뒤쪽은
 * - more: 새 쪽을 뒤에 붙인다(이미 있는 id는 건너뜀).
 * - 폴링(more 아님)인데 뒤쪽을 받아 둔 적이 있으면(extra): 첫 쪽을 새로 받되, 첫 쪽보다 오래된 받아 둔 기록은 버리지 않고
 *   커서(next)도 받아 둔 끝 자리를 유지한다. 사라진 기록은 목록을 다시 열 때 정리된다.
 */
export function mergeCatalogPage(prev: CatalogSnapshot | null, data: CatalogSnapshot, more: boolean, extra: boolean): CatalogSnapshot {
  if (more && prev) {
    const have = new Set(prev.sessions.map((x) => x.id));
    return { ...data, sessions: [...prev.sessions, ...data.sessions.filter((x) => !have.has(x.id))] };
  }
  if (prev && extra) {
    const fresh = new Set(data.sessions.map((x) => x.id));
    const oldest = data.sessions.length ? Math.min(...data.sessions.map((y) => y.last_activity ?? 0)) : Infinity;
    const tail = prev.sessions.filter((x) => !fresh.has(x.id) && (x.last_activity ?? 0) <= oldest);
    return { ...data, sessions: [...data.sessions, ...tail], next: prev.next };
  }
  return data;
}

/** 세션 목록 맨 아래 한 줄. 누르면 같은 자리가 보관함 화면으로 바뀐다(레일·폰 공용). */
export function ArchiveEntry(p: { count: number; onOpen: () => void }) {
  return <button type="button" class="vt-catalog-archive-link" onClick={p.onOpen}>
    <span class="vt-catalog-archive-mark" innerHTML={icon('archive', 14, 2)} />
    <span class="vt-catalog-archive-label">{t('보관함')}</span>
    <span class="vt-wgrail-group-count">{p.count}</span>
    <span class="vt-catalog-archive-go" innerHTML={icon('chevron-right', 12, 2)} />
  </button>;
}

export interface ArchivePage { sessions: any[]; projects?: any[]; workspaces?: any[]; project_counts?: Record<string, number>;
  archived_count?: number; auto_archive_days?: number; next: string | null }

/** 검색 결과를 저장소(루트 폴더)별로 묶는다 — 칩 순서와 같게 개수 많은 순, 저장소 밖은 맨 아래. */
export function groupArchive(page: ArchivePage): { key: string; name: string; root: string | null; sessions: any[] }[] {
  const ws = new Map((page.workspaces || []).map((w: any) => [w.id, w]));
  const projects = new Map((page.projects || []).map((p: any) => [p.id, p]));
  const groups = new Map<string, { key: string; name: string; root: string | null; sessions: any[] }>();
  for (const s of page.sessions) {
    const w: any = s.workspace_id ? ws.get(s.workspace_id) : null;
    const p: any = w?.project_id ? projects.get(w.project_id) : null;
    const key = p && p.kind === 'git' ? p.id : '_none';
    if (!groups.has(key)) groups.set(key, { key, name: key === '_none' ? '' : p.name, root: key === '_none' ? null : p.path, sessions: [] });
    groups.get(key)!.sessions.push(s);
  }
  return [...groups.values()].sort((a, b) => (a.key === '_none' ? 1 : 0) - (b.key === '_none' ? 1 : 0)
    || (page.project_counts?.[b.key] ?? b.sessions.length) - (page.project_counts?.[a.key] ?? a.sessions.length) || a.name.localeCompare(b.name));
}

export function ArchiveView(p: { remote: boolean; onBack: () => void; onChanged: () => void }) {
  const [page, setPage] = createSignal<ArchivePage | null>(null);
  const [q, setQ] = createSignal('');
  const [project, setProject] = createSignal('');
  const [error, setError] = createSignal('');
  const [busy, setBusy] = createSignal(false);
  // 칩(저장소 목록·개수)은 검색어와 상관없이 처음 받은 전체 기준을 유지한다 — 검색할 때마다 칩이 사라지지 않게.
  const [chips, setChips] = createSignal<{ id: string; name: string; root: string | null; count: number }[]>([]);
  let request = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => clearTimeout(timer));

  const load = async (more = false) => {
    if (p.remote) return;
    const mine = ++request;
    const params = new URLSearchParams({ archived: 'true', order: 'activity', limit: String(PAGE) });
    if (q().trim()) params.set('q', q().trim());
    if (project()) params.set('project', project());
    if (more && page()?.next) params.set('cursor', page()!.next!);
    try {
      const data: ArchivePage = await historyApi('/api/session-catalog?' + params);
      if (mine !== request) return;
      setError('');
      setPage(more && page() ? { ...data, sessions: [...page()!.sessions, ...data.sessions] } : data);
      if (!q().trim() && !project()) {
        const projects = new Map((data.projects || []).map((x: any) => [x.id, x]));
        setChips(Object.entries(data.project_counts || {}).map(([id, count]) => ({
          id, count: Number(count) || 0,
          name: id === '_none' ? t('저장소 밖') : (projects.get(id) as any)?.name || id,
          root: id === '_none' ? null : (projects.get(id) as any)?.path || null,
        })).sort((a, b) => (a.id === '_none' ? 1 : 0) - (b.id === '_none' ? 1 : 0) || b.count - a.count || a.name.localeCompare(b.name)));
      }
    } catch (e: any) {
      if (mine === request) setError(e?.message || t('보관함을 읽지 못했습니다'));
    }
  };
  void load();
  const changed = () => { p.onChanged(); void load(); };
  // 조건을 바꾸면 그 즉시 진행 중인 요청을 무효로 하고 「더 보기」 커서를 지운다 — 300ms 안에 「더 보기」를 누르면
  // 새 검색어 + 옛 검색의 커서로 받아 두 결과가 섞였다. 새 첫 쪽이 올 때까지 「더 보기」는 없다.
  const invalidate = () => { request++; const cur = page(); if (cur && cur.next) setPage({ ...cur, next: null }); };
  const onSearch = (v: string) => { setQ(v); invalidate(); clearTimeout(timer); timer = setTimeout(() => { void load(); }, 300); };
  const pick = (id: string) => { setProject(project() === id ? '' : id); invalidate(); void load(); };
  const groups = createMemo(() => (page() ? groupArchive(page()!) : []));
  const total = () => page()?.archived_count ?? 0;
  const days = () => page()?.auto_archive_days ?? 0;
  const filtered = () => !!q().trim() || !!project();

  const empty = async () => {
    const list = page()?.sessions || [];
    if (busy() || !list.length) return;
    if (!await confirmDialog(t('보관함의 세션 기록 {n}개를 삭제합니다. 대화 보관함의 대화와 원본은 남고, 위임 관계가 있는 세션은 삭제하지 않습니다. 계속할까요?', { n: list.length }), { title: t('세션 기록 삭제'), okLabel: t('삭제') })) return;
    setBusy(true); setError('');
    try {
      const { failed } = await deleteMany(list);
      if (failed) setError(t('{n}개는 삭제하지 못해 남겼습니다. 위임 관계가 있거나 다른 화면에서 바뀐 세션입니다', { n: failed }));
      changed();
    } finally { setBusy(false); }
  };

  return (
    <div class="vt-archive">
      <div class="vt-wgrail-group-head vt-catalog-archive-head">
        <button type="button" class="vt-catalog-back" onClick={p.onBack}>
          <span innerHTML={icon('chevron-left', 12, 2)} /><span>{t('세션')}</span>
        </button>
        <span class="vt-wgrail-group-label">{t('보관함')}<span class="vt-wgrail-group-count">{total()}</span></span>
        {/* 비우기는 거르지 않은 목록에서만 — 검색 결과만 지우는지 전체를 지우는지 헷갈리지 않게. */}
        <Show when={!p.remote && !filtered() && (page()?.sessions.length || 0) > 0}>
          <button type="button" class="vt-btn sm" disabled={busy()} onClick={empty}>{busy() ? t('삭제하는 중…') : t('비우기')}</button>
        </Show>
      </div>
      <Show when={!p.remote} fallback={<p class="vt-wgrail-note">{t('보관함은 이 맥에서만 볼 수 있습니다. 다른 맥의 보관함은 그 맥에서 여세요.')}</p>}>
        <Show when={days() > 0}>
          <p class="vt-wgrail-note">{t('마지막 활동 뒤 {n}일이 지난 종료 세션은 여기로 옮겨집니다. 자동으로 지우지는 않습니다.', { n: days() })}</p>
        </Show>
        <div class="vt-archive-search">
          <span class="vt-archive-search-ico" innerHTML={icon('search', 14, 2)} />
          <input type="search" class="vt-input" value={q()} placeholder={t('이름 · 대화 · 브랜치 · 지시 · 폴더 검색')}
            aria-label={t('보관함 검색')} onInput={(e) => onSearch(e.currentTarget.value)} />
        </div>
        <Show when={chips().length > 1}>
          <div class="vt-archive-chips" role="group" aria-label={t('저장소로 거르기')}>
            <For each={chips()}>{(c) => (
              <button type="button" class="vt-archive-chip" aria-pressed={project() === c.id} data-tip={c.root || undefined} data-tip-side="bottom"
                onClick={() => pick(c.id)}>
                <span innerHTML={shellIcon(c.id === '_none' ? 'home' : 'folder', 12, 2)} />
                <span class="vt-archive-chip-name">{c.name}</span><span class="vt-archive-chip-n">{c.count}</span>
              </button>
            )}</For>
          </div>
        </Show>
        <Show when={page() && page()!.sessions.length === 0}>
          <p class="vt-wgrail-note">{filtered() ? t('조건에 맞는 보관 세션이 없습니다.') : t('보관된 세션이 없습니다.')}</p>
        </Show>
        <For each={groups().map((g) => g.key)}>{(key) => (
          <Show when={groups().find((g) => g.key === key)}>
            {(g) => (
              <section class="vt-rg" classList={{ outside: g().key === '_none' }}>
                <div class="vt-rg-row vt-rg-head vt-archive-head" data-tip={g().root || undefined} data-tip-side="right">
                  <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={'<line x1="21" y1="26" x2="21" y2="100%"/>'} />
                    <span class="vt-rg-ic" innerHTML={shellIcon(g().key === '_none' ? 'home' : 'folder', 13, 2)} /></span>
                  <span class="vt-rg-body vt-rg-a">
                    <span class="vt-rg-rn">{g().key === '_none' ? t('저장소 밖') : g().name}</span>
                    <span class="vt-rg-sp" /><span class="vt-rg-tm">{page()?.project_counts?.[g().key] ?? g().sessions.length}</span>
                  </span>
                </div>
                <For each={g().sessions.map((s) => s.id)}>{(id, i) => (
                  <Show when={g().sessions.find((s) => s.id === id)}>
                    {(s) => <EndedItem session={s()} last={i() === g().sessions.length - 1} onChanged={changed} />}
                  </Show>
                )}</For>
              </section>
            )}
          </Show>
        )}</For>
        <Show when={page()?.next}>
          <button type="button" class="vt-catalog-more-link" onClick={() => { void load(true); }}>{t('더 보기')}</button>
        </Show>
      </Show>
      <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
    </div>
  );
}
