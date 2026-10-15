// 설정 「권한」 섹션의 본체 — 승인 대기·허용된 권한·감사 기록·비상 정지를 호스트 요소에 올린다.
import { t } from '../core/i18n.js';
import { vtEsc } from '../core/api.js';
import { authzApi } from './api.js';
import { withElevation } from './elevate.js';
import { errorText } from './data.js';
import { mountInboxList } from './inbox.js';
import { mountGrants } from './grants.js';
import { mountAudit } from './audit.js';

/** 비상 정지 — 두 단계 확인 + 재인증. 사람 기본 권한·공유 정책·로그인은 그대로고, 사람이 아닌 주체의 권한과 대기 요청만 거둔다. */
export function mountPanic(host) {
  let busy = false;
  const idle = () => {
    host.innerHTML = `<p class="vt-authz-note">${t('모든 에이전트의 권한을 한 번에 회수하고 대기 중인 요청을 비웁니다. 사람의 로그인과 기본 권한은 그대로입니다.')}</p>
      <button type="button" class="vt-btn lg danger vt-authz-panic-arm">${t('모든 에이전트 권한 회수…')}</button>`;
  };
  const armed = () => {
    host.innerHTML = `<p class="vt-authz-warn" role="alert">${t('정말 모든 에이전트 권한을 회수할까요? 공유 세션·메시지·다른 맥·위임·일반 권한이 전부 거두어지고, 에이전트는 다시 승인을 받아야 합니다. 비밀번호를 다시 확인합니다.')}</p>
      <div class="vt-dialog-actions"><button type="button" class="vt-btn lg vt-authz-panic-cancel">${t('취소')}</button>
      <button type="button" class="vt-btn lg danger vt-authz-panic-go">${t('지금 모두 회수')}</button></div>`;
    host.querySelector('.vt-authz-panic-cancel').focus();     // 파괴적 동작이라 기본 포커스는 취소
  };
  const done = (res) => {
    const r = (res && res.revoked) || {};
    const total = ['share', 'msg', 'peer', 'deleg', 'generic', 'requests'].reduce((n, k) => n + (Number(r[k]) || 0), 0);
    const failed = (res && res.failed) || [];
    host.innerHTML = `<p class="vt-authz-note" role="status">${t('회수했습니다 — {n}건', { n: total })}</p>
      ${failed.length ? `<p class="vt-authz-err" role="alert">${t('일부는 회수하지 못했습니다: {list}', { list: vtEsc(failed.map((f) => `${f.kind}(${f.error})`).join(', ')) })}</p>` : ''}
      <button type="button" class="vt-btn lg vt-authz-panic-back">${t('확인')}</button>`;
  };
  host.addEventListener('click', async (ev) => {
    if (busy) return;
    if (ev.target.closest('.vt-authz-panic-arm')) { armed(); return; }
    if (ev.target.closest('.vt-authz-panic-cancel') || ev.target.closest('.vt-authz-panic-back')) { idle(); return; }
    if (!ev.target.closest('.vt-authz-panic-go')) return;
    busy = true;
    host.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const res = await withElevation(() => authzApi.panic(), t('모든 에이전트 권한을 회수하려면 비밀번호를 다시 확인해야 합니다.'));
      if (res === null) armed(); else done(res);
    } catch (e) {
      armed();
      const p = document.createElement('p');
      p.className = 'vt-authz-err'; p.setAttribute('role', 'alert'); p.textContent = errorText(e);
      host.prepend(p);
    } finally {
      busy = false;
    }
  });
  idle();
}

/** hosts: {inbox, grants, audit, panic} — 설정 섹션이 만든 빈 요소들. */
export function mountPermissions(hosts) {
  mountInboxList(hosts.inbox);
  mountGrants(hosts.grants);
  mountAudit(hosts.audit);
  mountPanic(hosts.panic);
}
