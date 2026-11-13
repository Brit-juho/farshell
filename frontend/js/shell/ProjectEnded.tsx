// 저장소의 「종료 N」 — 그 저장소에서 있었던 끝난 대화(저장소 2차, 설계 §3.5). 세션이 아니라 대화라서, 터미널 세션이 어디로
// 가든 기록은 대화를 켠 저장소에 남는다.
//
//   ◌ 종료 12 · 마지막 3시간 전            ← 누르면 펼침(기기별 기억), 펼칠 때 처음 20개를 받는다
//   ◌ [마크] 대화 제목        3시간 전  [▶ 이어서]  ⋯
//        ⑂ fix/x · 지시 5
//        「키보드 올라올 때 입력창 가려지는 것부터 고쳐줘」
//      (칸을 누르면) 「이어서 열면 farshell에서 farshell-3 세션으로 열립니다」 + [대화 보기]
//   [더 보기]
//
// 「이어서」 버튼은 늘 보인다. 메뉴: 이어서 시작 · 대화 보기(대화 보관함) · 대화 보관(보관함에서는 보관 해제).
// 같은 칸을 보관함(ArchiveView)과 폰 「종료 N」 시트가 쓴다. 이 맥 전용 API라 다른 맥을 볼 때는 그리지 않는다(호출부가 거른다).
import { For, Show, createEffect, createSignal, on, onCleanup } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { agentMark } from './agent-mark.js';
import { Menu, type MenuItem } from './RailRow.js';
import { endLines } from './RepoBranch.js';
import { historyApi } from '../history/api.js';
import { resumeConversation, setConversationArchived, agoLabel } from '../history/session-actions.js';

export const ENDED_PAGE = 20;

export interface EndedConversation {
  id: string; native_id?: string; provider: string; title: string; cwd?: string; project_id?: string;
  activity: number | null; started?: number | null; archived?: number | null; branch?: string | null;
  last_user_text?: string; user_message_count?: number;
}

const itemLines = (last: boolean) => (last ? '<line x1="21" y1="0" x2="21" y2="12"/>' : endLines(false));

/** 대화 목록 한 쪽을 받는다(커서 이어 받기). params는 project·archived·q. */
export async function fetchConversations(params: Record<string, string>, cursor?: string | null) {
  const q = new URLSearchParams({ limit: String(ENDED_PAGE), ...params });
  if (cursor) q.set('cursor', cursor);
  const data = await historyApi('/api/session-catalog/conversations?' + q);
  return { items: (Array.isArray(data?.conversations) ? data.conversations : []) as EndedConversation[],
           next: typeof data?.next === 'string' ? data.next : null, counts: data?.project_counts as Record<string, number> | undefined };
}

/** 펼친 「종료 N」의 대화들 — 펼칠 때 처음 쪽을 받고, 「더 보기」로 이어 받고, 기록이 바뀌면(vt:catalog-changed) 다시 받는다. */
export function ConversationList(p: { params: Record<string, string>; reloadKey?: unknown; lastLine?: boolean; empty?: string }) {
  const [items, setItems] = createSignal<EndedConversation[] | null>(null);
  const [next, setNext] = createSignal<string | null>(null);
  const [error, setError] = createSignal('');
  let request = 0;
  const load = async (more = false) => {
    const mine = ++request;
    try {
      const page = await fetchConversations(p.params, more ? next() : null);
      if (mine !== request) return;
      setItems(more ? [...(items() || []), ...page.items.filter((x) => !(items() || []).some((y) => y.id === x.id))] : page.items);
      setNext(page.next);
      setError('');
    } catch (e: any) { if (mine === request) setError(e?.message || t('기록을 읽지 못했습니다')); }
  };
  createEffect(on(() => [JSON.stringify(p.params), p.reloadKey], () => { void load(); }));
  const changed = () => { void load(); };
  window.addEventListener('vt:catalog-changed', changed);
  onCleanup(() => window.removeEventListener('vt:catalog-changed', changed));
  return (
    <>
      <Show when={items()} fallback={<Show when={!error()}><div class="vt-rg-note">{t('불러오는 중…')}</div></Show>}>
        {(list) => (
          <>
            <For each={list()}>{(c, i) => <ConversationItem conversation={c} last={!!p.lastLine && i() === list().length - 1 && !next()} />}</For>
            <Show when={list().length === 0 && p.empty}><div class="vt-rg-note">{p.empty}</div></Show>
          </>
        )}
      </Show>
      <Show when={next()}>
        <button type="button" class="vt-catalog-more-link" onClick={() => { void load(true); }}>{t('더 보기')}</button>
      </Show>
      <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
    </>
  );
}

