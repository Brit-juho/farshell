// 레일의 **행 하나와 컨텍스트 메뉴**. 목록 조립·상태 관리는 Rail.tsx.
//
// 행 모양(리디자인 4단계, 시안 desktop.html D02 「행 해부」) — 왼쪽 16px 열에 상태 아이콘
// (읽지 않음이면 둘레 링), 오른쪽 열에 최대 4줄:
//   1줄 20  에이전트 14 · 이름 13/600(제자리 편집) · 위임 토글 · 경과 mono 12 · ⋯
//   2줄 18  WT · 디렉터리 · 브랜치(main도 항상) · 변경량 — mono 12. 정보가 없으면 「정보 갱신 대기」
//   3줄 18  상태 키워드 12/600 · 도구 · 배지 · 내부 서브에이전트 칩 · 공유 · 만든 곳
//   4줄 18  다른 화면 N · 웹이 아닌 터미널 연결 수(있을 때만)
// waiting이고 열린 행이면 그 아래에 답 버튼 줄(24)이 붙는다(AnswerButtons — 폰 Fleet과 같은 것).
// 묶음 제목은 ViewGroup 머리에 한 번만. 상태 왼쪽 막대(옛 vt-wgrail-bar)와 tmux 플러그 아이콘은
// 없다 — 상태는 아이콘의 색+모양이 말한다. 왼쪽 2px 선은 선택(강조색)·개입 필요(호박색)만 쓴다.
//
// ⚠ 여기도 `shell` 지연 청크다 — core/store.js 계열을 직접 import하지 않는다
// (Rail.tsx 머리말과 같은 이유, ADR-26/N35).
import { For, Show, createSignal, onCleanup, onMount } from 'solid-js';

import { icon } from '../ui/icons.js';
import { STATE_LABEL, normalizeState } from '../design/state-classes.js';
import { ringSvg, subBadge } from './compact-mark.js';
import { normalizeSubagents } from './native-subagents.js';
import { shellIcon } from '../ui/icons-shell.js';
import { agentMark } from './agent-mark.js';
import { t } from '../core/i18n.js';
import {
  stateKeyword, formatRelativeTime, lineageLabel,
  ORIGIN_ICON, originLabel, originShort, SHARE_MODE_LABEL, shareSentence, profileLabel, type SessionRow, type SessionProfile, type Origin, type ShareInfo, type RowSummaryDetails,
} from './rail-data.js';


/** 만든 곳 아이콘(local은 없다). 레일 행과 Fleet 행이 같이 쓴다.
 * `withText`면 아이콘 뒤에 짧은 글(「다른 맥에서 만듦」·「직접 만듦」)을 붙인다 — 레일 행 3줄(시안 D02). 글이 있으면
 * 아이콘은 장식이고 글이 읽힌다(같은 말을 두 번 읽지 않게). 툴팁은 늘 긴 문장이다. */
export function OriginMark(props: { origin: Origin; host?: string | null; withText?: boolean }) {
  const short = () => originShort(props.origin, props.host);
  const label = () => originLabel(props.origin, props.host);
  const ico = () => icon(ORIGIN_ICON[props.origin as Exclude<Origin, 'local'>], 12, 2);
  return (
    <Show when={props.origin !== 'local'}>
      <Show
        when={props.withText}
        fallback={
          <span
            class="vt-srow-origin"
            data-origin={props.origin}
            data-tip={label()}
            data-tip-side="right"
            role="img"
            aria-label={label()}
            innerHTML={ico()}
          />
        }
      >
        <span
          class="vt-srow-origin with-text"
          data-origin={props.origin}
          data-tip={label()}
          data-tip-side="right"
        >
          <span class="vt-srow-origin-ico" aria-hidden="true" innerHTML={ico()} />
          <span class="vt-srow-origin-text">{short()}</span>
        </span>
      </Show>
    </Show>
  );
}

