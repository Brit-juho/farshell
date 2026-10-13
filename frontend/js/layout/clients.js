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
import { activeSessionId } from '../core/store.js';
import { icon } from '../ui/icons.js';
import { t } from '../core/i18n.js';

// 훅(`VT_NOTIFY_CLIENT_EVENTS=1`)이 꺼져 있으면 attach/detach를 알 방법이
// 없으므로 폴링으로 폴백한다. **그 사실을 UI에 표시하지 않는다**(계획서 §3):
// 사용자가 알아야 할 정보가 아니고, "실시간 아님" 같은 문구는 불안만 준다.
const POLL_MS = 5000;

function timeAgo(activity) {
  const ts = Number(activity);
  if (!ts) return '';
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (sec < 60) return t('방금');
  if (sec < 3600) return t('{n}분 전', { n: Math.floor(sec / 60) });
  return t('{n}시간 전', { n: Math.floor(sec / 3600) });
}

function row(client, onDetach, readOnly = false) {
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
    badge.className = 'vt-tag vt-clients-badge';
    badge.textContent = t('나');
    el.appendChild(badge);
  } else if (!readOnly) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'vt-icon-btn sm danger vt-clients-detach';
    btn.title = t('{name} 끊기', { name: client.label || client.tty });
    btn.setAttribute('aria-label', t('{name} 끊기', { name: client.label || client.tty }));
    btn.innerHTML = icon('x', 12);
    btn.addEventListener('click', (e) => { e.stopPropagation(); onDetach(client); });
    el.appendChild(btn);
  }
  return el;
}

