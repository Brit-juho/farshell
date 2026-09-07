// 터미널 검색 (Ctrl+F / Cmd+F) — F5에서 classic script에서 ES 모듈로 전환.
import { register as registerKey } from './core/keymap.js';
import { activeSession } from './core/store.js';
import { registerAction } from './core/dom.js';

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

// S3: `Mod+F`는 셸의 forward-char와 겹친다 — 이제 키맵 레지스트리를 거치므로
// 사용자가 재바인딩하거나 passthrough(동작 + 터미널에도 전달)로 되돌릴 수 있다.
registerKey('search', () => toggleSearch());

// Escape는 레지스트리에 넣지 않는다 — 여러 표면(검색·팔레트·패널·모달)이 각자
// "열려 있으면 닫는다"로 쓰는 문맥 키라, 하나의 전역 액션으로 묶으면 어느 것이
// 닫힐지 예측할 수 없어진다. 재바인딩 대상도 아니다.
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

// F3(c): data-action 위임용 등록.
registerAction('search.toggle', () => toggleSearch());
registerAction('search.next', () => searchNext());
registerAction('search.prev', () => searchPrev());
registerAction('search.close', () => closeSearch());
