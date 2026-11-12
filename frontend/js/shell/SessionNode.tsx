// 가지 그래프의 세션 칸(세션 로직 점검 단계 5, 시안 5차 데스크톱). 목록 조립은 Rail.tsx · repo-tree.ts.
//
//   [거터 44px: 가지선 · 마디(상태 링 + 에이전트 마크)] [본문]
//     1줄  보이는 이름 · (공유) · (위임 접기) · 경과 또는 대기 시간 · ⋯ — ⋯는 늘 같은 자리(이름이 길면 이름이 줄어든다)
//     2줄  할 말이 있을 때만: 입력 대기 유형·작업 중 도구·「끝남 · 확인 안 함」·마지막 지시 + 변경량
// 위치(저장소·브랜치)는 칸에 없다 — 저장소 머리와 워크트리 가지가 말한다. 「유휴」·「직접 만듦」 같은 글도 없다.
//
// ⚠ shell 지연 청크다 — core/store.js 계열을 직접 import하지 않는다(Rail.tsx 머리말과 같은 이유).
import { Show, createSignal, onCleanup } from 'solid-js';
import { t } from '../core/i18n.js';
import { agentLabel, icon } from '../ui/icons.js';
import { STATE_LABEL, normalizeState } from '../design/state-classes.js';
import { ringSvg } from './compact-mark.js';
import { agentMark } from './agent-mark.js';
import { formatRelativeTime, nodeLine, kidLabel, type SessionRow } from './rail-data.js';
import { ShareMark, ProfileMark, ScreensMark, OriginMark, KidsToggle } from './RailRow.js';
import { NativeChip } from './NativeChildren.js';
import { normalizeSubagents } from './native-subagents.js';

function createNow() {
  const [now, setNow] = createSignal(Date.now());
  const timer = setInterval(() => setNow(Date.now()), 30000);
  onCleanup(() => clearInterval(timer));
  return now;
}

/** 거터 모양. lane 2 = 워크트리 가지(마디가 오른쪽으로 한 칸). lines = 가지선 SVG 조각(RepoBranch.tsx `gutter`). */
export interface NodeGeometry { lane: 1 | 2; lines: string }

export function SessionNode(props: {
  row: SessionRow; active: boolean; geometry: NodeGeometry;
  kidsCount?: number; kidsCollapsed?: boolean; onToggleKids?: () => void; progress?: { reported: number; open: number } | null;
  /** 이 세션이 속한 분할 화면 묶음 이름(있을 때 1줄에 작은 표식). */
  groupLabel?: string | null;
  editing?: boolean; editingValue?: string;
  onEditInput?: (v: string) => void; onEditCommit?: () => void; onEditCancel?: () => void;
  draggable?: boolean; onDragStart?: (e: DragEvent) => void;
  onOpen: (e: MouseEvent) => void;
  onContext: (e: MouseEvent) => void;
}) {
  const now = createNow();
  const row = () => props.row;
  const state = () => normalizeState(row().status);
  const line = () => nodeLine(row());
  const changed = () => { const c = row().changed; return c && (c.add > 0 || c.del > 0) ? c : null; };
  const unseen = () => row().unseen && state() !== 'idle';
  const label = () => STATE_LABEL[state() as keyof typeof STATE_LABEL] + (unseen() ? ' · ' + t('아직 확인 안 함') : '');
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    if (e.key !== 'Enter') e.preventDefault();
    props.onOpen(e as unknown as MouseEvent);
  };
  const g = () => props.geometry;
  const hasSubagents = () => normalizeSubagents(row().subagents).items.length > 0;
  return (
    <div
      class="vt-rg-row vt-rg-node"
      classList={{
        active: props.active, closed: !row().open, 'is-waiting': state() === 'waiting', 'is-quiet': state() === 'idle',
        l2: g().lane === 2,
      }}
      data-state={state()}
      role="button" tabindex="0"
      aria-current={props.active ? 'true' : undefined}
      // agent/paint.js의 전역 클릭 위임이 `[data-session-id]`로 「봤다」를 판정한다.
      data-session-id={row().sessionId || undefined}
      onClick={props.onOpen}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => { e.preventDefault(); props.onContext(e); }}
      draggable={!!props.draggable}
      onDragStart={props.onDragStart}
    >
      <div class="vt-rg-gut">
        <svg class="vt-rg-ln" aria-hidden="true" innerHTML={g().lines} />
        <span class="vt-rg-mark" data-state={state()} role="img" aria-label={label()}>
          <span class="vt-rg-ring" innerHTML={ringSvg(row().status)} />
          <span class="vt-rg-agent" data-tip={row().agent ? agentLabel(row().agent!) : undefined} data-tip-side="right"
            innerHTML={agentMark(row().agent || 'shell')} />
          <Show when={unseen()}><span class="vt-rg-unseen" aria-hidden="true" /></Show>
        </span>
      </div>
      <div class="vt-rg-body">
        <div class="vt-rg-a">
          <Show when={props.editing} fallback={
            <span class="vt-rg-name" data-tip={row().tmuxName && row().tmuxName !== row().name ? t('tmux 이름 · {name}', { name: row().tmuxName! }) : undefined}
              data-tip-side="right">{row().name}</span>
          }>
            <input
              type="text" class="vt-input vt-wgrail-name-edit" value={props.editingValue ?? ''}
              ref={(el) => { queueMicrotask(() => { el.focus(); el.select(); }); }}
              onInput={(e) => props.onEditInput?.(e.currentTarget.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); props.onEditCommit?.(); }
                else if (e.key === 'Escape') { e.preventDefault(); props.onEditCancel?.(); }
              }}
              onBlur={() => props.onEditCommit?.()}
            />
          </Show>
          <ShareMark share={row().share} />
          {/* 만든 곳은 다른 맥에서 만든 세션일 때만(「직접 만듦」·외부 tmux는 알릴 가치가 없다). */}
          <Show when={row().origin === 'peer'}><OriginMark origin={row().origin} host={row().originHost} /></Show>
          <Show when={props.groupLabel}>
            <span class="vt-rg-group" data-tip={t('분할 화면 묶음 · {name}', { name: props.groupLabel! })} data-tip-side="right"
              role="img" aria-label={t('분할 화면 묶음 · {name}', { name: props.groupLabel! })} innerHTML={icon('columns-2', 12, 2)} />
          </Show>
          <KidsToggle progress={props.progress ?? null} count={props.kidsCount || 0} collapsed={!!props.kidsCollapsed}
            name={row().name} onToggle={props.onToggleKids} />
          <span class="vt-rg-sp" />
          <span class="vt-rg-tm" classList={{ 'c-waiting': state() === 'waiting' }}>{formatRelativeTime(row().since, now())}</span>
          <button
            type="button" class="vt-icon-btn sm vt-rg-more"
            aria-haspopup="menu"
            aria-label={t('{name} 메뉴', { name: row().name })}
            data-tip={t('메뉴')} data-tip-side="left"
            onClick={(e) => { e.stopPropagation(); props.onContext(e); }}
            innerHTML={icon('more-horizontal', 16, 2)}
          />
        </div>
        <Show when={line() || changed() || row().otherScreens > 0 || row().otherClients > 0 || row().profile || hasSubagents()}>
          <div class="vt-rg-b">
            <Show when={line()}>
              {(l) => (
                <span class="vt-rg-sm" data-tone={l().tone}
                  data-tip={state() === 'waiting' && row().question ? row().question! : undefined} data-tip-side="right">
                  {l().text}<Show when={l().detail}><span class="vt-rg-detail">{' '}{l().detail}</span></Show>
                </span>
              )}
            </Show>
            <Show when={!line()}><span class="vt-rg-sp" /></Show>
            <NativeChip row={row()} />
            <ProfileMark profile={row().profile} />
            <ScreensMark row={row()} />
            <Show when={changed()}>
              {(c) => (
                <span class="vt-rg-diff">
                  <Show when={c().add > 0}><span class="vt-diff-add">+{c().add}</span></Show>
                  <Show when={c().del > 0}><span class="vt-diff-del">−{c().del}</span></Show>
                </span>
              )}
            </Show>
          </div>
        </Show>
      </div>
    </div>
  );
}

