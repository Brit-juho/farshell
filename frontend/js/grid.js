    // 음성 기능 설치 여부 확인 → 미설치 시 voice UI 숨김
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/capabilities`);
        const caps = await res.json();
        if (!caps.voice) {
          document.getElementById('voice-bar').style.display = 'none';
          document.getElementById('mic-status').style.display = 'none';
          return; // voice.js 로드 안 함
        }
      } catch (e) {
        // 서버 통신 실패 시 기본 표시
      }
      const s = document.createElement('script');
      s.src = '/static/voice.js';
      document.body.appendChild(s);
    })();

    // Phase 9 #2: agents 폴링 제거 — `/ws-agent` push가 모두 대체.
    // applyAgentBadges() / connectAgentWs()는 위쪽(약 ~1120행)에 정의됨.
    // grid 폴링만 visibilitychange로 정리.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (gridPollTimer) { clearInterval(gridPollTimer); gridPollTimer = null; }
      } else {
        if (gridViewEnabled) gridPollTimer = setInterval(refreshGrid, 1000);
      }
    });

    // ── 라이브 프리뷰 그리드 뷰 (Phase 7 #7-3) ─────────────────────────
    let gridViewEnabled = false;
    let gridPollTimer = null;
    const ANSI_COLOR_MAP = {
      30:'#45475a',31:'#f38ba8',32:'#a6e3a1',33:'#f9e2af',34:'#89b4fa',35:'#f5c2e7',36:'#94e2d5',37:'#cdd6f4',
      90:'#6c7086',91:'#eba0ac',92:'#a6e3a1',93:'#f9e2af',94:'#74c7ec',95:'#cba6f7',96:'#89dceb',97:'#cdd6f4',
    };

    // ANSI escape를 HTML span으로 단순 변환 (XSS 방지: textContent 사용 후 escape 시퀀스만 처리)
    function ansiToHtml(text) {
      // HTML escape 먼저
      let html = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      // ANSI CSI 시퀀스: ESC[<n>;<n>m
      html = html.replace(/\x1b\[([\d;]*)m/g, (m, codes) => {
        if (!codes || codes === '0') return '</span>';
        const parts = codes.split(';').map(Number);
        const styles = [];
        for (const code of parts) {
          if (code === 1) styles.push('font-weight:bold');
          else if (code === 3) styles.push('font-style:italic');
          else if (code === 4) styles.push('text-decoration:underline');
          else if (ANSI_COLOR_MAP[code]) styles.push(`color:${ANSI_COLOR_MAP[code]}`);
          else if (ANSI_COLOR_MAP[code-10]) styles.push(`background:${ANSI_COLOR_MAP[code-10]}`);
        }
        return styles.length ? `<span style="${styles.join(';')}">` : '<span>';
      });
      // 미닫힌 span 자동 종료
      const openCount = (html.match(/<span/g) || []).length;
      const closeCount = (html.match(/<\/span>/g) || []).length;
      for (let i = 0; i < openCount - closeCount; i++) html += '</span>';
      // 기타 ANSI escape 제거
      html = html.replace(/\x1b\[[?\d;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '');
      return html;
    }

    async function toggleGridView() {
      gridViewEnabled = !gridViewEnabled;
      const grid = document.getElementById('grid-view');
      const term = document.getElementById('terminal-container');
      const btn = document.getElementById('grid-toggle');
      if (gridViewEnabled) {
        grid.style.display = 'block';
        term.style.display = 'none';
        btn.classList.add('active');     // D2: CSS class로 활성 상태 관리
        await refreshGrid();
        // Phase 9 #1: setInterval(refreshGrid, 2000) 제거 — 카드별 ws push가 갱신 담당.
      } else {
        grid.style.display = 'none';
        term.style.display = '';
        btn.classList.remove('active');  // D2: 비활성 시 class 제거
        if (gridPollTimer) { clearInterval(gridPollTimer); gridPollTimer = null; }
        // 모든 preview ws 닫기
        for (const ws of Object.values(_previewWs)) { try { ws.close(); } catch (_) {} }
        Object.keys(_previewWs).forEach(k => delete _previewWs[k]);
      }
    }
    // Phase 9 #1: 카드별 preview ws — 변화 시에만 갱신.
    const _previewWs = {};
    function ensurePreviewWs(sessName) {
      if (_previewWs[sessName]) return;
      const ws = new WebSocket(`${WS_BASE}/ws-preview/${encodeURIComponent(sessName)}${_tokenQuery}`);
      _previewWs[sessName] = ws;
      ws.onmessage = (e) => {
        let msg; try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (msg.type !== 'preview' || !msg.content) return;
        const cards = document.getElementById('grid-cards');
        const card = cards && cards.querySelector(`[data-name="${CSS.escape(sessName)}"]`);
        if (!card) return;
        const pre = card.querySelector('.card-preview');
        if (pre) { pre.innerHTML = ansiToHtml(msg.content); pre.scrollTop = pre.scrollHeight; }
      };
      ws.onclose = () => { delete _previewWs[sessName]; };
      // 30초 keepalive — 끊김 방지
      const ka = setInterval(() => {
        try { ws.send('ping'); } catch (_) { clearInterval(ka); }
      }, 30000);
    }

    async function refreshGrid() {
      try {
        const res = await fetch(`${API_BASE}/api/tmux/sessions`);
        const tmuxSessions = await res.json();
        const cards = document.getElementById('grid-cards');

        // D3: 빈 상태 — 세션 없을 때 안내 메시지
        if (tmuxSessions.length === 0) {
          cards.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px 24px;color:#585b70;">
            <div style="font-size:32px;margin-bottom:12px">⊞</div>
            <div style="font-size:14px;color:#a6adc8;margin-bottom:8px">실행 중인 tmux 세션이 없습니다</div>
            <div style="font-size:12px;color:#585b70;">터미널에서 <code style="background:#313244;padding:2px 6px;border-radius:4px">tmux new -A -s dev</code> 실행 후 새로고침</div>
          </div>`;
          return;
        }
        const existingNames = new Set(Array.from(cards.children).map(c => c.dataset.name));
        const incomingNames = new Set(tmuxSessions.map(s => s.name));
        // 사라진 세션 카드 제거
        for (const card of Array.from(cards.children)) {
          if (!incomingNames.has(card.dataset.name)) card.remove();
        }
        // 카드 생성/갱신
        // D8: 카드 생성은 순차, 프리뷰 fetch는 Promise.all로 병렬화
        for (const sess of tmuxSessions) {
          let card = cards.querySelector(`[data-name="${CSS.escape(sess.name)}"]`);
          if (!card) {
            card = document.createElement('div');
            card.dataset.name = sess.name;
            card.style.cssText = 'background:#181825;border:1px solid #313244;border-radius:8px;padding:10px;cursor:pointer;display:flex;flex-direction:column;gap:6px;';
            card.innerHTML = `
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-weight:bold;color:#cdd6f4;font-size:13px;">${sess.name}</span>
                <span class="card-cmd" style="font-size:11px;color:#a6adc8;"></span>
              </div>
              <pre class="card-preview" style="margin:0;background:#11111b;border-radius:4px;padding:8px;font-family:monospace;font-size:11px;color:#cdd6f4;white-space:pre-wrap;overflow:hidden;max-height:240px;line-height:1.3;">
                <span style="color:#313244;font-style:italic;font-size:10px;">로딩 중...</span>
              </pre>
            `;
            card.onclick = () => {
              // Codex: toggleGridView 내부에서 gridViewEnabled를 반전시키므로
              // 그리드가 열려있을 때만 toggle 호출해야 닫힌다.
              if (gridViewEnabled) toggleGridView();
              if (sess.web_session_id && sessions[sess.web_session_id]) {
                switchTo(sess.web_session_id);
              } else {
                attachTmuxSession(sess.name);
              }
            };
            cards.appendChild(card);
          }
          card.querySelector('.card-cmd').textContent = sess.command || '';
        }

        // Phase 9 #1: 폴링 fetch 제거 — 카드별 ws push가 즉시 첫 콘텐츠 + 변화 push.
        for (const sess of tmuxSessions) ensurePreviewWs(sess.name);
        // 사라진 세션의 ws는 닫기
        for (const name of Object.keys(_previewWs)) {
          if (!incomingNames.has(name)) {
            try { _previewWs[name].close(); } catch (_) {}
            delete _previewWs[name];
          }
        }
      } catch (e) { console.warn('grid refresh fail', e); }
    }

    async function attachTmuxSession(name) {
      try {
        const res = await fetch(`${API_BASE}/api/tmux/attach`, {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ name })
        });
        const data = await res.json();
        if (data.id) {
          if (!sessions[data.id]) {
            addSession(data.id, data.name || `tmux:${name}`);
            if (sessions[data.id]) sessions[data.id].tmuxName = name;
          }
          switchTo(data.id);
        }
      } catch (e) { console.warn('attach fail', e); }
    }

    // ── 안전 모드 표시 ───────────────────────────────────────────────
    fetch(`${API_BASE}/api/safe-mode`).then(r => r.json()).then(data => {
      if (data.enabled) {
        const banner = document.createElement('div');
        banner.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#f38ba8;color:#1e1e2e;text-align:center;padding:4px;font-size:12px;z-index:9999;';
        banner.textContent = '🛡 안전 모드 — 위험 명령 차단됨';
        document.body.appendChild(banner);
      }
    }).catch(() => {});

    // ── Agent WebSocket — Phase 9 #2: 폴링 대체용 push 채널 + #5: heartbeat/reconnect ─
    let wsAgent = null;
    let _wsAgentRetries = 0;
    function connectAgentWs() {
      try {
        wsAgent = new WebSocket(`${WS_BASE}/ws-agent${_tokenQuery}`);
      } catch (e) { return scheduleAgentReconnect(); }
      wsAgent.onopen = () => { _wsAgentRetries = 0; };
      wsAgent.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (msg.type === 'ping') {
          try { wsAgent.send(JSON.stringify({ type: 'pong' })); } catch (_) {}
        } else if (msg.type === 'agent_snapshot' || msg.type === 'agents_change') {
          if (msg.agents) applyAgentBadges(msg.agents);
        } else if (msg.type === 'agent_event' && msg.state && msg.state.tool) {
          showToast(`🔧 ${msg.state.tool} 실행 중...`);
        }
      };
      wsAgent.onclose = scheduleAgentReconnect;
      wsAgent.onerror = () => { try { wsAgent.close(); } catch (_) {} };
    }
    function scheduleAgentReconnect() {
      if (_wsAgentRetries >= 15) return;
      _wsAgentRetries++;
      const delay = Math.min(1000 * Math.pow(2, _wsAgentRetries), 30000);
      setTimeout(connectAgentWs, delay);
    }
    function applyAgentBadges(agents) {
      document.querySelectorAll('.tab').forEach((tab) => {
        const sid = tab.dataset.sessionId;
        const sess = sessions[sid];
        const badge = tab.querySelector('.tab-agent');
        if (!sess || !badge) return;
        const tmuxName = sess.tmux_name || sess.tmuxName;
        const info = tmuxName && agents[tmuxName];
        badge.textContent = (info && info.icon) ? info.icon : '';
        if (info && info.label) badge.title = info.label;
      });
    }
    connectAgentWs();

    function showToast(text) {
      let toast = document.getElementById('agent-toast');
      if (!toast) {
        toast = document.createElement('div');
        toast.id = 'agent-toast';
        toast.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#313244;color:#cdd6f4;padding:8px 16px;border-radius:8px;font-size:13px;z-index:9999;opacity:0;transition:opacity .2s;';
        document.body.appendChild(toast);
      }
      toast.textContent = text;
      toast.style.opacity = '1';
      clearTimeout(toast._t);
      toast._t = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
    }