/** 저장소 묶음 맨 아래의 「종료 N」 접기. count는 저장소 목록 API의 끝난 대화 수. */
export function ProjectEnded(p: { projectId: string; count: number; latest?: number | null; open: boolean; onToggle: () => void }) {
  return (
    <>
      <button type="button" class="vt-rg-row vt-rg-end vt-rg-fold" aria-expanded={p.open} onClick={p.onToggle}>
        <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={endLines(!p.open)} /><span class="vt-rg-hol" /></span>
        <span class="vt-rg-body vt-rg-a">
          <span class="vt-rg-caret" classList={{ open: p.open }} innerHTML={icon('chevron-right', 12, 2)} />
          <span>{t('종료 {n}', { n: p.count })}</span>
          <span class="vt-rg-sp" />
          <Show when={p.latest}><span class="vt-rg-tm">{t('마지막 {ago}', { ago: agoLabel(p.latest) })}</span></Show>
        </span>
      </button>
      <Show when={p.open}>
        <ConversationList params={{ project: p.projectId }} reloadKey={p.count} lastLine />
      </Show>
    </>
  );
}

export function ConversationItem(p: { conversation: EndedConversation; last: boolean }) {
  const c = () => p.conversation;
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal('');
  const [menuAt, setMenuAt] = createSignal<{ x: number; y: number } | null>(null);
  const [preview, setPreview] = createSignal<any>(null);
  const [opened, setOpened] = createSignal(false);
  const archived = () => !!c().archived;
  const title = () => c().title || t('제목 없음');
  const run = async (fn: () => Promise<unknown>) => {
    if (busy()) return;
    setBusy(true); setError('');
    try { await fn(); } catch (e: any) { setError(e?.message || t('작업을 완료하지 못했습니다')); } finally { setBusy(false); }
  };
  const openHistory = async () => {
    try { const { openHistory: open } = await import('../history/panel.js'); open({ search: c().native_id || c().title || '' }); }
    catch (e: any) { setError(e?.message || t('작업을 완료하지 못했습니다')); }
  };
  const toggle = async () => {
    const next = !opened();
    setOpened(next);
    if (!next || preview() || archived()) return;
    try { setPreview(await historyApi(`/api/session-history/conversations/${encodeURIComponent(c().id)}/resume-preview`)); }
    catch (e: any) { setError(e?.message || t('기록을 읽지 못했습니다')); }
  };
  const items = (): MenuItem[] => [
    ...(archived() ? [] : [{ icon: 'play', label: t('이어서 시작'), run: () => { void run(() => resumeConversation(c().id)); } }]),
    { icon: 'history', label: t('대화 보기'), run: () => { void openHistory(); } },
    archived()
      ? { icon: 'archive', label: t('보관 해제'), run: () => { void run(() => setConversationArchived(c().id, false)); } }
      : { icon: 'archive', label: t('대화 보관'), run: () => { void run(() => setConversationArchived(c().id, true)); } },
  ];
  const openMenu = (e: MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    const btn = (e.target as HTMLElement | null)?.closest?.('.vt-rg-more');
    const r = btn?.getBoundingClientRect();
    setMenuAt({ x: r ? r.left : e.clientX, y: r ? r.bottom + 2 : e.clientY });
  };
  return (
    <div class="vt-rg-ended" classList={{ 'is-busy': busy(), sel: opened() }}>
      {/* 칸 자체는 버튼이 아니다 — 펼치기(제목 버튼)·이어서·메뉴가 서로 형제인 버튼이다(버튼 안에 버튼을 두지 않는다). */}
      <div class="vt-rg-row vt-rg-end vt-rg-er" onClick={() => { void toggle(); }} onContextMenu={openMenu}>
        <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={itemLines(p.last)} /><span class="vt-rg-hol" /></span>
        <div class="vt-rg-body">
          <div class="vt-rg-a">
            <button type="button" class="vt-rg-er-toggle" aria-expanded={opened()} aria-label={t('{name} 자세히', { name: title() })}
              onClick={(e) => { e.stopPropagation(); void toggle(); }}>
              <Show when={c().provider}><span class="vt-rg-mk2" innerHTML={agentMark(c().provider)} /></Show>
              <span class="vt-rg-name">{title()}</span>
            </button>
            <span class="vt-rg-sp" />
            <span class="vt-rg-tm">{agoLabel(c().activity || 0)}</span>
            <Show when={!archived()} fallback={
              <button type="button" class="vt-rg-rs q" disabled={busy()} aria-label={t('{name} 보관 해제', { name: title() })}
                onClick={(e) => { e.stopPropagation(); void run(() => setConversationArchived(c().id, false)); }}>
                <span innerHTML={icon('archive', 11, 2)} />{t('해제')}
              </button>
            }>
              <button type="button" class="vt-rg-rs" disabled={busy()} aria-label={t('{name} 이어서 시작', { name: title() })}
                onClick={(e) => { e.stopPropagation(); void run(() => resumeConversation(c().id)); }}>
                <span innerHTML={shellIcon('play', 10, 2)} />{busy() ? t('시작하는 중…') : t('이어서')}
              </button>
            </Show>
            <button type="button" class="vt-icon-btn sm vt-rg-more" aria-haspopup="menu" aria-label={t('{name} 메뉴', { name: title() })}
              data-tip={t('메뉴')} data-tip-side="left" onClick={(e) => { e.stopPropagation(); openMenu(e); }} innerHTML={icon('more-horizontal', 16, 2)} />
          </div>
          <Show when={c().branch || c().user_message_count}>
            <div class="vt-rg-b mono">
              <Show when={c().branch}><span innerHTML={icon('git-branch', 12, 2)} /><span class="vt-rg-nm">{c().branch}</span></Show>
              <Show when={c().user_message_count}><span class="vt-rg-tm ui">{t('지시 {n}', { n: c().user_message_count })}</span></Show>
            </div>
          </Show>
          <Show when={c().last_user_text}><div class="vt-rg-b"><span class="vt-rg-sm">「{c().last_user_text}」</span></div></Show>
          <Show when={opened()}>
            <div class="vt-rg-cv" onClick={(e) => e.stopPropagation()}>
              <Show when={preview()?.resumable}>
                <div class="vt-rg-cvn">
                  <Show when={preview().name && preview().folder} fallback={t('이어서 열면 이 대화를 이어 갑니다.')}>
                    {t('이어서 열면 {folder}에서 {name} 세션으로 열립니다.', { folder: preview().folder, name: preview().name })}
                  </Show>
                </div>
              </Show>
              <Show when={preview() && !preview().resumable}>
                <div class="vt-rg-cvn">{t('대화를 켰던 폴더가 없어 이어서 열 수 없습니다.')}</div>
              </Show>
              <button type="button" class="vt-btn sm quiet vt-rg-record" onClick={() => { void openHistory(); }}>{t('대화 보기')}</button>
            </div>
          </Show>
          <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
        </div>
      </div>
      <Show when={menuAt()}>{(m) => <Portal><Menu x={m().x} y={m().y} onClose={() => setMenuAt(null)} items={items()} /></Portal>}</Show>
    </div>
  );
}
