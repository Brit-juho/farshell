import { For, Show, createMemo, createSignal } from 'solid-js';
import { t } from '../core/i18n.js';
import { normalizeSubagents, nativeStateLabel } from './native-subagents.js';
import { formatRelativeTime, type SessionRow } from './rail-data.js';

export function NativeChip(props: { row: SessionRow }) {
  const summary = () => normalizeSubagents(props.row.subagents);
  const count = () => summary().items.filter((r: any) => r.state === 'working').length;
  const openList = async () => {
    const host = (window as any).vtRelayHost?.() || 'local';
    const mod = await import('../panels/subagent-viewer.js');
    if (host !== ((window as any).vtRelayHost?.() || 'local')) return;
    mod.showSubagents({ tmuxName: props.row.tmuxName, parentId: summary().parentId, items: summary().items, host });
  };
  return <Show when={summary().items.length || summary().limited}>
    <button type="button" class="vt-native-chip" onClick={(e) => { e.stopPropagation(); void openList(); }}>{summary().limited ? t('내부 에이전트 · 표시 한도 도달') : count() ? t('내부 에이전트 {n} 작업 중', { n: count() }) : t('내부 에이전트 {n}개', { n: summary().items.length })}</button>
  </Show>;
}

export function NativeChildren(props: { row: SessionRow }) {
  const summary = createMemo(() => normalizeSubagents(props.row.subagents));
  const current = () => summary().items.filter((r: any) => r.state === 'working' || r.state === 'idle');
  const ended = () => summary().items.filter((r: any) => r.state === 'done' || r.state === 'failed');
  const manyWorking = () => current().filter((r: any) => r.state === 'working').length > 4;
  const storageKey = () => `vt.native:${(window as any).vtRelayHost?.() || 'local'}:${summary().parentId || props.row.tmuxName}`;
  const saved = () => { try { return JSON.parse(localStorage.getItem(storageKey()) || '{}'); } catch { return {}; } };
  const [expanded, setExpanded] = createSignal(saved().expanded === true);
  const [endedOpen, setEndedOpen] = createSignal(saved().ended === true);
  const save = () => { try { localStorage.setItem(storageKey(), JSON.stringify({ expanded: expanded(), ended: endedOpen() })); } catch {} };
  const open = async (child: any) => {
    const host = (window as any).vtRelayHost?.() || 'local';
    const m = await import('../panels/subagent-viewer.js');
    if (host !== ((window as any).vtRelayHost?.() || 'local')) return;
    m.showSubagent({ tmuxName: props.row.tmuxName, parentId: summary().parentId, child, host });
  };
  const render = (child: any) => <div class="vt-native-row" data-state={child.state}>
    <button type="button" class="vt-native-open" onClick={() => void open(child)}>
      <span class="status-dot" data-state={child.state === 'failed' ? 'error' : child.state} />
      <span class="vt-native-name">{child.label}</span>
      <span class="vt-native-meta">{nativeStateLabel(child.state)}{child.stale ? ` · ${t('상태 확인 필요')}` : ''}</span>
      <Show when={child.tool}><span class="vt-native-tool">{t('도구: {tool}', { tool: child.tool })}</span></Show>
      <span class="vt-native-meta">{formatRelativeTime(child.started_at)}</span>
    </button>
    <details class="vt-native-menu"><summary aria-label={t('서브에이전트 메뉴')}>⋯</summary>
      <button type="button" onClick={() => void open(child)}>{t('기록 보기')}</button>
      <button type="button" onClick={() => (window as any).vtOpenSessionFromRail?.({ sessionId: props.row.sessionId, tmuxName: props.row.tmuxName })}>{t('부모 세션으로 이동')}</button>
    </details>
  </div>;
  return <Show when={summary().items.length || summary().limited}>
    <div class="vt-native-children" role="group" aria-label={t('네이티브 서브에이전트')}>
      <div class="vt-child-heading">{t('에이전트 내부')}<span>{t('기록 조회')}</span></div>
      <Show when={manyWorking()}>
        <button type="button" class="vt-native-fold" aria-expanded={expanded()} onClick={() => { setExpanded(!expanded()); save(); }}>{t('내부 에이전트 {n}개', { n: current().length })}</button>
      </Show>
      <Show when={!manyWorking() || expanded()}><For each={current()}>{render}</For></Show>
      <Show when={ended().length}>
        <button type="button" class="vt-native-fold" aria-expanded={endedOpen()} onClick={() => { setEndedOpen(!endedOpen()); save(); }}>{t('끝난 서브에이전트 {n}', { n: ended().length })}</button>
        <Show when={endedOpen()}><For each={ended()}>{render}</For></Show>
      </Show>
      <Show when={summary().limited}><p class="vt-native-meta">{t('표시 한도에 도달함')}</p></Show>
    </div>
  </Show>;
}
