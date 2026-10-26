import { openDialog, confirmDialog } from '../core/dialog.js';
import { closePanel } from '../panels/panel.js';
import { t } from '../core/i18n.js';
import { historyApi, operationId } from './api.js';
import { resumeSession, renameSession, setSessionArchived, deleteSession, recheckCatalog, executionStatus, agoLabel } from './session-actions.js';

const RESUME_BLOCKED = () => ({
  archived: t('보관된 세션은 보관을 해제한 뒤 시작할 수 있습니다'),
  running: t('이미 실행 중입니다'),
  no_conversation: t('이어갈 수 있는 대화가 없습니다'),
});

export function openSessionRecord(id, changed = () => {}) {
  const controller = new AbortController();
  const { body } = openDialog({ id: 'vt-session-record', title: t('작업 기록'), bodyHTML: '', onClose: () => controller.abort() });
  if (!body) return;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const message = el('p', t('불러오는 중…')); message.setAttribute('role', 'status'); body.append(message);
  const api = (path, options = {}) => historyApi(path, { ...options, signal: controller.signal });
  async function load() {
    try {
      const data = await api('/api/session-catalog/sessions/' + id);
      if (controller.signal.aborted) return;
      const { session } = data;
      const active = data.runtimes.find(r => r.ended == null);
      const last = data.runtimes[0];
      const legacy = !active && !session.archived && data.resumable?.reason === 'no_conversation' && !!last?.ended;
      const status = legacy ? { label: t('과거 기록'), hint: t('연결된 대화 기록이 생기기 전에 끝난 세션이라 이어서 시작할 수 없습니다') } : executionStatus({ ...session, ended: last ? last.ended : null, execution_state: last ? last.state : 'never_started' }, false);
      message.textContent = '';
      const button = (label, run, { solid = false, danger = false, disabled = false, title = '' } = {}) => {
        const n = el('button', label, 'vt-btn' + (solid ? ' solid' : '') + (danger ? ' danger' : '')); n.type = 'button';
        n.disabled = disabled; if (title) n.title = title;
        n.onclick = async () => {
          n.disabled = true; message.textContent = '';
          try { if (await run() !== false) { changed(); await load(); } }
          catch (error) { if (!controller.signal.aborted) message.textContent = error.message; }
          finally { n.disabled = false; }
        };
        return n;
      };
      const head = el('header', null, 'vt-record-head');
      head.append(el('h3', session.title), el('p', [status.label, last && agoLabel(last.ended || last.started)].filter(Boolean).join(' · '), 'vt-history-meta'));
      const main = el('div', null, 'vt-record-main');
      if (status.recheck) {
        main.append(button(t('다시 확인'), async () => {
          const r = await recheckCatalog({ signal: controller.signal });
          if (!r.complete) throw new Error(t('아직 터미널 상태를 읽지 못했습니다. 잠시 뒤 다시 시도하세요'));
        }, { solid: true }));
        if (status.hint) main.append(el('span', status.hint, 'vt-history-meta'));
      }
      if (active) {
        main.append(button(t('실행 종료'), async () => {
          if (!await confirmDialog(t('이 실행의 모든 터미널 창과 pane을 종료합니다. 별도 하위 세션은 유지합니다. 계속할까요?'), { danger: true })) return false;
          await api('/api/session-catalog/sessions/' + id + '/stop', { body: { operation_id: operationId(), force: true } });
        }, { danger: true }));
      } else if (session.archived) {
        main.append(button(t('보관 해제'), () => setSessionArchived(session, false, { signal: controller.signal }), { solid: true }));
      } else {
        const blocked = data.resumable?.ok || legacy ? '' : (RESUME_BLOCKED()[data.resumable?.reason] || '');
        if (legacy) main.append(el('span', status.hint, 'vt-history-meta'));
        else main.append(button(t('이어서 시작'), async () => {
          await resumeSession(id, { signal: controller.signal });
          closePanel('vt-session-record'); changed(); return false;
        }, { solid: true, disabled: !!blocked, title: blocked }));
        if (blocked) main.append(el('span', blocked, 'vt-history-meta'));
      }
      body.replaceChildren(head, main, message);

      if (data.conversations.length) {
        const section = el('section', null, 'vt-record-section'); section.append(el('h4', t('연결된 대화')));
        for (const c of data.conversations) {
          const card = el('button', null, 'vt-record-card'); card.type = 'button';
          card.append(el('span', c.title || c.native_id), el('small', [c.provider, agoLabel(c.updated)].filter(Boolean).join(' · ')));
          card.onclick = async () => { const { openHistory } = await import('./panel.js'); openHistory({ search: c.native_id, sessionId: id }); };
          section.append(card);
        }
        body.append(section);
      }
      const delegations = (data.relations || []).filter(r => r.kind === 'delegation').slice(0, 200);
      if (delegations.length) {
        const section = el('section', null, 'vt-record-section'); section.append(el('h4', t('FarShell 위임 관계')));
        for (const relation of delegations) {
          const child = relation.parent_id === id;
          const target = child ? relation.child_id : relation.parent_id;
          const card = el('button', null, 'vt-record-card'); card.type = 'button';
          card.append(el('span', (child ? t('하위 작업 기록') : t('상위 작업 기록')) + ' · ' + target.slice(0, 8)));
          card.onclick = () => openSessionRecord(target, changed);
          section.append(card);
        }
        body.append(section);
      }
      if (data.runtimes.length) {
        const section = el('section', null, 'vt-record-section'); section.append(el('h4', t('실행 이력')));
        for (const runtime of data.runtimes) {
          // 상태·시각이 한 줄, tmux 이름은 아래 줄 — 한 줄에 몰아 쓰면 좁은 창에서 이름이 시각을 밀어낸다.
          const item = el('div', null, 'vt-record-run');
          item.append(el('span', `${runtime.ended ? t('실행 종료됨') : t('실행 중')} · ${new Date(runtime.started * 1000).toLocaleString()}`), el('small', runtime.name));
          section.append(item);
        }
        body.append(section);
      }
      const foot = el('footer', null, 'vt-record-foot');
      foot.append(button(t('이름 변경'), () => renameSession(session, { signal: controller.signal })));
      if (!active && !session.archived) foot.append(button(t('세션 보관'), () => setSessionArchived(session, true, { signal: controller.signal })));
      if (!active && session.archived) {
        foot.append(button(t('삭제'), async () => {
          if (!await deleteSession(session, { signal: controller.signal })) return false;
          closePanel('vt-session-record'); changed(); return false;
        }, { danger: true }));
      }
      body.append(foot);
    } catch (error) { if (!controller.signal.aborted) message.textContent = error.message; }
  }
  load();
}
