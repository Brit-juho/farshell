    // --- 토스트 유틸리티 ---
    function updateSessionPicker() {
      const picker = document.getElementById('voice-session-picker');
      if (!picker) return;
      picker.innerHTML = '';
      for (const [id, s] of Object.entries(sessions)) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = s.tabEl?.querySelector('.tab-name')?.textContent || id.slice(0, 8);
        if (id === activeId) opt.selected = true;
        picker.appendChild(opt);
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
        if (data.ok) showToast(`맥 iTerm에 '${tmuxName}' 열림`, 'success');
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
