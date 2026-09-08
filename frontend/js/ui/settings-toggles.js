// rail 설정 패널(`#vt-rail-settings-tpl`)의 체크박스를 설정 스토어에 묶는다.
//
// ## 이 파일이 생긴 경위 (E2, 2026-09-08)
//
// 여기 있는 두 바인딩은 `ui/moreMenu.js`에 있었다. 그 파일은 폐지된 ⋯ 메뉴의
// 잔재로, 39줄 중 18줄이 "이 파일은 잔재다"라는 설명이었고 남은 로직 셋은 서로
// 무관했다 — `#add-btn` 키보드 핸들러 + 체크박스 둘. 이름이 내용과 안 맞아서
// 새 토글을 어디에 둬야 하는지 아무도 알 수 없는 상태였다.
//
// 셋을 각자 맞는 자리로 보냈다:
//   - `#add-btn` 키보드 접근 → `core/dom.js`의 data-action 위임으로 일반화.
//     같은 문제를 가진 `.wt-chevron`이 그냥 빠져 있었다 — 개별 핸들러 방식이
//     구조적으로 놓칠 수밖에 없는 모양이라 한 곳에서 처리하게 바꿨다.
//   - 체크박스 둘 → 이 파일.
//
// ## 왜 스토어인가
//
// 설정의 단일 진실은 `/api/workspace`의 `settings`다(ADR-5). 체크박스의
// `.checked`를 진실로 쓰면 그 값이 기기에 갇힌다 — 폰에서 켠 게 맥북에 안 넘어간다.
// `mouse.autocopyOnSelect`는 S2에서 이미 옮겨왔고, `session.openOnMac`은 E2에서
// 뒤늦게 따라왔다.
'use strict';

import { get as setting, set as setSetting, subscribe as onSettings } from '../core/settings.js';

/** 체크박스 하나를 불리언 설정 키에 양방향으로 묶는다. */
function bindCheckbox(id, key) {
  const cb = document.getElementById(id);
  if (!cb) return;
  const sync = () => { cb.checked = setting(key); };
  sync();
  // 다른 표면(설정 화면·커맨드 팔레트·다른 기기)에서 바뀌어도 따라간다.
  onSettings(sync);
  cb.addEventListener('change', () => setSetting(key, cb.checked));
}

// 드래그 선택 시 자동 복사 (S2에서 스토어로 승격)
bindCheckbox('autocopy-checkbox', 'mouse.autocopyOnSelect');

// 새 세션 생성 시 맥에서도 iTerm 창을 연다 (E2에서 스토어로 승격)
bindCheckbox('auto-mac-checkbox', 'session.openOnMac');