/** 위임 하위 세션 한 줄(가지에서 갈라진 작은 칸): 이름 · 「보고 도착」/「진행 중」 · ⋯. 누르면 그 세션이 열린다.
 * 일반 칸과 같은 이름 편집·끌어서 분할·메뉴를 받는다(메뉴는 ⋯ 버튼으로 키보드에서도 열린다). */
export function KidNode(props: Omit<Parameters<typeof SessionNode>[0], 'geometry'> & { lane: 1 | 2; lines: string }) {
  const l = () => props.row.lineage;
  const label = () => (l() ? kidLabel(l()!) : null);
  const state = () => normalizeState(props.row.status);
  return (
    <div
      class="vt-rg-row vt-rg-kid"
      classList={{ active: props.active, l2: props.lane === 2, closed: l()?.state === 'closed' }}
      data-state={state()}
      role="button" tabindex="0"
      data-session-id={props.row.sessionId || undefined}
      onClick={props.onOpen}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); props.onOpen(e as unknown as MouseEvent); } }}
      onContextMenu={(e) => { e.preventDefault(); props.onContext(e); }}
      draggable={!!props.draggable}
      onDragStart={props.onDragStart}
    >
      <div class="vt-rg-gut">
        <svg class="vt-rg-ln" aria-hidden="true" innerHTML={props.lines} />
        <span class="vt-rg-kidmark" data-state={state()} innerHTML={icon('terminal', 13, 2)} />
      </div>
      <div class="vt-rg-body vt-rg-a">
        <Show when={props.editing} fallback={
          <span class="vt-rg-name" data-tip={l()?.task ? t('맡은 일 · {task}', { task: l()!.task }) : undefined} data-tip-side="right">{props.row.name}</span>
        }>
          <input
            type="text" class="vt-input vt-wgrail-name-edit" value={props.editingValue ?? ''}
            ref={(el) => { queueMicrotask(() => { el.focus(); el.select(); }); }}
            onInput={(e) => props.onEditInput?.(e.currentTarget.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); props.onEditCommit?.(); }
              else if (e.key === 'Escape') { e.preventDefault(); props.onEditCancel?.(); }
            }}
            onBlur={() => props.onEditCommit?.()}
          />
        </Show>
        <span class="vt-rg-sp" />
        <Show when={state() === 'waiting'} fallback={
          <Show when={label()}>{(x) => <span class="vt-rg-tm" data-tone={x().tone}>{x().text}</span>}</Show>
        }>
          <span class="vt-rg-tm c-waiting">{STATE_LABEL.waiting}</span>
        </Show>
        <button
          type="button" class="vt-icon-btn sm vt-rg-more" aria-haspopup="menu"
          aria-label={t('{name} 메뉴', { name: props.row.name })}
          data-tip={t('메뉴')} data-tip-side="left"
          onClick={(e) => { e.stopPropagation(); props.onContext(e); }}
          innerHTML={icon('more-horizontal', 16, 2)}
        />
      </div>
    </div>
  );
}
