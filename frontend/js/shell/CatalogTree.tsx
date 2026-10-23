import { createMemo, createSignal, For, Show, type JSX } from 'solid-js';
import { t } from '../core/i18n.js';
import type { SessionRow } from './rail-data.js';

export interface CatalogSnapshot { host: string; revision: number; projects: any[]; workspaces: any[]; sessions: any[]; next: string | null; relations?: any[]; }
type Props = { flat?: boolean; data: CatalogSnapshot; live: SessionRow[]; renderLive: (row: SessionRow) => JSX.Element;
  onChanged: () => void; onMore: () => void; };

function key(row: SessionRow) { return row.tmuxName || row.name; }

export function CatalogTree(props: Props) {
  const [error, setError] = createSignal('');
  const liveByName = createMemo(() => new Map(props.live.map(row => [key(row), row])));
  const represented = createMemo(() => new Set(props.data.sessions.filter(s => s.ended == null && s.runtime_id &&
    liveByName().get(s.tmux_name)?.physicalKey === s.physical_key).map(s => s.tmux_name)));
  const openRecord = async (session: any) => {
    try { const { openSessionRecord } = await import('../history/session-record.js'); openSessionRecord(session.id, props.onChanged); }
    catch (e: any) { setError(e.message || t('작업을 완료하지 못했습니다')); }
  };
  const sessionRow = (session: any) => {
    const live = () => {
      const row = liveByName().get(session.tmux_name);
      return session.ended == null && row?.physicalKey === session.physical_key ? row : undefined;
    };
    return <Show when={live()} fallback={<button class="vt-catalog-session" type="button" onClick={() => openRecord(session)}>
      <span>{session.title}</span><small>{session.execution_state === 'stopped' ? t('실행 종료됨') : t('실행 확인 필요')}</small>
    </button>}>{row => <div class="vt-catalog-live">
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
  return <div class="vt-catalog-tree">
    <Show when={props.flat} fallback={<For each={props.data.projects}>{project => <Project project={project} />}</For>}>
      <For each={props.data.sessions}>{sessionRow}</For>
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