/** 에이전트에게 공유 중인 세션 표시: 아이콘(읽기 전용 = 눈, 읽기·쓰기 = 펜) + 닉네임. 보안 상태라 설정으로 끌 수 없다.
 * 읽기·쓰기는 모양과 글자(「쓰기」)와 색이 모두 다르다. 레일 행과 Fleet 행이 같이 쓴다. */
export function ShareMark(props: { share: ShareInfo | null | undefined }) {
  return (
    <Show when={props.share}>
      {(s) => (
        <span
          class="vt-srow-share"
          data-mode={s().mode}
          data-tip={shareSentence(s())}
          data-tip-side="right"
          role="img"
          aria-label={shareSentence(s())}
        >
          <span innerHTML={icon(s().mode === 'readwrite' ? 'share-write' : 'share-read', 12, 2)} />
          <span class="vt-srow-share-nick">{s().nick}</span>
          <Show when={s().mode === 'readwrite'}><span class="vt-srow-share-mode">{SHARE_MODE_LABEL.readwrite}</span></Show>
        </span>
      )}
    </Show>
  );
}

/** 새 세션을 만들 때 고른 계정 이름(서버가 기록한 것만 — 추측하지 않는다). 셸에서 환경을 바꿨는지는 알 수 없어서
 * 툴팁에 「생성 시 계정」이라고 적는다. 레일 행과 Fleet 행이 같이 쓴다. */
export function ProfileMark(props: { profile: SessionProfile | null | undefined }) {
  return (
    <Show when={props.profile}>
      {(p) => (
        <span
          class="vt-srow-profile"
          data-tip={t('생성 시 계정')}
          data-tip-sub={profileLabel(p())}
          data-tip-side="right"
          role="img"
          aria-label={t('생성 시 계정: {name}', { name: profileLabel(p()) })}
        >
          <span innerHTML={icon('user', 12, 2)} />
          <span class="vt-srow-profile-name">{profileLabel(p())}</span>
        </span>
      )}
    </Show>
  );
}

/** 같은 세션에 붙은 다른 화면. 「다른 화면 N」은 웹 화면(다른 브라우저 탭·폰, 이 탭 제외),
 * 터미널 아이콘 + N은 웹이 아닌 attach(맥 터미널 등). 레일 행과 Fleet 행이 같이 쓴다. */
export function ScreensMark(props: { row: Pick<SessionRow, 'otherScreens' | 'otherClients'> }) {
  return (
    <>
      <Show when={props.row.otherScreens > 0}>
        <span class="vt-srow-screens" data-tip={t('이 탭 말고 다른 브라우저 탭·폰에서도 열려 있음')} data-tip-side="right">
          <span class="vt-srow-screens-ico" innerHTML={icon('monitor-smartphone', 12, 2)} />
          {t('다른 화면 {n}', { n: props.row.otherScreens })}
        </span>
      </Show>
      <Show when={props.row.otherClients > 0}>
        <span
          class="vt-srow-clients"
          data-tip={t('웹이 아닌 터미널 {n}개가 붙어 있음 (맥 터미널 등)', { n: props.row.otherClients })}
          data-tip-side="right"
          role="img"
          aria-label={t('웹이 아닌 터미널 {n}개가 붙어 있음', { n: props.row.otherClients })}
        >
          <span innerHTML={icon('terminal', 12, 2)} />
          {props.row.otherClients}
        </span>
      </Show>
    </>
  );
}

/** 키워드 줄(상태 키워드 · 도구 · 배지 · 경과). 레일과 Fleet이 같이 쓴다.
 * 데스크톱 레일은 경과를 1줄 오른쪽에 따로 그리므로 `noSince`로 뺀다(Fleet은 아직 3줄에 둔다). */
