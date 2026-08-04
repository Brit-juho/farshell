    // 음성 기능 설치 여부 확인 → 미설치 시 voice UI 숨김
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/capabilities`);
        const caps = await res.json();
        if (!caps.voice) {
          const vb = document.getElementById('voice-bar');
          if (vb) vb.style.display = 'none';
          const ms = document.getElementById('mic-status');
          if (ms) ms.style.display = 'none';
          // 음성 전용/이어폰 메뉴 항목은 voice.js 함수에 의존 → 미설치 시 숨김
          document.querySelectorAll('.needs-voice').forEach(el => el.style.display = 'none');
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
      // 30초 keepalive — 끊김 방지.
      // ⚠ 닫힌 WebSocket에 send()는 예외를 던지지 않고 조용히 버린다(스펙: CLOSING/CLOSED).
      // 따라서 catch로만 정리하면 인터벌이 영구히 남아 죽은 소켓을 붙잡는다(메모리 누수).
      // onclose에서 명시적으로 clearInterval하고, 매 tick도 readyState로 방어한다.
      const ka = setInterval(() => {
        if (ws.readyState !== WebSocket.OPEN) { clearInterval(ka); return; }
        try { ws.send('ping'); } catch (_) { clearInterval(ka); }
      }, 30000);
      ws.onclose = () => { clearInterval(ka); delete _previewWs[sessName]; };
      ws.onerror = () => { try { ws.close(); } catch (_) {} };
    }

    async function refreshGrid() {
      try {
        const res = await fetch(`${API_BASE}/api/tmux/sessions`);
        const tmuxSessions = await res.json();
        const cards = document.getElementById('grid-cards');

        // D3: 빈 상태 — 세션 없을 때 안내 메시지
        if (tmuxSessions.length === 0) {
          cards.innerHTML = `<div class="vt-grid-empty">
            <div class="big">⊞</div>
            <div style="font-size:14px;margin-bottom:8px">실행 중인 tmux 세션이 없습니다</div>
            <div style="font-size:12px;">터미널에서 <code>tmux new -A -s dev</code> 실행 후 새로고침</div>
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
            card.className = 'vt-card';
            // 세션 이름은 tmux가 주는 임의 문자열이라 innerHTML 보간이 아니라
            // textContent로 넣는다 (`<`가 든 이름이 마크업으로 해석되지 않도록).
            card.innerHTML = `
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span class="card-title"></span>
                <span class="card-cmd"></span>
              </div>
              <pre class="card-preview"><span style="opacity:.5;font-style:italic;font-size:10px;">로딩 중...</span></pre>
            `;
            card.querySelector('.card-title').textContent = sess.name;
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
        banner.className = 'vt-banner';
        banner.textContent = '🛡 안전 모드 — 위험 명령 차단됨';
        document.body.appendChild(banner);
      }
    }).catch(() => {});

    // ── Agent WebSocket — Phase 9 #2: 폴링 대체용 push 채널 + #5: heartbeat/reconnect ─
    let wsAgent = null;
    let _wsAgentRetries = 0;
    let _wsAgentStableTimer = null;
    function connectAgentWs() {
      try {
        wsAgent = new WebSocket(`${WS_BASE}/ws-agent${_tokenQuery}`);
      } catch (e) { return scheduleAgentReconnect(); }
      // [회귀 fb827a6와 동일 패턴] 3초 이상 안정적으로 열린 뒤에만 백오프 리셋 —
      // accept 직후 닫히는 flap에서 지수가 자라지 못하고 빠르게 재연결하는 것을 방지.
      wsAgent.onopen = () => {
        clearTimeout(_wsAgentStableTimer);
        _wsAgentStableTimer = setTimeout(() => { _wsAgentRetries = 0; }, 3000);
      };
      wsAgent.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (msg.type === 'ping') {
          try { wsAgent.send(JSON.stringify({ type: 'pong' })); } catch (_) {}
        } else if (msg.type === 'agent_snapshot' || msg.type === 'agents_change') {
          if (msg.agents) applyAgentBadges(msg.agents);
          // 스냅샷에 활성 도구가 있으면 탭 파비콘을 '작업중'으로
          if (window.VTFavicon && msg.active && msg.active.length) VTFavicon.set('working');
        } else if (msg.type === 'agent_event') {
          // 탭 파비콘 상태: pre(도구 시작)=작업중, stop(응답 완료)=완료.
          // post(도구 종료)는 다음 도구가 이어질 수 있어 '작업중' 유지(무시).
          // voice 미설치 환경에서도 stop 신호로 완료 뱃지가 뜬다.
          if (msg.state && msg.state.tool) {
            showToast(`🔧 ${msg.state.tool} 실행 중...`);
            if (window.VTFavicon) VTFavicon.set('working');
          } else if (msg.event === 'stop' && window.VTFavicon) {
            VTFavicon.set('done');
          }
        }
      };
      wsAgent.onclose = () => { clearTimeout(_wsAgentStableTimer); scheduleAgentReconnect(); };
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
        toast.className = 'vt-toast';
        toast.style.opacity = '0';
        toast.style.transition = 'opacity .2s';
        document.body.appendChild(toast);
      }
      toast.textContent = text;
      toast.style.opacity = '1';
      clearTimeout(toast._t);
      toast._t = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
    }
