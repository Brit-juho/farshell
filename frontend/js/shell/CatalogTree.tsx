// 카탈로그(종료·보관된 세션) 쪽 레일 구역. 실행 중 행은 Rail/Fleet이 직접 그린다.
//   RecentEnded    「최근 종료」 구역 — 접을 수 있고 최근 N개만. 이어서 시작이 주 동작.
//   ArchiveList    「보관함」 — 같은 자리에서 목록이 바뀐다. 보관 해제·삭제·비우기.
//   CatalogTree    레일 설정 「저장소별로 묶기」를 켰을 때의 저장소 → 작업 공간 보기.
// 종료 행(StoppedRow)은 실행 중 행과 같은 3줄 구조이고 메뉴도 같은 Menu를 쓴다.
import { createMemo, createSignal, For, Show, type JSX } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { confirmDialog } from '../core/dialog-lazy.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { Menu, type MenuItem } from './RailRow.js';
import { pickRecentEnded, lastActiveOf, type SessionRow } from './rail-data.js';
import {
  resumeSession, renameSession, setSessionArchived, recheckCatalog, executionStatus, isLegacy,
  archiveMany, deleteSession, deleteMany, agoLabel,
} from '../history/session-actions.js';

export interface CatalogSnapshot {
  host: string; revision: number; projects: any[]; workspaces: any[]; sessions: any[]; next: string | null; relations?: any[];
  auto_archive_days?: number; archived_count?: number;
}

function key(row: SessionRow) { return row.tmuxName || row.name; }

/** 이 카탈로그 세션이 지금 실행 중 행으로 그려지고 있는가(같은 tmux 이름·같은 물리 키). */
export function liveIndex(live: SessionRow[]) {
  const byName = new Map(live.map(row => [key(row), row]));
  return {
    byName,
    isLive: (s: any) => s.ended == null && !!s.runtime_id && byName.get(s.tmux_name)?.physicalKey === s.physical_key,
  };
}