export function KeywordLine(props: { row: SessionRow; now: () => number; noSince?: boolean }) {
  const kw = () => stateKeyword(props.row);
  const rel = () => formatRelativeTime(props.row.since, props.now());
  return (
    <>
      <span
        class="vt-srow-kw"
        data-state={props.row.status}
        data-tip={props.row.status === 'waiting' && props.row.question ? props.row.question : undefined}
        data-tip-side="right"
      >{kw().text}</span>
      <Show when={kw().tool}><span class="vt-srow-tool">{t('도구: {tool}', { tool: kw().tool })}</span></Show>
      <Show when={kw().badge}><span class="vt-srow-badge">{kw().badge}</span></Show>
      <Show when={!props.noSince && rel()}><span class="vt-srow-since">{rel()}</span></Show>
    </>
  );
}

/** 하위 세션의 위임 상태 표시(진행 중·보고·닫힘). 레일 행과 Fleet 행이 같이 쓴다. */
export function LineageMark(props: { row: SessionRow }) {
  return (
    <Show when={props.row.lineage}>
      {(l) => (
        <span
          class="vt-srow-lineage"
          data-lstate={l().state}
          data-outcome={l().outcome || undefined}
          data-tip={l().task ? t('맡은 일 · {task}', { task: l().task }) : undefined}
          data-tip-side="right"
        >{lineageLabel(l())}</span>
      )}
    </Show>
  );
}

/** 부모 행의 「하위 보고 1/2」 칩 + 하위 접기 토글. 레일 행과 Fleet 행이 같이 쓴다. */
export function KidsToggle(props: {
  progress: { reported: number; open: number } | null; count: number; collapsed: boolean; name: string;
  onToggle?: () => void;
}) {
  return (
    <Show when={props.count > 0 && props.onToggle}>
      <button
        type="button"
        class="vt-srow-kids"
        classList={{ collapsed: props.collapsed }}
        aria-expanded={!props.collapsed}
        aria-label={props.collapsed ? t('{name}의 하위 세션 펼치기', { name: props.name }) : t('{name}의 하위 세션 접기', { name: props.name })}
        data-tip={t('FarShell 위임 세션 {n}개', { n: props.count })}
        data-tip-side="right"
        onClick={(e) => { e.stopPropagation(); props.onToggle?.(); }}
      >
        <span class="vt-srow-kids-ico" innerHTML={shellIcon('corner-down-right', 12, 2)} />
        <Show when={props.progress} fallback={<span>{t('위임 세션 {n}', { n: props.count })}</span>}>
          {(p) => <span>{t('위임 보고 {r}/{n}', { r: p().reported, n: p().open })}</span>}
        </Show>
        <span class="vt-srow-kids-caret" innerHTML={icon('chevron-down', 11, 2)} />
      </button>
    </Show>
  );
}

/**
 * 개입 필요 행의 인라인 답 버튼(「1 · 예」 「3 · 아니오」 …). 데스크톱 레일 행과 폰 Fleet 행이 같이 쓴다.
 * 선택지는 서버가 감지한 `row.options`, 보내기는 rail-fetch.ts `answerSessionPrompt` 하나다.
 *   rail   선택지 + 항상 「터미널로」(조용한 버튼) — 선택지에 없는 답은 터미널에서 한다.
 *   fleet  선택지(번호를 따로 그린 44px 버튼, 리디자인 8단계), 없으면 「터미널로」(채움) 하나.
 * 열려 있지 않은 세션은 키를 보낼 곳(열린 칸)이 없다 — 부르는 쪽이 `row.open`을 보고 안 그린다.
 */
