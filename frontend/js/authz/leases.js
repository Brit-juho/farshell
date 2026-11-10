// 설정 「폴더 lease」 — 지금 잡혀 있는 lease(보유자·경로·모드·배타·만료·상속). 사람은 관리자로 놓을 수 있다.
// 상속된 lease(위임한 상위 세션 것을 물려받은 것)는 읽기 전용 표시이고, 상위를 놓으면 함께 놓인다.
// lease 는 FarShell 자신의 쓰기 API 밖(셸·편집기)에서는 막지 못하는 알림 장치다 — 그 말을 항상 같이 보인다.
import { vtEsc } from '../core/api.js';
import { confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { authzApi } from './api.js';
import { onChange } from './store.js';
import { MODE_LABEL, leaseExpiry, errorText } from './data.js';

const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

function rowHTML(l) {
  const derived = !!l.derivedFrom;
  return `<li class="vt-authz-row vt-lease-row" data-lease="${vtEsc(l.id)}"${derived ? ' data-derived="1"' : ''}>
    <div class="vt-authz-row-main">
      <div class="vt-authz-row-top"><span class="vt-authz-kind">${vtEsc(MODE_LABEL[l.mode] || String(l.mode || ''))}</span>${l.exclusive ? `<span class="vt-authz-preset">${t('배타')}</span>` : ''}${derived ? `<span class="vt-lease-inherit">${t('상위에서 상속 · 읽기 전용')}</span>` : ''}</div>
      <div class="vt-authz-row-ask"><span class="vt-authz-who">${vtEsc(l.holder || '?')}</span><span class="vt-authz-sub">${t('경로')}</span><span class="vt-authz-code">${vtEsc(l.glob || '')}</span></div>
      <div class="vt-authz-row-meta">${vtEsc([leaseExpiry(l), derived ? t('상위 점유 {id}', { id: l.derivedFrom }) : ''].filter(Boolean).join(' · '))}</div>
      ${l.reason ? `<div class="vt-authz-row-meta">${t('사유')} · ${vtEsc(l.reason)}</div>` : ''}
    </div>
    ${derived ? '' : `<div class="vt-authz-row-acts"><button type="button" class="vt-btn lg danger vt-lease-revoke">${t('점유 해제')}</button></div>`}
  </li>`;
}

export function mountLeases(host) {
  host.innerHTML = `<p class="vt-authz-note vt-lease-note"></p>
    <div class="vt-authz-err" role="alert"></div>
    <div class="vt-authz-list" aria-live="polite"></div>`;
  const note = host.querySelector('.vt-lease-note');
  const list = host.querySelector('.vt-authz-list');
  const errEl = host.querySelector('.vt-authz-err');
  let seq = 0;
  let busy = false;
  let keepErr = '';

  const noteText = (enforced) => (enforced
    ? t('폴더 점유는 FarShell이 직접 하는 쓰기(커밋·업로드 등)만 막습니다. 셸이나 편집기에서 직접 쓰는 것은 막지 못하는 권고 장치입니다.')
    : t('폴더 점유는 누가 어느 폴더를 쓰는지 알리는 권고 장치입니다. 지금은 막지 않고, 켜도 FarShell 자신의 쓰기 밖(셸·편집기)에서는 막지 못합니다.'));
  note.textContent = noteText(false);

  async function load() {
    const mine = ++seq;
    try {
      const res = await authzApi.leases();
      if (mine !== seq || !host.isConnected) return;
      const rows = (res && res.leases) || [];
      note.textContent = noteText(!!(res && res.enforced));
      errEl.textContent = keepErr; keepErr = '';
      list.innerHTML = rows.length
        ? `<ul class="vt-authz-rows">${rows.map(rowHTML).join('')}</ul>`
        : `<p class="vt-authz-empty">${t('점유 중인 폴더가 없습니다. 에이전트가 폴더를 쓰기 시작하면 여기에 나타납니다.')}</p>`;
    } catch (e) {
      if (mine !== seq || !host.isConnected) return;
      list.innerHTML = `<p class="vt-authz-empty">${t('폴더 점유 목록을 불러오지 못했습니다.')}</p><button type="button" class="vt-btn lg vt-lease-retry">${t('다시 시도')}</button>`;
      errEl.textContent = errorText(e);
    }
  }

  host.addEventListener('click', async (ev) => {
    if (ev.target.closest('.vt-lease-retry')) { void load(); return; }
    const b = ev.target.closest('.vt-lease-revoke');
    if (!b || busy) return;
    const row = b.closest('.vt-lease-row');
    if (!(await confirmDialog(t('이 폴더 점유를 해제합니다. 상속된 하위 점유도 함께 해제되며, 에이전트는 다시 점유해야 합니다. 계속할까요?'), { title: t('점유 해제'), okLabel: t('점유 해제'), danger: true }))) return;
    busy = true;
    row.querySelectorAll('button').forEach((x) => { x.disabled = true; });
    try {
      await authzApi.leaseRevoke(row.dataset.lease);
      toast(t('폴더 점유를 해제했습니다'), 'success');
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
    if (!host.isConnected) { off(); return; }
    if (ev.type === 'leases' && !busy) void load();
  });
  void load();
  return { reload: load, dispose: () => { off(); seq++; } };
}
