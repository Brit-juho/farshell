// 코드 뷰어 / diff 패널 (읽기 전용) — P2.
// CLI만으로 원격 개발할 때 "코드를 눈으로 확인"이 안 되는 문제를 푼다.
// 편집 기능은 의도적으로 없다. 수정은 터미널/에이전트가 한다.
//
// grid.js 뒤에 로드되므로 API_BASE / _tokenQuery / showToast / activeId /
// fitAndResize 를 그대로 참조한다(classic script 최상위 스코프 공유).

    let _viewerState = {
      root: null,        // 현재 탐색 중인 루트
      cwd: null,         // 현재 디렉토리
      mode: 'tree',      // 'tree' | 'file' | 'diff'
      repo: null,        // git 저장소 루트 (diff 대상)
    };

    // 서버가 준 문자열은 절대 innerHTML 로 넣지 않는다(grid.js:146-147과 같은 규칙).
    // 하이라이팅된 코드만 예외 — hljs.highlight()가 입력을 이스케이프해서 돌려준다.
    function _esc(s) {
      const d = document.createElement('div');
      d.textContent = s == null ? '' : String(s);
      return d.innerHTML;
    }

    async function _api(path) {
      const res = await fetch(`${API_BASE}${path}${path.includes('?') ? '&' : '?'}${_tokenQuery.replace(/^[?&]/, '')}`);
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      if (!res.ok) {
        const reason = (data && (data.reason || data.error)) || `HTTP ${res.status}`;
        throw new Error(reason);
      }
      return data;
    }

    function _viewerEl() { return document.getElementById('vt-viewer'); }

    function closeViewer() {
      const el = _viewerEl();
      if (!el) return;
      el.remove();
      document.removeEventListener('keydown', _viewerKey);
      // 패널이 레이아웃을 건드렸을 수 있으므로 터미널 크기를 다시 맞춘다.
      try { setTimeout(() => fitAndResize(activeId), 60); } catch (_) {}
    }

    function _viewerKey(ev) {
      if (ev.key === 'Escape') { ev.stopPropagation(); closeViewer(); }
    }

    async function showViewer() {
      // 토글 — 이미 열려 있으면 닫는다 (showGuide 패턴)
      if (_viewerEl()) { closeViewer(); return; }

      const el = document.createElement('div');
      el.id = 'vt-viewer';
      el.className = 'vt-viewer-backdrop';
      el.innerHTML = `
        <div class="vt-viewer-card" role="dialog" aria-modal="true" aria-label="코드 뷰어">
          <div class="vt-viewer-head">
            <button class="vt-vw-back" aria-label="상위로" title="상위 디렉토리">‹</button>
            <div class="vt-vw-title" id="vt-vw-title">코드 뷰어</div>
            <button class="vt-vw-diff" id="vt-vw-diff" title="변경분 보기">diff</button>
            <button class="vt-vw-x" aria-label="닫기">✕</button>
          </div>
          <div class="vt-vw-path" id="vt-vw-path"></div>
          <div class="vt-vw-body" id="vt-vw-body">
            <div class="vt-vw-loading">불러오는 중…</div>
          </div>
        </div>
      `;
      // 닫기 3중: X · 배경 클릭 · Esc (showGuide와 동일)
      el.querySelector('.vt-vw-x').addEventListener('click', closeViewer);
      el.addEventListener('click', (ev) => { if (ev.target === el) closeViewer(); });
      document.addEventListener('keydown', _viewerKey);
      el.querySelector('.vt-vw-back').addEventListener('click', goUp);
      el.querySelector('#vt-vw-diff').addEventListener('click', () => showDiff());
      document.body.appendChild(el);

      try {
        const { roots } = await _api('/api/fs/roots');
        if (!roots || !roots.length) {
          _body(`<div class="vt-vw-empty">열람 가능한 루트가 없습니다.<br>VT_BROWSE_ROOTS 를 설정하세요.</div>`);
          return;
        }
        _viewerState.root = roots[0];
        await openDir(roots[0]);
      } catch (e) {
        _body(`<div class="vt-vw-empty">${_esc(e.message)}</div>`);
      }
    }

    function _body(html) {
      const b = document.getElementById('vt-vw-body');
      if (b) b.innerHTML = html;
    }

    function _setPath(p) {
      const el = document.getElementById('vt-vw-path');
      if (el) el.textContent = p || '';
    }

    function _setTitle(t) {
      const el = document.getElementById('vt-vw-title');
      if (el) el.textContent = t;
    }

    // --- 디렉토리 ------------------------------------------------------------

    async function openDir(path) {
      _viewerState.mode = 'tree';
      _viewerState.cwd = path;
      _setPath(path);
      _setTitle('코드 뷰어');
      _body('<div class="vt-vw-loading">불러오는 중…</div>');
      let data;
      try {
        data = await _api(`/api/fs/tree?path=${encodeURIComponent(path)}`);
      } catch (e) {
        _body(`<div class="vt-vw-empty">${_esc(e.message)}</div>`);
        return;
      }
      const list = document.createElement('div');
      list.className = 'vt-vw-list';
      if (!data.entries.length) {
        _body('<div class="vt-vw-empty">빈 디렉토리</div>');
        return;
      }
      data.entries.forEach(e => {
        const row = document.createElement('div');
        row.className = 'vt-vw-row' + (e.dir ? ' dir' : '');
        const icon = document.createElement('span');
        icon.className = 'vt-vw-icon';
        icon.textContent = e.dir ? '▸' : '·';
        const name = document.createElement('span');
        name.className = 'vt-vw-name';
        name.textContent = e.name;                       // textContent — XSS 방어
        const size = document.createElement('span');
        size.className = 'vt-vw-size';
        size.textContent = e.dir ? '' : _fmtSize(e.size);
        row.appendChild(icon); row.appendChild(name); row.appendChild(size);
        const full = path.replace(/\/$/, '') + '/' + e.name;
        row.onclick = () => (e.dir ? openDir(full) : openFile(full));
        list.appendChild(row);
      });
      _body('');
      document.getElementById('vt-vw-body').appendChild(list);
      if (data.truncated) {
        const n = document.createElement('div');
        n.className = 'vt-vw-note';
        n.textContent = `항목이 많아 일부만 표시했습니다 (최대 ${data.entries.length}개)`;
        document.getElementById('vt-vw-body').appendChild(n);
      }
    }

    function goUp() {
      if (_viewerState.mode !== 'tree') { openDir(_viewerState.cwd); return; }
      const cwd = _viewerState.cwd || '';
      const root = _viewerState.root || '';
      if (!cwd || cwd === root) return;                  // 루트 위로는 못 간다
      const parent = cwd.replace(/\/[^/]+\/?$/, '') || root;
      openDir(parent.length < root.length ? root : parent);
    }

    function _fmtSize(n) {
      if (n < 1024) return n + 'B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K';
      return (n / 1024 / 1024).toFixed(1) + 'M';
    }

    // --- 파일 ----------------------------------------------------------------

    async function openFile(path) {
      _viewerState.mode = 'file';
      _setPath(path);
      _setTitle(path.split('/').pop());
      _body('<div class="vt-vw-loading">불러오는 중…</div>');
      let d;
      try {
        d = await _api(`/api/fs/file?path=${encodeURIComponent(path)}`);
      } catch (e) {
        _body(`<div class="vt-vw-empty">${_esc(e.message)}</div>`);
        return;
      }
      if (d.binary) {
        _body(`<div class="vt-vw-empty">바이너리 파일 (${_fmtSize(d.size)})<br>미리보기를 지원하지 않습니다.</div>`);
        return;
      }
      const lang = window.VTDiffLex ? VTDiffLex.langForPath(path) : null;
      const lines = VTDiffLex.normalize(d.content).split('\n');
      let out = '<div class="vt-vw-code">';
      lines.forEach((ln, i) => {
        out += `<div class="vt-vw-cl"><span class="vt-vw-no">${i + 1}</span><span class="vt-vw-tx">${_hl(ln, lang)}</span></div>`;
      });
      out += '</div>';
      if (d.truncated) {
        out += `<div class="vt-vw-note">파일이 커서 앞부분만 표시했습니다 (전체 ${_fmtSize(d.size)})</div>`;
      }
      _body(out);
    }

    // 하이라이팅. 실패하면 반드시 이스케이프된 원문으로 폴백한다 —
    // 여기서 예외가 새면 뷰어 전체가 빈 화면이 된다.
    function _hl(text, lang) {
      if (!lang || !window.hljs) return _esc(text);
      try {
        return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
      } catch (_) {
        return _esc(text);
      }
    }

    // --- diff ----------------------------------------------------------------

    async function showDiff(repo) {
      const target = repo || _viewerState.cwd || _viewerState.root;
      if (!target) return;
      _viewerState.mode = 'diff';
      _setTitle('변경분');
      _setPath(target);
      _body('<div class="vt-vw-loading">git diff 실행 중…</div>');
      let d;
      try {
        d = await _api(`/api/git/diff?repo=${encodeURIComponent(target)}`);
      } catch (e) {
        _body(`<div class="vt-vw-empty">${_esc(e.message)}</div>`);
        return;
      }
      if (!d.repo) {
        _body('<div class="vt-vw-empty">git 저장소가 아닙니다.</div>');
        return;
      }
      if (!d.diff || !d.diff.trim()) {
        _body('<div class="vt-vw-empty">변경된 내용이 없습니다.</div>');
        return;
      }
      const files = VTDiffLex.parse(d.diff);
      let out = '';
      files.forEach(f => {
        const st = VTDiffLex.stats(f);
        const lang = VTDiffLex.langForPath(f.newPath || f.oldPath);
        out += `<div class="vt-vw-dfile">
          <div class="vt-vw-dhead">
            <span class="vt-vw-dpath">${_esc(f.newPath || f.oldPath)}</span>
            <span class="vt-vw-dstat"><b class="add">+${st.add}</b> <b class="del">-${st.del}</b></span>
          </div>`;
        if (f.binary) {
          out += '<div class="vt-vw-note">바이너리 파일</div>';
        } else {
          f.hunks.forEach(h => {
            out += `<div class="vt-vw-hunk">${_esc(h.header)}</div>`;
            h.lines.forEach(l => {
              const cls = l.type === 'add' ? 'add' : l.type === 'del' ? 'del' : l.type === 'meta' ? 'meta' : '';
              const oldNo = l.oldNo == null ? '' : l.oldNo;
              const newNo = l.newNo == null ? '' : l.newNo;
              const sign = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' ';
              out += `<div class="vt-vw-dl ${cls}">`
                + `<span class="vt-vw-no">${oldNo}</span><span class="vt-vw-no">${newNo}</span>`
                + `<span class="vt-vw-sign">${sign}</span>`
                + `<span class="vt-vw-tx">${l.type === 'meta' ? _esc(l.text) : _hl(l.text, lang)}</span>`
                + `</div>`;
            });
          });
        }
        out += '</div>';
      });
      if (d.truncated) {
        out += '<div class="vt-vw-note">diff가 커서 일부만 표시했습니다.</div>';
      }
      _body(out);
    }
