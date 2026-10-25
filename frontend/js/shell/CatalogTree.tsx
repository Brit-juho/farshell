import { createMemo, createSignal, For, Show, type JSX } from 'solid-js';
import { t } from '../core/i18n.js';
import { confirmDialog } from '../core/dialog.js';
import type { SessionRow } from './rail-data.js';
import { resumeSession, renameSession, setSessionArchived, recheckCatalog, executionStatus, isLegacy, archiveMany, agoLabel } from '../history/session-actions.js';

export interface CatalogSnapshot { host: string; revision: number; projects: any[]; workspaces: any[]; sessions: any[]; next: string | null; relations?: any[]; }
type Props = { flat?: boolean; data: CatalogSnapshot; live: SessionRow[]; renderLive: (row: SessionRow) => JSX.Element;
  onChanged: () => void; onMore: () => void; };

function key(row: SessionRow) { return row.tmuxName || row.name; }

function lastActive(s: any) { return s.ended || s.observed || s.updated || 0; }

// 종료된 세션 한 줄: 제목·상태·시각은 기록을 여는 영역, 오른쪽은 주 동작(이어서 시작 / 보관 해제)과 ⋯ 메뉴.
function StoppedRow(p: { session: any; workspaces: any[]; onOpen: () => void; onChanged: () => void }) {
  const [busy, setBusy] = createSignal(false);
  const [menu, setMenu] = createSignal(false);
  const [error, setError] = createSignal('');
  const s = () => p.session;
  const legacy = () => isLegacy(s());
  const status = () => legacy() ? { label: t('과거 기록'), hint: t('연결된 대화 기록이 생기기 전에 끝난 세션이라 이어서 시작할 수 없습니다'), tone: 'idle' } : executionStatus(s());
  const where = () => { const w = p.workspaces.find(x => x.id === s().workspace_id); return w ? (w.branch || w.path.split('/').pop()) : ''; };
  const meta = () => [status().label, legacy() ? t('연결된 대화 없음') : '', agoLabel(lastActive(s())), where()].filter(Boolean).join(' · ');
  const run = async (fn: () => Promise<unknown>) => {
    if (busy()) return;
    setBusy(true); setError(''); setMenu(false);
    try { await fn(); p.onChanged(); } catch (e: any) { setError(e.message || t('작업을 완료하지 못했습니다')); } finally { setBusy(false); }
  };
  return <div class="vt-catalog-stopped" classList={{ 'is-busy': busy() }}>
    <div class="vt-catalog-stopped-row">
      <button class="vt-catalog-session" type="button" onClick={p.onOpen} data-tone={status().tone} title={status().hint}>
        <span>{s().title}</span><small>{meta()}</small>
      </button>
      <Show when={status().recheck}>
        <button type="button" class="vt-btn sm" disabled={busy()} title={status().hint}
          onClick={() => run(async () => { const r = await recheckCatalog(); if (!r.complete) throw new Error(t('아직 터미널 상태를 읽지 못했습니다. 잠시 뒤 다시 시도하세요')); })}>{busy() ? t('확인하는 중…') : t('다시 확인')}</button>
      </Show>
      <Show when={s().archived}>
        <button type="button" class="vt-btn sm" disabled={busy()} onClick={() => run(() => setSessionArchived(s(), false))}>{t('보관 해제')}</button>
      </Show>
      <Show when={!s().archived && s().ended != null && s().resume_conversation_id}>
        <button type="button" class="vt-btn sm solid" disabled={busy()} onClick={() => run(() => resumeSession(s().id))}>{busy() ? t('시작하는 중…') : t('이어서 시작')}</button>
      </Show>
      <button type="button" class="vt-catalog-more" aria-label={t('더 보기')} aria-expanded={menu()} onClick={() => setMenu(!menu())}>⋯</button>
    </div>
    <Show when={menu()}>
      <div class="vt-catalog-menu" role="menu">
        <button type="button" role="menuitem" onClick={() => run(() => renameSession(s()))}>{t('이름 변경')}</button>
        <Show when={!s().archived && s().ended != null}>
          <button type="button" role="menuitem" onClick={() => run(() => setSessionArchived(s(), true))}>{t('세션 보관')}</button>
        </Show>
        <button type="button" role="menuitem" onClick={() => { setMenu(false); p.onOpen(); }}>{t('작업 기록')}</button>
      </div>
    </Show>
    <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
  </div>;
}

