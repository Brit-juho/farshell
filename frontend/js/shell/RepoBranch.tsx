// 저장소 한 묶음의 가지 그래프(세션 로직 점검 단계 5, 시안 5차 데스크톱 P1·P2).
//
//   [폴더] farshell  ⑂ dev                     2   ← 머리(누르면 접기)
//     ○ 세션                                       ← 메인 작업 트리
//     ╰ 워크트리 fix/x                             ← 같은 저장소의 다른 작업 트리
//         ○ 세션
//           ╰ ▣ 위임 하위 세션 · 보고 도착
//     ◌ 종료 2 · 마지막 3시간 전                    ← EndedFold
//
// 가지선은 칸마다 거터 SVG로 긋는다(시안과 같은 좌표: 줄기 x=21, 워크트리 가지 x=35, 마디 중심 y=16).
// 칸 사이가 끊기지 않게 각 칸은 위로 0부터, 아래로 100%까지 선을 잇고, 마지막 칸만 마디에서 멈춘다.
import { For, Show, type JSX } from 'solid-js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import type { RepoGroup, Lane } from './repo-tree.js';
import type { SessionRow } from './rail-data.js';
import { SessionNode, KidNode } from './SessionNode.js';

export const X1 = 21;
export const X2 = 35;
const v = (x: number, y1: number | string, y2: number | string) => `<line x1="${x}" y1="${y1}" x2="${x}" y2="${y2}"/>`;

/** 세션 칸의 가지선. more = 이 저장소에서 이 칸 아래로 무언가(다른 세션·가지·종료) 더 있다. */
export function sessionLines(lane: 1 | 2, opts: { more: boolean; moreInLane: boolean; kids: boolean }): string {
  if (lane === 1) return v(X1, 0, 8) + (opts.more || opts.kids ? v(X1, 24, '100%') : '');
  return (opts.more ? v(X1, 0, '100%') : '') + v(X2, 0, 8) + (opts.moreInLane || opts.kids ? v(X2, 24, '100%') : '');
}
/** 위임 하위 칸의 가지선 — 부모 마디의 x에서 굽어 나온다. */
export function kidLines(lane: 1 | 2, opts: { more: boolean; continueParent: boolean }): string {
  const x = lane === 2 ? X2 : X1;
  const trunk = lane === 2 && opts.more ? v(X1, 0, '100%') : '';
  return trunk + `<path d="M${x} 0V5C${x} 13 ${x + 3} 13 ${x + 9} 13"/>` + (opts.continueParent ? v(x, 0, '100%') : '');
}
/** 워크트리 가지 머리 — 줄기에서 x=35로 굽어 나간다. */
export function worktreeLines(more: boolean): string {
  return (more ? v(X1, 0, '100%') : v(X1, 0, 2)) + `<path d="M${X1} 0C${X1} 16 ${X2} 6 ${X2} 24"/>`;
}
export function headLines(hasBody: boolean): string {
  return hasBody ? v(X1, 26, '100%') : '';
}
export function endLines(last: boolean): string {
  return v(X1, 0, last ? 9 : '100%');
}

const laneKey = (l: Lane<SessionRow>) => l.worktree?.key ?? '';
const rowKey = (r: SessionRow) => r.tmuxName || r.sessionId || r.name;

export interface RepoBranchProps {
  group: RepoGroup<SessionRow, any>;
  collapsed: boolean;
  onToggle: () => void;
  isActive: (row: SessionRow) => boolean;
  /** 세션 칸에 넘길 나머지(편집·메뉴·열기·위임 접기 …) — Rail.tsx가 정한다. */
  nodeProps: (row: SessionRow, kids?: SessionRow[]) => Omit<Parameters<typeof SessionNode>[0], 'row' | 'active' | 'geometry'>;
  kidsCollapsed: (row: SessionRow) => boolean;
  /** 「종료 N」 칸들. 마지막 칸 여부를 받아 그린다. */
  ended?: (last: boolean) => JSX.Element;
  hasEnded: boolean;
  /** 세션 칸 바로 밑(내부 서브에이전트 목록). */
  afterNode?: (row: SessionRow) => JSX.Element;
}

