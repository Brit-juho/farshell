import { For, Show, createMemo, createSignal } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon, stateIcon } from '../ui/icons.js';
import { Menu } from './RailRow.js';
import { normalizeSubagents, nativeStateLabel, sameLabelOrdinals } from './native-subagents.js';
import { formatRelativeTime, type SessionRow } from './rail-data.js';

export function NativeChip(props: { row: SessionRow }) {
  const summary = () => normalizeSubagents(props.row.subagents);
  const count = () => summary().items.filter((r: any) => r.state === 'working').length;
  const fullLabel = () => summary().limited ? t('내부 에이전트 · 표시 한도 도달') : count() ? t('내부 에이전트 {n} 작업 중', { n: count() }) : t('내부 에이전트 {n}개', { n: summary().items.length });
  // 리디자인 6단계(시안 O15-B) — 칩을 누르면 「작업 기록」 창을 열고 오른쪽에 작업 중인 첫 서브에이전트를 고른다
  // (예전 「에이전트 내부」 목록 창은 없앴다 — 같은 목록이 작업 기록 왼쪽에 있다).
  const openList = async () => {
    const host = (window as any).vtRelayHost?.() || 'local';
    const mod = await import('../history/session-record.js');
    if (host !== ((window as any).vtRelayHost?.() || 'local')) return;
    mod.openWorkLog({ tmuxName: props.row.tmuxName, select: { kind: 'working' } });
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
  // 진행 중·대기. 상태를 오래 못 들은(stale) 대기 행은 숨긴다 — 이미 끝났을 가능성이 크다(작업 중이면 둔다).
  const current = () => summary().items.filter((r: any) => r.state === 'working' || (r.state === 'idle' && !r.stale));
  const ended = () => summary().items.filter((r: any) => r.state === 'done' || r.state === 'failed');
  const manyWorking = () => current().filter((r: any) => r.state === 'working').length > 4;
  const storageKey = () => `vt.native:${(window as any).vtRelayHost?.() || 'local'}:${summary().parentId || props.row.tmuxName}`;
  const saved = () => { try { return JSON.parse(localStorage.getItem(storageKey()) || '{}'); } catch { return {}; } };
  const [expanded, setExpanded] = createSignal(saved().expanded === true);
  const [endedOpen, setEndedOpen] = createSignal(saved().ended === true);
  const save = () => { try { localStorage.setItem(storageKey(), JSON.stringify({ expanded: expanded(), ended: endedOpen() })); } catch {} };
  // 「기록 보기」 — 작업 기록 창 오른쪽에 이 서브에이전트를 고른 채 연다(시안 O15-B 셋째 줄).
  const open = async (child: any) => {
    const host = (window as any).vtRelayHost?.() || 'local';
    const m = await import('../history/session-record.js');
    if (host !== ((window as any).vtRelayHost?.() || 'local')) return;
    m.openWorkLog({ tmuxName: props.row.tmuxName, select: { kind: 'subagent', id: child.id, child } });
  };
  const [menu, setMenu] = createSignal<{ x: number; y: number; child: any } | null>(null);
  const openMenu = (e: MouseEvent, child: any) => {
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ x: r.left, y: r.bottom + 2, child });
  };
  const menuItems = (child: any) => [
    { icon: 'history', label: t('기록 보기'), run: () => void open(child) },
    { icon: 'corner-down-left', label: t('부모 세션으로 이동'), run: () => (window as any).vtOpenSessionFromRail?.({ sessionId: props.row.sessionId, tmuxName: props.row.tmuxName }) },
  ];
  // 하위 행(시안 D02 「내부 서브에이전트 하위 행」): 들여쓰기 24 + 가지선, 1줄 상태 아이콘 · 이름 · 경과,
  // 2줄 「내부 · 작업 중」. 도구 이름은 그리지 않는다 — 호출마다 생겼다 사라져 깜빡인다.
  // 같은 이름이 둘 이상이면 「general-purpose #2」처럼 순번(시작 시각 순, **지금 보이는 행끼리**)을 붙이고,
  // 2줄 끝에 id 앞 4자(바뀌지 않아 깜빡이지 않는다)를 흐리게 덧붙인다.
  const visible = () => [...(!manyWorking() || expanded() ? current() : []), ...(endedOpen() ? ended() : [])];
  const ordinals = createMemo(() => sameLabelOrdinals(visible()));
  const shownName = (child: any) => ordinals().has(child.id) ? `${child.label} #${ordinals().get(child.id)}` : child.label;
  const render = (child: any) => <div class="vt-native-row" data-state={child.state}>
    <button type="button" class="vt-native-open" aria-label={`${shownName(child)}, ${nativeStateLabel(child.state)}${child.stale ? `, ${t('상태 확인 필요')}` : ''}`}
      data-tip={t('기록 보기')} data-tip-side="right" onClick={() => void open(child)}>
      <span class="vt-native-state" data-stale={child.stale ? '' : undefined} innerHTML={stateIcon(child.state, 16)} />
      <span class="vt-native-main">
        <span class="vt-native-head">
          <span class="vt-native-name">{shownName(child)}</span>
          <span class="vt-native-time">{formatRelativeTime(child.started_at)}</span>
        </span>
        <span class="vt-native-kw" data-state={child.state}>{t('내부 · {state}', { state: nativeStateLabel(child.state) })}<Show when={ordinals().has(child.id)}><span class="vt-native-id"> · {child.id.slice(0, 4)}</span></Show></span>
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
        <button type="button" class="vt-native-fold vt-native-fold-ended" aria-expanded={endedOpen()} onClick={() => { setEndedOpen(!endedOpen()); save(); }}>
          <span class="vt-native-fold-icon" aria-hidden="true" innerHTML={icon('state-done', 14, 2)} />{t('끝난 서브에이전트 {n}', { n: ended().length })}
        </button>
        <Show when={endedOpen()}><For each={ended()}>{render}</For></Show>
      </Show>
      <Show when={summary().limited}><p class="vt-native-meta">{t('표시 한도에 도달함')}</p></Show>
      <Show when={menu()}>{(m) => <Portal><Menu x={m().x} y={m().y} onClose={() => setMenu(null)} items={menuItems(m().child)} /></Portal>}</Show>
    </div>
  </Show>;
}
