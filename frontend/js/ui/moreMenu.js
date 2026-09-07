// 예전 더보기(⋯) 메뉴의 잔재 — L4에서 `#more-menu`/`#more-btn` 자체는 완전히
// 제거되고(index.html) 「설정」 그룹 마크업만 `#vt-rail-settings-tpl`로 옮겨져
// rail의 설정 패널이 됐다(layout/rail.js). 이 파일엔 그 메뉴를 열고 닫던
// toggleMoreMenu/menu.toggle·바깥 클릭 리스너가 있었는데, 대상 엘리먼트
// (#more-menu/#more-btn)가 이제 DOM에 없어 전부 죽은 코드가 됐다 — 삭제.
//
// 남는 건 그 메뉴 안에 있던 체크박스 2개(자동 복사·맥에서도 열기)의 최초
// 상태 동기화 IIFE뿐이다 — 이 로직은 ⋯ 메뉴와 무관하게 그 checkbox id가
// DOM 어디에 있든(지금은 #vt-rail-settings-tpl 안) 그대로 유효하다.
"use strict";
import { get as setting, set as setSetting, subscribe as onSettings } from '../core/settings.js';
// + 버튼 키보드 접근 — showAddMenu는 terminal.js(classic script)가 main.js의
// boot() 단계에서 나중에 정의한다. 이 콜백은 실제 키 입력 시점에만 실행되므로
// 그때는 이미 준비돼 있다(top-level에서 부르는 게 아니라 안전). ⋯ 메뉴와
// 무관 — #add-btn은 여전히 topbar에 있다.
document.getElementById('add-btn').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); window.showAddMenu(e); }
});
// "맥에서도 열기" — 기본 체크 OFF(새 세션마다 맥 터미널 창이 자동으로 뜨는 걸
// 원치 않는 사용자가 다수), 선택은 localStorage에 기억 (켜면 유지됨).
(function initAutoMac() {
  const cb = document.getElementById('auto-mac-checkbox');
  if (!cb) return;
  cb.checked = (localStorage.getItem('vt_auto_mac') ?? 'off') === 'on';
  cb.addEventListener('change', () => {
    localStorage.setItem('vt_auto_mac', cb.checked ? 'on' : 'off');
  });
})();
// "드래그 시 자동 복사" — S2에서 설정 스토어로 승격. 같은 키를 네 파일이 각자
// localStorage에서 읽던 것을(여기 + term/selection.js 2곳 + voice/notify.js)
// 스토어 하나로 모았다. 다른 표면에서 값이 바뀌면 이 체크박스도 따라간다.
(function initAutoCopy() {
  const cb = document.getElementById('autocopy-checkbox');
  if (!cb) return;
  const sync = () => { cb.checked = setting('mouse.autocopyOnSelect'); };
  sync();
  onSettings(sync);
  cb.addEventListener('change', () => setSetting('mouse.autocopyOnSelect', cb.checked));
})();
