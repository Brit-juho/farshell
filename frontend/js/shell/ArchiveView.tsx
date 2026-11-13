// 보관함(세션 로직 점검 단계 6 → 저장소 2차). 레일의 「보관함 N ›」을 누르면 같은 자리의 목록이 이 화면으로 바뀐다.
//
//   ‹ 세션   보관함 12
//   마지막 활동 뒤 30일이 지난 끝난 대화는 여기로 옮겨집니다. 자동으로 지우지는 않습니다.
//   [🔍 제목 · 지시 · 폴더 검색]
//   (farshell 8) (acme 3)                 ← 저장소 칩
//   [폴더] farshell                         8
//     ◌ 대화 제목 …               35일 전  [해제] ⋯
//   … 더 보기
//
// 보관함은 **대화** 단위다(「종료 N」과 같은 칸 — ProjectEnded.tsx ConversationItem). 검색(`q`)과 저장소(`project`)는 서버가
// 거른다(/api/session-catalog/conversations?archived=true). 대화 기록을 지우는 것은 「대화 보관함」(기록 삭제)에서 한다.
// 다른 맥을 보고 있으면 이 맥의 기록이 아니므로 「이 맥에서만 볼 수 있음」.
import { For, Show, createMemo, createSignal, onCleanup } from 'solid-js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { historyApi } from '../history/api.js';
import { ConversationItem, fetchConversations, type EndedConversation } from './ProjectEnded.js';

const PAGE = 50;

export interface CatalogSnapshot {
  host: string; revision: number; projects: any[]; workspaces: any[]; sessions: any[]; next: string | null; relations?: any[];
  auto_archive_days?: number; archived_count?: number; auto_name?: boolean; resume_ends_session?: boolean;
  project_counts?: Record<string, number>;
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

/** 보관한 대화를 저장소별로 묶는다 — 칩 순서와 같게 개수 많은 순. */
export function groupConversations(items: EndedConversation[], names: Map<string, { name: string; path: string }>,
  counts: Record<string, number>): { key: string; name: string; root: string | null; items: EndedConversation[] }[] {
  const groups = new Map<string, { key: string; name: string; root: string | null; items: EndedConversation[] }>();
  for (const c of items) {
    const key = c.project_id || '_none';
    if (!groups.has(key)) {
      const p = names.get(key);
      groups.set(key, { key, name: p?.name || (key === '_none' ? '' : t('제거한 저장소')), root: p?.path || null, items: [] });
    }
    groups.get(key)!.items.push(c);
  }
  return [...groups.values()].sort((a, b) => (counts[b.key] ?? b.items.length) - (counts[a.key] ?? a.items.length) || a.name.localeCompare(b.name));
}

export function ArchiveView(p: { remote: boolean; onBack: () => void; onChanged: () => void }) {
  const [items, setItems] = createSignal<EndedConversation[] | null>(null);
  const [next, setNext] = createSignal<string | null>(null);
  const [q, setQ] = createSignal('');
  const [project, setProject] = createSignal('');
  const [error, setError] = createSignal('');
  const [days, setDays] = createSignal(0);
  const [names, setNames] = createSignal(new Map<string, { name: string; path: string }>());
  // 칩(저장소·개수)은 검색어와 상관없이 처음 받은 전체 기준을 유지한다 — 검색할 때마다 칩이 사라지지 않게.
  const [counts, setCounts] = createSignal<Record<string, number>>({});
  let request = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => clearTimeout(timer));