// 종료된 세션 한 줄: 실행 중 행과 같은 3줄. 본문을 누르면 작업 기록, 오른쪽은 주 동작 하나와 ⋯ 메뉴.
export function StoppedRow(p: { session: any; workspaces: any[]; onOpen: () => void; onChanged: () => void }) {
  const [busy, setBusy] = createSignal(false);
  const [menuAt, setMenuAt] = createSignal<{ x: number; y: number } | null>(null);
  const [error, setError] = createSignal('');
  const s = () => p.session;
  const legacy = () => isLegacy(s());
  const status = () => legacy() ? { label: t('과거 기록'), hint: t('연결된 대화 기록이 생기기 전에 끝난 세션이라 이어서 시작할 수 없습니다'), tone: 'idle', recheck: false } : executionStatus(s());
  const where = () => { const w = p.workspaces.find(x => x.id === s().workspace_id); return w ? (w.branch || w.path.split('/').pop()) : ''; };
  const resumable = () => !s().archived && s().ended != null && !!s().resume_conversation_id;
  const run = async (fn: () => Promise<unknown>) => {
    if (busy()) return;
    setBusy(true); setError('');
    try { await fn(); p.onChanged(); } catch (e: any) { setError(e.message || t('작업을 완료하지 못했습니다')); } finally { setBusy(false); }
  };
  const recheck = () => run(async () => { const r = await recheckCatalog(); if (!r.complete) throw new Error(t('아직 터미널 상태를 읽지 못했습니다. 잠시 뒤 다시 시도하세요')); });
  const items = (): MenuItem[] => {
    const out: MenuItem[] = [];
    if (resumable()) out.push({ icon: 'play', label: t('이어서 시작'), run: () => { void run(() => resumeSession(s().id)); } });
    if (s().archived) out.push({ icon: 'archive', label: t('보관 해제'), run: () => { void run(() => setSessionArchived(s(), false)); } });
    out.push({ icon: 'history', label: t('작업 기록'), run: p.onOpen });
    out.push({ icon: 'pencil', label: t('이름 변경'), run: () => { void run(() => renameSession(s())); } });
    if (!s().archived && s().ended != null) out.push({ icon: 'archive', label: t('세션 보관'), run: () => { void run(() => setSessionArchived(s(), true)); } });
    if (s().archived) out.push({ icon: 'trash-2', label: t('삭제'), detail: t('되돌릴 수 없음'), danger: true, run: () => { void run(() => deleteSession(s())); } });
    return out;
  };
  const openMenu = (e: MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    const btn = (e.target as HTMLElement | null)?.closest?.('.vt-srow-menu');
    const r = btn?.getBoundingClientRect();
    setMenuAt({ x: r ? r.left : e.clientX, y: r ? r.bottom + 2 : e.clientY });
  };
  return <div class="vt-catalog-stopped" classList={{ 'is-busy': busy() }}>
    <div
      class="vt-srow vt-wgrail-row stopped" role="button" tabindex="0" data-tone={status().tone}
      onClick={p.onOpen} onContextMenu={openMenu}
      onKeyDown={(e) => { if (e.target !== e.currentTarget) return; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); p.onOpen(); } }}
    >
      {/* 시안 D02 「종료된 행」 — 실행 중 행과 같은 열: 점선 원 · (이름 · 경과 · ⋯) / 작업 위치 / 상태,
          그 아래 동작 줄(다시 확인 · 보관 해제 · 이어서 시작). 채움은 이어서 시작 하나뿐이다. */}
      <span class="vt-srow-state" aria-hidden="true" innerHTML={icon('state-stopped', 16, 1.75)} />
      <div class="vt-srow-main vt-wgrail-row-main">
        <div class="vt-srow-top vt-wgrail-row-top">
          <span class="vt-srow-name vt-wgrail-name">{s().title}</span>
          <span class="vt-srow-elapsed">{agoLabel(lastActiveOf(s()))}</span>
          <button type="button" class="vt-icon-btn sm vt-srow-menu" aria-label={t('{name} 메뉴', { name: s().title })} data-tip={t('메뉴')} data-tip-side="left"
            onClick={openMenu} innerHTML={icon('more-horizontal', 16, 2)} />
        </div>
        <Show when={where()}>
          <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line2">
            <span class="vt-srow-branch with-ico"><span class="vt-srow-branch-ico" innerHTML={icon('git-branch', 12, 2)} /><span class="vt-srow-branch-name">{where()}</span></span>
          </div>
        </Show>
        <div class="vt-srow-sub vt-wgrail-row-sub vt-srow-line3">
          <span class="vt-srow-kw" title={status().hint}>{status().label}</span>
          <Show when={legacy()}><span class="vt-srow-note">{t('연결된 대화 없음')}</span></Show>
        </div>
        <Show when={status().recheck || s().archived || resumable()}>
          <div class="vt-srow-actions" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
            <Show when={status().recheck}>
              <button type="button" class="vt-btn sm quiet" disabled={busy()} title={status().hint}
                onClick={() => { void recheck(); }}>{busy() ? t('확인하는 중…') : t('다시 확인')}</button>
            </Show>
            <Show when={s().archived}>
              <button type="button" class="vt-btn sm quiet" disabled={busy()}
                onClick={() => { void run(() => setSessionArchived(s(), false)); }}>{t('보관 해제')}</button>
            </Show>
            <Show when={resumable()}>
              <button type="button" class="vt-btn sm solid vt-srow-resume" disabled={busy()}
                onClick={() => { void run(() => resumeSession(s().id)); }}>
                <span class="vt-srow-resume-ico" innerHTML={shellIcon('play', 12, 2)} />
                {busy() ? t('시작하는 중…') : t('이어서 시작')}
              </button>
            </Show>
          </div>
        </Show>
      </div>
    </div>
    <Show when={menuAt()}>{(m) => <Portal><Menu x={m().x} y={m().y} onClose={() => setMenuAt(null)} items={items()} /></Portal>}</Show>
    <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
  </div>;
}

async function openRecord(session: any, onChanged: () => void): Promise<string> {
  try { const { openSessionRecord } = await import('../history/session-record.js'); openSessionRecord(session.id, onChanged); return ''; }
  catch (e: any) { return e.message || t('작업을 완료하지 못했습니다'); }
}

/** 구역 머리글(캐럿 + 이름 + 개수). 개입 필요와 같은 모양이다. */
function GroupHead(p: { label: string; count: number; collapsed?: boolean; onToggle?: () => void; extra?: JSX.Element }) {
  return <div class="vt-wgrail-group-head">
    <Show when={p.onToggle} fallback={<span class="vt-wgrail-group-label">{p.label}<span class="vt-wgrail-group-count">{p.count}</span></span>}>
      <button type="button" class="vt-wgrail-group-toggle" classList={{ collapsed: !!p.collapsed }} aria-expanded={!p.collapsed} onClick={p.onToggle}>
        <span class="vt-wgrail-group-caret" innerHTML={icon('chevron-down', 12, 2)} />
        <span class="vt-wgrail-group-label">{p.label}</span>
        <span class="vt-wgrail-group-count">{p.count}</span>
      </button>
    </Show>
    {p.extra}
  </div>;
}

