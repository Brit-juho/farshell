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

    // ─────────────────────────────────────────────────────────────
    // 클립보드: 복사(선택 자동복사/우클릭/단축키) · 붙여넣기 · 이미지 붙여넣기 업로드
    // ─────────────────────────────────────────────────────────────

    // 시스템 클립보드에 쓰기. HTTPS/localhost가 아니면 clipboard API가 막히므로
    // execCommand('copy') 폴백을 둔다.
    async function copyToClipboard(text) {
      if (!text) return false;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          return true;
        }
      } catch (_) { /* 폴백으로 */ }
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
        document.body.appendChild(ta);
        ta.focus(); ta.select();
        const ok = document.execCommand('copy');
        ta.remove();
        return ok;
      } catch (_) { return false; }
    }

    async function readClipboardText() {
      try {
        if (navigator.clipboard && navigator.clipboard.readText) {
          return await navigator.clipboard.readText();
        }
      } catch (_) { /* 권한/비보안 컨텍스트 */ }
      return null;
    }

    // 텍스트를 활성 세션 PTY로 주입 (붙여넣기 공통 경로)
    function sendToPty(id, text) {
      if (!text) return;
      const handle = sessions[id]?.wsHandle;
      if (handle && handle.readyState === WebSocket.OPEN) {
        handle.send(new TextEncoder().encode(text));
      }
    }

    async function pasteFromClipboard(id) {
      const text = await readClipboardText();
      if (text == null) {
        showToast('클립보드 읽기 불가 — HTTPS/localhost에서만 가능. Cmd/Ctrl+V를 쓰세요.');
        return;
      }
      // term.paste()는 앱이 bracketed paste 모드면 마커로 감싼다 — 멀티라인 붙여넣기가
      // 셸에서 줄마다 즉시 실행되는 것을 막는다. (raw sendToPty는 그 보호가 없음)
      const term = sessions[id]?.term;
      if (term && typeof term.paste === 'function') term.paste(text);
      else sendToPty(id, text);
    }

    // 이미지 붙여넣기 → 서버 업로드 → 저장 경로를 터미널에 삽입 (Claude에 그대로 넘길 수 있게)
    async function pasteImageUpload(id, file) {
      try {
        showToast('이미지 업로드 중...');
        const ext = ((file.type.split('/')[1] || 'png')).replace('jpeg', 'jpg').replace('svg+xml', 'svg');
        const fd = new FormData();
        fd.append('file', file, `pasted-${Date.now()}.${ext}`);
        const res = await fetch(`${API_BASE}/api/upload?session_id=${encodeURIComponent(id)}`, {
          method: 'POST', body: fd,
        });
        if (!res.ok) { showToast(`이미지 업로드 실패 (${res.status})`); return; }
        const data = await res.json();
        if (data && data.path) {
          sendToPty(id, data.path + ' ');
          showToast('이미지 경로 삽입됨');
        } else {
          showToast('업로드 응답에 경로 없음');
        }
      } catch (_) {
        showToast('이미지 업로드 오류');
      }
    }

    // 한 터미널에 복사/붙여넣기 배선. addSession에서 term.open 직후 호출.
    function wireClipboard(id, term, wrapper) {
      // 1) copy-on-select — 드래그(브라우저 선택) 끝나면 자동 복사.
      //    ⚠ tmux mouse on이면 일반 드래그는 tmux가 가로채므로, 브라우저 선택은
      //    Shift(또는 Option/Alt)+드래그에서 발생한다.
      wrapper.addEventListener('mouseup', () => {
        const sel = term.getSelection && term.getSelection();
        if (sel && sel.trim()) copyToClipboard(sel).then((ok) => { if (ok) showToast('복사됨'); });
      });

      // 2) 우클릭 — 선택 있으면 복사, 없으면 붙여넣기 (PuTTY 스타일)
      wrapper.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const sel = term.getSelection && term.getSelection();
        if (sel && sel.trim()) copyToClipboard(sel).then((ok) => { if (ok) showToast('복사됨'); });
        else pasteFromClipboard(id);
      });

      // 3) 이미지 붙여넣기 — clipboard에 이미지가 있으면 업로드+경로삽입, 아니면 텍스트는
      //    xterm 기본 붙여넣기에 위임(preventDefault 안 함). capture로 textarea보다 먼저 검사.
      wrapper.addEventListener('paste', (e) => {
        const items = (e.clipboardData && e.clipboardData.items) || [];
        for (const it of items) {
          if (it.type && it.type.indexOf('image/') === 0) {
            e.preventDefault();
            const file = it.getAsFile();
            if (file) pasteImageUpload(id, file);
            return;
          }
        }
      }, true);

      // 4) 복사 단축키 — Cmd+C / Ctrl+Shift+C. 선택이 있으면 xterm 내부 선택을 복사하고
      //    이벤트를 소비, 없으면 그대로 통과(Ctrl+C 단독 = SIGINT 유지).
      //    ⚠ 붙여넣기 단축키(Cmd+V/Ctrl+V/Ctrl+Shift+V)는 여기서 다루지 않는다 —
      //    xterm 네이티브 paste 이벤트가 이미 처리하며(아래 3번 capture 리스너가 이미지만
      //    가로챔), keydown에서 또 처리하면 이중 붙여넣기가 된다.
      term.attachCustomKeyEventHandler((e) => {
        if (e.type !== 'keydown') return true;
        const isCopy = (e.metaKey && !e.ctrlKey && e.key.toLowerCase() === 'c')
          || (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'c');
        if (isCopy) {
          const sel = term.getSelection && term.getSelection();
          if (sel && sel.trim()) { copyToClipboard(sel).then((ok) => { if (ok) showToast('복사됨'); }); return false; }
        }
        return true;
      });
    }

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
      // 방어: id 없이 호출되면(서버 오류 응답 등) 유령 탭 + /ws/undefined 무한재연결이
      // 생기므로 무시한다.
      if (!id) { showToast('세션 생성 실패 (id 없음)'); return; }
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
        fontFamily: (window.getVtXtermFont ? window.getVtXtermFont() : "'IBM Plex Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace"),
        theme: (window.getVtXtermTheme ? window.getVtXtermTheme() : { background: '#1e1e2e' }),
        allowProposedApi: true,
        // ⚠ screenReaderMode는 매 write마다 접근성 hidden DOM/live-region을 유지하는데,
        // Claude 같은 TUI가 대량 출력을 스트리밍하면(입력 후 응답) 이 버퍼가 총 출력량에
        // 비례해 커진다 — CDP 실측상 동일 출력에 힙 증가가 ~8배(+1.6MB→+13.6MB). 대부분
        // 사용자는 스크린리더를 안 쓰므로 기본 off로 두고, 필요 시에만 opt-in한다.
        // 켜기: 브라우저 콘솔에서 localStorage.setItem('vt-a11y','1') 후 새로고침.
        screenReaderMode: (() => { try { return localStorage.getItem('vt-a11y') === '1'; } catch (_) { return false; } })(),
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

      // 복사(자동복사/우클릭/단축키) · 붙여넣기 · 이미지 붙여넣기 배선
      wireClipboard(id, term, wrapper);

      // WebSocket URL 구성 — E2E 활성 시 ?e2e=1 (또는 토큰 뒤에 &e2e=1)
      const _wsPath = `/ws/${id}${_tokenQuery}${_e2eQuery}`;

      // sessions[id] 선 초기화 — wrapE2E의 동기 onReady 콜백이 참조할 수 있도록.
      // ws는 connectTerminalWs()에서 채운다.
      sessions[id] = { term, ws: null, tabEl: tab, fitAddon, searchAddon, wrapper, wsHandle: null, reconnTimer: null };

      // ── 소켓 수명주기 (초기 연결 + 자동 재연결 통합) ─────────────────────
      // [회귀 fb827a6] 재연결 상한(retries>=15)을 없애 무한 재시도로 바꾸면서, onopen에서
      // retries를 '즉시' 0으로 리셋하는 로직을 그대로 뒀다. 서버는 세션이 없으면
      // `accept()` 직후 code 4004로 닫는데(=half-open flap), 이때 onopen이 먼저 발화해
      // retries가 0으로 리셋된다 → 지수 백오프가 절대 자라지 못하고 2초마다 영구 재연결.
      // localStorage 워크스페이스가 복원한 죽은 세션 탭들이 서버 재시작 후 이 스톰에 빠지면
      // 매 2초 소켓 생성 + scrollback 재주입 + 접근성 DOM 재도색으로 Chrome 메모리가 폭증한다.
      //
      // 수정: (1) 4001/4004 같은 '재시도해도 동일'한 코드는 재연결하지 않고 중단.
      //       (2) 연결이 STABLE_MS 이상 안정적으로 유지된 뒤에만 백오프 카운터를 리셋.
      const TERMINAL_CLOSE_CODES = new Set([4001, 4004]);
      const STABLE_MS = 3000;
      let _retries = 0;
      let _stableTimer = null;

      function connectTerminalWs() {
        if (!(id in sessions)) return;
        const sock = new WebSocket(`${WS_BASE}${_wsPath}`);
        sock.binaryType = 'arraybuffer';
        sessions[id].ws = sock;

        sock.onopen = () => {
          updateConnStatus(id, true);
          // 재연결이었다면(첫 연결이 아니면) 서버가 scrollback(최대 256KB)을 통째로 재전송한다.
          // reset 없이 write하면 이전 출력이 중복 누적되므로 비운 뒤 깨끗하게 repaint한다.
          if (_retries > 0) { term.reset(); term.write('\x1b[32m[재연결됨]\x1b[0m\r\n'); }
          // 새(재)연결된 PTY는 크기를 모르므로 캐시를 비워 첫 fitAndResize가 반드시 보내게 한다.
          sessions[id]._lastCols = sessions[id]._lastRows = null;
          if (!E2E_ENABLED) fitAndResize(id);
          // STABLE_MS 이상 열려 있어야 백오프를 리셋 — 즉시 리셋하면 accept 직후 닫히는
          // flap에서 지수가 자라지 못해 무한 재연결 스톰이 된다.
          clearTimeout(_stableTimer);
          _stableTimer = setTimeout(() => { _retries = 0; }, STABLE_MS);
        };

        // Phase 8 G2: 서버 ping 응답 (heartbeat pong)
        sock.addEventListener('message', (e) => {
          if (typeof e.data !== 'string') return;
          try {
            const msg = JSON.parse(e.data);
            if (msg && msg.type === 'ping') sock.send(JSON.stringify({ type: 'pong' }));
          } catch (_) { /* binary or non-JSON */ }
        });

        // wrapE2E 가 핸드셰이크 후 handle 을 넘김. E2E 비활성이면 즉시 실행.
        wrapE2E(sock,
          (handle) => {
            sessions[id].wsHandle = handle;
            if (E2E_ENABLED) fitAndResize(id);
          },
          (bytes) => term.write(bytes)
        );

        sock.onclose = (ev) => {
          clearTimeout(_stableTimer);
          updateConnStatus(id, false);
          if (!(id in sessions)) return;                       // 탭 닫힘 → 중단
          const code = ev && ev.code;
          if (TERMINAL_CLOSE_CODES.has(code)) {                // 영구 실패 → 재연결 안 함
            const why = code === 4001 ? '인증 실패' : '세션이 서버에 없음(종료됨)';
            try { term.write(`\r\n\x1b[31m[재연결 중단 — ${why}. 탭을 닫고 새로 여세요.]\x1b[0m\r\n`); } catch (_) {}
            return;
          }
          _retries++;
          // Math.pow(2, retries)는 지수가 커지면 오버플로하므로 지수를 5로 clamp.
          const delay = Math.min(1000 * Math.pow(2, Math.min(_retries, 5)), 30000);
          term.write(`\r\n\x1b[33m[재연결 중... ${_retries}회]\x1b[0m\r\n`);
          sessions[id].reconnTimer = setTimeout(connectTerminalWs, delay);
        };

        sock.onerror = () => { try { sock.close(); } catch (_) {} };
      }

      connectTerminalWs();

      term.onData((data) => {
        const handle = sessions[id]?.wsHandle;
        if (handle && handle.readyState === WebSocket.OPEN) {
          handle.send(new TextEncoder().encode(data));
        }
      });

      // 리사이즈 디바운스 — 모바일 키보드가 뜨고/닫히거나 viewport가 흔들리면 resize가
      // 연속으로 쏟아진다. 매 이벤트마다 fit+sendResize하면 PTY가 SIGWINCH 폭탄을 맞아
      // TUI가 계속 전체 재도색(대량 출력)을 하고, 입력 중 메모리가 급증한다. 120ms로 합친다.
      let _resizeTimer = null;
      const onResize = () => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => fitAndResize(id), 120);
      };
      window.addEventListener('resize', onResize);

      // 모바일: visualViewport resize (키보드 나타날 때)
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', onResize);
      }

      // sessions[id]는 WebSocket 생성 직후 선 초기화됨. onResize만 추가.
      sessions[id].onResize = onResize;
      switchTo(id);
    }

    function sendResize(ws, term, s) {
      if (ws.readyState !== WebSocket.OPEN) return;
      // 같은 크기를 다시 보내면 PTY가 SIGWINCH를 받아 Claude 같은 TUI가 화면 전체를
      // 다시 그린다(대량 출력). fitAndResize가 resize·focus·탭전환마다 호출되므로,
      // 실제로 cols/rows가 바뀐 경우에만 보내 불필요한 전체 재도색을 없앤다.
      if (s && s._lastCols === term.cols && s._lastRows === term.rows) return;
      if (s) { s._lastCols = term.cols; s._lastRows = term.rows; }
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
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
      if (w && w.readyState === WebSocket.OPEN) sendResize(w, s.term, s);
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
      // 대기 중인 재연결 타이머 취소 — 안 그러면 탭을 닫은 뒤에도 setTimeout이 살아남아
      // (id는 이미 delete되지만) 죽은 타이머가 지연 후 깨어난다.
      if (s.reconnTimer) { clearTimeout(s.reconnTimer); s.reconnTimer = null; }
      if (s.ws) { try { s.ws.close(); } catch (_) {} }
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
      // 서버가 세션 없음(404) 등을 돌려주면 data.id가 없다. 그대로 addSession(undefined)하면
      // /ws/undefined로 무한 재연결하는 유령 탭이 생기므로 여기서 차단한다.
      if (!res.ok) { showToast(`세션 열기 실패: ${tmuxName} (${res.status})`); return; }
      const data = await res.json();
      if (!data.id) { showToast(`세션 열기 실패: ${tmuxName}`); return; }
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
      if (!res.ok) { showToast(`tmux 세션 생성 실패 (${res.status})`); return; }
      const data = await res.json();
      if (!data.id) { showToast('tmux 세션 생성 실패'); return; }
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
