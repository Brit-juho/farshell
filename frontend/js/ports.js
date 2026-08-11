// 포트 대시보드 (P3) — Ports.app이 메뉴바에서 하는 일을 원격에서.
// "지금 뭐가 떠 있지 / 3000번 죽여줘"를 폰에서 처리한다.
//
// 패널 껍데기(.vt-viewer-backdrop / .vt-viewer-card)는 viewer.js와 공유한다.
// 리스트 행만 .vt-pt-* 로 따로 둔다.

    let _portsTimer = null;

    function _ptEl() { return document.getElementById('vt-ports'); }

    function closePorts() {
      const el = _ptEl();
      if (!el) return;
      el.remove();
      document.removeEventListener('keydown', _ptKey);
      if (_portsTimer) { clearInterval(_portsTimer); _portsTimer = null; }
      try { setTimeout(() => fitAndResize(activeId), 60); } catch (_) {}
    }

    function _ptKey(ev) {
      if (ev.key === 'Escape') { ev.stopPropagation(); closePorts(); }
    }

    async function _ptApi(path, opts) {
      const sep = path.includes('?') ? '&' : '?';
      const res = await fetch(`${API_BASE}${path}${sep}${_tokenQuery.replace(/^[?&]/, '')}`, opts);
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
        e.status = res.status; e.data = data;
        throw e;
      }
      return data;
    }

    function showPorts() {
      if (_ptEl()) { closePorts(); return; }

      const el = document.createElement('div');
      el.id = 'vt-ports';
      el.className = 'vt-viewer-backdrop';
      el.innerHTML = `
        <div class="vt-viewer-card" role="dialog" aria-modal="true" aria-label="포트 대시보드">
          <div class="vt-viewer-head">
            <div class="vt-vw-title">포트 — 실행 중인 개발 서버</div>
            <button class="vt-vw-diff" id="vt-pt-refresh" title="새로고침">새로고침</button>
            <button class="vt-vw-x" aria-label="닫기">✕</button>
          </div>
          <div class="vt-vw-body" id="vt-pt-body">
            <div class="vt-vw-loading">불러오는 중…</div>
          </div>
        </div>
      `;
      el.querySelector('.vt-vw-x').addEventListener('click', closePorts);
      el.addEventListener('click', (ev) => { if (ev.target === el) closePorts(); });
      el.querySelector('#vt-pt-refresh').addEventListener('click', () => refreshPorts(true));
      document.addEventListener('keydown', _ptKey);
      document.body.appendChild(el);

      refreshPorts(true);
      // 5초 폴링. 패널이 열려 있을 때만 돈다 — 닫으면 closePorts가 정리한다.
      _portsTimer = setInterval(() => { if (_ptEl()) refreshPorts(false); else closePorts(); }, 5000);
    }

    function _fmtMem(kb) {
      if (!kb) return '';
      if (kb < 1024) return kb + 'K';
      return (kb / 1024).toFixed(0) + 'M';
    }

    // "20-04:05:48" → "20일", "07:10:55" → "7시간"
    function _fmtUptime(s) {
      if (!s) return '';
      const dm = /^(\d+)-/.exec(s);
      if (dm) return parseInt(dm[1], 10) + '일';       // "05-01:26" → "5일"
      const parts = s.split(':');
      if (parts.length === 3) return parseInt(parts[0], 10) + '시간';
      if (parts.length === 2) return parseInt(parts[0], 10) + '분';
      return s;
    }

    async function refreshPorts(fresh) {
      const body = document.getElementById('vt-pt-body');
      if (!body) return;
      let d;
      try {
        d = await _ptApi(`/api/ports${fresh ? '?fresh=true' : ''}`);
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${e.message}</div>`;
        return;
      }
      if (!d.ports.length) {
        body.innerHTML = '<div class="vt-vw-empty">리스닝 중인 포트가 없습니다.</div>';
        return;
      }

      const list = document.createElement('div');
      list.className = 'vt-vw-list';
      d.ports.forEach(p => {
        const row = document.createElement('div');
        row.className = 'vt-pt-row';

        const port = document.createElement('span');
        port.className = 'vt-pt-port';
        port.textContent = p.port;

        const meta = document.createElement('div');
        meta.className = 'vt-pt-meta';
        const name = document.createElement('div');
        name.className = 'vt-pt-cmd';
        name.textContent = p.cmd;                       // textContent — XSS 방어
        const sub = document.createElement('div');
        sub.className = 'vt-pt-sub';
        const bits = [`pid ${p.pid}`];
        if (p.uptime) bits.push(_fmtUptime(p.uptime));
        if (p.rss_kb) bits.push(_fmtMem(p.rss_kb));
        if (typeof p.cpu === 'number' && p.cpu > 0) bits.push(p.cpu.toFixed(1) + '%');
        sub.textContent = bits.join(' · ');
        meta.appendChild(name); meta.appendChild(sub);

        const tags = document.createElement('span');
        tags.className = 'vt-pt-tags';
        if (p.public) {
          const t = document.createElement('b');
          t.className = 'vt-pt-tag pub';
          t.textContent = '외부';
          t.title = '모든 인터페이스에 열려 있습니다 (*)';
          tags.appendChild(t);
        }

        const actions = document.createElement('span');
        actions.className = 'vt-pt-actions';
        if (p.protected) {
          const lock = document.createElement('span');
          lock.className = 'vt-pt-lock';
          lock.textContent = '보호됨';
          lock.title = p.protected_reason;
          actions.appendChild(lock);
        } else {
          const ex = document.createElement('button');
          ex.className = 'vt-pt-btn';
          ex.textContent = '공개';
          ex.title = '이 포트를 Cloudflare 터널로 인터넷에 공개합니다';
          ex.onclick = () => exposePort(p.port);
          const kb = document.createElement('button');
          kb.className = 'vt-pt-btn danger';
          kb.textContent = '종료';
          kb.onclick = () => killPort(p.port, p.pid, p.cmd);
          actions.appendChild(ex); actions.appendChild(kb);
        }

        row.appendChild(port); row.appendChild(meta);
        row.appendChild(tags); row.appendChild(actions);
        list.appendChild(row);
      });
      body.innerHTML = '';
      body.appendChild(list);
      if (d.truncated) {
        const n = document.createElement('div');
        n.className = 'vt-vw-note';
        n.textContent = '포트가 많아 일부만 표시했습니다.';
        body.appendChild(n);
      }
    }

    async function killPort(port, pid, cmd) {
      if (!confirm(`포트 ${port} (${cmd}, pid ${pid}) 를 종료할까요?`)) return;
      try {
        // pid를 함께 보낸다 — 조회 후 프로세스가 바뀌었으면 서버가 409로 거부한다.
        const r = await _ptApi(`/api/ports/${port}?pid=${pid}`, { method: 'DELETE' });
        showToast(`포트 ${port} 종료됨 (${r.signal})`);
      } catch (e) {
        showToast(`종료 실패: ${e.message}`);
      }
      refreshPorts(true);
    }

    async function exposePort(port) {
      // 2단계 확인. 서버도 confirm 없으면 428로 거부하지만, 오탭을 UI에서 먼저 막는다.
      if (!confirm(`포트 ${port} 를 공개 인터넷에 노출합니다.\n\n누구나 URL만 알면 접근할 수 있습니다. 계속할까요?`)) return;
      showToast(`포트 ${port} 터널 여는 중…`);
      try {
        const r = await _ptApi(`/api/ports/${port}/expose`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true }),
        });
        showToast(r.url ? `공개됨: ${r.url}` : '공개됨');
      } catch (e) {
        showToast(`공개 실패: ${e.message}`);
      }
    }
