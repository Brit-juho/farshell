// 더보기(⋯) 메뉴 열고닫기 — F3(d)에서 index.html 인라인 <script>(구 :446-492)를
// 모듈로 이관. ADR-8에 따라 이 메뉴 자체는 L1(레일+커맨드 팔레트)에서 폐지될
// 예정이지만, 폐지 전까지는 다른 화면 로직과 같은 방식(ESM)으로 관리한다.
import { registerAction } from '../core/dom.js';

function toggleMoreMenu(ev) {
  if (ev) ev.stopPropagation();
  const m = document.getElementById('more-menu');
  const btn = document.getElementById('more-btn');
  const open = m.classList.toggle('open');
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}
registerAction('menu.toggle', (el, e) => toggleMoreMenu(e));

document.addEventListener('click', (e) => {
  const m = document.getElementById('more-menu');
  const btn = document.getElementById('more-btn');
  if (m.classList.contains('open') && !m.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
    m.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
  }
});
// 메뉴 안에서 토글(맥에서 열기/파일)이 아닌 액션 클릭 시 메뉴 닫기
document.getElementById('more-menu').addEventListener('click', (e) => {
  const item = e.target.closest('.mi');
  if (item && !item.querySelector('input') && item.id !== 'voiceonly-btn' && item.id !== 'mediakey-btn') {
    document.getElementById('more-menu').classList.remove('open');
  }
});
// + 버튼 키보드 접근 — showAddMenu는 terminal.js(classic script)가 main.js의
// boot() 단계에서 나중에 정의한다. 이 콜백은 실제 키 입력 시점에만 실행되므로
// 그때는 이미 준비돼 있다(top-level에서 부르는 게 아니라 안전).
document.getElementById('add-btn').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); window.showAddMenu(e); }
});
// "맥에서도 열기" — 기본 체크 OFF(새 세션마다 맥 터미널 창이 자동으로 뜨는 걸
// 원치 않는 사용자가 다수), 선택은 localStorage에 기억 (켜면 유지됨)
(function initAutoMac() {
  const cb = document.getElementById('auto-mac-checkbox');
  if (!cb) return;
  cb.checked = (localStorage.getItem('vt_auto_mac') ?? 'off') === 'on';
  cb.addEventListener('change', () => {
    localStorage.setItem('vt_auto_mac', cb.checked ? 'on' : 'off');
  });
})();
// "드래그 시 자동 복사" — 기본 체크 ON, 선택은 localStorage에 기억 (terminal.js가 매 드래그마다 읽음)
(function initAutoCopy() {
  const cb = document.getElementById('autocopy-checkbox');
  if (!cb) return;
  cb.checked = (localStorage.getItem('vt_autocopy_on_select') ?? 'on') !== 'off';
  cb.addEventListener('change', () => {
    localStorage.setItem('vt_autocopy_on_select', cb.checked ? 'on' : 'off');
  });
})();
