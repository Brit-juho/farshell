// 통합 승인함·권한·감사 호출(`/api/authz/*`). 서버는 브라우저 로그인 쿠키만 사람으로 친다 —
// `?token=`·Authorization 이 실리면 403 이라, 쿠키만 보내는 share-api 의 fetch 를 그대로 쓴다.
// 응답에는 세션 화면·입력·메시지 같은 내용이 없다(길이와 해시 앞부분뿐).
import { shareFetch } from '../share/share-api.js';

const J = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/** 재인증(승격)이 필요하다는 서버 답인가. 결정은 403, 비상 정지는 401 로 온다. */
export function needsElevation(e) {
  if (!e) return false;
  const code = e.code || (e.data && e.data.error) || '';
  return code === 'elevation_required' || /elevation_required/.test(e.message || '');
}

export const authzApi = {
  approvals: () => shareFetch('/api/authz/approvals').then((d) => (d && d.approvals) || []),
  /** choice: once|session|deny. 'later' 는 서버에 아무것도 바꾸지 않는다. */
  decide: (id, decision, choice, extra) => shareFetch(
    `/api/authz/approvals/${encodeURIComponent(id)}/decision`,
    J('POST', { decision, ...(choice ? { choice } : {}), ...(extra || {}) }),
  ),
  /** 페어 승인 — `elevated` 는 사람이 직접 체크한 항목만(빈 목록이면 높은 권한 없이). 선택지(choice)는 받지 않는다. */
  decidePair: (id, elevated) => shareFetch(
    `/api/authz/approvals/${encodeURIComponent(id)}/decision`,
    J('POST', { decision: 'allow', elevated: elevated || [] }),
  ),
  grants: (filters = {}) => shareFetch(`/api/authz/grants${qs({ subject: filters.subject, resource: filters.resource, kind: filters.kind })}`)
    .then((d) => (d && d.grants) || []),
  revoke: (id, scopes) => shareFetch(
    `/api/authz/grants/${encodeURIComponent(id)}${qs({ scopes: scopes && scopes.length ? scopes.join(',') : '' })}`,
    { method: 'DELETE' },
  ),
  /** `{entries, chain:'ok'|'broken', lines, brokenSeq, denials}` */
  audit: (q = {}) => shareFetch(`/api/authz/audit${qs({ principal: q.principal, resource: q.resource, since: q.since || undefined, limit: q.limit })}`),
  /** 페어·lease — `pair_disabled`(409)는 기능이 꺼진 서버다(오류가 아니라 안내로 보인다). */
  pairs: () => shareFetch('/api/pairs').then((d) => (d && d.pairs) || []),
  pairCode: (id) => shareFetch(`/api/pairs/${encodeURIComponent(id)}/code`, J('POST', {})),
  pairEnd: (id) => shareFetch(`/api/pairs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  pairHandoff: (id) => shareFetch(`/api/pairs/${encodeURIComponent(id)}/handoff`, J('POST', {})),
  pairAccept: (id) => shareFetch(`/api/pairs/${encodeURIComponent(id)}/handoff/accept`, J('POST', {})),
  /** `{leases, enforced}` */
  leases: () => shareFetch('/api/authz/leases'),
  leaseRevoke: (id) => shareFetch(`/api/authz/leases/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  panic: () => shareFetch('/api/authz/panic', J('POST', {})),
};
