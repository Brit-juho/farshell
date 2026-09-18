// U2 — 사용량의 **상시 동작** 절반. 화면(panels/usage.js)과 분리돼 있다.
//
// 왜 쪼갰나: usage.js 전체를 지연 청크로 보내면 rail 배지 폴링이 죽는다 —
// 사용자가 「사용량」을 열기 전까지 배지가 영원히 안 뜬다. "상시 노출"이 이
// 기능의 존재 이유라 그건 기능을 없애는 것과 같다. 그래서 **켜져 있어야 하는
// 것**(배지 칠하기 + 60초 폴링 + 액션 등록)만 여기 남기고, **볼 때 필요한
// 것**(패널·우측 레일 렌더러 13KB)은 지연시킨다.
//
// 우측 레일은 예외적으로 렌더러가 필요하다(패널이 닫혀 있어도 값을 그린다).
// 그래서 여기서 동적 import하되 **레일이 실제로 보일 때만** 한다 —
// compact/regular 폭에서는 그 코드를 아예 안 받는다.
import { vtFetch } from '../core/api.js';
import { registerAction } from '../core/dom.js';

const BADGE_POLL_MS = 60000;   // 피드가 90초 주기라 60초면 충분하다

let _bgTimer = null;

// rail 버튼의 활성 프로필 표시. 화면 쪽(usage.js의 refresh)도 이걸 그대로
// 쓰므로 여기가 단일 구현이다 — 새 청크 규칙(vite.config.js) 아래에서는 지연
// 모듈이 상시 모듈을 평범하게 import해도 승격이 일어나지 않는다.
export function paintRailBadge(data) {
  const btn = document.getElementById('vt-rail-usage');
  if (!btn) return;
  const badge = btn.querySelector('.vt-rail-badge')
    || btn.appendChild(Object.assign(document.createElement('span'), { className: 'vt-rail-badge' }));
  const live = (data?.profiles || []).filter((p) => p.has_live_session).map((p) => p.name);
  const active = data?.active_profile;
  // 2026-09-18 — 한 줄짜리 title을 두 단 툴팁으로 나눈다(ui/tooltip.js): 이름은
  // 「사용량」 하나뿐이고, 활성 프로필·실행 중 목록은 값이므로 보조 단(mono)이다.
  btn.setAttribute('data-tip', '사용량');
  const detail = data?.available
    ? [active ? `활성: ${active}` : '', live.length ? `실행 중: ${live.join(', ')}` : ''].filter(Boolean).join(' · ')
    : '';
  if (detail) btn.setAttribute('data-tip-sub', detail); else btn.removeAttribute('data-tip-sub');
  badge.hidden = !live.length;
  // ● 글리프를 .status-dot 컴포넌트로. 배지가 "몇 개"가 아니라 "돌고 있다"를
  // 뜻하는 자리라 숫자가 아닌 점이 맞지만, 글리프는 상태색과 연결돼 있지 않았다.
  badge.textContent = '';
  badge.classList.toggle('vt-rail-badge-dot', true);
  badge.classList.toggle('status-dot', !!live.length);
  if (live.length) badge.dataset.state = 'working';
  else delete badge.dataset.state;
}

// 패널이 닫혀 있어도 rail 배지는 최신이어야 한다(그게 "상시 노출"의 의미다).
// 배지는 한도형(%) 전용이라(rail 버튼 하나에 %를 하나만 실을 수 있다) 누적형은
// 요청하지 않는다 — 자세한 값은 패널/우측 레일에서 본다.
async function refreshBadgeOnly() {
  try {
    const data = await vtFetch('/api/usage');
    paintRailBadge(data);
    // L8/U2: 우측 레일은 **패널이 닫혀 있어도** 최신이어야 한다. 화면에
    // 없으면(compact/regular) 아무 일도 안 한다 — 렌더러도 안 받는다.
    const rr = document.getElementById('vt-right-rail-body');
    if (rr && rr.offsetParent !== null) {
      let counterData = { available: false, reason: 'read-failed' };
      try { counterData = await vtFetch('/api/usage/counter'); } catch (_) { /* 조용히 무시 */ }
      const { renderBody } = await import('./usage.js');
      renderBody(data, counterData, rr);
    }
  } catch (_) { /* 조용히 무시 */ }
}

// 재진입 가드 — ports-lazy.js의 _opening과 같은 이유(청크 도착 전 두 번째
// 호출이 토글로 닫아버린다).
let _opening = false;

registerAction('usage.open', () => {
  if (_opening) return;
  _opening = true;
  import('./usage.js')
    .then((m) => m.showUsage())
    .catch((e) => console.error('[FarShell usage]', e))
    .finally(() => { _opening = false; });
});

// rail 항목이 보이는 환경(=사용량 소스가 있는 환경)에서만 배지를 돌린다.
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('vt-rail-usage');
  if (!btn) return;
  setTimeout(() => {
    if (getComputedStyle(btn).display === 'none') return;   // capability 게이팅에 걸린 환경
    refreshBadgeOnly();
    _bgTimer = setInterval(() => { if (!document.hidden) refreshBadgeOnly(); }, BADGE_POLL_MS);
  }, 1500);
});
