// 저장소 묶음 맨 아래의 「종료 N」(세션 로직 점검 단계 5, 시안 5차 P2).
//
//   ◌ 종료 2 · 마지막 3시간 전          ← 누르면 펼침(기기별 기억)
//   ◌ [마크] 제목            3시간 전  [▶ 이어서]  ⋯
//        ⑂ fix/device-findings · 대화 3
//        「키보드 올라올 때 입력창 가려지는 것부터 고쳐줘」
//      (칸을 누르면) 연결된 대화 목록 + 「이어서 시작하면 farshell에서 farshell-3 세션으로 열리고 맨 위 대화를 이어 갑니다」
//
// 「이어서」 버튼은 늘 보인다(호버에만 나오지 않는다). 종료된 세션의 이름 변경은 없다 — 보이는 이름은 실행 중에 바꾸고,
// 이어서 시작하면 같은 세션 기록으로 돌아온다.
import { For, Show, createSignal } from 'solid-js';
import { Portal } from 'solid-js/web';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { agentMark } from './agent-mark.js';
import { Menu, type MenuItem } from './RailRow.js';
import { endedActivity } from './repo-tree.js';
import { endLines } from './RepoBranch.js';
import { historyApi } from '../history/api.js';
import {
  resumeSession, setSessionArchived, recheckCatalog, executionStatus, isLegacy, agoLabel, deleteSession,
} from '../history/session-actions.js';

const hollow = (last: boolean) => endLines(last);
// 종료 칸(여러 줄)의 선 — 마지막 칸은 점선 마디(y=12)에서 멈춘다. 펼친 대화 상자 아래로 선이 늘어지지 않게.
const itemLines = (last: boolean) => (last ? '<line x1="21" y1="0" x2="21" y2="12"/>' : endLines(false));

export function EndedFold(p: {
  sessions: any[]; open: boolean; onToggle: () => void; onChanged: () => void; archived?: boolean;
}) {
  const count = () => p.sessions.length;
  const latest = () => (p.sessions[0] ? endedActivity(p.sessions[0]) : 0);
  return (
    <>
      <button type="button" class="vt-rg-row vt-rg-end vt-rg-fold" aria-expanded={p.open} onClick={p.onToggle}>
        <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={hollow(!p.open)} /><span class="vt-rg-hol" /></span>
        <span class="vt-rg-body vt-rg-a">
          <span class="vt-rg-caret" classList={{ open: p.open }} innerHTML={icon('chevron-right', 12, 2)} />
          <span>{p.archived ? t('보관 {n}', { n: count() }) : t('종료 {n}', { n: count() })}</span>
          <span class="vt-rg-sp" />
          <Show when={latest()}><span class="vt-rg-tm">{t('마지막 {ago}', { ago: agoLabel(latest()) })}</span></Show>
        </span>
      </button>
      <Show when={p.open}>
        <For each={p.sessions}>{(s, i) => <EndedItem session={s} last={i() === p.sessions.length - 1} onChanged={p.onChanged} />}</For>
      </Show>
    </>
  );
}

