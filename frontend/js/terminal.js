    const sessions = {};
    let activeId = null;
    const _urlParams = new URLSearchParams(location.search);
    const _hashParams = new URLSearchParams(location.hash.slice(1));
    let VT_TOKEN = _urlParams.get('token') || '';
    let _tokenQuery = VT_TOKEN ? `?token=${VT_TOKEN}` : '';
    let _tokenParam = VT_TOKEN ? `&token=${VT_TOKEN}` : '';

    // Phase 9 #8: URL의 토큰을 HttpOnly cookie로 1회 교환 후 URL에서 제거.
    // 이후 fetch는 credentials:'include'로 cookie 자동 전송, ws는 same-origin이라 자동.
    (async function _exchangeTokenForCookie() {
      if (!VT_TOKEN) return;
      try {
        const r = await fetch('/api/auth', {
          method: 'POST',
          credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({token: VT_TOKEN}),
        });
        if (r.ok) {
          // URL에서 토큰 제거 — 로그/공유/history 노출 차단
          _urlParams.delete('token');
          const newSearch = _urlParams.toString();
          history.replaceState({}, '', location.pathname + (newSearch ? '?' + newSearch : '') + location.hash);
          // 이후 ws/fetch는 cookie로 자동 인증되므로 query 파라미터 비우기
          VT_TOKEN = '';
          _tokenQuery = '';
          _tokenParam = '';
        }
      } catch (e) { /* 실패 시 query 토큰 그대로 사용 (호환) */ }
    })();
    // E2E 암호화 활성화 — URL에 ?e2e=1 또는 #e2e=1 있으면 ON (D3)
    const E2E_ENABLED = (_urlParams.get('e2e') === '1' || _hashParams.get('e2e') === '1');
    const _e2eQuery = E2E_ENABLED ? (_tokenQuery ? '&e2e=1' : '?e2e=1') : '';

    // base64url ↔ Uint8Array
    function _b64uDec(s) {
      const pad = '='.repeat((4 - s.length % 4) % 4);
      return nacl.util.decodeBase64(s.replace(/-/g, '+').replace(/_/g, '/') + pad);
    }
    function _b64uEnc(bytes) {
      return nacl.util.encodeBase64(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    // E2E WebSocket 래퍼 — 핸드셰이크 후 encrypt/decrypt 자동 처리
    // onReady(handle) 에서 handle은 { send(bytes), close(), readyState } 형태 (원본 WS 인터페이스와 호환)
    function wrapE2E(ws, onReady, onData) {
      if (!E2E_ENABLED) {
        ws.addEventListener('message', (e) => {
          if (e.data instanceof ArrayBuffer) onData(new Uint8Array(e.data));
        });
        onReady({
          send: (bytes) => ws.send(bytes),
          close: () => ws.close(),
          get readyState() { return ws.readyState; },
        });
        return;
      }
      let sharedKey = null;
      ws.addEventListener('message', (e) => {
        // 핸드셰이크: 첫 텍스트로 서버 공개키 수신
        if (typeof e.data === 'string' && !sharedKey) {
          let msg;
          try { msg = JSON.parse(e.data); } catch { return; }
          if (msg.type !== 'e2e-hello' || !msg.pub) return;
          const kp = nacl.box.keyPair();
          const serverPub = _b64uDec(msg.pub);
          // nacl.box.before() == PyNaCl Box.shared_key() (둘 다 crypto_box_beforenm)
          sharedKey = nacl.box.before(serverPub, kp.secretKey);
          ws.send(JSON.stringify({ type: 'e2e-ack', pub: _b64uEnc(kp.publicKey) }));
          onReady({
            send: (bytes) => {
              if (ws.readyState !== WebSocket.OPEN) return;
              const buf = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
              const nonce = nacl.randomBytes(nacl.secretbox.nonceLength);
              const ct = nacl.secretbox(buf, nonce, sharedKey);
              const wire = new Uint8Array(nonce.length + ct.length);
              wire.set(nonce, 0); wire.set(ct, nonce.length);
              ws.send(wire);
            },
            close: () => ws.close(),
            get readyState() { return ws.readyState; },
          });
          return;
        }
        // 암호화된 바이트 메시지
        if (e.data instanceof ArrayBuffer && sharedKey) {
          const wire = new Uint8Array(e.data);
          const nonce = wire.slice(0, nacl.secretbox.nonceLength);
          const ct = wire.slice(nacl.secretbox.nonceLength);
          const pt = nacl.secretbox.open(ct, nonce, sharedKey);
          if (pt) onData(pt);
          else console.warn('E2E decrypt failed');
        }
      });
    }
    const WS_BASE = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
    const API_BASE = `${location.protocol}//${location.host}`;
    const _authHeaders = VT_TOKEN ? {'Authorization': `Bearer ${VT_TOKEN}`} : {};
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
    const termFontSize = isMobile ? 12 : 14;

    // 인증 토큰 자동 첨부 fetch 래퍼
    const _origFetch = window.fetch;
    window.fetch = (url, opts = {}) => {
      if (VT_TOKEN && typeof url === 'string' && url.startsWith(API_BASE)) {
        opts.headers = { ...(opts.headers || {}), ..._authHeaders };
      }
      return _origFetch(url, opts);
    };

    async function createSession() {
      // "맥에서도 열기" 토글이 켜져 있으면 tmux 세션으로 생성하고
      // 서버에 osascript로 iTerm 창을 자동 오픈하도록 요청
      const autoMac = document.getElementById('auto-mac-checkbox')?.checked;
      if (autoMac) {
        const res = await fetch(`${API_BASE}/api/tmux/create`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auto_open_on_mac: true }),
        });
        const data = await res.json();
        addSession(data.id, data.name?.replace('tmux:', '') || data.id);
        return;
      }
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      // C5: 401(토큰)/500 시 JSON에 id가 없어 addSession(undefined) 방지.
      if (!res.ok) { showToast(`세션 생성 실패 (${res.status})`); return; }
      const { id } = await res.json();
      if (id) addSession(id);
    }

    function addSession(id, displayName) {
      const tab = document.createElement('div');
      tab.className = 'tab';
      tab.dataset.sessionId = id;
      const agentBadge = document.createElement('span');
      agentBadge.className = 'tab-agent';
      agentBadge.style.cssText = 'margin-right:4px;font-size:12px;';
      const nameSpan = document.createElement('span');
      nameSpan.className = 'tab-name';
      nameSpan.textContent = displayName || id.slice(0, 8);
      const closeSpan = document.createElement('span');
      closeSpan.className = 'close';
      closeSpan.textContent = '×';
      closeSpan.onclick = (e) => { e.stopPropagation(); removeSession(id); };
      tab.appendChild(agentBadge);
      tab.appendChild(nameSpan);
      tab.appendChild(closeSpan);
      tab.onclick = () => switchTo(id);
      // Phase 8 G7: 탭 드래그 정렬
      if (typeof makeTabDraggable === 'function') makeTabDraggable(tab);
      // 더블클릭으로 이름 편집
      nameSpan.ondblclick = (e) => {
        e.stopPropagation();
        nameSpan.contentEditable = 'true';
        nameSpan.focus();
        const finishEdit = async () => {
          nameSpan.contentEditable = 'false';
          const newName = nameSpan.textContent.trim();
          if (newName) {
            fetch(`${API_BASE}/api/sessions/${id}`, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name: newName }),
            });
          }
        };
        nameSpan.onblur = finishEdit;
        nameSpan.onkeydown = (ke) => { if (ke.key === 'Enter') { ke.preventDefault(); nameSpan.blur(); } };
      };
      document.getElementById('tabs').insertBefore(tab, document.getElementById('add-btn'));

      const term = new Terminal({
        cursorBlink: true,
        fontSize: termFontSize,
        fontFamily: "'IBM Plex Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace",
        theme: (window.getVtXtermTheme ? window.getVtXtermTheme() : { background: '#1e1e2e' }),
        allowProposedApi: true,
        screenReaderMode: true,  // a11y: Canvas 외에 hidden DOM도 유지
      });
      const fitAddon = new FitAddon.FitAddon();
      term.loadAddon(fitAddon);
      const searchAddon = new SearchAddon.SearchAddon();
      term.loadAddon(searchAddon);

      // 각 세션에 고유 wrapper div 생성 (show/hide로 탭 전환)
      const wrapper = document.createElement('div');
      wrapper.id = `term-${id}`;
      wrapper.style.cssText = 'height:100%;display:none;';
      document.getElementById('terminal-container').appendChild(wrapper);
      term.open(wrapper);

      // WebSocket URL 구성 — E2E 활성 시 ?e2e=1 (또는 토큰 뒤에 &e2e=1)
      const _wsPath = `/ws/${id}${_tokenQuery}${_e2eQuery}`;
      const ws = new WebSocket(`${WS_BASE}${_wsPath}`);
      ws.binaryType = 'arraybuffer';

      // sessions[id] 선 초기화 — wrapE2E의 동기 onReady 콜백이 참조할 수 있도록
      sessions[id] = { term, ws, tabEl: tab, fitAddon, searchAddon, wrapper, wsHandle: null };

      ws.onopen = () => {
        updateConnStatus(id, true);
        // E2E가 아니면 즉시 resize 가능, E2E면 wrapE2E의 onReady 안에서
        if (!E2E_ENABLED) fitAndResize(id);
      };
      // Phase 8 G2: 서버 ping 응답 (텍스트 메시지)
      const pingHandler = (e) => {
        if (typeof e.data !== 'string') return;
        try {
          const msg = JSON.parse(e.data);
          if (msg && msg.type === 'ping') {
            ws.send(JSON.stringify({ type: 'pong' }));
          }
        } catch (_) { /* binary or non-JSON */ }
      };
      ws.addEventListener('message', pingHandler);
      // wrapE2E 가 핸드셰이크 후 handle 을 넘김. E2E 비활성이면 즉시 실행.
      wrapE2E(ws,
        (handle) => {
          sessions[id].wsHandle = handle;
          if (E2E_ENABLED) fitAndResize(id);
        },
        (bytes) => term.write(bytes)
      );

      ws.onclose = () => {
        updateConnStatus(id, false);
        // [M1] 자동 재연결 (지수 백오프). C2: 상한 도달 시 영구 포기하지 않는다 —
        // 예전엔 15회 후 멈춰서 모바일 장시간 세션이 네트워크 flap을 반복하면 새로고침
        // 전까지 죽었다. 백오프 상한(30s)만 유지하고 무한 재시도한다.
        let retries = 0;
        const reconnect = () => {
          if (!(id in sessions)) return;
          retries++;
          // Math.pow(2, retries)는 지수가 커지면 오버플로하므로 지수를 5로 clamp.
          const delay = Math.min(1000 * Math.pow(2, Math.min(retries, 5)), 30000);
          term.write(`\r\n\x1b[33m[재연결 중... ${retries}회]\x1b[0m\r\n`);
          setTimeout(() => {
            const newWs = new WebSocket(`${WS_BASE}${_wsPath}`);
            newWs.binaryType = 'arraybuffer';
            newWs.onopen = () => {
              retries = 0;
              sessions[id].ws = newWs;
              updateConnStatus(id, true);
              // A2: 서버가 재접속 시 scrollback(최대 256KB)을 통째로 재전송하는데
              // reset 없이 write하면 이전 출력이 화면에 중복 누적된다. 재연결 직후
              // 터미널을 비워 scrollback이 깨끗하게 repaint 되도록 한다.
              term.reset();
              if (!E2E_ENABLED) fitAndResize(id);
              term.write('\x1b[32m[재연결됨]\x1b[0m\r\n');
            };
            // Codex: 재연결 소켓에도 heartbeat pong 핸들러 등록
            newWs.addEventListener('message', (e) => {
              if (typeof e.data !== 'string') return;
              try {
                const msg = JSON.parse(e.data);
                if (msg && msg.type === 'ping') newWs.send(JSON.stringify({ type: 'pong' }));
              } catch (_) {}
            });
            wrapE2E(newWs,
              (handle) => {
                sessions[id].wsHandle = handle;
                if (E2E_ENABLED) fitAndResize(id);
              },
              (bytes) => term.write(bytes)
            );
            newWs.onclose = reconnect;
            newWs.onerror = () => newWs.close();
          }, delay);
        };
        reconnect();
      };

      term.onData((data) => {
        const handle = sessions[id]?.wsHandle;
        if (handle && handle.readyState === WebSocket.OPEN) {
          handle.send(new TextEncoder().encode(data));
        }
      });

      const onResize = () => fitAndResize(id);
      window.addEventListener('resize', onResize);

      // 모바일: visualViewport resize (키보드 나타날 때)
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', onResize);
      }

      // sessions[id]는 WebSocket 생성 직후 선 초기화됨. onResize만 추가.
      sessions[id].onResize = onResize;
      switchTo(id);
    }

    function sendResize(ws, term) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    }

    // fit(xterm 칸 수 재계산) + PTY에 크기 통보를 항상 함께 한다. 예전엔 곳곳에서
    // fit만 하고 sendResize를 빠뜨려(switchTo 등) xterm 칸 수와 PTY 칸 수가 어긋났고,
    // 그 결과 Claude Code 같은 TUI가 박스/입력줄을 엉뚱한 행에 그리고 줄이 겹쳐 보였다.
    function fitAndResize(id) {
      const s = sessions[id];
      if (!s || !s.wrapper) return;
      // 숨김 탭(display:none)은 컨테이너가 0-height라 fit이 rows를 1로 깨뜨린다 —
      // 보이는 탭에서만 측정한다. switchTo가 표시 직후 다시 호출해 준다.
      if (s.wrapper.style.display === 'none') return;
      try { s.fitAddon.fit(); } catch (_) { return; }
      const w = s.ws;
      if (w && w.readyState === WebSocket.OPEN) sendResize(w, s.term);
    }

    function switchTo(id) {
      if (activeId && sessions[activeId]) {
        sessions[activeId].wrapper.style.display = 'none';
        sessions[activeId].tabEl.classList.remove('active');
      }
      activeId = id;
      const s = sessions[id];
      s.tabEl.classList.add('active');
      s.wrapper.style.display = 'block';
      s.term.focus();
      // display:block 직후엔 레이아웃이 아직 안 잡혀 fit이 stale 크기를 잡는다.
      // rAF로 레이아웃 확정 후 fit + PTY 크기 통보 — 탭 전환 시 xterm↔PTY 칸 수를
      // 반드시 재동기화한다(안 하면 TUI 정렬이 깨진 채로 남는다).
      requestAnimationFrame(() => fitAndResize(id));
      if (typeof notifyActiveSession === 'function') notifyActiveSession(id);
      updateSessionPicker();
      saveWorkspace();
    }

    // ── Phase 8 G7: localStorage 워크스페이스 + 탭 드래그 정렬 ─────────
    const WORKSPACE_KEY = 'vt-workspace-v1';

    function saveWorkspace() {
      try {
        const tabs = Array.from(document.querySelectorAll('#tabs .tab')).map(tab => {
          const id = tab.dataset.sessionId;
          const s = sessions[id];
          const nameSpan = tab.querySelector('.tab-name');
          return {
            id,
            name: nameSpan ? nameSpan.textContent : '',
            tmux_name: s && s.tmuxName ? s.tmuxName : null,
          };
        });
        localStorage.setItem(WORKSPACE_KEY, JSON.stringify({
          version: 1,
          active_id: activeId,
          tabs,
        }));
      } catch (e) { /* localStorage 실패 무시 */ }
    }

    async function restoreWorkspace() {
      try {
        const raw = localStorage.getItem(WORKSPACE_KEY);
        if (!raw) return false;
        const state = JSON.parse(raw);
        if (!state || !Array.isArray(state.tabs) || state.tabs.length === 0) return false;
        let restored = 0;
        let firstNewId = null;
        for (const tab of state.tabs) {
          if (tab.tmux_name) {
            try {
              const res = await fetch(`${API_BASE}/api/tmux/attach`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ name: tab.tmux_name })
              });
              if (res.ok) {
                const data = await res.json();
                if (data.id) {
                  if (sessions[data.id]) {
                    // 같은 PTY 이미 있으면 skip
                  } else {
                    addSession(data.id, data.name || `tmux:${tab.tmux_name}`);
                    if (sessions[data.id]) sessions[data.id].tmuxName = tab.tmux_name;
                    if (!firstNewId) firstNewId = data.id;
                  }
                  restored++;
                }
              }
            } catch (_) { /* 세션이 죽은 경우 skip */ }
          }
        }
        if (restored > 0 && firstNewId) {
          switchTo(firstNewId);
        }
        return restored > 0;
      } catch (e) { return false; }
    }

    function clearWorkspace() {
      localStorage.removeItem(WORKSPACE_KEY);
    }
    window.clearWorkspace = clearWorkspace; // 콘솔에서 호출 가능

    // 탭 드래그 정렬 (HTML5 DnD)
    function makeTabDraggable(tab) {
      tab.draggable = true;
      tab.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/vt-tab-id', tab.dataset.sessionId);
        tab.classList.add('dragging');
      });
      tab.addEventListener('dragend', () => {
        tab.classList.remove('dragging');
        // 모든 탭의 insertion indicator 제거
        document.querySelectorAll('#tabs .tab').forEach(t => {
          t.classList.remove('drag-over-left', 'drag-over-right');
        });
      });
      tab.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        // D4: insertion indicator — 커서 위치 기준 좌/우 border 표시
        const rect = tab.getBoundingClientRect();
        const after = e.clientX > rect.left + rect.width / 2;
        document.querySelectorAll('#tabs .tab').forEach(t => {
          t.classList.remove('drag-over-left', 'drag-over-right');
        });
        if (after) tab.classList.add('drag-over-right');
        else tab.classList.add('drag-over-left');
      });
      tab.addEventListener('dragleave', () => {
        tab.classList.remove('drag-over-left', 'drag-over-right');
      });
      tab.addEventListener('drop', (e) => {
        e.preventDefault();
        tab.classList.remove('drag-over-left', 'drag-over-right');
        const draggedId = e.dataTransfer.getData('text/vt-tab-id');
        if (!draggedId || draggedId === tab.dataset.sessionId) return;
        const dragged = document.querySelector(`#tabs .tab[data-session-id="${CSS.escape(draggedId)}"]`);
        if (!dragged) return;
        const tabsContainer = document.getElementById('tabs');
        const rect = tab.getBoundingClientRect();
        const after = e.clientX > rect.left + rect.width / 2;
        if (after) {
          tabsContainer.insertBefore(dragged, tab.nextSibling);
        } else {
          tabsContainer.insertBefore(dragged, tab);
        }
        saveWorkspace();
      });
    }

    async function removeSession(id) {
      const s = sessions[id];
      if (!s) return;
      s.ws.close();
      s.term.dispose();
      s.wrapper.remove();
      s.tabEl.remove();
      window.removeEventListener('resize', s.onResize);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', s.onResize);
      }
      delete sessions[id];
      await fetch(`${API_BASE}/api/sessions/${id}`, { method: 'DELETE' });
      if (activeId === id) {
        const remaining = Object.keys(sessions);
        if (remaining.length > 0) switchTo(remaining[0]);
        else { activeId = null; document.getElementById('terminal-container').innerHTML = ''; }
      }
      updateSessionPicker();
      saveWorkspace();
    }

    function updateConnStatus(id, connected) {
      const el = document.getElementById('conn-status');
      let overlay = document.getElementById('conn-overlay');
      if (!connected && id === activeId) {
        el.textContent = '서버 연결 끊김';
        el.className = 'disconnected';
        // 전체 화면 연결 끊김 오버레이
        if (!overlay) {
          overlay = document.createElement('div');
          overlay.id = 'conn-overlay';
          overlay.className = 'vt-overlay';
          overlay.innerHTML = `
            <div class="vt-ov-icon"><i class="icon-wifi-off"></i></div>
            <div class="vt-ov-title">서버 연결 끊김</div>
            <div class="vt-ov-sub">자동 재연결 시도 중...</div>
          `;
          document.body.appendChild(overlay);
        }
      } else {
        el.className = '';
        if (overlay) overlay.remove();
      }
    }

    // --- tmux 세션 관리 패널 (깨우기 / 완전 종료) ---
    async function showTmuxSessions() {
      // 토글: 이미 열려 있으면 닫기
      let menu = document.getElementById('tmux-menu');
      if (menu) { menu.remove(); return; }

      menu = document.createElement('div');
      menu.id = 'tmux-menu';
      menu.className = 'vt-menu';
      document.body.appendChild(menu);
      await renderTmuxMenu(menu);

      setTimeout(() => {
        document.addEventListener('click', function _close(e) {
          if (!document.body.contains(menu)) { document.removeEventListener('click', _close); return; }
          if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', _close); }
        });
      }, 100);
    }

    // 패널 내용을 다시 그린다 (kill 후 목록 갱신에도 재사용)
    async function renderTmuxMenu(menu) {
      menu.innerHTML = '';
      let tmuxList = [];
      try {
        const res = await fetch(`${API_BASE}/api/tmux/sessions`);
        tmuxList = await res.json();
      } catch (_) { /* 서버 오류 시 빈 목록 */ }

      if (!Array.isArray(tmuxList) || tmuxList.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'vt-menu-item';
        empty.style.cssText = 'opacity:.6;cursor:default;';
        empty.textContent = '실행 중인 tmux 세션 없음';
        menu.appendChild(empty);
      } else {
        for (const s of tmuxList) menu.appendChild(buildTmuxRow(menu, s));
      }

      const sep = document.createElement('div');
      sep.className = 'vt-menu-sep';
      menu.appendChild(sep);
      const newItem = document.createElement('div');
      newItem.className = 'vt-menu-item new';
      newItem.textContent = '+ 새 tmux 세션';
      newItem.onclick = async () => { menu.remove(); await createTmuxSession(); };
      menu.appendChild(newItem);
    }

    // 세션 한 줄: [상태·이름 → 깨우기/전환]  [🗑 완전 종료(2단계 확인)]
    function buildTmuxRow(menu, s) {
      const row = document.createElement('div');
      row.className = 'vt-menu-item';
      row.style.cssText = 'display:flex;align-items:center;gap:8px;';

      const openInWeb = !!s.web_session_id;
      const badge = openInWeb ? '🟢' : (s.attached > 0 ? '🖥️' : '💤');
      const statusText = openInWeb ? '웹에 열림' : (s.attached > 0 ? '데스크톱 attach' : '잠듦');

      const label = document.createElement('span');
      label.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      const cmd = s.command ? ` · ${s.command}` : '';
      label.textContent = `${badge} ${s.name}  (${s.windows}win · ${statusText}${cmd})`;
      label.title = openInWeb ? '이 탭으로 전환' : '깨워서 열기 (attach)';
      label.onclick = async () => { menu.remove(); await attachTmux(s.name); };
      row.appendChild(label);

      // 완전 종료 — 2단계 인라인 확인 (실수 방지, 네이티브 dialog 미사용)
      const kill = document.createElement('button');
      const reset = () => {
        kill.textContent = '🗑'; kill.style.color = 'var(--sub)'; kill.style.fontSize = '14px';
      };
      kill.title = '완전 종료 (tmux 세션 kill — 되돌릴 수 없음)';
      kill.style.cssText = 'flex-shrink:0;background:transparent;border:none;cursor:pointer;padding:2px 6px;border-radius:5px;';
      reset();
      let armed = false, armTimer = null;
      kill.onclick = async (e) => {
        e.stopPropagation();
        if (!armed) {
          armed = true;
          kill.textContent = '종료?'; kill.style.color = 'var(--err)'; kill.style.fontSize = '11px';
          armTimer = setTimeout(() => { armed = false; reset(); }, 3000);
          return;
        }
        clearTimeout(armTimer);
        kill.textContent = '…';
        await killTmuxSession(s.name, s.web_session_id);
        if (document.body.contains(menu)) await renderTmuxMenu(menu);
      };
      row.appendChild(kill);
      return row;
    }

    // tmux 세션 완전 종료. 웹에 열린 탭이 있으면 먼저 정리해 무한 재연결을 막는다.
    async function killTmuxSession(name, webSessionId) {
      // 서버 kill이 웹 PTY까지 destroy하므로, 열린 탭을 그대로 두면 WS가 끊긴 뒤
      // 재연결 루프에 빠진다. 클라이언트 탭을 먼저 정리(= detach)한 뒤 kill한다.
      if (webSessionId && sessions[webSessionId]) {
        await removeSession(webSessionId);
      }
      try {
        const res = await fetch(`${API_BASE}/api/tmux/kill/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (!res.ok) { showToast(`완전 종료 실패: ${name} (${res.status})`); return false; }
        showToast(`완전 종료됨: ${name}`);
        return true;
      } catch (_) {
        showToast(`완전 종료 오류: ${name}`);
        return false;
      }
    }

    async function attachTmux(tmuxName) {
      const res = await fetch(`${API_BASE}/api/tmux/attach`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: tmuxName }),
      });
      const data = await res.json();
      // 이미 웹에 열려 있으면 해당 탭으로 전환
      if (data.id in sessions) {
        switchTo(data.id);
      } else {
        addSession(data.id, data.name?.replace('tmux:', '') || data.id);
      }
    }

    async function createTmuxSession() {
      const res = await fetch(`${API_BASE}/api/tmux/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      addSession(data.id, data.name?.replace('tmux:', '') || data.id);
    }

    // 시작 시: URL hash에 #tmux=<name>이 있으면 해당 세션 우선 attach (handoff 링크)
    //         → localStorage 워크스페이스 복원 (Phase 8 G7)
    //         → 기존 웹 세션 복원 → tmux 세션 자동 attach → 온보딩
    (async () => {
      try {
        // 0. handoff 링크 (#tmux=<name>) 처리
        const hashParams = new URLSearchParams(location.hash.slice(1));
        const targetTmux = hashParams.get('tmux');
        if (targetTmux) {
          await attachTmux(targetTmux);
          return;
        }

        // 0.5. localStorage 워크스페이스 복원 (Phase 8 G7)
        if (await restoreWorkspace()) {
          return;
        }

        // 1. 기존 웹 세션 복원
        const res = await fetch(`${API_BASE}/api/sessions`);
        const existing = await res.json();
        if (existing.length > 0) {
          for (const s of existing) addSession(s.id, s.name || s.id);
          return;
        }

        // 2. tmux 세션이 있으면 첫 번째 자동 attach
        const tmuxRes = await fetch(`${API_BASE}/api/tmux/sessions`);
        const tmuxList = await tmuxRes.json();
        if (tmuxList.length > 0) {
          await attachTmux(tmuxList[0].name);
          return;
        }

        // 3. 아무것도 없으면 온보딩 표시
        showOnboarding();
      } catch (e) {
        createSession();
      }
    })();

    function showOnboarding() {
      const el = document.createElement('div');
      el.id = 'onboarding';
      el.className = 'vt-onboarding';
      el.innerHTML = `
        <div class="vt-ob-icon"><i class="icon-mic"></i></div>
        <h2>Voice Terminal</h2>
        <p>음성으로 터미널을 조작하세요.<br>tmux 세션을 만들거나, 새 터미널을 시작할 수 있습니다.</p>
        <div class="vt-ob-actions">
          <button class="vt-btn-primary" onclick="document.getElementById('onboarding').remove();createTmuxSession()">tmux 세션 시작</button>
          <button class="vt-btn-secondary" onclick="document.getElementById('onboarding').remove();createSession()">일반 터미널</button>
        </div>
        <p class="vt-ob-hint">맥북에서 Ctrl+Shift+V로 음성 입력 (voice daemon 실행 시)</p>
      `;
      document.body.appendChild(el);
    }