export function AnswerButtons(props: {
  row: SessionRow; variant: 'rail' | 'fleet';
  onAnswer: (key: string) => Promise<void>; onTerminal: () => void;
}) {
  // P1-1(ADR-33) — 같은 화면의 이중 클릭을 여기서 먼저 막는다(서버 409는 그 다음 방어선).
  // 응답이 돌아오기 전까지 이 행의 버튼을 다시 못 누르게 한다.
  const [answering, setAnswering] = createSignal(false);
  const answer = async (key: string) => {
    if (answering()) return;
    setAnswering(true);
    try { await props.onAnswer(key); } finally { setAnswering(false); }
  };
  const options = () => props.row.options || [];
  const rail = () => props.variant === 'rail';
  return (
    <div
      class={rail() ? 'vt-srow-answers' : 'vt-fleet-answers'}
      role="group"
      aria-label={t('{name}에 답하기', { name: props.row.name })}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <For each={options()}>
        {(opt) => (
          // 선택지 글이 길면(「Yes, and don't ask again …」) 버튼이 행 폭을 넘지 않게 말줄임하고 전문은 툴팁으로.
          <button type="button" class="vt-btn sm vt-srow-answer" disabled={answering()} onClick={() => void answer(opt.key)}
            data-tip={`${opt.key} · ${opt.label}`} data-tip-side="bottom">
            {/* 폰 카드(시안 M01)는 번호를 대기색으로 따로 그린다 — 「1 예」. 데스크톱 레일은 한 덩어리 「1 · 예」. */}
            <Show when={rail()} fallback={<><span class="vt-srow-answer-key">{opt.key}</span><span class="vt-srow-answer-label">{opt.label}</span></>}>
              <span class="vt-srow-answer-label">{opt.key} · {opt.label}</span>
            </Show>
          </button>
        )}
      </For>
      <Show when={rail() || options().length === 0}>
        <button
          type="button"
          class="vt-btn sm vt-srow-to-term"
          classList={{ quiet: rail(), solid: !rail() }}
          onClick={() => props.onTerminal()}
        >
          <Show when={rail()}><span class="vt-srow-to-term-ico" innerHTML={icon('terminal', 12, 2)} /></Show>
          {t('터미널로')}
        </button>
      </Show>
    </div>
  );
}

/**
 * 접힌 48px 띠의 칸 하나 — 에이전트 마크 가운데 + 둘레 상태 링(시안 3-B). 펼친 레일은 가지 그래프 칸
 * (SessionNode.tsx)을 쓴다. 이름·상태 문장은 숨기고 호버·포커스 때 뜨는 상세 카드(RailHoverCard.tsx)가 맡는다 —
 * 공용 data-tip 툴팁은 달지 않는다(이중으로 뜨지 않게). 칸의 이름은 aria-label, 카드는 열린 동안 aria-describedby.
 */
export function Row(props: {
  row: SessionRow; active: boolean;
  /** 늘 접힌 띠의 칸이다(옛 호출 호환용 표시). */
  compact?: boolean;
  /** 위임 하위 세션 칸(부모 밑 가지선은 Rail.tsx의 .vt-wgrail-kidcell이 긋는다). */
  kid?: boolean;
  /** 열린 칸만 끌 수 있다 — 터미널 칸에 놓으면 분할(묶음)이 생긴다. */
  draggable?: boolean; onDragStart?: (e: DragEvent) => void;
  onOpen: (e: MouseEvent) => void;
  /** 우클릭 메뉴(⋯ 버튼은 접힌 칸에 없다). */
  onContext: (e: MouseEvent) => void;
}) {
  const unseen = () => props.row.unseen && props.row.status !== 'idle';
  // 칸 오른쪽 위 개수 배지(S1) — 작업 중인 내부 서브에이전트 수.
  const sub = () => normalizeSubagents(props.row.subagents);
  const subWorking = () => sub().items.filter((r: any) => r.state === 'working').length;
  const subTip = () => sub().limited ? t('내부 에이전트 · 표시 한도 도달') : subWorking() > 0 ? t('내부 에이전트 {n} 작업 중', { n: subWorking() }) : '';
  const badge = () => subBadge(subWorking(), sub().limited);
  // 상태 이름(스크린리더) — 링은 모양뿐이라 이름이 없으면 읽히지 않는다.
  const ringLabel = () => STATE_LABEL[normalizeState(props.row.status) as keyof typeof STATE_LABEL] + (unseen() ? ' · ' + t('아직 확인 안 함') : '');
  const cardKey = () => props.row.tmuxName || props.row.name;
  // div에 role=button만 붙이면 Enter·Space가 아무 일도 안 한다 — 직접 붙인다(Space는 스크롤을 막는다).
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    if (e.key !== 'Enter') e.preventDefault();
    props.onOpen(e as unknown as MouseEvent);
  };
  return (
    <div
      class="vt-srow vt-wgrail-row"
      classList={{
        active: props.active, closed: !props.row.open, kid: !!props.kid,
        'is-waiting': props.row.status === 'waiting', 'is-quiet': props.row.status === 'idle',
      }}
      data-state={props.row.status}
      onClick={props.onOpen}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => { e.preventDefault(); props.onContext(e); }}
      draggable={!!props.draggable}
      onDragStart={props.onDragStart}
      role="button"
      tabindex="0"
      aria-current={props.active ? 'true' : undefined}
      // agent/paint.js의 전역 클릭 위임이 `[data-session-id]`로 「봤다」를 판정한다.
      data-session-id={props.row.sessionId || undefined}
      aria-label={props.row.name}
      data-card-key={cardKey()}
    >
      <span class="vt-srow-state vt-wgrail-ring" data-state={normalizeState(props.row.status)} role="img" aria-label={ringLabel()} innerHTML={ringSvg(props.row.status)} />
      {/* 에이전트를 아직 모르거나 셸이면 셸 프롬프트 마크 — 칸이 비면 링만 덩그러니 남는다. */}
      <span class="vt-srow-agent vt-wgrail-agent vt-wgrail-agent-compact" data-state={props.row.status} innerHTML={agentMark(props.row.agent || 'shell')} />
      <Show when={unseen()}><span class="vt-wgrail-unseen" aria-hidden="true" /></Show>
      <Show when={badge()}>
        {(b) => <span class="vt-wgrail-subbadge" classList={{ limited: b().limited }} role="img" aria-label={subTip()}>{b().text}</span>}
      </Show>
    </div>
  );
}

