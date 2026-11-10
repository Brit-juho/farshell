// 설정 > 권한 — 승인 대기, 페어, 폴더 lease, 브라우저, 허용된 권한, 감사 기록, 비상 정지. 본체는 `share` 청크(authz/*)에 있고 여기는 자리만 만든다.
import { group, statusLine } from '../controls.js';
import { t } from '../../../core/i18n.js';

export function renderPermissionsSection() {
  const frag = document.createDocumentFragment();
  const mk = (cls) => { const d = document.createElement('div'); d.className = `vt-authz-host ${cls}`; return d; };
  const hosts = { inbox: mk('vt-authz-inbox'), pairs: mk('vt-authz-pairs'), leases: mk('vt-authz-leases'), browser: mk('vt-authz-browser'), grants: mk('vt-authz-grants'), audit: mk('vt-authz-auditbox'), panic: mk('vt-authz-panic') };
  frag.append(
    group(t('승인 대기'), hosts.inbox),
    group(t('페어'), hosts.pairs),
    group(t('폴더 점유(lease)'), hosts.leases),
    group(t('브라우저'), hosts.browser),
    group(t('허용된 권한'), hosts.grants),
    group(t('감사 기록'), hosts.audit),
    group(t('비상 정지'), hosts.panic),
  );
  for (const h of Object.values(hosts)) h.appendChild(statusLine(t('불러오는 중…')));
  import('../../../authz/entry.js')
    .then((m) => {
      for (const h of Object.values(hosts)) h.textContent = '';
      m.mountPermissions(hosts);
      return m.refresh();
    })
    .catch(() => { hosts.inbox.textContent = ''; hosts.inbox.appendChild(statusLine(t('권한 화면을 불러오지 못했습니다.'))); });
  return frag;
}
