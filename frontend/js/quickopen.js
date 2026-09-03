// Quick open (D) — 세션 · 최근 파일 · 명령을 한 입력창에서 찾는다.
// L8/L1과 같은 문제(진입 동선)를 없애는 목적. 새 서버 엔드포인트 없이 이미 있는
// 데이터(열린 세션, 코드 뷰어의 최근 파일 목록, 고정 명령 리스트)만으로 만든다 —
// 파일시스템 전체를 훑는 진짜 fuzzy 검색은 별도 서버 API가 필요해 스코프 밖으로 뺐다.
//
// _loadRecent()/showViewer()/_selectFile()은 F4에서 panels/viewer/{tree,shell}.js로
// 옮겨간 뒤에도 window 브리지로 계속 노출된다(그 파일들 하단 주석 참고) — main.js의
// 정적 import가 이 classic script보다 먼저 평가되므로 항상 이미 준비돼 있다.
// openPanel/closePanel/vtFetch도 마찬가지로 core/api.js·panels/panel.js가 먼저 평가된다.

    function closeQuickOpen() { closePanel('vt-qopen'); }

    // 정적 명령 목록 — core/dom.js의 액션 레지스트리를 그대로 조회한다(F3(c)).
    // 예전엔 window[c.fn]를 직접 찾았는데, 그 함수가 const로 선언돼 있었다면
    // window 프로퍼티가 아니라 조용히 실패했을 구조였다 — registerAction()으로
    // 명시 등록된 것만 걸리므로 그 위험이 없다. 로드 순서(voice.js 미설치 등)에
    // 따라 아직 등록 안 된 액션도 있을 수 있어 getAction()으로 필터링한다.
    function _quickOpenCommands() {
      const cmds = [
        { label: '코드 뷰어 열기', hint: 'Ctrl+Shift+E', action: 'viewer.show' },
        { label: '프롬프트 큐', hint: '', action: 'queue.show' },
        { label: '포트 대시보드', hint: '', action: 'ports.show' },
        { label: 'Grid 뷰', hint: '', action: 'grid.toggle' },
        { label: '터미널 내 검색', hint: 'Ctrl/Cmd+F', action: 'search.toggle' },
        { label: '새 세션', hint: '', action: 'session.add-menu' },
      ];
      return cmds.filter(c => typeof getAction(c.action) === 'function');
    }

    function _quickOpenSessionItems() {
      return Object.keys(allSessions()).map(id => {
        const nameEl = getSession(id).tabEl?.querySelector('.tab-name');
        return { id, name: (nameEl?.textContent || id).trim() };
      });
    }

    function _fuzzyMatch(hay, needle) {
      if (!needle) return true;
      return hay.toLowerCase().includes(needle.toLowerCase());
    }

    function openQuickOpen() {
      const panel = openPanel({
        id: 'vt-qopen',
        ariaLabel: '빠른 열기',
        headHTML: `<div class="vt-vw-title">빠른 열기</div>`,
        extraHTML: `<input class="vt-vw-path" id="vt-qo-input" type="text" spellcheck="false"
          autocapitalize="off" autocomplete="off" placeholder="세션 · 최근 파일 · 명령 검색…">`,
        bodyId: 'vt-qo-body',
        bodyHTML: `<div class="vt-vw-loading">불러오는 중…</div>`,
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      const input = document.getElementById('vt-qo-input');
      const body = document.getElementById('vt-qo-body');

      // 최근 파일은 viewer.js의 로컬 저장소 목록을 그대로 가져온다 — 뷰어를
      // 실제로 연 적이 없으면(_loadRecent 미정의) 빈 배열로 조용히 넘어간다.
      const recentFiles = (typeof _loadRecent === 'function') ? _loadRecent() : [];

      function render(query) {
        const q = query.trim();
        const sessionItems = _quickOpenSessionItems().filter(s => _fuzzyMatch(s.name, q));
        const fileItems = recentFiles.filter(p => _fuzzyMatch(p, q));
        const cmdItems = _quickOpenCommands().filter(c => _fuzzyMatch(c.label, q));

        body.innerHTML = '';
        if (!sessionItems.length && !fileItems.length && !cmdItems.length) {
          const empty = document.createElement('div');
          empty.className = 'vt-vw-empty';
          empty.textContent = '일치하는 항목이 없습니다.';
          body.appendChild(empty);
          return;
        }

        const section = (title, items, renderRow) => {
          if (!items.length) return;
          const head = document.createElement('div');
          head.className = 'vt-qo-section-head';
          head.textContent = title;
          body.appendChild(head);
          items.forEach(item => body.appendChild(renderRow(item)));
        };

        section('세션', sessionItems, (s) => {
          const row = document.createElement('div');
          row.className = 'vt-vw-row vt-qo-row';
          row.textContent = s.name;
          row.addEventListener('click', () => { switchTo(s.id); closeQuickOpen(); });
          return row;
        });

        section('최근 파일', fileItems, (p) => {
          const row = document.createElement('div');
          // vt-vw-recent-row가 세로 배치(파일명 위 · 경로 아래)를 준다 — 기본
          // vt-vw-row는 가로 flex라 두 줄이 나란히 눌려버린다.
          row.className = 'vt-vw-row vt-vw-recent-row vt-qo-row';
          const name = document.createElement('div');
          name.className = 'vt-vw-name';
          name.textContent = p.split('/').pop() || p;
          const dir = document.createElement('div');
          dir.className = 'vt-vw-recent-dir';
          dir.textContent = p.split('/').slice(0, -1).join('/') || '.';
          row.appendChild(name);
          row.appendChild(dir);
          row.addEventListener('click', async () => {
            closeQuickOpen();
            await showViewer();
            if (typeof _selectFile === 'function') _selectFile(p, null);
          });
          return row;
        });

        section('명령', cmdItems, (c) => {
          const row = document.createElement('div');
          row.className = 'vt-vw-row vt-qo-row';
          const label = document.createElement('div');
          label.className = 'vt-vw-name';
          label.textContent = c.label;
          row.appendChild(label);
          if (c.hint) {
            const hint = document.createElement('div');
            hint.className = 'vt-qo-hint';
            hint.textContent = c.hint;
            row.appendChild(hint);
          }
          row.addEventListener('click', () => {
            closeQuickOpen();
            const fn = getAction(c.action);
            if (typeof fn === 'function') fn();
          });
          return row;
        });
      }

      input.addEventListener('input', () => render(input.value));
      input.addEventListener('keydown', (ev) => {
        // Enter는 목록의 첫 항목을 연다 — 소프트 키보드에서 화면 스크롤 없이 바로 실행.
        if (ev.key !== 'Enter') return;
        const first = body.querySelector('.vt-qo-row');
        if (first) first.click();
      });
      render('');
      // 패널이 DOM에 붙은 다음 포커스해야 소프트 키보드가 뜬다(레이아웃 전에 focus하면
      // iOS Safari가 무시하는 경우가 있다).
      requestAnimationFrame(() => input.focus());
    }

// F3(c): data-action 위임용 등록.
registerAction('quickopen.open', () => openQuickOpen());