export interface MenuItem {
  label: string;
  run: () => void;
  /** C1 호스트 메뉴의 둘째 줄("세션 3 · 12ms" 또는 "응답 없음"). 없으면 안 그린다. */
  detail?: string;
  /** 흐리게(오프라인 호스트). 선택 자체는 막지 않는다 — 꺼진 호스트를 고르면
   * 이유를 보여주는 게 목적이다. */
  dim?: boolean;
  checked?: boolean;
  /** 되돌릴 수 없는 항목(종료). */
  danger?: boolean;
  /** 항목 앞 14px 아이콘 이름(icons.js · icons-shell.js). 메뉴 한 규격(components.css)이 아이콘 칸을 둔다. */
  icon?: string;
  /** 위에 구분선을 긋는다. 생략하면 위험 항목(danger)만 — 첫 항목이 아닐 때 — 자동으로 선이 선다. */
  sepBefore?: boolean;
}

/** 행 메뉴 항목 id → 아이콘. 레일 메뉴(Rail.tsx)와 폰 시트(Fleet.tsx)가 같이 쓴다. */
export const ROW_MENU_ICON: Record<string, string> = {
  open: 'play', close: 'x', rename: 'pencil', openOnMac: 'laptop', screens: 'monitor-smartphone',
  kill: 'trash-2', splitRight: 'columns-2', splitDown: 'rows-2', sessionAccess: 'shield', share: 'share-read',
  moveUp: 'arrow-up', moveDown: 'arrow-down', report: 'file-text', closeDelegation: 'x',
  openParent: 'corner-down-left', record: 'history',
};

