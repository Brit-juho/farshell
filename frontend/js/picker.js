    // --- 토스트 유틸리티 ---
    function updateSessionPicker() {
      const picker = document.getElementById('voice-session-picker');
      if (!picker) return;
      const s = activeId && sessions[activeId];
      picker.textContent = s?.tabEl?.querySelector('.tab-name')?.textContent || '세션';
      const sheet = document.getElementById('session-manager');
      if (sheet) renderSessionManager(sheet);
    }

    function sessionName(id, s) {
      return s?.tabEl?.querySelector('.tab-name')?.textContent || id.slice(0, 8);
    }

    function openSessionManager() {
      let backdrop = document.getElementById('session-manager');
      if (backdrop) { closeSessionManager(); return; }
      backdrop = document.createElement('div');
      backdrop.id = 'session-manager';
      backdrop.className = 'vt-session-backdrop';
      backdrop.setAttribute('role', 'presentation');
      backdrop.innerHTML = '<section class="vt-session-sheet" role="dialog" aria-modal="true" aria-labelledby="session-manager-title"></section>';
      backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeSessionManager(); });
      document.body.appendChild(backdrop);
      document.getElementById('voice-session-picker')?.setAttribute('aria-expanded', 'true');
      renderSessionManager(backdrop);
      const close = backdrop.querySelector('.vt-session-close');
      if (close) close.focus();
      backdrop._onKeydown = (e) => { if (e.key === 'Escape') closeSessionManager(); };
      document.addEventListener('keydown', backdrop._onKeydown);
    }

    function closeSessionManager() {
      const backdrop = document.getElementById('session-manager');
      if (!backdrop) return;
      if (backdrop._onKeydown) document.removeEventListener('keydown', backdrop._onKeydown);
      backdrop.remove();
      const picker = document.getElementById('voice-session-picker');
      if (picker) { picker.setAttribute('aria-expanded', 'false'); picker.focus(); }
    }

    function renderSessionManager(backdrop) {
      const sheet = backdrop.querySelector?.('.vt-session-sheet');
      if (!sheet) return;
      sheet.innerHTML = '<div class="vt-session-head"><h2 id="session-manager-title">세션 관리</h2><button class="vt-session-close" type="button" aria-label="세션 관리 닫기">×</button></div><div class="vt-session-list"></div>';
      sheet.querySelector('.vt-session-close').onclick = closeSessionManager;
      const list = sheet.querySelector('.vt-session-list');
      const entries = Object.entries(sessions);
      if (!entries.length) { list.innerHTML = '<p class="vt-session-empty">열려 있는 세션이 없습니다.</p>'; return; }
      for (const [id, s] of entries) {
        const row = document.createElement('div');
        row.className = 'vt-session-row' + (id === activeId ? ' active' : '');
        const select = document.createElement('button');
        select.type = 'button'; select.className = 'vt-session-select'; select.textContent = sessionName(id, s);
        select.setAttribute('aria-current', id === activeId ? 'true' : 'false');
        select.onclick = () => { switchTo(id); closeSessionManager(); };
        const rename = document.createElement('button');
        rename.type = 'button'; rename.className = 'vt-session-action'; rename.textContent = '✎'; rename.setAttribute('aria-label', `${sessionName(id, s)} 이름 변경`);
        rename.onclick = async () => {
          const next = window.prompt('새 세션 이름', sessionName(id, s));
          if (next === null) return;
          if (await renameSession(id, next)) renderSessionManager(backdrop);
        };
        const close = document.createElement('button');
        close.type = 'button'; close.className = 'vt-session-action'; close.textContent = '×'; close.setAttribute('aria-label', `${sessionName(id, s)} 닫기`);
        close.onclick = async () => { await removeSession(id); if (document.body.contains(backdrop)) renderSessionManager(backdrop); };
        row.append(select, rename, close); list.appendChild(row);
      }
    }

    function showToast(msg, type = 'info') {
      const cls = { info: 'info', error: 'err', success: 'ok' };
      const toast = document.createElement('div');
      toast.className = 'vt-toast ' + (cls[type] || 'info');
      toast.textContent = msg;
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 4000);
    }

    // --- 이 세션 맥에서 열기 (tmux 세션을 iTerm에 나중에 attach) ---
    async function openSessionOnMac() {
      if (!activeId || !sessions[activeId]) { showToast('열려 있는 세션이 없습니다', 'error'); return; }
      const s = sessions[activeId];
      const tmuxName = s.tmuxName || s.tmux_name;
      if (!tmuxName) { showToast('이 세션은 tmux 세션이 아니라 맥에서 열 수 없습니다', 'error'); return; }
      try {
        const res = await fetch(`${API_BASE}/api/tmux/open-on-mac`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: tmuxName }),
        });
        const data = await res.json();
        if (data.skipped) showToast(`'${tmuxName}'은 이미 맥에서 열려 있습니다`, 'success');
        else if (data.ok) showToast(`맥 iTerm에 '${tmuxName}' 열림`, 'success');
        else showToast('맥에서 열기 실패: ' + (data.error || ''), 'error');
      } catch (e) {
        showToast('맥에서 열기 실패: ' + e.message, 'error');
      }
    }

    // --- 파일 업로드 ---
    async function uploadFile(input) {
      const file = input.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append('file', file);
      const sid = activeId || '';
      try {
        const res = await fetch(`${API_BASE}/api/upload?session_id=${sid}`, { method: 'POST', body: formData });
        const data = await res.json();
        if (data.ok && data.path && activeId && sessions[activeId]) {
          // 화면에 찍기만 하면(term.write) 드래그 선택 말고는 경로를 집어낼 수 없다.
          // 이미지 붙여넣기(pasteImageUpload)와 동일하게 경로를 명령줄에 실제로 타이핑해
          // Claude 등에 그대로 넘길 수 있게 한다.
          sendToPty(activeId, data.path + ' ');
          showToast('업로드 완료 — 경로 삽입됨', 'success');
        }
      } catch (e) {
        showToast('업로드 실패: ' + e.message, 'error');
      }
      input.value = '';
    }
