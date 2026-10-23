import { openDialog, promptDialog, confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { historyApi, operationId } from './api.js';

export function openSessionRecord(id, changed = () => {}) {
  const controller = new AbortController();
  const { body } = openDialog({ id: 'vt-session-record', title: t('작업 기록'), bodyHTML: '', onClose: () => controller.abort() });
  if (!body) return;
  const el = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const message = el('p', t('불러오는 중…')); message.setAttribute('role', 'status'); body.append(message);
  const api = (path, options = {}) => historyApi(path, { ...options, signal: controller.signal });
  async function load() {
    try {
      const data = await api('/api/session-catalog/sessions/' + id);
      if (controller.signal.aborted) return;
      body.replaceChildren(el('h3', data.session.title), message); message.textContent = '';
      const button = (label, run) => {
        const n = el('button', label); n.type = 'button'; n.className = 'vt-btn';
        n.onclick = async () => { n.disabled = true; try { await run(); changed(); await load(); }
          catch (error) { if (!controller.signal.aborted) message.textContent = error.message; }
          finally { n.disabled = false; } };
        body.append(n);
      };
      button(t('이름 변경'), async () => {
        const title = await promptDialog(t('작업 이름'), { defaultValue: data.session.title });
        if (title === null) return;
        await api('/api/session-catalog/sessions/' + id, { body: { title, revision: data.session.revision } });
      });
      const active = data.runtimes.find(r => r.ended == null);
      if (active) button(t('실행 종료'), async () => {
        if (!await confirmDialog(t('이 실행의 모든 터미널 창과 pane을 종료합니다. 별도 하위 세션은 유지합니다. 계속할까요?'), { danger: true })) return;
        await api('/api/session-catalog/sessions/' + id + '/stop', { body: { operation_id: operationId(), force: true } });
      });
      else button(data.session.archived ? t('보관 해제') : t('세션 보관'), () => api('/api/session-catalog/sessions/' + id,
        { body: { archived: !data.session.archived, revision: data.session.revision } }));
      for (const conversation of data.conversations) button(conversation.title || conversation.native_id, async () => {
        const { openHistory } = await import('./panel.js'); openHistory({ search: conversation.native_id, sessionId: id });
      });
      if (data.relations?.length) {
        body.append(el('h4', t('FarShell 위임 관계')));
        for (const relation of data.relations.slice(0, 200)) {
          if (relation.kind !== 'delegation') continue;
          const child = relation.parent_id === id;
          const target = child ? relation.child_id : relation.parent_id;
          button((child ? t('하위 작업 기록') : t('상위 작업 기록')) + ' · ' + target.slice(0, 8), async () => {
            openSessionRecord(target, changed);
          });
        }
      }
      for (const runtime of data.runtimes) {
        const row = el('p', `${runtime.name} · ${runtime.ended ? t('실행 종료됨') : t('실행 중')} · ${new Date(runtime.started * 1000).toLocaleString()}`);
        row.className = 'vt-history-meta'; body.append(row);
      }
    } catch (error) { if (!controller.signal.aborted) message.textContent = error.message; }
  }
  load();
}