// container 안에 「연결된 화면」 블록을 그린다. 반환값은 정리 함수(폴링 해제).
//
// `remote`가 오면(=`{ host, screen }`) 같은 화면을 **원격 호스트의 tmux**에 대고
// 그린다. 2.1.2에서 원격 세션은 이 블록을 통째로 숨겼다 — `/api/tmux/clients`가
// 이 맥의 tmux를 보기 때문이다. 2.1.3에서 PTY 소유 호스트에게 물어보는 경로가
// 생겼고(`/api/hosts/{id}/clients` → peer), 등급 경계가 그대로 UI가 된다:
// **목록은 view면 보이고, 끊기는 control이 없으면 403** — 그때는 버튼을 숨기고
// 이유를 한 줄로 적는다(눌러서 실패하게 두지 않는다).
export function mountClients(container, tmuxName, remote = null, sessionId = activeSessionId()) {
  if (!container || !tmuxName) return () => {};
  const screen = remote ? remote.screen || '' : '';
  const base = remote ? `/api/hosts/${encodeURIComponent(remote.host)}/clients` : '/api/tmux/clients';
  const listUrl = base;
  const detachUrl = remote ? `${base}/detach` : '/api/tmux/detach-client';
  const soloUrl = remote ? `${base}/solo` : '/api/tmux/clients/solo';

  const wrap = document.createElement('div');
  wrap.className = 'vt-clients';
  const head = document.createElement('div');
  head.className = 'vt-clients-head';
  head.appendChild(Object.assign(document.createElement('span'), {
    className: 'vt-clients-title', textContent: t('연결된 화면'),
  }));
  const solo = document.createElement('button');
  solo.type = 'button';
  solo.className = 'vt-btn sm quiet';
  solo.textContent = t('이 화면만 남기기');
  head.appendChild(solo);
  wrap.appendChild(head);
  const list = document.createElement('div');
  list.className = 'vt-clients-list';
  wrap.appendChild(list);
  const status = document.createElement('div');
  status.className = 'vt-clients-status';
  status.setAttribute('role', 'status');
  status.textContent = t('연결된 화면을 확인하는 중…');
  wrap.appendChild(status);
  // 원격에서 control 등급이 없으면 끊기 자체가 불가능하다 — 그 사실을 여기 적는다.
  const note = document.createElement('div');
  note.className = 'vt-clients-note';
  note.hidden = true;
  wrap.appendChild(note);
  container.appendChild(wrap);

  let stopped = false;
  let readOnly = false;   // 원격이 control을 안 준 상태
  let requestId = 0;

  // 패널을 연 시점의 대상 세션을 고정한다. 다른 행의 메뉴에서 열거나 패널이
  // 열린 채 활성 pane이 바뀌어도 '나'가 엉뚱한 PTY를 가리키면 안 된다.
  const me = () => sessionId;

  async function refresh() {
    if (stopped) return;
    const current = ++requestId;
    try {
      const q = remote
        ? `?session=${encodeURIComponent(tmuxName)}&screen=${encodeURIComponent(screen)}`
        : `?session=${encodeURIComponent(tmuxName)}&me=${encodeURIComponent(me() || '')}`;
      const data = await vtFetch(`${listUrl}${q}`);
      if (stopped || current !== requestId) return;
      list.innerHTML = '';
      status.replaceChildren();
      const clients = data.clients || [];
      // 열람은 사용자가 직접 요청한 것(칩/메뉴 클릭)이라 결과가 나만 있어도
      // 그대로 보여준다 — "혼자입니다"를 목록으로 보여주는 게 통째로 숨기는
      // 것보다 낫다. 조회 실패(catch 블록)만 숨긴다.
      wrap.hidden = false;
      // 남길 화면을 특정할 수 없으면 solo는 서버가 400으로 거부한다.
      // 눌러서 실패하게 두지 말고 미리 비활성화한다.
      solo.disabled = !data.me_tty || readOnly || clients.length < 2;
      solo.hidden = readOnly;
      solo.title = !data.me_tty ? t('이 화면의 tty를 확인할 수 없어 사용할 수 없습니다')
        : clients.length < 2 ? t('다른 화면이 없습니다') : '';
      note.hidden = !readOnly;
      for (const c of clients) list.appendChild(row(c, detach, readOnly));
      if (!clients.length) status.textContent = t('연결된 화면이 없습니다');
    } catch (e) {
      if (stopped || current !== requestId) return;
      list.replaceChildren();
      solo.disabled = true;
      status.replaceChildren();
      const message = document.createElement('span');
      message.textContent = t('목록을 불러오지 못했습니다: {reason}', { reason: e.message || t('연결을 확인하세요') });
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'vt-btn sm quiet';
      retry.textContent = t('다시 시도');
      retry.addEventListener('click', refresh);
      status.append(message, retry);
    }
  }

  // 원격이 view 등급이면 끊기만 403이 온다(목록은 보인다). 그 뒤로는 버튼을
  // 감추고 이유를 적는다 — 등급은 상대 맥에서만 올릴 수 있으므로 여기서
  // 재시도해봐야 계속 실패한다.
  function _handleDenied(e) {
    if (remote && e && e.status === 403) {
      readOnly = true;
      note.textContent = t("읽기 전용(view)으로 연결돼 있어 화면을 끊을 수 없습니다 — 그 호스트에서 'fsh host allow-control'로 켜세요.");
      return true;
    }
    return false;
  }

  async function detach(client) {
    try {
      await vtFetch(detachUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(remote
          ? { tty: client.tty, screen }
          : { tty: client.tty, me: me() }),
      });
    } catch (e) {
      if (!_handleDenied(e) && typeof window.showToast === 'function') {
        window.showToast(e.message || t('끊기 실패'), 'error');
      }
    }
    refresh();
  }

  solo.addEventListener('click', async () => {
    try {
      const r = await vtFetch(soloUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(remote
          ? { session: tmuxName, screen }
          : { session: tmuxName, me: me() }),
      });
      if (typeof window.showToast === 'function') {
        const n = (r.detached || []).length;
        window.showToast(n ? t('화면 {n}개를 끊었습니다', { n }) : t('끊을 화면이 없습니다'));
      }
    } catch (e) {
      if (!_handleDenied(e) && typeof window.showToast === 'function') {
        window.showToast(e.message || t('실패'), 'error');
      }
    }
    refresh();
  });

  refresh();
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  return () => { stopped = true; requestId++; clearInterval(timer); wrap.remove(); };
}
