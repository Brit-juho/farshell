// U2 — 사용량 화면을 여는 액션(`usage.open`)만 상시 청크에 둔다. 화면 본체(panels/usage.js)는 settings 청크다.
//
// 리디자인 9단계(2026-11) — 2.0의 「상시 노출」 절반(옛 레일 「사용량」 버튼의 배지 + 우측 사용량 레일 + 60초 폴링)을
// 지웠다. 둘 다 작업대(활동 막대 · 사이드바)로 바뀐 뒤 화면에 없는 DOM에만 그리고 있었다 — 아무도 못 보는 배지를
// 위해 /api/usage를 1분마다 부르던 셈이다. 사용량은 활동 막대 「사용량」(사이드바 도구 보기)에서 본다.
import { registerAction } from '../core/dom.js';

// 재진입 가드 — ports-lazy.js의 _opening과 같은 이유(청크 도착 전 두 번째 호출이 토글로 닫아버린다).
let _opening = false;

registerAction('usage.open', () => {
  if (_opening) return;
  _opening = true;
  import('./usage.js')
    .then((m) => m.showUsage())
    .catch((e) => console.error('[FarShell usage]', e))
    .finally(() => { _opening = false; });
});
