// 설정 > 「업데이트」 — 새 버전 확인(읽기 전용)과 자동 업데이트(기본 꺼짐).
//
// 서버가 24시간마다 확인하고(server/update_check.py) 결과를 `GET /api/update/status` 로 준다. 여기서 바꾸는 값은
// 이 기기의 설정(localStorage)이 아니라 **서버 설정**이다 — 설정 변경(`PUT /api/update/settings`)은 사람 세션에 더해
// 웹 비밀번호 재확인(승격)이 필요하다: 네트워크에서 받은 코드를 실행하게 만드는 스위치이기 때문이다.
import { vtFetch } from '../../../core/api.js';
import { t } from '../../../core/i18n.js';
import { icon } from '../../../ui/icons.js';
import { withElevation } from '../../../authz/elevate.js';
import { row, group, toast, fmtWhen } from '../controls.js';
import { reasonText, blockedText, AUTO_HOURS } from '../../../shell/update-data.js';

function switchControl(on, disabled, onToggle) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'vt-toggle';
  btn.setAttribute('role', 'switch');
  btn.setAttribute('aria-checked', on ? 'true' : 'false');
  btn.disabled = !!disabled;
  const a = document.createElement('span'); a.textContent = 'ON';
  const b = document.createElement('span'); b.textContent = 'OFF';
  btn.append(a, b);
  btn.addEventListener('click', () => onToggle(btn.getAttribute('aria-checked') !== 'true'));
  return btn;
}

function selectControl(options, value, disabled, onChange) {
  const wrap = document.createElement('div');
  wrap.className = 'vt-select-wrap';
  const sel = document.createElement('select');
  sel.className = 'vt-select';
  sel.disabled = !!disabled;
  for (const [v, label] of options) {
    const opt = document.createElement('option');
    opt.value = String(v);
    opt.textContent = label;
    sel.appendChild(opt);
  }
  sel.value = String(value);
  sel.addEventListener('change', () => onChange(sel.value));
  wrap.appendChild(sel);
  wrap.insertAdjacentHTML('beforeend', icon('chevron-down', 12, 2));
  return wrap;
}

function note(text, cls = 'vt-set-help') {
  const el = document.createElement('div');
  el.className = cls;
  el.textContent = text;
  return el;
}

