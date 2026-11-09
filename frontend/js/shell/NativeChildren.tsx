import { For, Show, createMemo, createSignal } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon, stateIcon } from '../ui/icons.js';
import { Menu } from './RailRow.js';
import { normalizeSubagents, nativeStateLabel } from './native-subagents.js';
import { formatRelativeTime, type SessionRow } from './rail-data.js';

export function NativeChip(props: { row: SessionRow }) {
  const summary = () => normalizeSubagents(props.row.subagents);
  const count = () => summary().items.filter((r: any) => r.state === 'working').length;
  const fullLabel = () => summary().limited ? t('내부 에이전트 · 표시 한도 도달') : count() ? t('내부 에이전트 {n} 작업 중', { n: count() }) : t('내부 에이전트 {n}개', { n: summary().items.length });
  const openList = async () => {
    const host = (window as any).vtRelayHost?.() || 'local';
    const mod = await import('../panels/subagent-viewer.js');
    if (host !== ((window as any).vtRelayHost?.() || 'local')) return;
    mod.showSubagents({ tmuxName: props.row.tmuxName, parentId: summary().parentId, items: summary().items, host });
  };
  return <Show when={summary().items.length || summary().limited}>
    {/* 행 폭이 좁아 칩 글자는 줄이고(「내부 3 작업 중」), 전체 문장은 툴팁에 둔다. 아이콘이 에이전트 가지임을 말한다. */}
    <button type="button" class="vt-native-chip" data-working={count() ? '' : undefined}
      data-tip={fullLabel()} data-tip-side="right" aria-label={fullLabel()}
      onClick={(e) => { e.stopPropagation(); void openList(); }}>
      <span class="vt-native-chip-icon" innerHTML={icon('git-branch', 12, 2)} />
      <span>{summary().limited ? t('내부 · 한도 도달') : count() ? t('내부 {n} 작업 중', { n: count() }) : t('내부 {n}개', { n: summary().items.length })}</span>
    </button>
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
  const [menu, setMenu] = createSignal<{ x: number; y: number; child: any } | null>(null);
  const openMenu = (e: MouseEvent, child: any) => {
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ x: r.left, y: r.bottom + 2, child });
  };
  const menuItems = (child: any) => [
    { label: t('기록 보기'), run: () => void open(child) },
    { label: t('부모 세션으로 이동'), run: () => (window as any).vtOpenSessionFromRail?.({ sessionId: props.row.sessionId, tmuxName: props.row.tmuxName }) },
  ];
  // 하위 행(시안 D02 「내부 서브에이전트 하위 행」): 들여쓰기 24 + 가지선, 1줄 상태 아이콘 · 이름 · 경과,
  // 2줄 「내부 · 작업 중」. 도구 이름은 그리지 않는다 — 호출마다 생겼다 사라져 깜빡인다.
  const render = (child: any) => <div class="vt-native-row" data-state={child.state}>
    <button type="button" class="vt-native-open" aria-label={`${child.label}, ${nativeStateLabel(child.state)}${child.stale ? `, ${t('상태 확인 필요')}` : ''}`}
      data-tip={t('기록 보기')} data-tip-side="right" onClick={() => void open(child)}>
      <span class="vt-native-state" data-stale={child.stale ? '' : undefined} innerHTML={stateIcon(child.state, 16)} />
      <span class="vt-native-main">
        <span class="vt-native-head">
          <span class="vt-native-name">{child.label}</span>
          <span class="vt-native-time">{formatRelativeTime(child.started_at)}</span>
        </span>
        <span class="vt-native-kw" data-state={child.state}>{t('내부 · {state}', { state: nativeStateLabel(child.state) })}</span>
      </span>
    </button>
    <button type="button" class="vt-icon-btn xs vt-native-menu-btn" aria-label={t('서브에이전트 메뉴')} aria-haspopup="menu" onClick={(e) => openMenu(e, child)} innerHTML={icon('more-horizontal', 14, 2)} />
  </div>;
  return <Show when={summary().items.length || summary().limited}>
    <div class="vt-native-children" role="group" aria-label={t('네이티브 서브에이전트')}>
      <Show when={manyWorking()}>
        <button type="button" class="vt-native-fold" aria-expanded={expanded()} onClick={() => { setExpanded(!expanded()); save(); }}>{t('내부 에이전트 {n}개', { n: current().length })}</button>
      </Show>
      <Show when={!manyWorking() || expanded()}><For each={current()}>{render}</For></Show>
      <Show when={ended().length}>
        <button type="button" class="vt-native-fold" aria-expanded={endedOpen()} onClick={() => { setEndedOpen(!endedOpen()); save(); }}>{t('끝난 서브에이전트 {n}', { n: ended().length })}</button>
        <Show when={endedOpen()}><For each={ended()}>{render}</For></Show>
      </Show>
      <Show when={summary().limited}><p class="vt-native-meta">{t('표시 한도에 도달함')}</p></Show>
      <Show when={menu()}>{(m) => <Portal><Menu x={m().x} y={m().y} onClose={() => setMenu(null)} items={menuItems(m().child)} /></Portal>}</Show>
    </div>
  </Show>;
}