  const load = async (more = false) => {
    if (p.remote) return;
    const mine = ++request;
    const params: Record<string, string> = { archived: 'true' };
    if (q().trim()) params.q = q().trim();
    if (project()) params.project = project();
    try {
      const page = await fetchConversations(params, more ? next() : null, PAGE);
      if (mine !== request) return;
      setError('');
      setItems(more && items() ? [...items()!, ...page.items.filter((x) => !items()!.some((y) => y.id === x.id))] : page.items);
      setNext(page.next);
      if (page.counts && !q().trim() && !project()) setCounts(page.counts);
    } catch (e: any) {
      if (mine === request) setError(e?.message || t('보관함을 읽지 못했습니다'));
    }
  };
  const loadMeta = async () => {
    if (p.remote) return;
    try {
      const [list, settings] = await Promise.all([
        historyApi('/api/session-catalog/projects?include_hidden=true'),
        historyApi('/api/session-catalog?limit=1&meta=false').catch(() => null),
      ]);
      setNames(new Map<string, { name: string; path: string }>((list?.projects || []).map((x: any) => [String(x.id), { name: String(x.name), path: String(x.path) }])));
      setDays(Number(settings?.auto_archive_days) || 0);
    } catch { /* 이름이 없으면 「제거한 저장소」로 */ }
  };
  void load();
  void loadMeta();
  const changed = () => { p.onChanged(); void load(); };
  window.addEventListener('vt:catalog-changed', changed);
  onCleanup(() => window.removeEventListener('vt:catalog-changed', changed));
  // 조건을 바꾸면 그 즉시 진행 중인 요청을 무효로 하고 「더 보기」 커서를 지운다 — 300ms 안에 「더 보기」를 누르면
  // 새 검색어 + 옛 검색의 커서로 받아 두 결과가 섞였다. 새 첫 쪽이 올 때까지 「더 보기」는 없다.
  const invalidate = () => { request++; setNext(null); };
  const onSearch = (v: string) => { setQ(v); invalidate(); clearTimeout(timer); timer = setTimeout(() => { void load(); }, 300); };
  const pick = (id: string) => { setProject(project() === id ? '' : id); invalidate(); void load(); };
  const chips = createMemo(() => Object.entries(counts()).map(([id, count]) => ({
    id, count: Number(count) || 0, name: names().get(id)?.name || t('제거한 저장소'), root: names().get(id)?.path || null,
  })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)));
  const groups = createMemo(() => groupConversations(items() || [], names(), counts()));
  const total = () => Object.values(counts()).reduce((a, b) => a + (Number(b) || 0), 0);
  const filtered = () => !!q().trim() || !!project();

  return (
    <div class="vt-archive">
      <div class="vt-wgrail-group-head vt-catalog-archive-head">
        <button type="button" class="vt-catalog-back" onClick={p.onBack}>
          <span innerHTML={icon('chevron-left', 12, 2)} /><span>{t('세션')}</span>
        </button>
        <span class="vt-wgrail-group-label">{t('보관함')}<span class="vt-wgrail-group-count">{total()}</span></span>
      </div>
      <Show when={!p.remote} fallback={<p class="vt-wgrail-note">{t('보관함은 이 맥에서만 볼 수 있습니다. 다른 맥의 보관함은 그 맥에서 여세요.')}</p>}>
        <Show when={days() > 0}>
          <p class="vt-wgrail-note">{t('마지막 활동 뒤 {n}일이 지난 끝난 대화는 여기로 옮겨집니다. 자동으로 지우지는 않습니다.', { n: days() })}</p>
        </Show>
        <div class="vt-archive-search">
          <span class="vt-archive-search-ico" innerHTML={icon('search', 14, 2)} />
          <input type="search" class="vt-input" value={q()} placeholder={t('제목 · 지시 · 폴더 검색')}
            aria-label={t('보관함 검색')} onInput={(e) => onSearch(e.currentTarget.value)} />
        </div>
        <Show when={chips().length > 1}>
          <div class="vt-archive-chips" role="group" aria-label={t('저장소로 거르기')}>
            <For each={chips()}>{(c) => (
              <button type="button" class="vt-archive-chip" aria-pressed={project() === c.id} data-tip={c.root || undefined} data-tip-side="bottom"
                onClick={() => pick(c.id)}>
                <span innerHTML={shellIcon('folder', 12, 2)} />
                <span class="vt-archive-chip-name">{c.name}</span><span class="vt-archive-chip-n">{c.count}</span>
              </button>
            )}</For>
          </div>
        </Show>
        <Show when={items() && items()!.length === 0}>
          <p class="vt-wgrail-note">{filtered() ? t('조건에 맞는 보관 대화가 없습니다.') : t('보관된 대화가 없습니다.')}</p>
        </Show>
        <For each={groups().map((g) => g.key)}>{(key) => (
          <Show when={groups().find((g) => g.key === key)}>
            {(g) => (
              <section class="vt-rg">
                <div class="vt-rg-row vt-rg-head vt-archive-head" data-tip={g().root || undefined} data-tip-side="right">
                  <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={'<line x1="21" y1="26" x2="21" y2="100%"/>'} />
                    <span class="vt-rg-ic" innerHTML={shellIcon('folder', 13, 2)} /></span>
                  <span class="vt-rg-body vt-rg-a">
                    <span class="vt-rg-rn">{g().name}</span>
                    <span class="vt-rg-sp" /><span class="vt-rg-tm">{counts()[g().key] ?? g().items.length}</span>
                  </span>
                </div>
                <For each={g().items.map((c) => c.id)}>{(id, i) => (
                  <Show when={g().items.find((c) => c.id === id)}>
                    {(c) => <ConversationItem conversation={c()} last={i() === g().items.length - 1} />}
                  </Show>
                )}</For>
              </section>
            )}
          </Show>
        )}</For>
        <Show when={next()}>
          <button type="button" class="vt-catalog-more-link" onClick={() => { void load(true); }}>{t('더 보기')}</button>
        </Show>
      </Show>
      <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
    </div>
  );
}
