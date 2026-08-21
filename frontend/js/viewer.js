// 코드 뷰어 / diff 패널 (읽기 전용) — P2.
// CLI만으로 원격 개발할 때 "코드를 눈으로 확인"이 안 되는 문제를 푼다.
// 편집 기능은 의도적으로 없다. 수정은 터미널/에이전트가 한다.
//
// 표시 모드 3종:
//   sheet — 폰/좁은 화면 기본. 트리 → 파일/diff 로 전체 화면을 전환한다.
//   dock  — 우측에 고정. 배경을 덮지 않아 터미널을 보면서 동시에 쓸 수 있다.
//   full  — 화면 대부분을 차지하는 모달. 좌측 트리 + 우측 코드 2단 분할.
// 트리는 계층형(누적 expand/collapse) — 펼친 디렉토리는 다시 요청하지 않고
// DOM에 남겨둔 채로 접었다 편다.
//
// grid.js 뒤에 로드되므로 activeId / fitAndResize 를,
// vtapi.js/panel.js 뒤에 로드되므로 vtFetch / vtEsc / openPanel / closePanel 을
// 그대로 참조한다(classic script 최상위 스코프 공유).

    const VT_MODE_KEY = 'vt_viewer_mode';
    const VT_DOCKW_KEY = 'vt_viewer_dockw';
    const VT_DOCK_W_DEFAULT = 420;
    const VT_DOCK_W_MIN = 280;

    const _ICON_CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 6 6 6-6 6"/></svg>';
    const _ICON_SHEET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="15" x2="21" y2="15"/></svg>';
    const _ICON_DOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="14" y1="3" x2="14" y2="21"/></svg>';
    const _ICON_FULL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>';

    let _viewerState = {
      root: null,             // 트리 최상단 디렉토리. 시작값은 서버 시작 루트(기본 ~/GitHub)지만
                                // ".." 로 위로 이동하면 바뀐다(서버 경계 — 기본 홈 — 까지 허용).
      cwd: null,               // diff 대상 기본값(루트)
      mode: 'tree',            // 콘텐츠: 'tree' | 'file' | 'diff' — sheet의 활성 화면 판정에 쓴다
      displayMode: 'sheet',    // 레이아웃: 'sheet' | 'dock' | 'full'
      selectedPath: null,
      expanded: new Set(),     // 펼쳐진 디렉토리 절대경로 — 접었다 다시 펼 때 재요청 판단에 씀
    };

    function _isMobile() { return window.matchMedia('(max-width:560px)').matches; }

    function _loadMode() {
      if (_isMobile()) return 'sheet';
      try {
        const m = localStorage.getItem(VT_MODE_KEY);
        return (m === 'dock' || m === 'full') ? m : 'sheet';
      } catch (_) { return 'sheet'; }
    }
    function _saveMode(m) { try { localStorage.setItem(VT_MODE_KEY, m); } catch (_) {} }

    function _loadDockW() {
      try {
        const w = parseInt(localStorage.getItem(VT_DOCKW_KEY), 10);
        return Number.isFinite(w) && w > 0 ? w : VT_DOCK_W_DEFAULT;
      } catch (_) { return VT_DOCK_W_DEFAULT; }
    }
    function _saveDockW(w) { try { localStorage.setItem(VT_DOCKW_KEY, String(w)); } catch (_) {} }
    function _clampDockW(w) {
      const maxW = Math.floor(window.innerWidth * 0.7);
      return Math.max(VT_DOCK_W_MIN, Math.min(w, maxW));
    }

    // 정적 안내 메시지용 — textContent 만 쓰므로 이스케이프가 필요 없다.
    // 줄바꿈은 <br> 엘리먼트로 표현한다(문자열 조립 없이).
    function _setMsg(container, className, lines) {
      container.innerHTML = '';
      const div = document.createElement('div');
      div.className = className;
      lines.forEach((line, i) => {
        if (i > 0) div.appendChild(document.createElement('br'));
        div.appendChild(document.createTextNode(line));
      });
      container.appendChild(div);
    }

    function closeViewer() { closePanel('vt-viewer'); }

    async function showViewer() {
      const displayMode = _loadMode();
      const panel = openPanel({
        id: 'vt-viewer',
        ariaLabel: '코드 뷰어',
        extraClass: displayMode === 'sheet' ? '' : 'mode-' + displayMode,
        headHTML: `
          <button class="vt-vw-back" aria-label="트리로" title="트리로">‹</button>
          <div class="vt-vw-title" id="vt-vw-title">코드 뷰어</div>
          <div class="vt-vw-modes" role="group" aria-label="표시 모드">
            <button class="vt-vw-mode-btn" data-mode="sheet" title="시트">${_ICON_SHEET}</button>
            <button class="vt-vw-mode-btn" data-mode="dock" title="도킹 — 터미널과 함께 보기">${_ICON_DOCK}</button>
            <button class="vt-vw-mode-btn" data-mode="full" title="전체화면">${_ICON_FULL}</button>
          </div>
          <button class="vt-vw-diff" id="vt-vw-diff" title="Git 변경사항 · stage · commit">git</button>
        `,
        extraHTML: `
          <div class="vt-vw-path" id="vt-vw-path"></div>
          <div class="vt-vw-resizer" id="vt-vw-resizer"></div>
        `,
        bodyId: 'vt-vw-body',
        bodyHTML: `
          <div class="vt-vw-tree-pane" id="vt-vw-tree"><div class="vt-vw-loading">불러오는 중…</div></div>
          <div class="vt-vw-code-pane" id="vt-vw-code-pane"><div class="vt-vw-code-empty">파일을 선택하세요.</div></div>
        `,
        onKey: () => {
          // 도킹 모드는 터미널과 동시에 보이는 상태 — Esc는 vim 등 터미널 쪽에 쓰이므로
          // 패널을 닫지 않는다. 시트/전체화면에서는 기본 동작(닫기) 그대로.
          if (_viewerState.displayMode === 'dock') return true;
        },
        onClose: () => {
          // X · 배경 클릭 · Esc · 재호출 토글 — 어느 경로로 닫히든 panel.js가 이걸 불러준다.
          const t = document.getElementById('viewer-toggle');
          if (t) t.classList.remove('active');
          document.body.classList.remove('vt-docked');
        },
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      const btn = document.getElementById('viewer-toggle');
      if (btn) btn.classList.add('active');

      document.getElementById('vt-vw-body').classList.add('split');
      _viewerState.mode = 'tree';
      _viewerState.selectedPath = null;
      _viewerState.expanded = new Set();
      _viewerState.displayMode = displayMode;
      _applyDisplayMode(displayMode, panel.el);

      panel.el.querySelector('.vt-vw-back').addEventListener('click', () => _setActivePane('tree'));
      panel.el.querySelectorAll('.vt-vw-mode-btn').forEach(b => {
        b.addEventListener('click', () => _setDisplayMode(b.dataset.mode));
      });
      panel.el.querySelector('#vt-vw-diff').addEventListener('click', () => showGit());
      _wireResizer(panel.el);

      const treeEl = document.getElementById('vt-vw-tree');
      try {
        const { roots } = await vtFetch('/api/fs/roots');
        if (!roots || !roots.length) {
          _setMsg(treeEl, 'vt-vw-empty', ['열람 가능한 루트가 없습니다.', 'VT_BROWSE_ROOTS 를 설정하세요.']);
          return;
        }
        _viewerState.root = roots[0];
        _viewerState.cwd = roots[0];
        _setPath(roots[0]);
        await _renderRootTree();
      } catch (e) {
        _setMsg(treeEl, 'vt-vw-empty', [e.message]);
      }
    }

    function _setPath(p) {
      const el = document.getElementById('vt-vw-path');
      if (el) el.textContent = p || '';
    }

    function _setTitle(t) {
      const el = document.getElementById('vt-vw-title');
      if (el) el.textContent = t;
    }

    function _setActivePane(which) {
      const body = document.getElementById('vt-vw-body');
      if (body) body.dataset.active = which;
      _viewerState.mode = which === 'tree' ? 'tree' : _viewerState.mode;
    }

    // --- 표시 모드 --------------------------------------------------------------

    function _applyDisplayMode(mode, el) {
      el = el || document.getElementById('vt-viewer');
      if (!el) return;
      el.classList.remove('mode-dock', 'mode-full');
      if (mode === 'dock' || mode === 'full') el.classList.add('mode-' + mode);
      el.querySelectorAll('.vt-vw-mode-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
      });

      const nowDocked = mode === 'dock';
      const wasDocked = document.body.classList.contains('vt-docked');
      if (nowDocked) {
        document.documentElement.style.setProperty('--vt-dock-w', _clampDockW(_loadDockW()) + 'px');
      }
      document.body.classList.toggle('vt-docked', nowDocked);

      // 도킹 진입/이탈만 터미널 폭을 바꾼다(margin-right). 그 전환(.18s)이 끝난 뒤
      // 딱 한 번만 fit() — terminal.js:683 경고와 같은 이유로 전환 도중엔 부르지 않는다.
      // sheet↔full은 터미널 크기에 영향이 없으므로 fit이 필요 없다.
      if (wasDocked !== nowDocked) {
        try { setTimeout(() => fitAndResize(activeId), 200); } catch (_) {}
      }
    }

    function _setDisplayMode(mode) {
      if (_isMobile()) mode = 'sheet';
      if (mode === _viewerState.displayMode) return;
      _viewerState.displayMode = mode;
      _saveMode(mode);
      _applyDisplayMode(mode);
    }

    // 도킹 폭 드래그 리사이저. 매 프레임 fit()을 부르면 xterm이 glyph 아틀라스를
    // 계속 갈아엎으므로(terminal.js:683 주석), 드래그 중엔 CSS 변수만 갱신하고
    // pointerup 시점에 딱 한 번만 fitAndResize 한다.
    function _wireResizer(el) {
      const handle = el.querySelector('#vt-vw-resizer');
      if (!handle) return;
      let startX = 0, startW = 0;

      const onMove = (ev) => {
        const delta = startX - ev.clientX;   // 우측 도킹 — 왼쪽으로 끌수록 넓어진다
        document.documentElement.style.setProperty('--vt-dock-w', _clampDockW(startW + delta) + 'px');
      };
      const onUp = () => {
        document.removeEventListener('pointermove', onMove);
        handle.classList.remove('dragging');
        document.body.classList.remove('vt-resizing');
        const w = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-dock-w'), 10);
        if (Number.isFinite(w)) _saveDockW(w);
        try { fitAndResize(activeId); } catch (_) {}
      };
      handle.addEventListener('pointerdown', (ev) => {
        if (_viewerState.displayMode !== 'dock') return;
        ev.preventDefault();
        startX = ev.clientX;
        startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-dock-w'), 10) || _loadDockW();
        handle.classList.add('dragging');
        document.body.classList.add('vt-resizing');
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp, { once: true });
      });
    }

    // --- 계층 트리 ----------------------------------------------------------------

    function _fmtSize(n) {
      if (n < 1024) return n + 'B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K';
      return (n / 1024 / 1024).toFixed(1) + 'M';
    }

    function _isDescendant(path, of) {
      return path !== of && path.startsWith(of.replace(/\/$/, '') + '/');
    }

    function _treeRowEl(entry, path, depth) {
      const row = document.createElement('div');
      row.className = 'vt-vw-trow' + (entry.dir ? ' dir' : '');
      row.style.setProperty('--d', depth);
      row.dataset.path = path;

      const chev = document.createElement('span');
      chev.className = 'vt-vw-chev';
      chev.innerHTML = _ICON_CHEVRON;

      const name = document.createElement('span');
      name.className = 'vt-vw-name';
      name.textContent = entry.name;                      // textContent — XSS 방어

      row.appendChild(chev);
      row.appendChild(name);

      if (!entry.dir) {
        const size = document.createElement('span');
        size.className = 'vt-vw-size';
        size.textContent = _fmtSize(entry.size);
        row.appendChild(size);
      }
      return row;
    }

    function _wireTreeRow(row, entry, path, depth) {
      row.addEventListener('click', () => {
        if (entry.dir) _toggleDir(row, path, depth);
        else _selectFile(path, row);
      });
    }

    async function _renderRootTree() {
      const treeEl = document.getElementById('vt-vw-tree');
      let data;
      try {
        data = await vtFetch(`/api/fs/tree?path=${encodeURIComponent(_viewerState.root)}`);
      } catch (e) {
        _setMsg(treeEl, 'vt-vw-empty', [e.message]);
        return;
      }
      _renderTopLevel(treeEl, data);
    }

    // 상위 이동(".." 행) — 서버(fsguard)가 정한 경계까지만 실제로 올라간다.
    // 경계는 프론트가 미리 알지 못하므로 항상 시도해보고, 막히면(403) 토스트만 띄운다.
    function _upRowEl() {
      const row = document.createElement('div');
      row.className = 'vt-vw-trow vt-vw-up';
      row.style.setProperty('--d', 0);
      const chev = document.createElement('span');
      chev.className = 'vt-vw-chev';
      chev.innerHTML = _ICON_CHEVRON;
      const name = document.createElement('span');
      name.className = 'vt-vw-name';
      name.textContent = '..';
      row.appendChild(chev);
      row.appendChild(name);
      row.addEventListener('click', _goUpRoot);
      return row;
    }

    async function _goUpRoot() {
      const cur = _viewerState.root;
      if (!cur || cur === '/') return;
      const parent = cur.replace(/\/[^/]+\/?$/, '') || '/';
      let data;
      try {
        data = await vtFetch(`/api/fs/tree?path=${encodeURIComponent(parent)}`);
      } catch (e) {
        showToast(`더 위로는 이동할 수 없습니다: ${e.message}`);
        return;
      }
      _viewerState.root = parent;
      _viewerState.expanded = new Set();
      _setPath(parent);
      _renderTopLevel(document.getElementById('vt-vw-tree'), data);
    }

    // 트리 최상단(현재 root의 자식들) 렌더링 — 초기 로드와 "위로 이동" 양쪽에서 쓴다.
    function _renderTopLevel(treeEl, data) {
      treeEl.innerHTML = '';
      const frag = document.createDocumentFragment();
      if (_viewerState.root !== '/') frag.appendChild(_upRowEl());

      if (!data.entries.length) {
        treeEl.appendChild(frag);
        const empty = document.createElement('div');
        empty.className = 'vt-vw-empty';
        empty.textContent = '빈 디렉토리';
        treeEl.appendChild(empty);
        return;
      }
      data.entries.forEach(entry => {
        const childPath = _viewerState.root.replace(/\/$/, '') + '/' + entry.name;
        const row = _treeRowEl(entry, childPath, 0);
        _wireTreeRow(row, entry, childPath, 0);
        frag.appendChild(row);
      });
      if (data.truncated) {
        const note = document.createElement('div');
        note.className = 'vt-vw-note warn';
        note.style.setProperty('--d', 0);
        note.textContent = `항목이 많아 일부만 표시했습니다 (최대 ${data.entries.length}개)`;
        frag.appendChild(note);
      }
      treeEl.appendChild(frag);
    }

    async function _toggleDir(row, path, depth) {
      if (_viewerState.expanded.has(path)) { _collapseDir(row, path); return; }

      row.classList.add('open');
      _viewerState.expanded.add(path);
      _viewerState.cwd = path;
      const chev = row.querySelector('.vt-vw-chev');
      const prevIcon = chev.innerHTML;
      chev.innerHTML = '';
      const spin = document.createElement('span');
      spin.className = 'vt-vw-trow-spin';
      chev.appendChild(spin);

      let data;
      try {
        data = await vtFetch(`/api/fs/tree?path=${encodeURIComponent(path)}`);
      } catch (e) {
        chev.innerHTML = prevIcon;
        row.classList.remove('open');
        _viewerState.expanded.delete(path);
        showToast(`목록을 불러오지 못했습니다: ${e.message}`);
        return;
      }
      chev.innerHTML = prevIcon;   // 펼쳐진 화살표 방향은 .open의 CSS 회전이 담당한다

      const frag = document.createDocumentFragment();
      data.entries.forEach(entry => {
        const childPath = path.replace(/\/$/, '') + '/' + entry.name;
        const childRow = _treeRowEl(entry, childPath, depth + 1);
        _wireTreeRow(childRow, entry, childPath, depth + 1);
        frag.appendChild(childRow);
      });
      if (data.truncated) {
        const note = document.createElement('div');
        note.className = 'vt-vw-note warn';
        note.style.setProperty('--d', depth + 1);
        note.textContent = `일부만 표시했습니다 (최대 ${data.entries.length}개)`;
        frag.appendChild(note);
      }
      if (!data.entries.length && !data.truncated) {
        const empty = document.createElement('div');
        empty.className = 'vt-vw-empty';
        empty.style.setProperty('--d', depth + 1);
        empty.textContent = '빈 디렉토리';
        frag.appendChild(empty);
      }
      row.after(frag);
    }

    function _collapseDir(row, path) {
      row.classList.remove('open');
      _viewerState.expanded.delete(path);
      // 이 행 바로 다음부터, path 하위였던 행(과 그 사이의 안내문)을 전부 제거한다.
      let next = row.nextElementSibling;
      while (next && (!next.dataset.path || _isDescendant(next.dataset.path, path))) {
        const rm = next;
        next = next.nextElementSibling;
        rm.remove();
      }
      for (const p of Array.from(_viewerState.expanded)) {
        if (_isDescendant(p, path)) _viewerState.expanded.delete(p);
      }
    }

    function _selectFile(path, row) {
      document.querySelectorAll('.vt-vw-trow.active').forEach(r => r.classList.remove('active'));
      if (row) row.classList.add('active');
      openFile(path);
      if (_viewerState.displayMode === 'sheet') _setActivePane('code');
    }

    // --- 파일 ----------------------------------------------------------------

    // 하이라이팅. 실패하면 반드시 이스케이프된 원문으로 폴백한다 —
    // 여기서 예외가 새면 뷰어 전체가 빈 화면이 된다.
    function _hl(text, lang) {
      if (!lang || !window.hljs) return vtEsc(text);
      try {
        return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
      } catch (_) {
        return vtEsc(text);
      }
    }

    function _renderFileDOM(container, content, lang) {
      const lines = VTDiffLex.normalize(content).split('\n');
      const wrap = document.createElement('div');
      wrap.className = 'vt-vw-code';
      lines.forEach((ln, i) => {
        const row = document.createElement('div');
        row.className = 'vt-vw-cl';
        const no = document.createElement('span');
        no.className = 'vt-vw-no';
        no.textContent = i + 1;
        const tx = document.createElement('span');
        tx.className = 'vt-vw-tx';
        tx.innerHTML = _hl(ln, lang);   // hljs.highlight()/vtEsc 결과만 innerHTML 예외
        row.appendChild(no);
        row.appendChild(tx);
        wrap.appendChild(row);
      });
      container.appendChild(wrap);
    }

    async function openFile(path) {
      _viewerState.mode = 'file';
      _viewerState.selectedPath = path;
      _viewerState.cwd = path.replace(/\/[^/]+$/, '') || _viewerState.root;
      _setPath(path);
      _setTitle(path.split('/').pop());
      const pane = document.getElementById('vt-vw-code-pane');
      pane.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
      let d;
      try {
        d = await vtFetch(`/api/fs/file?path=${encodeURIComponent(path)}`);
      } catch (e) {
        _setMsg(pane, 'vt-vw-empty', [e.message]);
        return;
      }
      if (d.binary) {
        _setMsg(pane, 'vt-vw-empty', [`바이너리 파일 (${_fmtSize(d.size)})`, '미리보기를 지원하지 않습니다.']);
        return;
      }
      pane.innerHTML = '';
      const lang = window.VTDiffLex ? VTDiffLex.langForPath(path) : null;
      _renderFileDOM(pane, d.content, lang);
      if (d.truncated) {
        const note = document.createElement('div');
        note.className = 'vt-vw-note warn';
        note.textContent = `파일이 커서 앞부분만 표시했습니다 (전체 ${_fmtSize(d.size)})`;
        pane.appendChild(note);
      }
    }

    // --- diff ----------------------------------------------------------------

    function _renderDiffDOM(container, diffText) {
      const files = VTDiffLex.parse(diffText);
      files.forEach(f => {
        const st = VTDiffLex.stats(f);
        const lang = VTDiffLex.langForPath(f.newPath || f.oldPath);

        const fileEl = document.createElement('div');
        fileEl.className = 'vt-vw-dfile';

        const head = document.createElement('div');
        head.className = 'vt-vw-dhead';
        const pathEl = document.createElement('span');
        pathEl.className = 'vt-vw-dpath';
        pathEl.textContent = f.newPath || f.oldPath;
        const statEl = document.createElement('span');
        statEl.className = 'vt-vw-dstat';
        const addB = document.createElement('b'); addB.className = 'add'; addB.textContent = `+${st.add}`;
        const delB = document.createElement('b'); delB.className = 'del'; delB.textContent = `-${st.del}`;
        statEl.appendChild(addB);
        statEl.appendChild(document.createTextNode(' '));
        statEl.appendChild(delB);
        head.appendChild(pathEl);
        head.appendChild(statEl);
        fileEl.appendChild(head);

        if (f.binary) {
          const note = document.createElement('div');
          note.className = 'vt-vw-note';
          note.textContent = '바이너리 파일';
          fileEl.appendChild(note);
        } else {
          f.hunks.forEach(h => {
            const hunk = document.createElement('div');
            hunk.className = 'vt-vw-hunk';
            hunk.textContent = h.header;                    // textContent — XSS 방어
            fileEl.appendChild(hunk);

            h.lines.forEach(l => {
              const cls = l.type === 'add' ? 'add' : l.type === 'del' ? 'del' : l.type === 'meta' ? 'meta' : '';
              const row = document.createElement('div');
              row.className = 'vt-vw-dl' + (cls ? ' ' + cls : '');

              const oldNo = document.createElement('span');
              oldNo.className = 'vt-vw-no';
              oldNo.textContent = l.oldNo == null ? '' : l.oldNo;
              const newNo = document.createElement('span');
              newNo.className = 'vt-vw-no';
              newNo.textContent = l.newNo == null ? '' : l.newNo;
              const sign = document.createElement('span');
              sign.className = 'vt-vw-sign';
              sign.textContent = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' ';
              const tx = document.createElement('span');
              tx.className = 'vt-vw-tx';
              if (l.type === 'meta') tx.textContent = l.text;
              else tx.innerHTML = _hl(l.text, lang);         // hljs 결과만 innerHTML 예외

              row.appendChild(oldNo);
              row.appendChild(newNo);
              row.appendChild(sign);
              row.appendChild(tx);
              fileEl.appendChild(row);
            });
          });
        }
        container.appendChild(fileEl);
      });
    }

    // 파일 하나의 diff. status 목록에서 특정 파일을 눌렀을 때 쓴다.
    async function _showFileDiff(repo, file, staged) {
      _viewerState.mode = 'diff';
      _setTitle(file.split('/').pop());
      _setPath(file);
      const pane = document.getElementById('vt-vw-code-pane');
      pane.innerHTML = '<div class="vt-vw-loading">git diff 실행 중…</div>';
      if (_viewerState.displayMode === 'sheet') _setActivePane('code');

      let d;
      try {
        const q = `repo=${encodeURIComponent(repo)}&file=${encodeURIComponent(file)}&staged=${staged ? 'true' : 'false'}`;
        d = await vtFetch(`/api/git/diff?${q}`);
      } catch (e) {
        _setMsg(pane, 'vt-vw-empty', [e.message]);
        return;
      }
      if (!d.diff || !d.diff.trim()) { _setMsg(pane, 'vt-vw-empty', ['변경된 내용이 없습니다.']); return; }

      pane.innerHTML = '';
      _renderDiffDOM(pane, d.diff);
      if (d.truncated) {
        const note = document.createElement('div');
        note.className = 'vt-vw-note warn';
        note.textContent = 'diff가 커서 일부만 표시했습니다.';
        pane.appendChild(note);
      }
    }

    // --- git status / stage / commit (D16) ---------------------------------------
    //
    // 코드 뷰어의 유일한 쓰기 경로. push·브랜치 조작은 절대 추가하지 않는다.
    // 스코프를 stage/unstage/commit 으로만 좁게 유지한다 — TODOS.md D16 참고.

    function _gitFileLabel(entry) {
      if (entry.index_status === '?' || entry.worktree_status === '?') return '추가되지 않음';
      const code = entry.index_status || entry.worktree_status;
      return { M: '수정됨', A: '추가됨', D: '삭제됨', R: '이름변경', C: '복사됨', U: '충돌' }[code] || code;
    }

    async function _gitAction(repo, path, files) {
      return vtFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo, files }),
      });
    }

    function _gitRowEl(repo, entry, staged) {
      const row = document.createElement('div');
      row.className = 'vt-vw-grow';

      const btn = document.createElement('button');
      btn.className = 'vt-vw-gact';
      btn.textContent = staged ? '－' : '＋';
      btn.title = staged ? '스테이지 해제' : '스테이지';
      btn.setAttribute('aria-label', btn.title);
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        btn.disabled = true;
        try {
          await _gitAction(repo, staged ? '/api/git/unstage' : '/api/git/stage', [entry.file]);
          await showGit(repo);
        } catch (e) {
          showToast(`${btn.title} 실패: ${e.message}`);
          btn.disabled = false;
        }
      });

      const badge = document.createElement('span');
      badge.className = 'vt-vw-gstat';
      badge.textContent = entry.index_status === '?' ? '??' : (staged ? entry.index_status : entry.worktree_status) || '';

      const name = document.createElement('span');
      name.className = 'vt-vw-name';
      name.textContent = entry.orig_file ? `${entry.orig_file} → ${entry.file}` : entry.file;
      name.title = _gitFileLabel(entry);

      row.appendChild(btn);
      row.appendChild(badge);
      row.appendChild(name);
      row.addEventListener('click', () => _showFileDiff(repo, entry.file, staged));
      return row;
    }

    function _gitSectionEl(title, entries, repo, staged) {
      const sec = document.createElement('div');
      sec.className = 'vt-vw-gsec';
      const head = document.createElement('div');
      head.className = 'vt-vw-ghead';
      head.textContent = `${title} (${entries.length})`;
      sec.appendChild(head);
      entries.forEach(e => sec.appendChild(_gitRowEl(repo, e, staged)));
      return sec;
    }

    async function _doCommit(repo, pane) {
      const ta = pane.querySelector('#vt-vw-commit-msg');
      const btn = pane.querySelector('#vt-vw-commit-btn');
      const message = (ta.value || '').trim();
      if (!message) return;
      btn.disabled = true;
      try {
        await vtFetch('/api/git/commit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ repo, message }),
        });
        showToast('커밋했습니다.');
        await showGit(repo);
      } catch (e) {
        showToast(`커밋 실패: ${e.message}`);
        btn.disabled = false;
      }
    }

    async function showGit(repo) {
      const target = repo || _viewerState.cwd || _viewerState.root;
      if (!target) return;
      _viewerState.mode = 'diff';
      _viewerState.cwd = target;
      _setTitle('Git');
      _setPath(target);
      const pane = document.getElementById('vt-vw-code-pane');
      pane.innerHTML = '<div class="vt-vw-loading">git status 확인 중…</div>';
      if (_viewerState.displayMode === 'sheet') _setActivePane('code');

      let d;
      try {
        d = await vtFetch(`/api/git/status?repo=${encodeURIComponent(target)}`);
      } catch (e) {
        _setMsg(pane, 'vt-vw-empty', [e.message]);
        return;
      }
      if (!d.repo) { _setMsg(pane, 'vt-vw-empty', ['git 저장소가 아닙니다.']); return; }

      // 미추적 파일("??")은 index_status/worktree_status 둘 다 '?'로 채워지는데,
      // 실제 인덱스에는 없으므로 스테이지됨으로 분류하면 안 된다.
      const staged = d.files.filter(f => f.index_status && f.status !== '??');
      const unstaged = d.files.filter(f => f.status === '??' || (!f.index_status && f.worktree_status));

      pane.innerHTML = '';
      const wrap = document.createElement('div');
      wrap.className = 'vt-vw-git';

      if (!d.files.length) {
        const empty = document.createElement('div');
        empty.className = 'vt-vw-empty';
        empty.textContent = '변경된 내용이 없습니다.';
        wrap.appendChild(empty);
      } else {
        if (staged.length) wrap.appendChild(_gitSectionEl('스테이지됨', staged, target, true));
        if (unstaged.length) wrap.appendChild(_gitSectionEl('변경사항', unstaged, target, false));
      }

      const commitBox = document.createElement('div');
      commitBox.className = 'vt-vw-gcommit';
      commitBox.innerHTML = `
        <textarea id="vt-vw-commit-msg" class="vt-vw-gmsg" placeholder="커밋 메시지" rows="2"
          ${staged.length ? '' : 'disabled'}></textarea>
        <button id="vt-vw-commit-btn" class="vt-vw-gcommit-btn" ${staged.length ? '' : 'disabled'}>커밋</button>
      `;
      wrap.appendChild(commitBox);

      const logSec = document.createElement('div');
      logSec.className = 'vt-vw-glog';
      wrap.appendChild(logSec);

      pane.appendChild(wrap);

      pane.querySelector('#vt-vw-commit-btn').addEventListener('click', () => _doCommit(target, pane));
      _renderCommitLog(target, logSec, 0);
    }

    // --- git log / show (커밋 기록 · 커밋 간 diff, 읽기 전용) -----------------------

    function _commitRowEl(repo, c) {
      const row = document.createElement('div');
      row.className = 'vt-vw-grow vt-vw-crow';
      const sha = document.createElement('span');
      sha.className = 'vt-vw-gstat';
      sha.textContent = c.short;
      const name = document.createElement('span');
      name.className = 'vt-vw-name';
      name.textContent = c.subject;
      name.title = `${c.author} · ${c.date}`;
      row.appendChild(sha);
      row.appendChild(name);
      row.addEventListener('click', () => _showCommit(repo, c.hash));
      return row;
    }

    // skip=0이면 헤더부터 새로 그린다. "더 보기"는 같은 container에 이어 붙인다.
    async function _renderCommitLog(repo, container, skip) {
      if (skip === 0) {
        container.innerHTML = '';
        const head = document.createElement('div');
        head.className = 'vt-vw-ghead';
        head.textContent = '커밋 기록';
        container.appendChild(head);
      }
      const more = container.querySelector('.vt-vw-glog-more');
      if (more) more.remove();

      let d;
      try {
        d = await vtFetch(`/api/git/log?repo=${encodeURIComponent(repo)}&skip=${skip}&limit=20`);
      } catch (e) {
        const err = document.createElement('div');
        err.className = 'vt-vw-empty';
        err.textContent = e.message;
        container.appendChild(err);
        return;
      }
      if (!d.commits.length) {
        if (skip === 0) {
          const empty = document.createElement('div');
          empty.className = 'vt-vw-empty';
          empty.textContent = '커밋이 없습니다.';
          container.appendChild(empty);
        }
        return;
      }
      d.commits.forEach(c => container.appendChild(_commitRowEl(repo, c)));
      if (d.has_more) {
        const btn = document.createElement('button');
        btn.className = 'vt-pt-btn vt-vw-glog-more';
        btn.textContent = '더 보기';
        btn.addEventListener('click', () => _renderCommitLog(repo, container, skip + d.commits.length));
        container.appendChild(btn);
      }
    }

    async function _showCommit(repo, sha) {
      _viewerState.mode = 'diff';
      _setTitle(sha.slice(0, 7));
      _setPath(repo);
      const pane = document.getElementById('vt-vw-code-pane');
      pane.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
      if (_viewerState.displayMode === 'sheet') _setActivePane('code');

      let d;
      try {
        d = await vtFetch(`/api/git/show?repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}`);
      } catch (e) {
        _setMsg(pane, 'vt-vw-empty', [e.message]);
        return;
      }

      pane.innerHTML = '';
      const back = document.createElement('button');
      back.className = 'vt-pt-btn vt-vw-cback';
      back.textContent = '‹ 상태로';
      back.addEventListener('click', () => showGit(repo));
      pane.appendChild(back);

      const wrap = document.createElement('div');
      wrap.className = 'vt-vw-git';

      const meta = document.createElement('div');
      meta.className = 'vt-vw-cmeta';
      const subj = document.createElement('div');
      subj.className = 'vt-vw-cmeta-subject';
      subj.textContent = d.commit.subject;
      meta.appendChild(subj);
      if (d.commit.body) {
        const body = document.createElement('div');
        body.className = 'vt-vw-cmeta-body';
        body.textContent = d.commit.body;
        meta.appendChild(body);
      }
      const info = document.createElement('div');
      info.className = 'vt-vw-cmeta-info';
      info.textContent = `${d.commit.short} · ${d.commit.author} · ${d.commit.date}`;
      meta.appendChild(info);
      wrap.appendChild(meta);

      const sec = document.createElement('div');
      sec.className = 'vt-vw-gsec';
      const head = document.createElement('div');
      head.className = 'vt-vw-ghead';
      head.textContent = `변경된 파일 (${d.files.length})`;
      sec.appendChild(head);
      d.files.forEach(f => {
        const row = document.createElement('div');
        row.className = 'vt-vw-grow';
        const badge = document.createElement('span');
        badge.className = 'vt-vw-gstat';
        badge.textContent = f.status;
        const name = document.createElement('span');
        name.className = 'vt-vw-name';
        name.textContent = f.orig_file ? `${f.orig_file} → ${f.file}` : f.file;
        row.appendChild(badge);
        row.appendChild(name);
        row.addEventListener('click', () => _showCommitFileDiff(repo, sha, f.file));
        sec.appendChild(row);
      });
      wrap.appendChild(sec);
      pane.appendChild(wrap);
    }

    async function _showCommitFileDiff(repo, sha, file) {
      _viewerState.mode = 'diff';
      _setTitle(file.split('/').pop());
      _setPath(file);
      const pane = document.getElementById('vt-vw-code-pane');
      pane.innerHTML = '<div class="vt-vw-loading">git show 실행 중…</div>';
      if (_viewerState.displayMode === 'sheet') _setActivePane('code');

      let d;
      try {
        const q = `repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}&file=${encodeURIComponent(file)}`;
        d = await vtFetch(`/api/git/show?${q}`);
      } catch (e) {
        _setMsg(pane, 'vt-vw-empty', [e.message]);
        return;
      }

      pane.innerHTML = '';
      const back = document.createElement('button');
      back.className = 'vt-pt-btn vt-vw-cback';
      back.textContent = '‹ 커밋으로';
      back.addEventListener('click', () => _showCommit(repo, sha));
      pane.appendChild(back);

      if (!d.diff || !d.diff.trim()) {
        const empty = document.createElement('div');
        empty.className = 'vt-vw-empty';
        empty.textContent = '변경된 내용이 없습니다.';
        pane.appendChild(empty);
        return;
      }
      _renderDiffDOM(pane, d.diff);
      if (d.truncated) {
        const note = document.createElement('div');
        note.className = 'vt-vw-note warn';
        note.textContent = 'diff가 커서 일부만 표시했습니다.';
        pane.appendChild(note);
      }
    }
