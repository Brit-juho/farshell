// 허용된 권한 — 모든 종류(share·msg·peer·deleg·generic)의 grant 목록, 필터, 회수. 기본 규칙은 따로(회수 불가).
import { vtEsc } from '../core/api.js';
import { confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { authzApi } from './api.js';
import { onChange } from './store.js';
import {
  kindLabel, KIND_LABEL, grantSummary, detailLine, approvedText, grantExpiry, lineageText, canRevokeWriteOnly, builtinDefaults, errorText, pairIdOfGrant,
} from './data.js';

export const FILTER_DEBOUNCE_MS = 250;
const FILTER_KINDS = ['share', 'msg', 'peer', 'deleg', 'generic', 'pair'];
const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

/** 권한 행의 페어 표시에서 같은 화면의 페어 행으로 간다. 목록에 없으면(끝난 지 오래) 알린다. */
function goToPair(id) {
  const row = [...document.querySelectorAll('.vt-pair-row')].find((r) => r.dataset.pair === id);
  if (!row) { toast(t('목록에 없는 페어입니다 — 이미 끝났을 수 있습니다'), 'info'); return; }
  row.tabIndex = -1;
  row.scrollIntoView?.({ block: 'center' });
  row.focus();
}

function rowHTML(g) {
  const sub = (g.subject && (g.subject.name || g.subject.id)) || '?';
  const res = typeof g.resource === 'string' ? g.resource : '?';
  const lin = lineageText({ lineage: g.lineage });
  const approved = approvedText(g);
  const pid = g.kind === 'pair' ? pairIdOfGrant(g) : '';
  return `<li class="vt-authz-row" data-grant="${vtEsc(g.id)}" data-kind="${vtEsc(g.kind)}">
    <div class="vt-authz-row-main">
      <div class="vt-authz-row-top"><span class="vt-authz-kind">${vtEsc(kindLabel(g.kind))}</span><span class="vt-authz-preset">${vtEsc(grantSummary(g))}</span>${pid ? `<button type="button" class="vt-authz-tag vt-pair-link" data-pair-link="${vtEsc(pid)}" aria-label="${vtEsc(t('페어 {id} 로 이동', { id: pid }))}">${t('페어')} ${vtEsc(pid.slice(3, 9))}</button>` : ''}</div>
      <div class="vt-authz-row-ask"><span class="vt-authz-who">${vtEsc(sub)}</span><span class="vt-authz-sub">${t('대상')}</span><span class="vt-authz-code">${vtEsc(res)}</span></div>
      <div class="vt-authz-row-detail">${vtEsc(detailLine(g))}</div>
      <div class="vt-authz-row-meta">${vtEsc([approved, grantExpiry(g), lin].filter(Boolean).join(' · '))}</div>
    </div>
    <div class="vt-authz-row-acts">
      ${canRevokeWriteOnly(g) ? `<button type="button" class="vt-btn lg vt-authz-revoke" data-mode="write">${t('입력만 회수')}</button>` : ''}
      <button type="button" class="vt-btn lg danger vt-authz-revoke" data-mode="all">${t('회수')}</button>
    </div>
  </li>`;
}

/** `host` 에 권한 패널을 그린다. 필터는 서버 쿼리로 보낸다. */
export function mountGrants(host, { session = '' } = {}) {
  host.innerHTML = `
    <div class="vt-authz-filters" role="search">
      <input type="search" class="vt-authz-in" data-f="subject" placeholder="${vtEsc(t('주체(이름·ID)'))}" aria-label="${vtEsc(t('주체로 거르기'))}" autocomplete="off">
      <input type="search" class="vt-authz-in" data-f="resource" ${session ? 'hidden' : ''} placeholder="${vtEsc(t('리소스(앞부분)'))}" aria-label="${vtEsc(t('리소스로 거르기'))}" autocomplete="off">
      <select class="vt-authz-in" data-f="kind" aria-label="${vtEsc(t('종류로 거르기'))}">
        <option value="">${vtEsc(t('모든 종류'))}</option>
        ${FILTER_KINDS.map((k) => `<option value="${k}">${vtEsc(KIND_LABEL[k])}</option>`).join('')}
      </select>
    </div>
    <div class="vt-authz-err" role="alert"></div>
    <div class="vt-authz-list" aria-live="polite"></div>
    ${session ? '' : `<h4 class="vt-authz-h">${t('기본 규칙')}</h4>
    <p class="vt-authz-note">${t('코드가 정해 둔 기본 권한입니다. 읽기 전용이라 회수할 수 없고, 아래 목록에 없는 권한은 모두 승인이 필요합니다.')}</p>
    <ul class="vt-authz-builtin">${builtinDefaults().map((b) => `<li data-builtin="${b.id}"><span class="vt-authz-who">${vtEsc(b.who)}</span><span class="vt-authz-row-detail">${vtEsc(b.what)}</span></li>`).join('')}</ul>`}`;
  const list = host.querySelector('.vt-authz-list');
  const errEl = host.querySelector('.vt-authz-err');
  const filters = { session, subject: '', resource: '', kind: '' };
  let disposed = false;
  let seq = 0;
  let timer = null;
  let busy = false;
  let keepErr = '';          // 회수 실패 문구 — 뒤따르는 목록 새로고침이 지우지 않게 한다

  const filtered = () => !!(filters.subject || filters.resource || filters.kind);

  async function load() {
    if (disposed) return;
    const mine = ++seq;
    list.innerHTML = `<p class="vt-authz-empty">${t('불러오는 중…')}</p>`;
    try {
      const grants = await authzApi.grants(filters);
      if (mine !== seq || disposed || !host.isConnected) return;
      errEl.textContent = keepErr; keepErr = '';
      list.innerHTML = grants.length
        ? `<ul class="vt-authz-rows">${grants.map(rowHTML).join('')}</ul>`
        : `<p class="vt-authz-empty">${filtered() ? t('조건에 맞는 권한이 없습니다.') : t('허용된 권한이 없습니다. 에이전트가 권한이 필요하면 승인을 묻습니다.')}</p>`;
    } catch (e) {
      if (mine !== seq || disposed || !host.isConnected) return;
      list.innerHTML = `<p class="vt-authz-empty">${t('권한 목록을 불러오지 못했습니다.')}</p>
        <button type="button" class="vt-btn lg vt-authz-retry">${t('다시 시도')}</button>`;
      errEl.textContent = errorText(e);
    }
  }

  host.addEventListener('input', (ev) => {
    const f = ev.target.dataset && ev.target.dataset.f;
    if (!f || f === 'kind') return;
    filters[f] = ev.target.value.trim();
    clearTimeout(timer);
    timer = setTimeout(load, FILTER_DEBOUNCE_MS);
  });
  host.addEventListener('change', (ev) => {
    if (ev.target.dataset && ev.target.dataset.f === 'kind') { filters.kind = ev.target.value; clearTimeout(timer); void load(); }
  });
  host.addEventListener('click', async (ev) => {
    if (ev.target.closest('.vt-authz-retry')) { void load(); return; }
    const link = ev.target.closest('.vt-pair-link');
    if (link) { goToPair(link.dataset.pairLink); return; }
    const b = ev.target.closest('.vt-authz-revoke');
    if (!b || busy) return;
    const row = b.closest('.vt-authz-row');
    const id = row.dataset.grant;
    const writeOnly = b.dataset.mode === 'write';
    const kind = row.dataset.kind;
    const msg = writeOnly
      ? t('이 에이전트의 입력 권한만 거두고 읽기는 남깁니다. 계속할까요?')
      : kind === 'pair' ? t('이 권한을 회수하면 이 페어 전체가 끝납니다. 두 쪽이 받은 권한과 폴더 점유가 모두 거두어집니다. 계속할까요?')
      : kind === 'deleg' ? t('이 위임을 닫습니다. 하위 세션은 남지만 더 이상 지시를 받지 않습니다. 계속할까요?')
        : t('이 권한을 회수합니다. 에이전트는 다시 요청해야 쓸 수 있습니다. 계속할까요?');
    if (!(await confirmDialog(msg, { title: t('권한 회수'), okLabel: writeOnly ? t('입력만 회수') : t('회수'), danger: true }))) return;
    if (disposed || !host.isConnected) return;
    busy = true;
    row.querySelectorAll('button').forEach((x) => { x.disabled = true; });
    try {
      await authzApi.revoke(id, writeOnly ? ['session.input'] : undefined);
      toast(writeOnly ? t('입력 권한을 회수했습니다') : t('권한을 회수했습니다'), 'success');
      errEl.textContent = '';
    } catch (e) {
      keepErr = errorText(e);
      errEl.textContent = keepErr;
    } finally {
      busy = false;
    }
    void load();
  });

  const off = onChange((ev) => {
    if (disposed || !host.isConnected) { off(); return; }
    if (ev.type === 'grants' && !busy) void load();
  });
  void load();
  return { reload: load, dispose: () => { disposed = true; off(); clearTimeout(timer); seq++; } };
}