export function RepoBranch(p: RepoBranchProps) {
  const g = () => p.group;
  const lanes = () => g().lanes;
  const hasBody = () => !p.collapsed && (lanes().length > 0 || p.hasEnded);
  const title = () => (g().repository ? g().name : t('저장소 밖'));
  // 이 가지(lane) 뒤에 다른 가지나 종료 칸이 있는가 — 줄기(x=21)를 계속 그을지.
  const moreAfterLane = (li: number) => li < lanes().length - 1 || p.hasEnded;
  return (
    <section class="vt-rg" classList={{ outside: !g().repository, collapsed: p.collapsed }} aria-label={title()}>
      <button type="button" class="vt-rg-row vt-rg-head" aria-expanded={!p.collapsed} onClick={p.onToggle}
        data-tip={g().root || undefined} data-tip-side="right">
        <span class="vt-rg-gut">
          <svg class="vt-rg-ln" aria-hidden="true" innerHTML={headLines(hasBody())} />
          <span class="vt-rg-ic" innerHTML={shellIcon(g().repository ? 'folder' : 'home', 13, 2)} />
        </span>
        <span class="vt-rg-body vt-rg-a">
          <span class="vt-rg-rn">{title()}</span>
          <Show when={g().mainBranch}>
            <span class="vt-rg-rb" data-tip={t('메인 작업 트리의 브랜치')} data-tip-side="right">
              <span innerHTML={icon('git-branch', 12, 2)} />{g().mainBranch}
            </span>
          </Show>
          <span class="vt-rg-sp" />
          <Show when={g().count > 0}><span class="vt-rg-tm">{g().count}</span></Show>
          <span class="vt-rg-caret" innerHTML={icon('chevron-down', 12, 2)} />
        </span>
      </button>
      <Show when={!p.collapsed}>
        {/* 갈래·세션은 열쇠(문자열)로 돈다 — 폴링마다 새 객체가 와도 칸 DOM(편집 중 입력창·열린 메뉴·포커스)이 그대로다. */}
        <For each={lanes().map(laneKey)}>{(key, li) => (
          <Show when={lanes().find((l) => laneKey(l) === key)}>
            {(lane) => <LaneView lane={lane()} more={moreAfterLane(li())} {...p} />}
          </Show>
        )}</For>
        <Show when={p.hasEnded}>{p.ended?.(true)}</Show>
      </Show>
    </section>
  );
}

function LaneView(props: RepoBranchProps & { lane: Lane<SessionRow>; more: boolean }) {
  const lane2 = () => !!props.lane.worktree;
  const laneNo = (): 1 | 2 => (lane2() ? 2 : 1);
  const sessions = () => props.lane.sessions;
  return (
    <>
      <Show when={props.lane.worktree}>
        {(w) => (
          <div class="vt-rg-row vt-rg-wt" data-tip={w().key} data-tip-side="right">
            <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={worktreeLines(true)} /></span>
            <span class="vt-rg-body">
              <span class="vt-rg-wtl">{t('워크트리')}</span>
              <span class="vt-rg-name">{w().branch || w().name}</span>
            </span>
          </div>
        )}
      </Show>
      <For each={sessions().map((x) => rowKey(x.row))}>{(key, si) => {
        const entry = () => sessions().find((x) => rowKey(x.row) === key);
        const lastInLane = () => si() === sessions().length - 1;
        const kidsShown = () => { const e = entry(); return !e || props.kidsCollapsed(e.row) ? [] : e.kids; };
        // 메인 가지(lane 1)는 줄기 자체가 가지라 「아래에 더 있음」에 같은 가지의 다음 세션도 든다.
        const more = () => (lane2() ? props.more : !lastInLane() || props.more);
        return (
          <Show when={entry()}>
            {(s) => <>
              <SessionNode
                row={s().row}
                active={props.isActive(s().row)}
                geometry={{ lane: laneNo(), lines: sessionLines(laneNo(), { more: more(), moreInLane: !lastInLane(), kids: kidsShown().length > 0 }) }}
                {...props.nodeProps(s().row, s().kids)}
              />
              {props.afterNode?.(s().row)}
              <For each={kidsShown().map(rowKey)}>{(kk, ki) => (
                <Show when={kidsShown().find((k) => rowKey(k) === kk)}>
                  {(kid) => (
                    <KidNode
                      row={kid()}
                      active={props.isActive(kid())}
                      lane={laneNo()}
                      lines={kidLines(laneNo(), {
                        more: props.more,
                        continueParent: ki() < kidsShown().length - 1 || (lane2() ? !lastInLane() : more()),
                      })}
                      {...props.nodeProps(kid())}
                    />
                  )}
                </Show>
              )}</For>
            </>}
          </Show>
        );
      }}</For>
    </>
  );
}
