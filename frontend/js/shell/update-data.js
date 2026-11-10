// 업데이트 확인·자동 적용 표시용 순수 로직 — FarShell 메뉴 줄(TitleBar.tsx)과 설정 > 업데이트(panels/settings/sections/update.js)가
// 같이 읽는다. 서버 응답 모양은 API.md «Update» · server/update_check.py `public_status()`.
//
// ⚠ core/i18n.js 말고 아무것도 import하지 않는다(지연 청크 안에서 core 싱글톤이 복제되지 않게 — hud-layout.js와 같은 이유).
import { t } from '../core/i18n.js';

const SEEN_KEY = 'vt.update.applySeen';

/** 메뉴에 「업데이트 있음」 줄을 띄울 상태인가. 있을 때만 {label, applicable} — 없으면 null. */
export function menuLine(st) {
  if (!st || !st.available || !st.latest) return null;
  const label = st.latest_tag
    ? t('업데이트 있음 ({ver})', { ver: st.latest_tag })
    : t('업데이트 있음 (dev {ver})', { ver: st.latest });
  return { label, applicable: !!st.applicable };
}

/** 자동 적용 결과 알림(한 번만 보인다). `seenAt` 이후의 ok/failed 만. 없으면 null. */
export function applyNotice(st, seenAt) {
  const la = st && st.last_apply;
  if (!la || (la.result !== 'ok' && la.result !== 'failed')) return null;
  if (!(la.at > (Number(seenAt) || 0))) return null;
  if (la.result === 'ok') {
    return { at: la.at, type: 'success', text: t('자동 업데이트 완료 v{ver}', { ver: String(la.to || '').replace(/^v/, '') }) };
  }
  const why = la.reason ? String(la.reason).split('\n')[0].slice(0, 120) : t('알 수 없는 오류');
  const text = la.rolled_back
    ? t('자동 업데이트 실패: {why} (되돌림)', { why })
    : t('자동 업데이트 실패: {why}', { why });
  return { at: la.at, type: 'error', text };
}

export function readSeen() {
  try { return Number(localStorage.getItem(SEEN_KEY)) || 0; } catch (_) { return 0; }
}

export function writeSeen(at) {
  try { localStorage.setItem(SEEN_KEY, String(at)); } catch (_) { /* 저장소를 못 쓰면 다음에 한 번 더 보일 뿐이다 */ }
}

/** `auto.reason` 코드(서버 update_check.py)를 사람 말로. 모르는 코드는 null. */
export function reasonText(code) {
  switch (code) {
    case 'waiting_window': return t('적용 시각을 기다리는 중입니다');
    case 'busy_terminal_input': return t('최근 터미널 입력이 있어 미뤘습니다');
    case 'busy_agent_busy': return t('작업 중이거나 입력을 기다리는 에이전트가 있어 미뤘습니다');
    case 'applying': return t('적용 중입니다');
    case 'gave_up': return t('같은 버전의 자동 적용이 3번 실패해 멈췄습니다 — 직접 fsh update 를 실행하세요');
    case 'stalled': return t('7일 넘게 적용하지 못해 멈췄습니다 — 직접 fsh update 를 실행하세요');
    case 'channel': return t('채널이 stable 이 아니어서 자동으로 적용하지 않습니다 (알림만)');
    case 'major': return t('메이저 버전이 오르는 업데이트는 자동으로 적용하지 않습니다');
    case 'dirty': return t('로컬 변경이 있어 자동으로 적용하지 않습니다');
    case 'branch': return t('릴리스 브랜치가 아닌 곳이라 자동으로 적용하지 않습니다');
    case 'no_trusted_key': return t('신뢰하는 서명 키가 없습니다 — 터미널에서 fsh update trust 를 먼저 하세요');
    case 'no_master': return t('origin/master 를 확인할 수 없어 적용하지 않습니다');
    case 'signature': return t('서명 검증을 통과하지 못해 적용하지 않습니다');
    case 'not_newer': return null;
    default: return null;
  }
}

/** `blocked`(이 설치가 업데이트를 받을 수 없는 이유 코드). */
export function blockedText(code) {
  switch (code) {
    case 'no_trusted_key': return t('서명 키가 고정돼 있지 않아 서명을 확인하지 못했습니다 — 터미널에서 fsh update trust');
    case 'dirty': return t('로컬 변경이 있어 업데이트할 수 없습니다');
    case 'devmode': return t('devmode 설치는 알림만 합니다 — 직접 git pull 하세요');
    case 'channel_unset': return t('채널이 정해지지 않았습니다 — fsh update --channel stable');
    default: return null;
  }
}

export const AUTO_MODES = ['off', 'stable-only'];
export const AUTO_HOURS = Array.from({ length: 24 }, (_, i) => i);
