import { openPanel, closePanel } from './panel.js';
import { t } from '../core/i18n.js';
import { startSubagentPolling, mergeTranscript } from './subagent-poll.js';

export function showSubagent({ tmuxName, parentId, child, host }) {
  closePanel('vt-subagent', { history: 'replace' });
  let poll, unsubscribe;
  const panel = openPanel({ id: 'vt-subagent', ariaLabel: t('서브에이전트 기록'), bodyId: 'vt-subagent-body',
    headHTML: `<div class="vt-vw-title"></div>`,
    onClose: () => { poll?.stop(); unsubscribe?.(); document.removeEventListener('visibilitychange', visibility); },
  });
  if (!panel) return;
  panel.el.querySelector('.vt-vw-title').textContent = `${t('서브에이전트 기록')} · ${child.label}`;
  let cursor = null, items = [], currentState = child.state;
  const status = document.createElement('div'); status.className = 'vt-native-notice'; status.setAttribute('role', 'status');
  const content = document.createElement('div'); content.className = 'vt-native-transcript';
  const refresh = document.createElement('button'); refresh.type = 'button'; refresh.className = 'vt-btn'; refresh.textContent = t('새로고침'); refresh.onclick = () => poll?.refresh();
  panel.body.replaceChildren(refresh, status, content);
  const selected = () => panel.el.isConnected && host === (window.vtRelayHost?.() || 'local');
  const state = () => {
    const summary = window.agentDetails?.()[tmuxName]?.subagents;
    if (summary && summary.parent_id !== parentId) return 'gone';
    const live = summary?.items?.find((r) => r.id === child.id);
    return live?.state || currentState;
  };
  const visibility = () => poll?.visibility();
  document.addEventListener('visibilitychange', visibility);
  const labels = { user: t('지시'), assistant: t('답변'), tool_call: t('도구 호출'), tool_result: t('도구 결과') };
  poll = startSubagentPolling({ state, visible: () => selected() && !document.hidden,
    fetchPage: async (signal) => {
      if (!selected() || state() === 'gone') { const e = new Error(t('서브에이전트를 찾을 수 없습니다')); e.status = 404; throw e; }
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
      const page = await window.vtFetch(`/api/agents/${encodeURIComponent(tmuxName)}/subagents/${encodeURIComponent(child.id)}/transcript${query}`, { signal });
      if (!selected() || page.parent_id !== parentId || state() === 'gone') { const e = new Error(t('서브에이전트를 찾을 수 없습니다')); e.status = 404; throw e; }
      return page;
    },
    receive: (page) => {
      currentState = page.state;
      panel.el.querySelector('.vt-vw-title').textContent = `${t('서브에이전트 기록')} · ${String(page.label || child.label).slice(0, 200)}`;
      cursor = page.next_cursor;
      items = mergeTranscript(items, page);
      status.textContent = page.pending ? t('기록이 아직 생성되지 않았습니다') : page.truncated ? t('일부 기록이 생략되었습니다') : '';
      const atBottom = content.scrollHeight - content.scrollTop - content.clientHeight < 40;
      const scroll = content.scrollTop;
      const fragment = document.createDocumentFragment();
      const draw = (row, temporary = false) => {
        const article = document.createElement('article'); article.className = 'vt-native-entry'; article.dataset.kind = row.kind;
        const title = document.createElement('div'); title.className = 'vt-native-entry-label'; title.textContent = `${labels[row.kind]}${row.tool ? ` · ${String(row.tool).slice(0, 200)}` : ''}${temporary ? ` · ${t('기록 반영 대기')}` : ''}`;
        const text = document.createElement('pre'); text.textContent = String(row.text || '').slice(0, 2000);
        article.append(title, text); fragment.append(article);
      };
      items.forEach((row) => draw(row));
      const last = items.filter((r) => r.kind === 'assistant').at(-1);
      if (currentState !== 'working' && page.last_message && last?.text !== page.last_message) draw({ kind: 'assistant', text: page.last_message }, true);
      content.replaceChildren(fragment);
      content.scrollTop = atBottom ? content.scrollHeight : scroll;
    },
    error: (e) => { if (selected()) status.textContent = e.status === 404 ? t('기록을 찾을 수 없거나 서버가 지원하지 않습니다') : e.message || t('불러오지 못했습니다'); },
  });
  unsubscribe = window.onStatusChange?.(() => poll.changed());
}


export function showSubagents({ tmuxName, parentId, items, host }) {
  closePanel('vt-subagent', { history: 'replace' });
  const panel = openPanel({ id: 'vt-subagent', ariaLabel: t('에이전트 내부'), bodyId: 'vt-subagent-body', headHTML: '<div class="vt-vw-title"></div>' });
  if (!panel) return;
  panel.el.querySelector('.vt-vw-title').textContent = t('에이전트 내부');
  panel.body.replaceChildren();
  for (const child of items) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'vt-btn vt-native-picker';
    const labels = { working: t('작업 중'), idle: t('유휴'), done: t('완료'), failed: t('실패') };
    button.textContent = `${child.label} · ${labels[child.state]}`;
    button.onclick = () => { if (host === (window.vtRelayHost?.() || 'local')) showSubagent({ tmuxName, parentId, child, host }); };
    panel.body.append(button);
  }
}
