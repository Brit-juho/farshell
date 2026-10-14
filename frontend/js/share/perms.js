// 「에이전트 권한…」 — 지금 허용돼 있는 권한 목록, 철회, 최근 접근 기록.
// 열 때 세 목록(권한·감사·대기)을 한 번에 받고, 철회한 뒤 다시 받는다.
import { openDialog, nextId } from '../core/dialog.js';
import { closePanel } from '../panels/panel.js';
import { vtEsc } from '../core/api.js';
import { shareApi } from './share-api.js';
import { setPending, showPending } from './approvals.js';
import { grantLevel, lastUsedOf, auditLine, relTime, errorText, MODE_LABEL } from './share-data.js';
import { t } from '../core/i18n.js';

const AUDIT_SHOWN = 8;
const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

function grantRow(g, entries) {
  const used = lastUsedOf(g, entries);
  const nick = g.target.nick || t('(공유 해제됨)');
  const canWrite = !!g.write;
  return `
    <li class="vt-share-grant" data-grant="${vtEsc(g.id)}">
      <div class="vt-share-grant-main">
        <div class="vt-share-grant-who"><span class="vt-share-agent">${vtEsc(g.agent.name || '?')}</span>
          <span class="vt-share-to">${t('대상')}</span><span class="vt-share-nick-name">${vtEsc(nick)}</span></div>
        <div class="vt-share-grant-meta">${vtEsc(grantLevel(g))}${g.target.mode ? ` · ${t('공유 {mode}', { mode: vtEsc(MODE_LABEL[g.target.mode] || g.target.mode) })}` : ''} · ${t('마지막 사용 {when}', { when: used ? vtEsc(relTime(used)) : t('없음') })}</div>
      </div>
      <div class="vt-share-grant-acts">
        ${canWrite ? `<button type="button" class="vt-btn lg vt-share-revoke" data-scope="write" aria-label="${t('{name}의 쓰기 권한만 철회', { name: vtEsc(g.agent.name) })}">${t('쓰기만 철회')}</button>` : ''}
        <button type="button" class="vt-btn lg danger vt-share-revoke" data-scope="all" aria-label="${t('{name}의 모든 권한 철회', { name: vtEsc(g.agent.name) })}">${t('전체 철회')}</button>
      </div>
    </li>`;
}

function paint(body, state) {
  const { grants, entries, requests } = state;
  const pending = requests.length;
  body.innerHTML = `
    ${pending ? `<div class="vt-share-pending"><span>${t('승인 대기 {n}건', { n: pending })}</span><button type="button" class="vt-btn lg vt-share-open-pending">${t('보기')}</button></div>` : ''}
    <h3 class="vt-share-h">${t('허용된 권한')}</h3>
    ${grants.length
    ? `<ul class="vt-share-grants">${grants.map((g) => grantRow(g, entries)).join('')}</ul>`
    : `<p class="vt-share-empty">${t('허용된 권한이 없습니다. 에이전트가 공유 세션을 처음 쓰면 승인을 묻습니다.')}</p>`}
    <div class="vt-share-err" role="alert">${vtEsc(state.error || '')}</div>
    <h3 class="vt-share-h">${t('최근 기록')}</h3>
    ${entries.length
    ? `<ol class="vt-share-audit">${entries.slice(0, AUDIT_SHOWN).map((e) => `<li>${vtEsc(auditLine(e))}</li>`).join('')}</ol>`
    : `<p class="vt-share-empty">${t('기록이 없습니다.')}</p>`}
    <p class="vt-share-note">${t('기록에는 글자 수와 해시 앞 8자리만 남고 입력한 글 자체는 남지 않습니다.')}</p>`;
}

/** 권한 패널을 연다. */
export function openPermsPanel() {
  const id = nextId();
  const { body } = openDialog({
    id, title: t('에이전트 권한'),
    bodyHTML: `<div class="vt-vw-loading">${t('불러오는 중…')}</div>`,
  });
  if (!body) return null;
  const state = { grants: [], entries: [], requests: [], error: '' };
  const alive = () => body.isConnected;

  const load = async () => {
    try {
      const [grants, entries, requests] = await Promise.all([shareApi.grants(), shareApi.audit(200), shareApi.requests()]);
      if (!alive()) return;
      Object.assign(state, { grants, entries, requests });
      setPending(requests);          // 이 목록이 가장 새 대기 현황이다 — 모달 큐도 맞춰 둔다
    } catch (e) {
      if (!alive()) return;
      state.error = errorText(e);
      if (!state.grants.length) { body.innerHTML = `<p class="vt-share-err" role="alert">${vtEsc(state.error)}</p>`; return; }
    }
    paint(body, state);
  };

  body.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('button');
    if (!btn) return;
    if (btn.classList.contains('vt-share-open-pending')) { closePanel(id); showPending(); return; }
    if (!btn.classList.contains('vt-share-revoke')) return;
    const gid = btn.closest('.vt-share-grant').dataset.grant;
    const scope = btn.dataset.scope;
    body.querySelectorAll('.vt-share-revoke').forEach((b) => { b.disabled = true; });
    try {
      await shareApi.revoke(gid, scope);
      toast(scope === 'write' ? t('쓰기 권한을 철회했습니다') : t('권한을 철회했습니다'), 'success');
      state.error = '';
    } catch (e) {
      state.error = errorText(e);
    }
    await load();
  });

  void load();
  return { id };
}
