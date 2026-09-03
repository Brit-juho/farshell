// 터미널 검색 (Ctrl+F / Cmd+F) — F5에서 classic script에서 ES 모듈로 전환.
import { activeSession } from './core/store.js';
import { registerAction } from './core/dom.js';
import { gridViewEnabled, toggleGridView } from './agent/preview.js';

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

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault();
    toggleSearch();
  }
  if (e.key === 'Escape' && searchBar.classList.contains('visible')) {
    closeSearch();
  }
  // D7: 그리드 뷰 Esc 닫기 (overlay는 Esc로 닫히는 게 App UI 표준)
  if (e.key === 'Escape' && gridViewEnabled) {
    toggleGridView();
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