export function CatalogTree(props: Props) {
  const [error, setError] = createSignal('');
  const liveByName = createMemo(() => new Map(props.live.map(row => [key(row), row])));
  const represented = createMemo(() => new Set(props.data.sessions.filter(s => s.ended == null && s.runtime_id &&
    liveByName().get(s.tmux_name)?.physicalKey === s.physical_key).map(s => s.tmux_name)));
  // 전체 세션·보관함은 최근에 쓴 순서. 서버 페이지는 id순이라 불러온 범위 안에서 정렬한다.
  const flatSessions = createMemo(() => [...props.data.sessions].sort((a, b) => lastActive(b) - lastActive(a)));
  const openRecord = async (session: any) => {
    try { const { openSessionRecord } = await import('../history/session-record.js'); openSessionRecord(session.id, props.onChanged); }
    catch (e: any) { setError(e.message || t('작업을 완료하지 못했습니다')); }
  };
  const sessionRow = (session: any) => {
    const live = () => {
      const row = liveByName().get(session.tmux_name);
      return session.ended == null && row?.physicalKey === session.physical_key ? row : undefined;
    };
    return <Show when={live()} fallback={<StoppedRow session={session} workspaces={props.data.workspaces}
      onOpen={() => openRecord(session)} onChanged={props.onChanged} />}>{row => <div class="vt-catalog-live">
      {props.renderLive({ ...row(), name: session.title, catalogSessionId: session.id, runtimeId: session.runtime_id })}
      <button type="button" class="vt-catalog-record" onClick={() => openRecord(session)}>{t('작업 기록')}</button>
    </div>}</Show>;
  };
  const Project = (p: { project: any }) => {
    const spaces = createMemo(() => props.data.workspaces.filter(w => w.project_id === p.project.id));
    const members = createMemo(() => props.data.sessions.filter(s => spaces().some(w => w.id === s.workspace_id)));
    const essential = () => members().some(s => { const row = liveByName().get(s.tmux_name); return row?.open || row?.status === 'waiting'; });
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
  const legacySessions = createMemo(() => props.data.sessions.filter(isLegacy));
  const [bulkBusy, setBulkBusy] = createSignal(false);
  const archiveLegacy = async () => {
    const list = legacySessions();
    if (bulkBusy() || !list.length) return;
    if (!await confirmDialog(`${list.length}${t('개의 과거 기록을 세션 보관함으로 옮깁니다. 삭제하지 않으며 보관함에서 해제할 수 있습니다. 계속할까요?')}`)) return;
    setBulkBusy(true); setError('');
    try {
      const { failed } = await archiveMany(list);
      if (failed) setError(`${failed}${t('개는 다른 화면에서 바뀌어 건너뛰었습니다. 새로고침 후 다시 시도하세요')}`);
      props.onChanged();
    } finally { setBulkBusy(false); }
  };
  return <div class="vt-catalog-tree">
    <Show when={legacySessions().length > 0}>
      <div class="vt-catalog-legacy">
        <span>{t('과거 기록')} {legacySessions().length}{t('개')}</span>
        <button type="button" class="vt-btn sm" disabled={bulkBusy()} onClick={archiveLegacy}>{bulkBusy() ? t('보관하는 중…') : t('모두 보관')}</button>
      </div>
    </Show>
    <Show when={props.flat} fallback={<For each={props.data.projects}>{project => <Project project={project} />}</For>}>
      <For each={flatSessions()}>{sessionRow}</For>
    </Show>
    <Show when={props.data.sessions.some(s => !s.workspace_id) || props.live.some(row => !represented().has(key(row)))}>
      <section class="vt-catalog-project"><div class="vt-catalog-workspace-head">{t('미분류')}</div>
        <For each={props.data.sessions.filter(s => !props.flat && !s.workspace_id)}>{sessionRow}</For>
        <For each={props.live.filter(row => !represented().has(key(row)))}>{props.renderLive}</For>
      </section>
    </Show>
    <Show when={props.data.next}><button class="vt-btn" type="button" onClick={props.onMore}>{t('세션 더 불러오기')}</button></Show>
    <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
  </div>;
}
