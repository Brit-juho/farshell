// 터미널 내 검색(xterm searchAddon) — 구 js/search.js(F5 classic→ESM 전환)에서
// 이름만 바꿔 옮겼다. N40/N46(60-settings-palette.md §3)에서 `Mod+F`가
// 팔레트 `~`(스크롤백 검색) 모드로 재배선되면서, 이 파일이 담당하던 자리
// (`#search-bar` DOM 검색창)는 `Mod+Shift+F`(키맵 액션 id `searchInPane`,
// core/keymap.js)로 내려갔다 — **액션 id `search.toggle`은 그대로 유지**한다
// (팔레트·키맵 화면·rail이 이 id를 참조한다). 이 파일 자체는 작고
// `#search-bar`가 index.html에 항상 존재하는 정적 마크업이라, palette-lazy.js
// 처럼 지연 청크로 옮길 이유가 없다 — main.js 엔트리 그래프에 그대로 둔다.
import { activeSession } from '../core/store.js';
import { registerAction } from '../core/dom.js';

const searchBar = document.getElementById('search-bar');
const searchInput = document.getElementById('search-input');

function toggleSearch() {
  searchBar.classList.toggle('visible');
  if (searchBar.classList.contains('visible')) {
    searchInput.focus();
    searchInput.select();
  }
}
function closeSearch() {
  searchBar.classList.remove('visible');
  const s = activeSession();
  if (s) s.term.focus();
}
function searchNext() {
  const s = activeSession();
  if (!s) return;
  s.searchAddon.findNext(searchInput.value);
}
function searchPrev() {
  const s = activeSession();
  if (!s) return;
  s.searchAddon.findPrevious(searchInput.value);
}

// Escape는 키맵 레지스트리에 넣지 않는다 — 여러 표면(검색·팔레트·패널·모달)이
// 각자 "열려 있으면 닫는다"로 쓰는 문맥 키라, 하나의 전역 액션으로 묶으면 어느
// 것이 닫힐지 예측할 수 없어진다. 재바인딩 대상도 아니다.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && searchBar.classList.contains('visible')) {
    closeSearch();
  }
});
searchInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.shiftKey ? searchPrev() : searchNext();
  }
});

// F3(c): data-action 위임용 등록. **id는 바꾸지 않는다** — palette-lazy.js의
// `searchInPane` 키맵 핸들러가 getAction('search.toggle')로 이걸 찾는다.
registerAction('search.toggle', () => toggleSearch());
registerAction('search.next', () => searchNext());
registerAction('search.prev', () => searchPrev());
registerAction('search.close', () => closeSearch());
