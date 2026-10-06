// 공유·승인 엔드포인트 호출. 서버는 **브라우저 로그인 세션 쿠키만** 받는다(auth.require_human_session):
// `Authorization` 헤더나 `?token=` 이 하나라도 실리면 사람 요청으로 치지 않아 403 이다.
// 그래서 vtFetch(`?token=` 부착)·apiFetch(Bearer 부착)를 쓰지 않고 쿠키만 보내는 fetch 를 따로 둔다.
// 이 기능은 이 맥 전용이라(호스트 중계 허용 목록에 없다) 호스트 전환 경로도 타지 않는다.

export async function shareFetch(path, opts = {}) {
  const res = await fetch(`${window.API_BASE || ''}${path}`, { credentials: 'same-origin', ...opts });
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (!res.ok) {
    const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
    e.status = res.status; e.data = data; e.code = data && data.error;
    throw e;
  }
  return data;
}

const J = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const shareApi = {
  share: (name, nick, mode) => shareFetch(`/api/tmux/${encodeURIComponent(name)}/share`, J('POST', { nick, mode })),
  unshare: (name) => shareFetch(`/api/tmux/${encodeURIComponent(name)}/share`, { method: 'DELETE' }),
  requests: () => shareFetch('/api/agent/share/requests').then((d) => (d && d.requests) || []),
  decide: (id, decision, level) => shareFetch(
    `/api/agent/share/requests/${encodeURIComponent(id)}/decision`,
    J('POST', level ? { decision, level } : { decision }),
  ),
  grants: () => shareFetch('/api/agent/share/grants').then((d) => (d && d.grants) || []),
  revoke: (id, scope) => shareFetch(`/api/agent/share/grants/${encodeURIComponent(id)}?scope=${scope}`, { method: 'DELETE' }),
  audit: (limit = 100) => shareFetch(`/api/agent/share/audit?limit=${limit}`).then((d) => (d && d.entries) || []),
};