/** deps 는 시험용: { vtFetch, withElevation, toast }. */
export function renderUpdateSection(deps = {}) {
  const api = deps.vtFetch || vtFetch;
  const elevate = deps.withElevation || withElevation;
  const say = deps.toast || toast;
  const root = document.createElement('div');
  root.className = 'vt-set-update';
  let st = null;
  let busy = false;

  const json = (method, path, body) => api(path, {
    method, headers: { 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}),
  });

  const errText = (e) => {
    try { const d = e && e.data; if (d && d.reason) return d.reason; } catch (_) { /* 아래로 */ }
    return (e && e.message) || t('실패했습니다');
  };

  async function save(patch) {
    if (busy) return;
    busy = true; paint();
    try {
      const res = await elevate(() => json('PUT', '/api/update/settings', patch));
      if (res) st = res;                         // null 이면 비밀번호 입력을 취소한 것 — 그대로 둔다
    } catch (e) {
      say(errText(e), 'error');
    }
    busy = false; paint();
  }

  async function checkNow() {
    if (busy) return;
    busy = true; paint();
    try {
      const res = await json('POST', '/api/update/check');
      if (res && res.status) st = res.status;
      if (res && res.ran === false) say(t('방금 확인했습니다 — 잠시 뒤에 다시 눌러 주세요'), 'info');
    } catch (e) {
      say(errText(e), 'error');
    }
    busy = false; paint();
  }

  function paint() {
    root.replaceChildren();
    if (!st) { root.appendChild(note(t('업데이트 상태 확인 중…'))); return; }
    const s = st.settings || {};
    const envOff = !!st.env_off;

    // ── 상태 ──
    const state = document.createElement('div');
    state.appendChild(row(t('현재 버전'), note(st.current || '?', 'vt-set-about')));
    let latest;
    if (!st.check_enabled) latest = t('확인 끔');
    else if (!st.checked_at) latest = t('아직 확인한 기록이 없습니다');
    else if (st.available) latest = st.latest_tag || `dev ${st.latest}`;
    else latest = t('최신입니다');
    const latestRow = row(t('최신 버전'), note(latest, 'vt-set-about'));
    const why = st.error || (st.available && st.signature_ok === true ? t('서명 검증을 통과했습니다') : null) || blockedText(st.blocked);
    if (why) latestRow.querySelector('.vt-set-meta')?.appendChild(note(why));
    state.appendChild(latestRow);
    const check = document.createElement('button');
    check.type = 'button'; check.className = 'vt-btn sm';
    check.textContent = busy ? t('확인 중…') : t('지금 확인');
    check.disabled = busy || !st.check_enabled;
    check.addEventListener('click', checkNow);
    state.appendChild(row(t('마지막 확인'), check, st.checked_at ? fmtWhen(st.checked_at) : t('기록 없음')));
    if (st.available && st.applicable) state.appendChild(note(t('적용하려면 터미널에서 fsh update 를 실행하세요 (자동 업데이트를 켜면 한가한 시각에 알아서 합니다).')));
    root.appendChild(group(t('상태'), state));

    // ── 확인 ──
    const checkBox = document.createElement('div');
    checkBox.appendChild(row(t('새 버전 확인'),
      switchControl(!!s.check && !envOff, busy || envOff, (on) => save({ check: on })),
      envOff
        ? t('환경변수 VT_UPDATE_CHECK=0 으로 꺼져 있습니다 — 설정 화면에서 켤 수 없습니다.')
        : t('끄면 서버가 어떤 네트워크 요청도 하지 않습니다. 켜 두면 24시간마다 git 원격만 확인합니다(토큰·사용자 정보 전송 없음).')));
    root.appendChild(group(t('확인'), checkBox));

    // ── 자동 업데이트 ──
    const auto = document.createElement('div');
    auto.appendChild(row(t('자동 업데이트'),
      selectControl([['off', t('끔')], ['stable-only', t('서명된 stable 릴리스만')]], s.auto || 'off', busy, (v) => save({ auto: v })),
      t('기본은 꺼짐입니다. 켜면 서명 검증을 통과한 stable 릴리스를 정해 둔 시각에, 한가할 때만 받아 서버를 다시 시작합니다(tmux 세션은 유지). 메이저 버전 상승·dev 채널·로컬 변경이 있는 설치는 절대 자동으로 하지 않고 알리기만 합니다. 신뢰 키는 터미널에서 fsh update trust 로 사람이 한 번 고정해야 합니다.')));
    auto.appendChild(row(t('적용 시각'),
      selectControl(AUTO_HOURS.map((h) => [h, `${String(h).padStart(2, '0')}:00 – ${String(h).padStart(2, '0')}:59`]), s.auto_hour ?? 4,
        busy || (s.auto || 'off') === 'off', (v) => save({ auto_hour: Number(v) })),
      t('이 기기가 아니라 서버(맥)의 지역 시간입니다. 최근 15분 동안 터미널 입력이 없고 작업 중인 에이전트가 없을 때만 합니다. 못 맞추면 다음 점검으로 미루고, 7일 넘게 못 하면 알립니다.')));
    const reason = (s.auto || 'off') !== 'off' && st.available ? reasonText(st.auto && st.auto.reason) : null;
    if (reason) auto.appendChild(note(reason));
    const la = st.last_apply;
    if (la && (la.result === 'ok' || la.result === 'failed')) {
      const line = la.result === 'ok'
        ? t('마지막 자동 적용: {from} → {to} ({when})', { from: la.from || '?', to: la.to || '?', when: fmtWhen(la.at) })
        : t('마지막 자동 적용 실패: {why}{back} ({when})', {
          why: String(la.reason || '').split('\n')[0].slice(0, 120) || t('알 수 없는 오류'),
          back: la.rolled_back ? ` ${t('(되돌림)')}` : '', when: fmtWhen(la.at) });
      auto.appendChild(note(line));
    }
    root.appendChild(group(t('자동 업데이트'), auto));
  }

  paint();
  api('/api/update/status').then((r) => { st = r; paint(); }).catch((e) => {
    root.replaceChildren(note(errText(e) || t('업데이트 상태를 확인할 수 없습니다.')));
  });
  return root;
}
