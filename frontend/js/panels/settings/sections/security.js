import { vtFetch } from '../../../core/api.js';
import { statusLine, secRow, fmtWhen } from '../controls.js';
import { t } from '../../../core/i18n.js';

// ── 「보안」 ──────────────────────────────────────────────────────────────
// 60-settings-palette.md §2 — 비밀번호 · OTP · 기기 · 승격 상태.
//
// **인증 수단은 읽기 전용이다.** 비밀번호 설정·OTP 활성화·기기 폐기는 `fsh`
// CLI에서만 한다. 공개 터널 너머에서 인증 수단 자체를 바꿀 수 있으면, 그 인증으로
// 들어온 사람이 그 인증을 풀 수 있다는 뜻이라 여기서는 상태와 명령어만 안내한다.
//
// 2026-09-18 — 로그아웃은 그 규칙의 예외다(유일한 쓰기 조작).
// 위 근거는 "권한을 **늘리거나** 인증을 무력화하는 조작"을 막자는 것인데,
// 로그아웃은 반대로 자기 권한을 **끊는다** — 남이 호출해도 자기 세션만 끝난다.
// 그런데 지금까지 웹에는 로그아웃 경로가 아예 없었다(`POST /api/auth/logout`은
// 서버에만 있고 프론트가 부르지 않았다). 공용 맥이나 남의 기기에서 열었을 때
// 끝낼 방법이 없다는 뜻이라, 상태만 보여주는 화면에 이 하나만 추가한다.
// 기기 등록(vt_device)은 서버가 유지한다 — 다시 들어올 때 OTP를 또 치지 않게.
export function renderSecuritySection() {
  const frag = document.createDocumentFragment();

  const authHost = document.createElement('div');
  authHost.className = 'vt-set-sechost';
  authHost.appendChild(statusLine(t('인증 상태 확인 중…')));
  frag.appendChild(authHost);

  const devTitle = document.createElement('div');
  devTitle.className = 'vt-set-label';
  devTitle.textContent = t('등록 기기');
  frag.appendChild(devTitle);

  const devHost = document.createElement('div');
  devHost.className = 'vt-set-sechost';
  devHost.appendChild(statusLine(t('기기 목록 확인 중…')));
  frag.appendChild(devHost);

  const elevHost = document.createElement('div');
  elevHost.className = 'vt-set-sechost';
  elevHost.appendChild(statusLine(t('승격 상태 확인 중…')));
  frag.appendChild(elevHost);

  // 로그아웃은 비밀번호가 실제로 걸려 있을 때만 의미가 있다 — 인증이 없는
  // 설치에서는 끊을 세션 자체가 없으므로 버튼을 그리지 않는다(누르면 아무 일도
  // 안 일어나는 버튼을 두지 않는다).
  const outHost = document.createElement('div');
  outHost.className = 'vt-set-sechost';
  frag.appendChild(outHost);

  vtFetch('/api/auth/status').then((r) => {
    authHost.innerHTML = '';
    const pw = !!(r && r.password_set);
    if (pw) outHost.appendChild(_logoutRow());
    authHost.appendChild(secRow(
      t('웹 로그인 비밀번호'),
      pw ? t('설정됨') : t('설정 안 됨'),
      pw ? 'on' : 'off',
      pw ? null : t("터미널에서 'fsh password'로 설정합니다."),
    ));
    const otp = !!(r && r.otp_enabled);
    authHost.appendChild(secRow(
      t('OTP (새 기기 등록 관문)'),
      otp ? t('활성') : t('비활성'),
      otp ? 'on' : 'off',
      otp ? t('처음 보는 기기를 등록할 때만 OTP를 요구합니다.')
          : t("터미널에서 'fsh otp setup'으로 활성화합니다."),
    ));
  }).catch(() => { authHost.innerHTML = ''; authHost.appendChild(statusLine(t('인증 상태를 확인할 수 없습니다.'))); });

  vtFetch('/api/devices').then((r) => {
    const rows = (r && Array.isArray(r.devices)) ? r.devices : [];
    devHost.innerHTML = '';
    if (!rows.length) {
      devHost.appendChild(statusLine(t('등록된 기기가 없습니다. 휴대폰에서 QR로 처음 접속하면 등록됩니다.')));
      return;
    }
    // 2026-09-24(2.1 D6) — 폭이 좁은 화면(폰)에서 열(기기/id/등록/마지막
    // 사용) 4개가 다 안 들어가면 표가 화면 밖으로 그냥 넘쳤다 — 감지
    // 커버리지 표(agents.js)가 이미 쓰는 가로 스크롤 host를 똑같이 씌운다.
    const tableHost = document.createElement('div');
    tableHost.className = 'vt-set-covtable-host';
    tableHost.appendChild(renderDeviceTable(rows));
    devHost.appendChild(tableHost);
    devHost.appendChild(statusLine(t("기기 폐기는 터미널에서 'fsh device revoke <id>'로 합니다 — 그 기기의 세션도 함께 끊깁니다.")));
    if (rows.some((d) => d.expired)) {
      devHost.appendChild(statusLine(t("만료된 기기는 접속할 수 없고 7일 뒤 목록에서 지워집니다. 지금 정리하려면 'fsh device prune'.")));
    }
  }).catch(() => { devHost.innerHTML = ''; devHost.appendChild(statusLine(t('기기 목록을 확인할 수 없습니다.'))); });

  vtFetch('/api/auth/elevation').then((r) => {
    const until = (r && Number(r.elevated_until)) || 0;
    const left = until ? Math.max(0, Math.round((until * 1000 - Date.now()) / 60000)) : 0;
    elevHost.innerHTML = '';
    elevHost.appendChild(secRow(
      t('승격 세션'),
      until && left ? t('승격됨 · {n}분 남음', { n: left }) : t('승격 안 됨'),
      until && left ? 'on' : 'off',
      // ADR-27 — push/PR을 만들지 않기로 해서 승격을 요구하는 경로가 지금은 없다.
      // 이 문장이 없으면 "왜 항상 비활성이지?"로 읽힌다.
      t('현재 이 기능을 쓰는 경로가 없습니다(ADR-27) — 승격을 요구하는 조작이 아직 없습니다.'),
    ));
  }).catch(() => { elevHost.innerHTML = ''; elevHost.appendChild(statusLine(t('승격 상태를 확인할 수 없습니다.'))); });

  return frag;
}

