// C3 — rail 세션 패널 안 「연결된 화면」.
//
// 문제(계획서 §3): 웹 attach 1개 = `tmux attach-session` PTY 1개다. 여러
// 브라우저 + 맥북 iTerm2가 같은 세션에 붙으면 tmux가 **가장 작은 클라이언트**에
// 맞춰 계속 리레이아웃한다. `L2`(window-size latest)로 대부분 해소됐지만,
// "맥북에 열어둔 창을 닫고 원격으로 쓰는 것만 남기고 싶다"는 요구는 남는다.
//
// **tty를 클라이언트가 고르게 하지 않는다.** 목록에는 tty가 보이지만, 끊기
// 요청에는 항상 내 web session id(`me`)를 함께 보내 서버가 "이게 너인지"를
// 판정하게 한다 — 지금 보고 있는 화면을 스스로 끊으면 복구 경로가 없다.
import { vtFetch } from '../core/api.js';
import { activeSessionId, getSession } from '../core/store.js';
import { icon } from '../ui/icons.js';

// 훅(`VT_NOTIFY_CLIENT_EVENTS=1`)이 꺼져 있으면 attach/detach를 알 방법이
// 없으므로 폴링으로 폴백한다. **그 사실을 UI에 표시하지 않는다**(계획서 §3):
// 사용자가 알아야 할 정보가 아니고, "실시간 아님" 같은 문구는 불안만 준다.
const POLL_MS = 5000;

function timeAgo(activity) {
  const t = Number(activity);
  if (!t) return '';
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (sec < 60) return '방금';
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  return `${Math.floor(sec / 3600)}시간 전`;
}

function row(client, onDetach) {
  const el = document.createElement('div');
  el.className = 'vt-clients-row';
  if (client.is_me) el.classList.add('me');

  const label = document.createElement('span');
  label.className = 'vt-clients-label';
  label.textContent = client.label || client.tty;
  el.appendChild(label);

  const meta = document.createElement('span');
  meta.className = 'vt-clients-meta';
  const size = client.width && client.height ? `${client.width}×${client.height}` : '';
  meta.textContent = [size, timeAgo(client.activity)].filter(Boolean).join(' · ');
  el.appendChild(meta);

  if (client.is_me) {
    const badge = document.createElement('span');
    badge.className = 'vt-clients-badge';
    badge.textContent = '나';
    el.appendChild(badge);
  } else {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'vt-clients-detach';
    btn.title = `${client.label || client.tty} 끊기`;
    btn.setAttribute('aria-label', `${client.label || client.tty} 끊기`);
    btn.innerHTML = icon('x', 12);
    btn.addEventListener('click', (e) => { e.stopPropagation(); onDetach(client); });
    el.appendChild(btn);
  }
  return el;
}

// container 안에 「연결된 화면」 블록을 그린다. 반환값은 정리 함수(폴링 해제).
export function mountClients(container, tmuxName) {
  if (!container || !tmuxName) return () => {};

  const wrap = document.createElement('div');
  wrap.className = 'vt-clients';
  const head = document.createElement('div');
  head.className = 'vt-clients-head';
  head.appendChild(Object.assign(document.createElement('span'), {
    className: 'vt-clients-title', textContent: '연결된 화면',
  }));
  const solo = document.createElement('button');
  solo.type = 'button';
  solo.className = 'vt-clients-solo';
  solo.textContent = '이 화면만 남기기';
  head.appendChild(solo);
  wrap.appendChild(head);
  const list = document.createElement('div');
  list.className = 'vt-clients-list';
  wrap.appendChild(list);
  container.appendChild(wrap);

  let stopped = false;

  const me = () => activeSessionId();

  async function refresh() {
    if (stopped) return;
    try {
      const q = `?session=${encodeURIComponent(tmuxName)}&me=${encodeURIComponent(me() || '')}`;
      const data = await vtFetch(`/api/tmux/clients${q}`);
      if (stopped) return;
      list.innerHTML = '';
      const clients = data.clients || [];
      // 클라이언트가 하나뿐이면(=나만 붙어 있으면) 관리할 게 없다. 블록을
      // 통째로 숨긴다 — 빈 목록을 보여주는 것보다 낫다(§4 "빈 패널 금지").
      wrap.hidden = clients.length < 2;
      // 남길 화면을 특정할 수 없으면 solo는 서버가 400으로 거부한다.
      // 눌러서 실패하게 두지 말고 미리 비활성화한다.
      solo.disabled = !data.me_tty;
      solo.title = data.me_tty ? '' : '이 화면의 tty를 확인할 수 없어 사용할 수 없습니다';
      for (const c of clients) list.appendChild(row(c, detach));
    } catch (_) {
      wrap.hidden = true;   // 조회 실패는 조용히 — 이건 부가 기능이다
    }
  }

  async function detach(client) {
    try {
      await vtFetch('/api/tmux/detach-client', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tty: client.tty, me: me() }),
      });
    } catch (e) {
      if (typeof window.showToast === 'function') window.showToast(e.message || '끊기 실패', 'error');
    }
    refresh();
  }

  solo.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/api/tmux/clients/solo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: tmuxName, me: me() }),
      });
      if (typeof window.showToast === 'function') {
        const n = (r.detached || []).length;
        window.showToast(n ? `화면 ${n}개를 끊었습니다` : '끊을 화면이 없습니다');
      }
    } catch (e) {
      if (typeof window.showToast === 'function') window.showToast(e.message || '실패', 'error');
    }
    refresh();
  });

  refresh();
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  return () => { stopped = true; clearInterval(timer); wrap.remove(); };
}
