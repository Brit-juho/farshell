// 내부 서브에이전트 기록(지시 · 답변 · 도구 호출 · 도구 결과)을 그리는 본체.
//
// 리디자인 6단계(시안 O15) — 따로 뜨던 「서브에이전트 기록」 창(#vt-subagent)은 없앴다. 같은 내용이 「작업 기록」
// 창(history/session-record.js) 오른쪽에 나온다. 이 파일은 그 오른쪽 칸에 기록을 붙이는 함수 하나와 기록 줄 그리기만
// 남긴다 — 받아 오기(폴링·이어 받기·합치기)는 그대로 panels/subagent-poll.js다.
//
// 기록 글자는 에이전트가 쓴 것이라 전부 textContent로 넣는다(마크업으로 해석하지 않는다).
import { t } from '../core/i18n.js';
import { nativeStateLabel } from '../shell/native-subagents.js';
import { startSubagentPolling, mergeTranscript } from './subagent-poll.js';

/** 기록 종류 → 보이는 이름. 작업 기록 창의 위임 세션 기록(대화 보관함 메시지)도 같은 이름을 쓴다. */
export function kindLabels() {
  return { user: t('지시'), assistant: t('답변'), tool_call: t('도구 호출'), tool_result: t('도구 결과') };
}

/**
 * 기록 한 줄(시안 O15 오른쪽): 왼쪽 76px 열에 종류(와 도구 이름), 오른쪽에 본문. 도구 호출·결과는 터미널 면 상자에.
 * row: { kind, text, tool?, note? } — note는 「기록 반영 대기」처럼 덧붙이는 말.
 */
export function drawEntry(doc, row) {
  const labels = kindLabels();
  const article = doc.createElement('article');
  article.className = 'vt-wl-entry vt-native-entry';
  article.dataset.kind = row.kind;
  const head = doc.createElement('div');
  head.className = 'vt-wl-entry-kind vt-native-entry-label';
  head.textContent = labels[row.kind] || row.kind;
  if (row.tool || row.note) {
    const sub = doc.createElement('div');
    sub.className = 'vt-wl-entry-sub';
    sub.textContent = [row.tool ? String(row.tool).slice(0, 200) : '', row.note || ''].filter(Boolean).join(' · ');
    head.appendChild(sub);
  }
  const text = doc.createElement('pre');
  text.className = row.kind === 'tool_call' || row.kind === 'tool_result' ? 'vt-wl-entry-body vt-wl-term' : 'vt-wl-entry-body';
  text.textContent = String(row.text || '').slice(0, 2000);
  article.append(head, text);
  return article;
}

/**
 * `list`(오른쪽 칸 기록 목록)에 내부 서브에이전트 기록을 이어 붙이고 바뀌면 다시 그린다.
 *   tmuxName · parentId · child · host — 어느 세션의 어느 서브에이전트인지(host는 그때 보던 호스트).
 *   status(text) — 「기록이 아직 생성되지 않았습니다」 같은 안내 줄을 바꾼다.
 *   meta({ label, state }) — 머리(이름 · 상태 칩)를 바꾼다.
 * 반환: { refresh(), stop() }. 창이 닫히거나 다른 항목을 고르면 stop()을 부른다.
 */
export function mountSubagentTranscript(list, { tmuxName, parentId, child, host, status = () => {}, meta = () => {} }) {
  const doc = list.ownerDocument;
  let cursor = null, items = [], currentState = child.state;
  let unsubscribe = null;
  const selected = () => list.isConnected && host === (window.vtRelayHost?.() || 'local');
  const state = () => {
    const summary = window.agentDetails?.()[tmuxName]?.subagents;
    if (summary && summary.parent_id !== parentId) return 'gone';
    const live = summary?.items?.find((r) => r.id === child.id);
    return live?.state || currentState;
  };
  const visibility = () => poll?.visibility();
  document.addEventListener('visibilitychange', visibility);
  const poll = startSubagentPolling({ state, visible: () => selected() && !document.hidden,
    fetchPage: async (signal) => {
      if (!selected() || state() === 'gone') { const e = new Error(t('서브에이전트를 찾을 수 없습니다')); e.status = 404; throw e; }
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
      const page = await window.vtFetch(`/api/agents/${encodeURIComponent(tmuxName)}/subagents/${encodeURIComponent(child.id)}/transcript${query}`, { signal });
      if (!selected() || page.parent_id !== parentId || state() === 'gone') { const e = new Error(t('서브에이전트를 찾을 수 없습니다')); e.status = 404; throw e; }
      return page;
    },
    receive: (page) => {
      currentState = page.state;
      meta({ label: String(page.label || child.label || '').slice(0, 200), state: currentState, stateLabel: nativeStateLabel(currentState) });
      cursor = page.next_cursor;
      items = mergeTranscript(items, page);
      status(page.pending ? t('기록이 아직 생성되지 않았습니다') : page.truncated ? t('일부 기록이 생략되었습니다') : '');
      const scroller = list.parentElement || list;
      const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
      const scroll = scroller.scrollTop;
      const fragment = doc.createDocumentFragment();
      items.forEach((row) => fragment.append(drawEntry(doc, row)));
      // 끝난 서브에이전트의 마지막 답이 아직 기록 파일에 안 들어왔으면 임시로 붙인다(기록이 오면 이 줄은 사라진다).
      const last = items.filter((r) => r.kind === 'assistant').at(-1);
      if (currentState !== 'working' && page.last_message && last?.text !== page.last_message) {
        fragment.append(drawEntry(doc, { kind: 'assistant', text: page.last_message, note: t('기록 반영 대기') }));
      }
      list.replaceChildren(fragment);
      scroller.scrollTop = atBottom ? scroller.scrollHeight : scroll;
    },
    error: (e) => { if (selected()) status(e.status === 404 ? t('기록을 찾을 수 없거나 서버가 지원하지 않습니다') : e.message || t('불러오지 못했습니다')); },
  });
  unsubscribe = window.onStatusChange?.(() => poll.changed());
  return {
    refresh: () => poll.refresh(),
    stop: () => { poll.stop(); unsubscribe?.(); document.removeEventListener('visibilitychange', visibility); },
  };
}