export function EndedItem(p: { session: any; last: boolean; onChanged: () => void }) {
  const s = () => p.session;
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal('');
  const [menuAt, setMenuAt] = createSignal<{ x: number; y: number } | null>(null);
  const [detail, setDetail] = createSignal<{ conversations: any[]; preview: any } | null>(null);
  const [opened, setOpened] = createSignal(false);
  const legacy = () => isLegacy(s());
  const status = () => executionStatus(s());
  const archived = () => !!s().archived;
  const resumable = () => !archived() && s().ended != null && !!s().resume_conversation_id;
  const run = async (fn: () => Promise<unknown>) => {
    if (busy()) return;
    setBusy(true); setError('');
    try { await fn(); p.onChanged(); } catch (e: any) { setError(e?.message || t('작업을 완료하지 못했습니다')); } finally { setBusy(false); }
  };
  const openRecord = async () => {
    try { const { openSessionRecord } = await import('../history/session-record.js'); openSessionRecord(s().id, p.onChanged); }
    catch (e: any) { setError(e?.message || t('작업을 완료하지 못했습니다')); }
  };
  const toggle = async () => {
    const next = !opened();
    setOpened(next);
    if (!next || detail()) return;
    try {
      const id = encodeURIComponent(s().id);
      const [info, preview] = await Promise.all([
        historyApi(`/api/session-catalog/sessions/${id}`),
        resumable() ? historyApi(`/api/session-catalog/sessions/${id}/resume-preview`).catch(() => null) : Promise.resolve(null),
      ]);
      setDetail({ conversations: Array.isArray(info?.conversations) ? info.conversations : [], preview });
    } catch (e: any) { setError(e?.message || t('기록을 읽지 못했습니다')); }
  };
  const items = (): MenuItem[] => {
    const out: MenuItem[] = [];
    if (resumable()) out.push({ icon: 'play', label: t('이어서 시작'), run: () => { void run(() => resumeSession(s().id)); } });
    out.push({ icon: 'history', label: t('작업 기록'), run: () => { void openRecord(); } });
    if (archived()) out.push({ icon: 'archive', label: t('보관 해제'), run: () => { void run(() => setSessionArchived(s(), false)); } });
    else if (s().ended != null) out.push({ icon: 'archive', label: t('세션 보관'), run: () => { void run(() => setSessionArchived(s(), true)); } });
    if (archived()) out.push({ icon: 'trash-2', label: t('삭제'), detail: t('되돌릴 수 없음'), danger: true, run: () => { void run(() => deleteSession(s())); } });
    return out;
  };
  const openMenu = (e: MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    const btn = (e.target as HTMLElement | null)?.closest?.('.vt-rg-more');
    const r = btn?.getBoundingClientRect();
    setMenuAt({ x: r ? r.left : e.clientX, y: r ? r.bottom + 2 : e.clientY });
  };
  const branch = () => s().branch_at_end || '';
  const lastText = () => (typeof s().last_user_text === 'string' && s().last_user_text ? s().last_user_text : '');
  return (
    <div class="vt-rg-ended" classList={{ 'is-busy': busy(), sel: opened() }}>
      {/* 칸 자체는 버튼이 아니다 — 펼치기(제목 버튼)·이어서·메뉴가 서로 형제인 버튼이다(버튼 안에 버튼을 두지 않는다).
          칸의 빈 곳을 눌러도 펼쳐지는 것은 마우스 편의일 뿐이고, 키보드는 제목 버튼으로 같은 일을 한다. */}
      <div class="vt-rg-row vt-rg-end vt-rg-er" onClick={() => { void toggle(); }} onContextMenu={openMenu}>
        <span class="vt-rg-gut"><svg class="vt-rg-ln" aria-hidden="true" innerHTML={itemLines(p.last)} /><span class="vt-rg-hol" /></span>
        <div class="vt-rg-body">
          <div class="vt-rg-a">
            <button type="button" class="vt-rg-er-toggle" aria-expanded={opened()}
              aria-label={t('{name} 연결된 대화 보기', { name: s().title })}
              onClick={(e) => { e.stopPropagation(); void toggle(); }}>
              <Show when={s().provider}><span class="vt-rg-mk2" innerHTML={agentMark(s().provider)} /></Show>
              <span class="vt-rg-name">{s().title}</span>
            </button>
            <span class="vt-rg-sp" />
            <span class="vt-rg-tm">{agoLabel(endedActivity(s()))}</span>
            <Show when={resumable()} fallback={
              <Show when={status().recheck}>
                <button type="button" class="vt-rg-rs q" disabled={busy()} title={status().hint}
                  onClick={(e) => { e.stopPropagation(); void run(async () => { const r = await recheckCatalog(); if (!r.complete) throw new Error(t('아직 터미널 상태를 읽지 못했습니다. 잠시 뒤 다시 시도하세요')); }); }}>
                  {busy() ? t('확인하는 중…') : t('다시 확인')}
                </button>
              </Show>
            }>
              <button type="button" class="vt-rg-rs" disabled={busy()}
                aria-label={t('{name} 이어서 시작', { name: s().title })}
                onClick={(e) => { e.stopPropagation(); void run(() => resumeSession(s().id)); }}>
                <span innerHTML={shellIcon('play', 10, 2)} />{busy() ? t('시작하는 중…') : t('이어서')}
              </button>
            </Show>
            <button type="button" class="vt-icon-btn sm vt-rg-more" aria-haspopup="menu" aria-label={t('{name} 메뉴', { name: s().title })}
              data-tip={t('메뉴')} data-tip-side="left" onClick={(e) => { e.stopPropagation(); openMenu(e); }} innerHTML={icon('more-horizontal', 16, 2)} />
          </div>
          <Show when={branch() || s().conversation_count}>
            <div class="vt-rg-b mono">
              <Show when={branch()}><span innerHTML={icon('git-branch', 12, 2)} /><span class="vt-rg-nm">{branch()}</span></Show>
              <Show when={s().conversation_count}><span class="vt-rg-tm ui">{t('대화 {n}', { n: s().conversation_count })}</span></Show>
            </div>
          </Show>
          <Show when={lastText()}><div class="vt-rg-b"><span class="vt-rg-sm">「{lastText()}」</span></div></Show>
          <Show when={legacy()}><div class="vt-rg-b"><span class="vt-rg-sm">{t('연결된 대화 없음')}</span></div></Show>
          <Show when={opened() && detail()}>
            {(d) => (
              <div class="vt-rg-cv" onClick={(e) => e.stopPropagation()}>
                <Show when={d().conversations.length > 0}>
                  <div class="vt-rg-cvh">{t('연결된 대화 {n}', { n: d().conversations.length })}</div>
                </Show>
                <For each={d().conversations}>{(c, ci) => (
                  <div class="vt-rg-cvr" classList={{ on: ci() === 0 && resumable() }}>
                    <Show when={c.provider}><span class="vt-rg-mk2" innerHTML={agentMark(c.provider)} /></Show>
                    <span class="vt-rg-nm">{c.title || t('제목 없음')}</span>
                    <span class="vt-rg-tm">{agoLabel(Number(c.updated) || 0)}</span>
                  </div>
                )}</For>
                <Show when={d().preview?.resumable}>
                  <div class="vt-rg-cvn">
                    <Show when={d().preview.name && d().preview.folder} fallback={t('이어서 시작하면 맨 위 대화를 이어 갑니다.')}>
                      {t('이어서 시작하면 {folder}에서 {name} 세션으로 열리고, 맨 위 대화를 이어 갑니다.', { folder: d().preview.folder, name: d().preview.name })}
                    </Show>
                  </div>
                </Show>
                <button type="button" class="vt-btn sm quiet vt-rg-record" onClick={() => { void openRecord(); }}>{t('작업 기록 열기')}</button>
              </div>
            )}
          </Show>
          <Show when={error()}><p role="status" class="vt-wgrail-note">{error()}</p></Show>
        </div>
      </div>
      <Show when={menuAt()}>{(m) => <Portal><Menu x={m().x} y={m().y} onClose={() => setMenuAt(null)} items={items()} /></Portal>}</Show>
    </div>
  );
}