export function RecentEnded(p: {
  data: CatalogSnapshot; live: SessionRow[]; count: number; collapsed: boolean; onToggle: () => void; onChanged: () => void;
}) {
  const [all, setAll] = createSignal(false);
  const [error, setError] = createSignal('');
  const idx = createMemo(() => liveIndex(p.live));
  const picked = createMemo(() => pickRecentEnded(p.data.sessions, idx().isLive, all() ? Number.MAX_SAFE_INTEGER : p.count));
  const legacy = createMemo(() => p.data.sessions.filter(isLegacy));
  const [bulkBusy, setBulkBusy] = createSignal(false);
  const archiveLegacy = async () => {
    const list = legacy();
    if (bulkBusy() || !list.length) return;
    if (!await confirmDialog(`${list.length}${t('개의 과거 기록을 세션 보관함으로 옮깁니다. 삭제하지 않으며 보관함에서 해제할 수 있습니다. 계속할까요?')}`, { title: t('세션 보관'), okLabel: t('보관') })) return;
    setBulkBusy(true); setError('');
    try {
      const { failed } = await archiveMany(list);
      if (failed) setError(`${failed}${t('개는 다른 화면에서 바뀌어 건너뛰었습니다. 새로고침 후 다시 시도하세요')}`);
      p.onChanged();
    } finally { setBulkBusy(false); }
  };
  return <Show when={picked().total > 0}>
    <GroupHead label={t('최근 종료')} count={picked().total} collapsed={p.collapsed} onToggle={p.onToggle} />
    <Show when={!p.collapsed}>
      <For each={picked().shown}>{(session) => <StoppedRow session={session} workspaces={p.data.workspaces}
        onOpen={() => { void openRecord(session, p.onChanged).then(setError); }} onChanged={p.onChanged} />}</For>
      <Show when={picked().total > p.count}>
        <button type="button" class="vt-catalog-more-link" onClick={() => setAll(!all())}>
          {all() ? t('접기') : t('{n}개 더 보기', { n: picked().total - p.count })}
        </button>
      </Show>
      <Show when={legacy().length > 0}>
        <div class="vt-catalog-legacy">
          <span>{t('과거 기록')} {legacy().length}{t('개')}</span>
          <button type="button" class="vt-btn sm" disabled={bulkBusy()} onClick={archiveLegacy}>{bulkBusy() ? t('보관하는 중…') : t('모두 보관')}</button>
        </div>
      </Show>
      <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
    </Show>
  </Show>;
}

/** 목록 맨 아래 한 줄. 누르면 같은 자리가 보관함 목록으로 바뀐다. */
export function ArchiveEntry(p: { count: number; onOpen: () => void }) {
  return <button type="button" class="vt-catalog-archive-link" onClick={p.onOpen}>
    <span class="vt-catalog-archive-mark" innerHTML={icon('archive', 14, 2)} />
    <span class="vt-catalog-archive-label">{t('보관함')}</span>
    <span class="vt-wgrail-group-count">{p.count}</span>
    <span class="vt-catalog-archive-go" innerHTML={icon('chevron-right', 12, 2)} />
  </button>;
}

export function ArchiveList(p: { data: CatalogSnapshot; onBack: () => void; onChanged: () => void; onMore: () => void }) {
  const [error, setError] = createSignal('');
  const [busy, setBusy] = createSignal(false);
  const sessions = createMemo(() => p.data.sessions.filter(x => x.archived).sort((a, b) => lastActiveOf(b) - lastActiveOf(a)));
  const days = () => p.data.auto_archive_days ?? 0;
  const empty = async () => {
    const list = sessions();
    if (busy() || !list.length) return;
    if (!await confirmDialog(t('보관함의 세션 기록 {n}개를 삭제합니다. 대화 보관함의 대화와 원본은 남고, 위임 관계가 있는 세션은 삭제하지 않습니다. 계속할까요?', { n: list.length }), { title: t('세션 기록 삭제'), okLabel: t('삭제') })) return;
    setBusy(true); setError('');
    try {
      const { failed } = await deleteMany(list);
      if (failed) setError(t('{n}개는 삭제하지 못해 남겼습니다. 위임 관계가 있거나 다른 화면에서 바뀐 세션입니다', { n: failed }));
      p.onChanged();
    } finally { setBusy(false); }
  };
  return <div class="vt-catalog-tree">
    <div class="vt-wgrail-group-head vt-catalog-archive-head">
      <button type="button" class="vt-catalog-back" onClick={p.onBack}>
        <span innerHTML={icon('chevron-left', 12, 2)} /><span>{t('세션')}</span>
      </button>
      <span class="vt-wgrail-group-label">{t('보관함')}<span class="vt-wgrail-group-count">{sessions().length}</span></span>
      <Show when={sessions().length > 0}>
        <button type="button" class="vt-btn sm" disabled={busy()} onClick={empty}>{busy() ? t('삭제하는 중…') : t('비우기')}</button>
      </Show>
    </div>
    <Show when={days() > 0}><p class="vt-wgrail-note">{t('종료 후 {n}일이 지난 세션이 자동으로 보관됩니다. 자동으로는 삭제하지 않습니다.', { n: days() })}</p></Show>
    <Show when={sessions().length === 0}><p class="vt-wgrail-note">{t('보관된 세션이 없습니다.')}</p></Show>
    <For each={sessions()}>{(session) => <StoppedRow session={session} workspaces={p.data.workspaces}
      onOpen={() => { void openRecord(session, p.onChanged).then(setError); }} onChanged={p.onChanged} />}</For>
    <Show when={p.data.next}><button class="vt-btn" type="button" onClick={p.onMore}>{t('세션 더 불러오기')}</button></Show>
    <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
  </div>;
}