/** 레일과 모바일 시트가 공유하는 선택 불가 정보 헤더. */
export function MenuSummary(props: { summary: string | RowSummaryDetails }) {
  const details = () => typeof props.summary === 'string' ? null : props.summary;
  const accessible = () => typeof props.summary === 'string' ? props.summary : props.summary.accessible;
  return (
    <div class="vt-menu-summary" role="note" aria-label={t('요약: {summary}', { summary: accessible() })}>
      <div class="vt-menu-summary-status">{details()?.status ?? accessible()}</div>
      <Show when={details()?.cwd}>
        <div class="vt-menu-summary-section">
          <span class="vt-menu-summary-label">{t('작업 위치')}</span>
          <strong class="vt-menu-summary-project">{details()?.project}</strong>
          <span class="vt-menu-summary-path">{details()?.cwd}</span>
        </div>
      </Show>
      <Show when={details()?.branch}>
        <div class="vt-menu-summary-section">
          <span class="vt-menu-summary-label">{t('브랜치')}</span>
          <span class="vt-menu-summary-branch">{details()?.branch}</span>
        </div>
      </Show>
    </div>
  );
}

/** `summary`: 선택 불가 정보 헤더. 일반 문자열 메뉴도 그대로 지원한다. */
export function Menu(props: { x: number; y: number; onClose: () => void; items: MenuItem[]; summary?: string | RowSummaryDetails }) {
  let ref: HTMLDivElement | undefined;
  const previousFocus = document.activeElement as HTMLElement | null;
  const onDocClick = (e: MouseEvent) => { if (ref && !ref.contains(e.target as Node)) props.onClose(); };
  document.addEventListener('mousedown', onDocClick, true);
  onCleanup(() => {
    document.removeEventListener('mousedown', onDocClick, true);
    if (ref?.contains(document.activeElement) && previousFocus?.isConnected) previousFocus.focus();
  });
  // 가로 폰(보이는 높이 331px)에서 아래쪽 행의 메뉴가 뷰포트 밖으로 넘쳐 항목에 닿을 수
  // 없었다(2026-10-06 폰 실측). 그려진 뒤 크기를 재서 안으로 당기고, 그래도 넘치면 스크롤.
  onMount(() => {
    if (!ref) return;
    const vv = window.visualViewport;
    const vh = vv ? vv.height : window.innerHeight;
    const vw = vv ? vv.width : window.innerWidth;
    const pad = 8;
    const r = ref.getBoundingClientRect();
    ref.style.maxHeight = `${Math.max(120, vh - pad * 2)}px`;
    ref.style.overflowY = 'auto';
    const h = Math.min(r.height, vh - pad * 2);
    ref.style.top = `${Math.max(pad, Math.min(props.y, vh - h - pad))}px`;
    ref.style.left = `${Math.max(pad, Math.min(props.x, vw - r.width - pad))}px`;
    ref.querySelector<HTMLElement>('.vt-menu-item')?.focus();
  });

  return (
    <div ref={ref} class="vt-menu" style={{ left: `${props.x}px`, top: `${props.y}px`, right: 'auto' }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') { e.stopPropagation(); props.onClose(); return; }
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        e.preventDefault(); e.stopPropagation();
        const items = Array.from(ref?.querySelectorAll<HTMLElement>('.vt-menu-item') || []);
        const index = items.indexOf(document.activeElement as HTMLElement);
        items[(index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
      }}>
      <Show when={props.summary}>
        <MenuSummary summary={props.summary!} />
      </Show>
      <For each={props.items}>
        {(it, i) => (<>
          <Show when={it.sepBefore ?? (!!it.danger && i() > 0)}><div class="vt-menu-sep" role="separator" /></Show>
          <div
            class="vt-menu-item"
            role="button" tabindex="0"
            onKeyDown={(e) => {
              if (e.key !== 'Enter' && e.key !== ' ') return;
              e.preventDefault(); e.stopPropagation(); props.onClose(); it.run();
            }}
            classList={{ dim: !!it.dim, checked: !!it.checked, danger: !!it.danger }}
            onClick={() => { props.onClose(); it.run(); }}
          >
            <Show when={it.icon}><span class="vt-menu-item-ico" innerHTML={shellIcon(it.icon!, 14, 2)} /></Show>
            <span class="vt-menu-item-label">{it.checked ? '✓ ' : ''}{it.label}</span>
            <Show when={it.detail}><span class="vt-menu-item-detail">{it.detail}</span></Show>
          </div>
        </>)}
      </For>
    </div>
  );
}
