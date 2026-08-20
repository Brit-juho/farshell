    // 음성 기능 설치 여부 확인 → 미설치 시 voice UI 숨김
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/capabilities`);
        const caps = await res.json();
        // P2: 열람 가능한 루트가 없으면 코드 뷰어 진입점을 숨긴다.
        // (voice와 달리 return보다 먼저 처리해야 음성 미설치 환경에서도 게이팅이 걸린다)
        if (!caps.fs) {
          document.querySelectorAll('.needs-fs').forEach(el => el.style.display = 'none');
        }
        // P3: lsof 없는 환경이면 포트 대시보드 진입점을 숨긴다.
        if (!caps.ports) {
          document.querySelectorAll('.needs-ports').forEach(el => el.style.display = 'none');
        }
        // P5: 서버에 pywebpush 가 없으면 푸시 토글을 숨긴다.
        // (secure context / iOS PWA 여부는 클라이언트 사정이라 pushui.js가 따로 안내한다)
        if (!caps.push) {
          document.querySelectorAll('.needs-push').forEach(el => el.style.display = 'none');
        }
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

    // ANSI→HTML 변환 + 빈 줄/긴 패딩 정리는 순수 로직이라 ansilex.js로 분리했다
    // (keyseq.js/difflex.js와 같은 이유 — Node 테스트에서 재사용하기 위해서다).
    // ansiToHtml은 그리드 카드 라이브 프리뷰의 유일한 XSS 방어선이다: 서버가 보낸
    // tmux capture-pane 원문을 innerHTML로 꽂기 때문에(아래 refreshCard), 반드시
    // 여기서 HTML escape를 거쳐야 한다.
    const ansiToHtml = VTAnsiLex.ansiToHtml;
    const _trimBlankLines = VTAnsiLex.trimBlankLines;

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
        if (pre) { pre.innerHTML = ansiToHtml(_trimBlankLines(msg.content)); pre.scrollTop = pre.scrollHeight; }
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
        // 세션 목록 + 에이전트 배지(어떤 CLI가 그 pane에 떠 있는지)를 함께 가져온다.
        // agents는 /ws-agent 최초 접속 시 한 번(snapshot)만 오므로, 그리드를 열 때마다
        // 여기서도 새로 물어봐야 배지가 stale해지지 않는다.
        const [sessRes, agentsRes] = await Promise.all([
          fetch(`${API_BASE}/api/tmux/sessions`),
          fetch(`${API_BASE}/api/agents`).catch(() => null),
        ]);
        const tmuxSessions = await sessRes.json();
        const agents = agentsRes ? await agentsRes.json().catch(() => ({})) : {};
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
              <div class="card-head">
                <span class="card-agent"></span>
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
              // "완료" 강조는 확인했다는 뜻이니 클릭하면 지운다.
              card.classList.remove('done');
              if (sess.web_session_id && sessions[sess.web_session_id]) {
                switchTo(sess.web_session_id);
              } else {
                attachTmuxSession(sess.name);
              }
            };
            cards.appendChild(card);
          }
          card.querySelector('.card-cmd').textContent = sess.command || '';
          // cwd는 dataset에 저장해둔다 — agent_event(pre/stop)가 cwd로만 오므로
          // "어느 카드가 지금 작업 중인지"를 여기 저장된 값과 매칭해서 찾는다.
          card.dataset.cwd = sess.cwd || '';
          // 이미 탭으로 열려 있으면(전환 vs attach — 클릭 결과가 달라진다) 왼쪽에 표시.
          const isOpenTab = !!(sess.web_session_id && sessions[sess.web_session_id]);
          card.classList.toggle('open-tab', isOpenTab);
          card.title = isOpenTab ? '이미 탭으로 열려 있음 — 클릭하면 그 탭으로 전환' : '클릭하면 이 세션에 접속';
          _applyCardAgent(card, agents[sess.name]);
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

        // 스냅샷에 이미 작업 중인 세션이 있으면(그리드를 늦게 열었을 수 있음) 바로 반영.
        _applyActiveHighlights(agent_status_active_cache);
      } catch (e) { console.warn('grid refresh fail', e); }
    }

    function _applyCardAgent(card, info) {
      const badge = card.querySelector('.card-agent');
      if (!badge) return;
      badge.textContent = (info && info.icon) ? info.icon : '';
      if (info && info.label) badge.title = info.label; else badge.removeAttribute('title');
    }

    // cwd로 그리드 카드를 찾는다 — agent_event/snapshot 둘 다 cwd 기준.
    // Claude Code 훅은 tmux pane을 직접 알려주지 않아 cwd로만 매칭할 수 있는데,
    // 같은 디렉토리에 여러 세션이 떠 있으면(둘 다 $HOME 등) cwd가 유일하지 않다 —
    // 그럴 땐 아무 데나 강조하는 대신 아무 것도 안 켠다("틀리게 확신"보다 낫다).
    function _cardByCwd(cwd) {
      if (!cwd) return null;
      const cards = document.getElementById('grid-cards');
      if (!cards) return null;
      const matches = cards.querySelectorAll(`.vt-card[data-cwd="${CSS.escape(cwd)}"]`);
      return matches.length === 1 ? matches[0] : null;
    }

    // /ws-agent 스냅샷(agent_snapshot)의 active 목록을 카드 강조에 반영.
    // 그리드를 연 시점에 이미 도구를 쓰고 있던 세션도 놓치지 않기 위함.
    let agent_status_active_cache = [];
    function _applyActiveHighlights(active) {
      document.querySelectorAll('.vt-card.working').forEach(c => c.classList.remove('working'));
      (active || []).forEach(a => {
        const card = _cardByCwd(a.cwd);
        if (card) card.classList.add('working');
      });
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
          // 그리드를 늦게 열었을 때도(이미 작업 중이던 세션) 카드 강조가 맞도록 캐시+반영.
          agent_status_active_cache = msg.active || [];
          _applyActiveHighlights(agent_status_active_cache);
        } else if (msg.type === 'agent_event') {
          // 탭 파비콘 상태: pre(도구 시작)=작업중, stop(응답 완료)=완료.
          // post(도구 종료)는 다음 도구가 이어질 수 있어 '작업중' 유지(무시).
          // voice 미설치 환경에서도 stop 신호로 완료 뱃지가 뜬다.
          // 그리드 카드도 cwd로 매칭해 같은 규칙(pre=작업중, stop=완료)을 적용한다.
          if (msg.state && msg.state.tool) {
            showToast(`🔧 ${msg.state.tool} 실행 중...`);
            if (window.VTFavicon) VTFavicon.set('working');
            const card = _cardByCwd(msg.state.cwd);
            if (card) { card.classList.add('working'); card.classList.remove('done'); }
          } else if (msg.event === 'stop') {
            if (window.VTFavicon) VTFavicon.set('done');
            const card = _cardByCwd(msg.state && msg.state.cwd);
            if (card) { card.classList.remove('working'); card.classList.add('done'); }
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
      document.querySelectorAll('.vt-card').forEach((card) => {
        _applyCardAgent(card, agents[card.dataset.name]);
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