type Props = { data: CatalogSnapshot; live: SessionRow[]; renderLive: (row: SessionRow) => JSX.Element;
  onChanged: () => void; onMore: () => void; };

// 「저장소별로 묶기」 보기: 저장소 → 작업 공간 → 세션. 실행 중 행도 같은 자리에 섞여 나온다.
export function CatalogTree(props: Props) {
  const [error, setError] = createSignal('');
  const idx = createMemo(() => liveIndex(props.live));
  const represented = createMemo(() => new Set(props.data.sessions.filter(idx().isLive).map(s => s.tmux_name)));
  const sessionRow = (session: any) => {
    const live = () => {
      const row = idx().byName.get(session.tmux_name);
      return session.ended == null && row?.physicalKey === session.physical_key ? row : undefined;
    };
    return <Show when={live()} fallback={<StoppedRow session={session} workspaces={props.data.workspaces}
      onOpen={() => { void openRecord(session, props.onChanged).then(setError); }} onChanged={props.onChanged} />}>
      {row => props.renderLive({ ...row(), name: session.title, catalogSessionId: session.id, runtimeId: session.runtime_id })}
    </Show>;
  };
  const Project = (p: { project: any }) => {
    const spaces = createMemo(() => props.data.workspaces.filter(w => w.project_id === p.project.id));
    const members = createMemo(() => props.data.sessions.filter(s => spaces().some(w => w.id === s.workspace_id)));
    const essential = () => members().some(s => { const row = idx().byName.get(s.tmux_name); return row?.open || row?.status === 'waiting'; });
    const [expanded, setExpanded] = createSignal(essential());
    return <Show when={!p.project.hidden || essential()}><section class="vt-catalog-project">
      <button class="vt-catalog-project-head" type="button" aria-expanded={expanded()} onClick={() => setExpanded(!expanded())}>
        <span>{p.project.name}</span><small>{members().length}</small>
      </button>
      <Show when={expanded()}>
        <For each={spaces()}>{space => <section class="vt-catalog-workspace">
          <div class="vt-catalog-workspace-head"><span>{space.kind === 'checkout' ? t('기본 작업 공간') : space.kind === 'worktree' ? t('워크트리') : t('폴더')}</span>
            <span>{space.branch || space.path.split('/').pop()}</span></div>
          <For each={members().filter(s => s.workspace_id === space.id)}>{sessionRow}</For>
          <Show when={!members().some(s => s.workspace_id === space.id)}><p class="vt-wgrail-note">{t('저장된 세션이 없습니다')}</p></Show>
        </section>}</For>
      </Show>
    </section></Show>;
  };
  return <div class="vt-catalog-tree">
    <For each={props.data.projects}>{project => <Project project={project} />}</For>
    <Show when={props.data.sessions.some(s => !s.workspace_id) || props.live.some(row => !represented().has(key(row)))}>
      <section class="vt-catalog-project">
        <div class="vt-catalog-workspace-head" data-tip={t('저장소 밖에서 연 세션입니다')}>{t('저장소 밖 세션')}</div>
        <For each={props.data.sessions.filter(s => !s.workspace_id)}>{sessionRow}</For>
        <For each={props.live.filter(row => !represented().has(key(row)))}>{props.renderLive}</For>
      </section>
    </Show>
    <Show when={props.data.next}><button class="vt-btn" type="button" onClick={props.onMore}>{t('세션 더 불러오기')}</button></Show>
    <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
  </div>;
}
