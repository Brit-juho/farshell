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
      const colors = { info: '#89b4fa', error: '#f38ba8', success: '#a6e3a1' };
      const toast = document.createElement('div');
      toast.style.cssText = `
        position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
        background:#313244;color:${colors[type] || '#cdd6f4'};padding:10px 20px;
        border-radius:10px;font-size:13px;z-index:200;max-width:90vw;
        box-shadow:0 4px 12px rgba(0,0,0,0.4);
      `;
      toast.textContent = msg;
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 4000);
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
        if (data.ok && activeId && sessions[activeId]) {
          sessions[activeId].term.write(`\r\n\x1b[32m[업로드 완료: ${data.path}]\x1b[0m\r\n`);
        }
      } catch (e) {
        showToast('업로드 실패: ' + e.message, 'error');
      }
      input.value = '';
    }
