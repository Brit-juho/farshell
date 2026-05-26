    // --- 터미널 검색 (Ctrl+F / Cmd+F) ---
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
      if (activeId && sessions[activeId]) sessions[activeId].term.focus();
    }
    function searchNext() {
      if (!activeId || !sessions[activeId]) return;
      sessions[activeId].searchAddon.findNext(searchInput.value);
    }
    function searchPrev() {
      if (!activeId || !sessions[activeId]) return;
      sessions[activeId].searchAddon.findPrevious(searchInput.value);
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