// 로그아웃 한 줄. 누르면 세션 쿠키가 지워지므로 그 자리에서 다시 그릴 화면이
// 없다 — 서버 응답을 받은 뒤 통째로 다시 연다(로그인 게이트가 뜬다).
function _logoutRow() {
  const wrap = document.createElement('div');
  wrap.className = 'vt-set-logoutrow';

  const label = document.createElement('div');
  label.className = 'vt-set-label';
  label.textContent = t('이 브라우저에서 로그아웃');
  wrap.appendChild(label);

  const help = document.createElement('div');
  help.className = 'vt-set-help';
  help.textContent = t("이 브라우저의 세션만 끝냅니다. 기기 등록은 남아 있어 다시 들어올 때 비밀번호만 입력하면 됩니다. 기기 자체를 끊으려면 터미널에서 'fsh device revoke <id>'를 씁니다.");
  wrap.appendChild(help);

  // 2026-09-22(사용자 지적) — danger(빨강)는 이 저장소에서 되돌릴 수 없는
  // 동작 전용이다(스니펫·큐 항목·포트 프로세스·음성 모델·워크트리 삭제).
  // 로그아웃은 바로 위 문구가 말하듯 되돌리기 쉬운 동작이라 그 등급이 아니다.
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'vt-btn sm';
  btn.textContent = t('로그아웃');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = t('로그아웃 중…');
    try {
      await vtFetch('/api/auth/logout', { method: 'POST' });
    } catch (_) {
      // 실패해도 되돌리지 않는다 — 쿠키가 이미 지워졌을 수도 있으므로
      // 다시 여는 쪽이 진실을 보여준다(게이트가 뜨면 끊긴 것이다).
    }
    location.reload();
  });
  wrap.appendChild(btn);

  return wrap;
}

function renderDeviceTable(rows) {
  const table = document.createElement('table');
  table.className = 'vt-set-covtable vt-set-devtable';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of [t('기기'), 'id', t('등록'), t('마지막 사용'), t('기간')]) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const d of rows) {
    const tr = document.createElement('tr');
    if (d.current) tr.dataset.current = '1';
    if (d.expired) tr.dataset.expired = '1';

    const label = document.createElement('td');
    label.textContent = d.label || t('기기');
    if (d.current) {
      const badge = document.createElement('span');
      badge.className = 'vt-tag vt-set-devme';
      badge.textContent = t('이 기기');
      label.appendChild(badge);
    }
    const meta = deviceMeta(d);
    if (meta) {
      const sub = document.createElement('span');
      sub.className = 'vt-set-devmeta';
      sub.textContent = meta;
      label.appendChild(sub);
    }

    const id = document.createElement('td');
    id.className = 'vt-set-covcli';
    id.textContent = d.id || '—';

    const added = document.createElement('td');
    added.textContent = fmtWhen(d.added_at);

    const seen = document.createElement('td');
    seen.textContent = fmtWhen(d.last_seen);

    const life = document.createElement('td');
    const [lifeText, lifeState] = deviceLife(d, Date.now() / 1000);
    life.className = 'vt-set-devlife';
    life.dataset.state = lifeState;
    life.textContent = lifeText;

    tr.append(label, id, added, seen, life);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

const VIA_LABEL = { tunnel: t('터널'), tailscale: 'Tailscale', lan: 'LAN', local: t('이 맥'), direct: t('직접') };

// "Android 15 · Chrome 140 · 203.0.113.5 (터널)". 예전에 등록된 기기는 이 정보가 없어 빈 문자열.
export function deviceMeta(d) {
  const parts = [d.os, d.browser].filter(Boolean);
  if (d.ip_last) {
    const via = VIA_LABEL[d.via] || '';
    parts.push(via ? `${d.ip_last} (${via})` : d.ip_last);
  }
  return parts.join(' · ');
}

function span(sec) {
  const s = Math.max(0, Math.floor(sec));
  if (s < 3600) return t('{n}분', { n: Math.max(1, Math.floor(s / 60)) });
  if (s < 86400) return t('{n}시간', { n: Math.floor(s / 3600) });
  return t('{n}일', { n: Math.floor(s / 86400) });
}

// [표시 문구, data-state]. state: expired / temp / persistent.
export function deviceLife(d, nowSec) {
  const exp = Number(d.expires_at) || 0;
  if (d.expired || (exp && exp <= nowSec)) return [t('만료됨'), 'expired'];
  if (!exp) return ['—', 'persistent'];
  const left = t('{time} 남음', { time: span(exp - nowSec) });
  return d.kind === 'temporary' ? [t('임시 · {time}', { time: left }), 'temp'] : [left, 'persistent'];
}

