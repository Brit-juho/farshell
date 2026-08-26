    const sessions = {};
    let activeId = null;
    const _urlParams = new URLSearchParams(location.search);
    const _hashParams = new URLSearchParams(location.hash.slice(1));
    let VT_TOKEN = _urlParams.get('token') || '';
    let _tokenQuery = VT_TOKEN ? `?token=${VT_TOKEN}` : '';
    let _tokenParam = VT_TOKEN ? `&token=${VT_TOKEN}` : '';
    // [fix 2026-08-19] WS 쿼리는 이 두 변수를 문자열로 이어붙이지 않고 항상
    // _wsQuery()로 그때그때 조립한다 — _e2eQuery를 '?'/'&' 구분자까지 미리
    // const로 굳혀두면, 토큰 교환(_exchangeTokenForCookie)이 나중에
    // _tokenQuery만 비우고 _e2eQuery는 못 건드려서 `/ws/id&e2e=1`처럼 구분자가
    // 깨진 URL이 나갔다(E2E가 실제로는 안 켜지는 버그).

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

    // WS 경로용 쿼리 문자열을 호출 시점의 VT_TOKEN/E2E_ENABLED로 항상 새로
    // 조립한다. URLSearchParams를 쓰므로 구분자(?/&)를 손으로 이어붙이다
    // 상태가 어긋나는 문제(위 주석 참조)가 구조적으로 재발하지 않는다.
    function _wsQuery() {
      const params = new URLSearchParams();
      if (VT_TOKEN) params.set('token', VT_TOKEN);
      if (E2E_ENABLED) params.set('e2e', '1');
      const qs = params.toString();
      return qs ? `?${qs}` : '';
    }

    // base64url ↔ Uint8Array
    function _b64uDec(s) {
      const pad = '='.repeat((4 - s.length % 4) % 4);
      return nacl.util.decodeBase64(s.replace(/-/g, '+').replace(/_/g, '/') + pad);
    }
    function _b64uEnc(bytes) {
      return nacl.util.encodeBase64(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    // TOFU 핀닝: 서버 장기 identity 공개키를 호스트별로 localStorage에 저장.
    // Fix 1 (능동 MITM 방어) — ephemeral 키(nacl.box)는 세션마다 바뀌므로 그 자체로는
    // "진짜 서버"임을 증명하지 못한다. 서버가 안정적인 Ed25519 identity 키로 ephemeral
    // 공개키에 서명해 보내면, 클라이언트는 그 서명을 검증하고 identity_pub을 첫 접속 때
    // 신뢰(Trust On First Use)한 뒤 이후 접속마다 같은 키인지 대조한다.
    function _e2eTofuKey() {
      return `vt_e2e_identity_pub:${location.hostname}`;
    }
    function _e2eGetPinned() {
      try { return localStorage.getItem(_e2eTofuKey()); } catch { return null; }
    }
    function _e2eSetPinned(identityPub) {
      try { localStorage.setItem(_e2eTofuKey(), identityPub); } catch { /* localStorage 불가 — 핀닝 생략 */ }
    }
    // 사용자가 재신뢰를 명시적으로 승인했을 때만 핀을 갱신한다 (silent fallback 금지).
    function _e2eConfirmRetrust(newIdentityPub) {
      return window.confirm(
        '⚠️ 이 서버의 E2E 암호화 identity 키가 이전과 다릅니다.\n' +
        '서버를 재설치했거나 키를 재발급했다면 정상이지만, 중간자 공격(MITM)일 수도 있습니다.\n\n' +
        '새 키를 신뢰하고 계속하시겠습니까? (신뢰하지 않으면 연결을 종료합니다)'
      );
    }

    // E2E WebSocket 래퍼 — 핸드셰이크 후 encrypt/decrypt 자동 처리
    // onReady(handle) 에서 handle은 { send(bytes), close(), readyState } 형태 (원본 WS 인터페이스와 호환)
    // onE2EError(msg)가 있으면 핸드셰이크 실패(서명 불일치/재신뢰 거부) 시 호출된다.
    function wrapE2E(ws, onReady, onData, onE2EError) {
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
      const fail = (msg) => {
        console.error('[E2E]', msg);
        try { ws.close(); } catch {}
        if (typeof onE2EError === 'function') onE2EError(msg);
        else alert('E2E 암호화 핸드셰이크 실패: ' + msg);
      };
      let sharedKey = null;
      ws.addEventListener('message', (e) => {
        // 핸드셰이크: 첫 텍스트로 서버 공개키 수신
        if (typeof e.data === 'string' && !sharedKey) {
          let msg;
          try { msg = JSON.parse(e.data); } catch { return; }
          if (msg.type !== 'e2e-hello' || !msg.pub) return;

          // identity_pub/sig가 없는 서버(구버전) — 서명 검증 없이 진행하면 능동 MITM을
          // 막을 수 없으므로 명시적으로 거부한다. 조용한 평문 폴백은 하지 않는다.
          if (!msg.identity_pub || !msg.sig) {
            fail('서버가 identity 서명을 보내지 않았습니다 (구버전 서버 또는 변조된 응답)');
            return;
          }
          let ephemeralPub, identityPub, sig;
          try {
            ephemeralPub = _b64uDec(msg.pub);
            identityPub = _b64uDec(msg.identity_pub);
            sig = _b64uDec(msg.sig);
          } catch {
            fail('e2e-hello 페이로드 디코딩 실패');
            return;
          }
          // Ed25519 서명 검증: identity_pub이 실제로 이 ephemeral 공개키에 서명했는가.
          if (!nacl.sign.detached.verify(ephemeralPub, sig, identityPub)) {
            fail('서명 검증 실패 — ephemeral 공개키가 identity 키로 서명되지 않았습니다 (MITM 의심)');
            return;
          }
          // TOFU 핀닝: 첫 접속이면 신뢰하고 저장, 이후 접속은 대조.
          const pinned = _e2eGetPinned();
          if (!pinned) {
            _e2eSetPinned(msg.identity_pub);
          } else if (pinned !== msg.identity_pub) {
            if (_e2eConfirmRetrust(msg.identity_pub)) {
              _e2eSetPinned(msg.identity_pub);
            } else {
              fail('identity 키 변경을 사용자가 거부했습니다 — 연결을 중단합니다');
              return;
            }
          }

          const kp = nacl.box.keyPair();
          const serverPub = ephemeralPub;
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
      // 이미지 우선 — Ctrl+Shift+V / 우클릭 붙여넣기는 네이티브 paste 이벤트를
      // 안 거치므로(그쪽은 Cmd+V/Ctrl+V 전용), 여기서 async Clipboard API로
      // 이미지를 직접 읽어 업로드한다. read()는 HTTPS/localhost(보안 컨텍스트)에서만
      // 되므로 실패하면 조용히 텍스트 붙여넣기로 폴백한다.
      try {
        if (navigator.clipboard && navigator.clipboard.read) {
          const items = await navigator.clipboard.read();
          for (const it of items) {
            const imgType = it.types.find((t) => t.indexOf('image/') === 0);
            if (imgType) {
              const blob = await it.getType(imgType);
              pasteImageUpload(id, new File([blob], 'pasted', { type: imgType }));
              return;
            }
          }
        }
      } catch (_) { /* 권한/비보안 컨텍스트 — 텍스트 폴백 */ }
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
    // 모바일 터치 스크롤 — 손가락 세로 드래그를 휠 이벤트로 번역한다.
    //
    // 왜 필요한가: tmux가 `set -g mouse on`(config/vt-tmux.conf)이라 터미널이 마우스
    // 트래킹 모드로 들어간다(term.modes.mouseTrackingMode !== 'none'). 그러면 xterm.js는
    // 포인터 입력을 뷰포트 스크롤이 아니라 '앱(tmux)'으로 넘긴다. 데스크톱은 휠이 있어서
    // tmux가 그걸 copy-mode 스크롤로 번역해주지만, 터치에는 휠이 없다 → 폰에서 화면을
    // 위아래로 못 넘기는 상태가 된다.
    //
    // 그래서 세로 드래그를 감지해 직접 휠 이벤트를 합성한다. 인코딩은 xterm.js가 하던
    // 대로 맡기므로 데스크톱 휠과 동작이 정확히 같아진다(tmux copy-mode 진입 → 스크롤).
    // 마우스 트래킹이 꺼진 세션(일반 셸)에서는 앱에 보낼 곳이 없으므로 xterm 자체
    // 스크롤백을 직접 움직인다.
    function wireTouchScroll(id, term, wrapper) {
        // 한 노치로 칠 이동 거리(px). 너무 작으면 손 떨림에 스크롤이 튄다.
        const NOTCH_PX = 18;
        // 이 정도는 움직여야 '스크롤 의도'로 본다. 탭(포커스/키보드)을 막지 않기 위함.
        const SLOP = 8;

        let startX = 0, startY = 0, lastY = 0, acc = 0, engaged = false, multi = false;

        wrapper.addEventListener('touchstart', (e) => {
          multi = e.touches.length > 1;
          if (multi) return;
          startX = e.touches[0].clientX;
          startY = lastY = e.touches[0].clientY;
          acc = 0;
          engaged = false;
        }, { passive: true });

        wrapper.addEventListener('touchmove', (e) => {
          if (multi || e.touches.length !== 1) return;
          const y = e.touches[0].clientY;
          const x = e.touches[0].clientX;

          if (!engaged) {
            const dy = Math.abs(y - startY);
            const dx = Math.abs(x - startX);
            if (dy < SLOP) return;        // 아직 탭인지 드래그인지 모른다
            if (dx > dy) return;          // 가로 제스처는 앱/선택에 맡긴다
            engaged = true;
          }

          // 여기서부터는 스크롤 제스처다. 앱으로 드래그가 새면 tmux가 선택을 시작한다.
          e.preventDefault();

          acc += y - lastY;
          lastY = y;

          while (Math.abs(acc) >= NOTCH_PX) {
            const dir = acc > 0 ? 1 : -1;   // +1 = 손가락을 아래로 = 위(과거)로 스크롤
            acc -= dir * NOTCH_PX;
            _scrollNotch(term, wrapper, dir);
          }
        }, { passive: false });

        wrapper.addEventListener('touchend', () => { engaged = false; multi = false; }, { passive: true });
        wrapper.addEventListener('touchcancel', () => { engaged = false; multi = false; }, { passive: true });
    }

    function _scrollNotch(term, wrapper, dir) {
      const tracking = term.modes && term.modes.mouseTrackingMode
        && term.modes.mouseTrackingMode !== 'none';
      if (tracking) {
        // 앱이 마우스를 잡고 있다 → 휠로 넘겨 데스크톱과 같은 경로를 타게 한다.
        const scr = wrapper.querySelector('.xterm-screen');
        if (!scr) return;
        const r = scr.getBoundingClientRect();
        scr.dispatchEvent(new WheelEvent('wheel', {
          deltaY: -dir * 120,             // 손가락 아래로(dir=+1) → 휠 업
          deltaMode: 0,
          clientX: r.left + Math.min(40, r.width / 2),
          clientY: r.top + Math.min(40, r.height / 2),
          bubbles: true,
          cancelable: true,
        }));
      } else {
        // 앱이 마우스를 안 잡는다(일반 셸) → xterm 자체 스크롤백을 움직인다.
        try { term.scrollLines(-dir * 3); } catch (_) {}
      }
    }

    function wireClipboard(id, term, wrapper) {
      // 1) copy-on-select — 드래그(브라우저 선택) 끝나면 자동 복사.
      //    ⚠ tmux mouse on이면 일반 드래그는 tmux가 가로채므로, 브라우저 선택은
      //    Shift(또는 Option/Alt)+드래그에서 발생한다.
      //    "⋯ → 설정 → 드래그 시 자동 복사"로 끌 수 있다 — 꺼도 선택 자체는 그대로
      //    되고(브라우저 네이티브), 실제 복사만 우클릭으로 넘어간다.
      wrapper.addEventListener('mouseup', () => {
        if ((localStorage.getItem('vt_autocopy_on_select') ?? 'on') === 'off') return;
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

      // 4) 붙여넣기 단축키 — Ctrl+Shift+V 하나로 통일 (크로스플랫폼 안전 경로).
      //    · Cmd+V(Mac)는 위 3)의 브라우저 네이티브 paste 이벤트로 처리된다(이미지 포함)
      //      — 여기서 안 건드린다.
      //    · 순수 Ctrl+V는 그대로 pty로 흘려보낸다 — bash readline(quoted-insert),
      //      vim(visual-block) 등 터미널 프로그램이 실제로 쓰는 키라 가로채면 깨진다.
      //    복사 단축키(예전 Cmd+C / Ctrl+Insert)와 붙여넣기 Shift+Insert는 제거했다:
      //    드래그하면 이미 자동 복사(copy-on-select)되고, 수동 복사/붙여넣기는 우클릭으로
      //    되므로 중복이라 오히려 혼란만 준다.
      //    ⚠ Ctrl+Shift+V는 Chrome/Firefox 등에서 "서식 없이 붙여넣기" 네이티브 단축키와
      //    겹친다 — preventDefault()로 먼저 막지 않으면 이중 붙여넣기가 된다.
      term.attachCustomKeyEventHandler((e) => {
        if (e.type !== 'keydown') return true;
        if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'v') {
          e.preventDefault();
          pasteFromClipboard(id);
          return false;
        }
        // 코드 뷰어 토글. Ctrl+B는 tmux prefix라 절대 쓰지 않는다(viewer.js 참고).
        if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'e') {
          e.preventDefault();
          showViewer();
          return false;
        }
        return true;
      });

      // 5) OSC 52 — 서버 쪽 프로그램(vim, tmux copy-mode 등)이 클립보드에 쓰려는
      //    요청은 PTY 출력 스트림에 이스케이프 시퀀스로 실려 이미 여기까지 온다.
      //    별도 연결 없이 가로채서 이 브라우저(=이 기기)의 시스템 클립보드에 반영.
      //    "?"(쿼리) 응답은 미지원 — set 요청만 처리.
      if (term.parser && typeof term.parser.registerOscHandler === 'function') {
        term.parser.registerOscHandler(52, (data) => {
          const semi = data.indexOf(';');
          const payload = semi >= 0 ? data.slice(semi + 1) : data;
          if (!payload || payload === '?') return true;
          // tmux가 `set-clipboard external`이면 일반 드래그도 자체 copy-mode를 거쳐
          // 여기로 OSC52를 쏜다(키보드로 하는 vim/tmux copy-mode 복사도 동일 경로라
          // 드래그만 따로 구분할 수 없음) — "드래그 시 자동 복사" 토글을 꺼도 이 경로가
          // 살아있으면 사용자 입장에선 "꺼도 계속 복사된다"로 보이므로 같은 설정을 공유한다.
          if ((localStorage.getItem('vt_autocopy_on_select') ?? 'on') === 'off') return true;
          try {
            const bin = atob(payload);
            const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
            const text = new TextDecoder('utf-8').decode(bytes);
            copyToClipboard(text).then((ok) => { if (ok) showToast('클립보드 동기화됨 (OSC52)'); });
          } catch (_) { /* 잘못된 base64 무시 */ }
          return true;
        });
      }
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
        addSession(data.id, data.name || data.id);
        // ⚠ tmuxName을 안 채우면 openSessionOnMac()의 tmuxName 가드가 항상 실패해
        // "이 세션은 tmux 세션이 아니라 맥에서 열 수 없습니다"를 잘못 띄운다 —
        // 실제로는 진짜 tmux 세션인데도(restoreWorkspace 경로는 이걸 항상 채워왔음).
        if (sessions[data.id]) sessions[data.id].tmuxName = data.tmux_session;
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

    // + 버튼: 일반 터미널 / tmux 중 선택하는 드롭다운. (기존 createSession은
    // 온보딩·auto-mac 호환을 위해 그대로 둔다.)
    function showAddMenu(e) {
      if (e) e.stopPropagation();
      // 토글: 이미 열려 있으면 닫기
      const existing = document.getElementById('add-menu');
      if (existing) { existing.remove(); return; }

      const menu = document.createElement('div');
      menu.id = 'add-menu';
      menu.className = 'vt-menu';
      // + 버튼 바로 아래에 정렬 (기본 .vt-menu는 우측 고정이라 left로 재배치)
      const btn = document.getElementById('add-btn');
      const r = btn ? btn.getBoundingClientRect() : { left: 8, bottom: 44 };
      menu.style.right = 'auto';
      menu.style.left = `${Math.round(r.left)}px`;
      menu.style.top = `${Math.round(r.bottom + 6)}px`;
      menu.style.minWidth = '200px';

      const mkItem = (label, hint, onClick) => {
        const it = document.createElement('div');
        it.className = 'vt-menu-item';
        it.innerHTML = `<div>${label}</div><div style="opacity:.55;font-size:11px;margin-top:2px;">${hint}</div>`;
        it.onclick = () => { menu.remove(); onClick(); };
        return it;
      };
      menu.appendChild(mkItem('일반 터미널', '단발 셸 (tmux 아님)', createPlainSession));
      menu.appendChild(mkItem('tmux 세션', 'detach 유지 · 맥/모바일 공유', createTmuxSession));
      document.body.appendChild(menu);

      setTimeout(() => {
        document.addEventListener('click', function _close(ev) {
          if (!document.body.contains(menu)) { document.removeEventListener('click', _close); return; }
          if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', _close); }
        });
      }, 0);
    }

    // 일반(비 tmux) 터미널 세션 생성
    async function createPlainSession() {
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (!res.ok) { showToast(`세션 생성 실패 (${res.status})`); return; }
      const { id } = await res.json();
      if (id) addSession(id);
    }

    function addSession(id, displayName, insertBeforeId) {
      // 방어: id 없이 호출되면(서버 오류 응답 등) 유령 탭 + /ws/undefined 무한재연결이
      // 생기므로 무시한다.
      if (!id) { showToast('세션 생성 실패 (id 없음)'); return; }
      // 빈 상태 온보딩이 떠 있으면 제거 — 안 그러면 새 터미널이 온보딩 뒤에 가려져
      // 탭은 생겼는데 이동/조작이 안 되는 것처럼 보인다.
      document.getElementById('onboarding')?.remove();
      const tab = document.createElement('div');
      tab.className = 'tab';
      tab.dataset.sessionId = id;
      // 좌우 이동 단축키 안내(호버 툴팁)
      tab.title = '탭 이동: Ctrl/Cmd + Shift + ← / →';
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
        const originalName = nameSpan.textContent;
        nameSpan.contentEditable = 'true';
        nameSpan.focus();
        const finishEdit = async () => {
          nameSpan.contentEditable = 'false';
          await renameSession(id, nameSpan.textContent, originalName);
        };
        nameSpan.onblur = finishEdit;
        nameSpan.onkeydown = (ke) => { if (ke.key === 'Enter') { ke.preventDefault(); nameSpan.blur(); } };
      };
      // insertBeforeId가 주어지고 아직 DOM에 있으면 그 앞에 삽입 — 복원 시 원래
      // 탭 순서를 지키기 위함(없거나 이미 사라졌으면 기존처럼 끝에 append).
      const insertBeforeTab = insertBeforeId
        ? document.querySelector(`#tabs .tab[data-session-id="${insertBeforeId}"]`)
        : null;
      if (insertBeforeTab) {
        document.getElementById('tabs').insertBefore(tab, insertBeforeTab);
      } else {
        document.getElementById('tabs').appendChild(tab);
      }

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
      // 이모지 등 wide 문자 폭 계산을 최신 유니코드로 전환 — 없으면 xterm.js 기본값(V6
      // 테이블)이 최근 이모지를 좁은 문자(1칸)로 오판해, 폰트가 실제로는 2칸 너비로
      // 그리면서 다음 글자와 겹쳐 보인다(원격 웹 터미널에서만 발생, 로컬 Terminal.app/
      // iTerm2는 자체 폰트 렌더러라 폭 판정과 렌더링이 항상 일치해 영향 없음).
      if (window.Unicode11Addon) {
        term.loadAddon(new Unicode11Addon.Unicode11Addon());
        term.unicode.activeVersion = '11';
      }

      // 각 세션에 고유 wrapper div 생성 (show/hide로 탭 전환)
      const wrapper = document.createElement('div');
      wrapper.id = `term-${id}`;
      wrapper.style.cssText = 'height:100%;display:none;';
      document.getElementById('terminal-container').appendChild(wrapper);

      term.open(wrapper);

      // GPU 렌더러 — 기본 DOM 렌더러는 활발한 스트리밍(TUI 등)에서 매 write마다 DOM
      // 노드를 갱신해 CPU/메모리 비용이 크다. WebGL 우선, 실패(GPU/드라이버 미지원 또는
      // context-lost) 시 Canvas로, 그마저 실패하면 DOM 그대로 유지한다(기능 저하 없음,
      // 성능만 낮음). WebGL/Canvas addon은 term.open() '이후'(DOM 부착 후)에만 로드 가능.
      try {
        if (window.WebglAddon) {
          const webgl = new WebglAddon.WebglAddon();
          webgl.onContextLoss(() => { try { webgl.dispose(); } catch (_) {} });
          term.loadAddon(webgl);
        } else if (window.CanvasAddon) {
          term.loadAddon(new CanvasAddon.CanvasAddon());
        }
      } catch (_) {
        try { if (window.CanvasAddon) term.loadAddon(new CanvasAddon.CanvasAddon()); } catch (_) {}
      }

      // 복사(자동복사/우클릭/단축키) · 붙여넣기 · 이미지 붙여넣기 배선
      wireClipboard(id, term, wrapper);

      // 모바일 터치 스크롤 (tmux mouse on 이면 xterm이 터치를 앱으로 넘겨 스크롤이 죽는다)
      wireTouchScroll(id, term, wrapper);

      // WebSocket URL 구성 — E2E 활성 시 ?e2e=1 (토큰 있으면 ?token=...&e2e=1)
      const _wsPath = `/ws/${id}${_wsQuery()}`;

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
          // _lastFitW/H도 같이 비워야 한다 — 컨테이너 픽셀 크기가 그대로면 fitAndResize의
          // fit() 자체를 건너뛰어 sendResize까지 도달 못 하고 새 PTY에 크기를 못 알린다.
          sessions[id]._lastCols = sessions[id]._lastRows = null;
          sessions[id]._lastFitW = sessions[id]._lastFitH = null;
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
          // ⚠ 탭을 사용자가 직접 닫은 경우(removeSession이 sessions[id]를 이미 delete)에도
          // 이 close 이벤트가 큐잉돼 나중에 실행된다. 예전엔 이 체크보다 먼저
          // updateConnStatus(id, false)를 불러 "서버 연결 끊김" 전체 화면 오버레이가
          // 잠깐이라도 무조건 떴다 — 의도적으로 닫은 건데 마치 네트워크가 끊긴 것처럼
          // 보였다. 탭 닫힘 여부를 먼저 확인해 그 경우엔 아예 아무 것도 안 한다.
          if (!(id in sessions)) return;                       // 탭 닫힘 → 중단
          updateConnStatus(id, false);
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
          // keybar의 sticky Ctrl이 armed면 소프트 키보드로 친 문자에 Ctrl 조합 적용.
          handle.send(new TextEncoder().encode(applyStickyMod(data)));
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
      // ⚠ fitAddon.fit()은 호출될 때마다 무조건 dimension을 재계산하고, xterm.js 내부적으로
      // (this._terminal.rows/cols가 계산값과 조금이라도 다르면) _renderService.clear()를
      // 실행한다 — 문자 아틀라스(glyph 캐시) 폐기 + 재생성으로, xterm.js 자체 이슈(#955)에서도
      // "비용이 크다"고 명시된 작업이다. 아래 _lastCols/_lastRows 가드는 서버로 보내는 WS
      // 메시지만 막을 뿐 이 내부 fit() 호출 자체는 막지 못해서, 탭 전환/포커스마다(피사체 크기가
      // 실제로는 그대로인데) 서브픽셀 반올림 오차만으로도 매번 아틀라스가 갈아엎어질 수 있다.
      // 컨테이너의 실제 픽셀 크기가 안 바뀌었으면 fit() 자체를 건너뛴다.
      const cw = s.wrapper.clientWidth, ch = s.wrapper.clientHeight;
      if (s._lastFitW === cw && s._lastFitH === ch) return;
      s._lastFitW = cw; s._lastFitH = ch;
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

    // 탭 더블클릭과 모바일 세션 관리 시트가 같은 경로로 이름을 바꾼다.
    // 성공한 경우에만 탭·피커·워크스페이스를 함께 동기화한다.
    async function renameSession(id, rawName, previousNameOverride) {
      const s = sessions[id];
      const newName = String(rawName || '').trim();
      if (!s || !newName) return false;
      const nameEl = s.tabEl?.querySelector('.tab-name');
      const previousName = previousNameOverride ?? (nameEl ? nameEl.textContent : '');
      if (newName === previousName) return true;
      try {
        const res = await fetch(`${API_BASE}/api/sessions/${id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: newName }),
        });
        if (!res.ok) throw new Error('rename failed');
        if (nameEl) nameEl.textContent = newName;
        updateSessionPicker();
        saveWorkspace();
        return true;
      } catch (_) {
        if (nameEl) nameEl.textContent = previousName;
        if (typeof showToast === 'function') showToast('세션 이름 변경 실패', 'error');
        return false;
      }
    }

    // 탭(터미널 섹션) 좌/우 이동. DOM 순서(= 화면 순서) 기준으로 끝에서 순환한다.
    function switchTabByOffset(delta) {
      const tabEls = Array.from(document.querySelectorAll('#tabs .tab'));
      if (tabEls.length < 2) return;
      let idx = tabEls.findIndex((t) => t.dataset.sessionId === activeId);
      if (idx === -1) idx = 0;
      const next = (idx + delta + tabEls.length) % tabEls.length;
      const nid = tabEls[next].dataset.sessionId;
      if (nid && nid !== activeId) switchTo(nid);
    }

    // 단축키로 터미널 섹션 좌우 이동: Ctrl/Cmd + Shift + ← / →.
    // xterm이 키를 먹기 전에 잡아야 하므로 document의 capture 단계에서 처리하고,
    // stopPropagation으로 PTY까지 흘러가지 않게 막는다(포커스 위치와 무관하게 동작).
    document.addEventListener('keydown', (e) => {
      if (!e.shiftKey || !(e.ctrlKey || e.metaKey) || e.altKey) return;
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      // 검색창·탭 이름 편집 중에는 텍스트 선택(Shift+화살표)을 방해하지 않는다.
      const ae = document.activeElement;
      if (ae && (ae.id === 'search-input' || ae.isContentEditable)) return;
      e.preventDefault();
      e.stopPropagation();
      switchTabByOffset(e.key === 'ArrowLeft' ? -1 : 1);
    }, true);

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
        let failed = 0;
        let firstNewId = null;
        // tmux_name이 없는(순수 PTY) 탭은 서버가 재시작되지 않은 한 session_store에
        // 그대로 살아있을 수 있다 — /api/sessions로 살아있는 id 목록을 한 번에 확인.
        let liveWebIds = new Set();
        try {
          const wsRes = await fetch(`${API_BASE}/api/sessions`);
          if (wsRes.ok) liveWebIds = new Set((await wsRes.json()).map(s => s.id));
        } catch (_) { /* 조회 실패 시 아래에서 전부 유실 처리됨 */ }

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
                } else { failed++; }
              } else { failed++; }
            } catch (_) { failed++; }
          } else {
            // 비-tmux(순수 PTY) 탭: 원래 조용히 버려지던 부분 — 서버가 아직
            // 그 PTY를 들고 있으면(=재시작 안 됐으면) 그대로 재연결한다.
            if (liveWebIds.has(tab.id)) {
              if (!sessions[tab.id]) {
                addSession(tab.id, tab.name || tab.id);
                if (!firstNewId) firstNewId = tab.id;
              }
              restored++;
            } else {
              failed++;
            }
          }
        }
        if (failed > 0) {
          showToast(`탭 ${failed}개 복원 실패 (세션이 이미 종료됨)`);
        }
        if (restored > 0 && firstNewId) {
          switchTo(firstNewId);
        }
        return restored > 0;
      } catch (e) { return false; }
    }

    // localStorage 워크스페이스 스냅샷은 "마지막 저장 시점"만 기억한다 — 그 이후
    // 다른 탭/기기/CLI(fsh)에서 만들어진 tmux 세션은 서버엔 실제로 살아있어도
    // 이 스냅샷에 없으면 restoreWorkspace()가 절대 못 찾는다. 서버의 진짜 tmux
    // 목록과 대조해 빠진 것만 추가로 붙인다(활성 탭은 건드리지 않는다).
    async function reconcileMissingTmuxSessions() {
      const keepActive = activeId; // addSession()이 매번 switchTo()를 호출하므로 복원해야 함
      try {
        const res = await fetch(`${API_BASE}/api/tmux/sessions`);
        const tmuxList = await res.json();
        if (!Array.isArray(tmuxList)) return;
        const known = new Set(Object.values(sessions).map(s => s.tmuxName).filter(Boolean));
        const missing = tmuxList.filter(s => !known.has(s.name));
        if (missing.length === 0) return;

        // 저장된 스냅샷에 있던 순서를 알아야 "원래 자리"에 되돌려 넣을 수 있다
        // (attach가 일시 실패해서 restoreWorkspace가 놓쳤던 세션 등). 스냅샷에
        // 없던(=저장 이후 다른 곳에서 새로 생긴) 세션은 원래 자리가 없으므로
        // 알파벳/숫자 순으로 정렬해 끝에 붙인다.
        let savedOrder = [];
        try {
          const raw = localStorage.getItem(WORKSPACE_KEY);
          const state = raw ? JSON.parse(raw) : null;
          if (state && Array.isArray(state.tabs)) savedOrder = state.tabs;
        } catch (_) { /* 파싱 실패 시 전부 새 세션 취급 */ }

        const knownFromSnapshot = missing.filter(s => savedOrder.some(t => t.tmux_name === s.name));
        const genuinelyNew = missing.filter(s => !savedOrder.some(t => t.tmux_name === s.name))
          .sort((a, b) => a.name.localeCompare(b.name));

        let added = false;
        for (const s of [...knownFromSnapshot, ...genuinelyNew]) {
          const res2 = await fetch(`${API_BASE}/api/tmux/attach`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name: s.name })
          });
          if (!res2.ok) continue;
          const data = await res2.json();
          if (data.id && !sessions[data.id]) {
            // 스냅샷 순서상 이 세션 다음에 와야 할, 이미 DOM에 있는 탭을 찾아 그 앞에 삽입
            let insertBeforeId = null;
            const idx = savedOrder.findIndex(t => t.tmux_name === s.name);
            if (idx !== -1) {
              for (let i = idx + 1; i < savedOrder.length; i++) {
                const nextId = Object.keys(sessions).find(sid => sessions[sid].tmuxName === savedOrder[i].tmux_name);
                if (nextId) { insertBeforeId = nextId; break; }
              }
            }
            addSession(data.id, data.name || `tmux:${s.name}`, insertBeforeId);
            if (sessions[data.id]) sessions[data.id].tmuxName = s.name;
            added = true;
          }
        }
        if (added && keepActive && sessions[keepActive]) switchTo(keepActive);
      } catch (e) { /* 서버 통신 실패 시 조용히 무시 — 복원된 탭은 이미 정상 동작 중 */ }
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
        if (remaining.length > 0) {
          switchTo(remaining[0]);
        } else {
          // 마지막 세션을 닫은 경우 — 빈 컨테이너만 남기지 않고 온보딩(빈 상태) 화면으로.
          activeId = null;
          document.getElementById('terminal-container').innerHTML = '';
          if (!document.getElementById('onboarding')) showOnboarding();
        }
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
        addSession(data.id, data.name || data.id);
        // ⚠ tmuxName 미설정 시 openSessionOnMac()이 "tmux 세션 아님"으로 오판한다.
        if (sessions[data.id]) sessions[data.id].tmuxName = data.tmux_session || tmuxName;
      }
    }

    async function createTmuxSession() {
      // "맥에서도 열기" 토글이 켜져 있으면 서버가 osascript로 iTerm 창도 함께 연다.
      const autoMac = document.getElementById('auto-mac-checkbox')?.checked;
      const res = await fetch(`${API_BASE}/api/tmux/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(autoMac ? { auto_open_on_mac: true } : {}),
      });
      if (!res.ok) { showToast(`tmux 세션 생성 실패 (${res.status})`); return; }
      const data = await res.json();
      if (!data.id) { showToast('tmux 세션 생성 실패'); return; }
      addSession(data.id, data.name || data.id);
      // ⚠ tmuxName 미설정 시 openSessionOnMac()이 "tmux 세션 아님"으로 오판한다.
      if (sessions[data.id]) sessions[data.id].tmuxName = data.tmux_session;
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
          // 복원된 스냅샷은 "마지막 저장 시점" 기준이라 그 이후 다른 곳에서 생긴
          // tmux 세션을 놓칠 수 있다 — 서버 목록과 대조해 누락분만 보충.
          await reconcileMissingTmuxSessions();
          return;
        }

        // 1. 기존 웹 세션 복원
        const res = await fetch(`${API_BASE}/api/sessions`);
        const existing = await res.json();
        if (existing.length > 0) {
          for (const s of existing) {
            addSession(s.id, s.name || s.id);
            // ⚠ 없으면 openSessionOnMac()이 진짜 tmux 세션도 "tmux 아님"으로 오판한다.
            if (s.tmux_name && sessions[s.id]) sessions[s.id].tmuxName = s.tmux_name;
          }
          return;
        }

        // 2. tmux 세션이 있으면 전부 자동 attach (저장된 탭 순서가 없는 상태이므로
        //    이름 알파벳/숫자 순으로 정렬 — 예전엔 tmuxList[0] 하나만 열고 끝냈다)
        const tmuxRes = await fetch(`${API_BASE}/api/tmux/sessions`);
        const tmuxList = await tmuxRes.json();
        if (tmuxList.length > 0) {
          const sorted = [...tmuxList].sort((a, b) => a.name.localeCompare(b.name));
          for (const s of sorted) {
            await attachTmux(s.name);
          }
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
        <h2>FarShell</h2>
        <p>음성으로 터미널을 조작하세요.<br>tmux 세션을 만들거나, 새 터미널을 시작할 수 있습니다.</p>
        <div class="vt-ob-actions">
          <button class="vt-btn-primary" onclick="document.getElementById('onboarding').remove();createTmuxSession()">tmux 세션 시작</button>
          <button class="vt-btn-secondary" onclick="document.getElementById('onboarding').remove();createSession()">일반 터미널</button>
        </div>
        <p class="vt-ob-hint">맥북에서 Ctrl+Shift+V로 음성 입력 (voice daemon 실행 시)</p>
      `;
      document.body.appendChild(el);
    }

    // "⋯ → 가이드 보기" — 언제든 열고/닫을 수 있는 서비스 전체 사용 가이드(첫 사용자용).
    // 온보딩(showOnboarding)은 첫 실행 시 세션 생성을 강제하는 화면이라 재사용하지 않고
    // 별도로 둔다.
    function showGuide() {
      const existing = document.getElementById('vt-guide');
      if (existing) { existing.remove(); return; }

      // 섹션 정의 — {icon, title, rows:[{key, desc}]}. key는 왼쪽 라벨, desc는 설명(HTML).
      const sections = [
        { icon: 'icon-rocket', title: '시작하기', rows: [
          { key: '<kbd>+</kbd> 버튼', desc: '새 세션 생성 (tmux 세션 또는 일반 터미널)' },
          { key: '탭', desc: '더블클릭 → 이름 변경 · <kbd>×</kbd> → 닫기(tmux는 detach만 됨, 완전 종료는 ⋯ 메뉴)' },
          { key: 'Grid 뷰', desc: '상단 <i class="icon-layout-grid"></i> 아이콘 → 모든 tmux 세션 라이브 미리보기' },
        ]},
        { icon: 'icon-mic', title: '음성 입력', rows: [
          { key: '마이크 버튼', desc: '상단 <i class="icon-mic"></i> 탭 → 말하면 STT로 텍스트 입력' },
          { key: '맥 핫키', desc: '<code>fsh voice</code> 실행 후 <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> 토글' },
          { key: '핸즈프리', desc: '모바일 🔄 버튼 → 녹음·인식 연속 자동 반복' },
          { key: '음성 전용', desc: '⋯ 메뉴 → 터미널 숨기고 큰 마이크만 표시(이어폰용)' },
        ]},
        { icon: 'icon-clipboard-copy', title: '복사 · 붙여넣기', rows: [
          { key: '복사', desc: '텍스트 드래그 → <b>자동 복사</b> · 또는 선택 후 우클릭' },
          { key: '드래그해도 선택이 안 될 때', desc: 'tmux 마우스 모드(<code>mouse on</code>)가 켜져 있으면 드래그를 tmux가 먼저 가로챔 → <kbd>Shift</kbd>+드래그(맥은 <kbd>Option</kbd>+드래그도 가능)로 강제 선택' },
          { key: '붙여넣기', desc: '<kbd>Cmd</kbd>/<kbd>Ctrl</kbd>+<kbd>V</kbd> · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> · 선택 없이 우클릭' },
          { key: '이미지', desc: '이미지를 복사해 붙여넣으면 서버에 <b>자동 업로드</b> + 경로가 명령줄에 삽입' },
          { key: '자동 복사 켬/끔', desc: '⋯ 메뉴 → 설정에서 토글. <b>켬</b>: 드래그·vim/tmux 복사가 클립보드에 즉시 반영. <b>끔</b>: 선택만 되고 클립보드는 그대로 — 우클릭으로 원할 때만 복사' },
          { key: '맥↔웹 동기화', desc: 'vim/tmux copy-mode 복사는 자동(OSC52). 그 밖(Safari 등)은 맥에서 <code>fsh clip</code> 실행' },
        ]},
        { icon: 'icon-square-terminal', title: 'tmux 세션', rows: [
          { key: 'tmux 세션', desc: '⋯ 메뉴 → 기존 세션 목록 확인·attach' },
          { key: '맥에서도 열기', desc: '새 세션 생성 시 맥 iTerm 창도 자동으로 열림(토글)' },
          { key: '이 세션 열기', desc: '지금 보는 세션을 맥 iTerm 새 창으로 열기' },
        ]},
        { icon: 'icon-file-up', title: '파일 · 검색', rows: [
          { key: '파일 업로드', desc: '⋯ 메뉴 → 업로드(경로 자동 삽입). 이미지는 붙여넣기로도 업로드' },
          { key: '검색', desc: '<kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>F</kbd> 로 터미널 출력 검색' },
        ]},
        { icon: 'icon-monitor-smartphone', title: '모바일 · 원격', rows: [
          { key: '모바일 접속', desc: '<code>fsh mobile</code> → QR/URL로 폰 접속(같은 세션 이어쓰기)' },
          { key: '핸드오프', desc: '<code>fsh handoff mobile</code> / <code>desktop</code> 으로 폰↔맥 전환' },
        ]},
        { icon: 'icon-palette', title: '테마', rows: [
          { key: '스킨 변경', desc: '⋯ 메뉴 → macOS · Catppuccin · Windows · VS Code · Notepad' },
        ]},
      ];

      const secHtml = sections.map(s => `
        <div class="vt-guide-sec">
          <div class="vt-gs-title"><i class="${s.icon}"></i>${s.title}</div>
          ${s.rows.map(r => `
            <div class="vt-guide-row">
              <div class="vt-gr-key">${r.key}</div>
              <div class="vt-gr-desc">${r.desc}</div>
            </div>`).join('')}
        </div>`).join('');

      const el = document.createElement('div');
      el.id = 'vt-guide';
      el.className = 'vt-guide-backdrop';
      el.innerHTML = `
        <div class="vt-guide-card" role="dialog" aria-modal="true" aria-label="사용 가이드">
          <div class="vt-guide-head">
            <div class="vt-gh-icon"><i class="icon-terminal"></i></div>
            <div>
              <h2>FarShell</h2>
              <p>브라우저로 tmux 터미널을 — 웹·폰 어디서든 이어서</p>
            </div>
            <button class="vt-guide-x" aria-label="닫기">✕</button>
          </div>
          <div class="vt-guide-scroll">${secHtml}</div>
        </div>
      `;
      // 닫기: X 버튼 · 배경 클릭 · Esc
      const close = () => { el.remove(); document.removeEventListener('keydown', onKey); };
      const onKey = (ev) => { if (ev.key === 'Escape') close(); };
      el.querySelector('.vt-guide-x').addEventListener('click', close);
      el.addEventListener('click', (ev) => { if (ev.target === el) close(); });
      document.addEventListener('keydown', onKey);
      document.body.appendChild(el);
    }

    // ---- 모바일 특수키 바 (keybar) -------------------------------------------
    // 소프트 키보드에 없는 특수키/조합키를 활성 PTY에 원시 시퀀스로 주입한다.
    // 기존 sendToPty(activeId, ...)와 activeId를 그대로 재사용.
    // Sticky Ctrl: Ctrl을 한 번 탭하면 "다음 한 키만" Ctrl 조합으로 전송된다
    // (keybar 문자 버튼 · 소프트 키보드 문자 양쪽 모두). Claude Code/셸의 Ctrl 단축키용.
    // 순수 키-시퀀스 로직(_KEYBAR_SEQ, ctrlByte, Ctrl+화살표, sticky 변환)은
    // keyseq.js(window.VTKeySeq)로 분리 — DOM/세션 상태가 없어 단위 테스트 대상.
    let _ctrlArmed = false;

    function _setCtrlArmed(on) {
      _ctrlArmed = on;
      const btn = document.querySelector('#keybar .kb-mod[data-mod="ctrl"]');
      if (btn) {
        btn.classList.toggle('armed', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      }
    }

    // term.onData 경로에서 호출 — armed Ctrl이면 입력 첫 글자에 Ctrl 조합 적용 후 해제.
    // (함수 선언이라 addSession의 onData보다 뒤에 있어도 호이스팅되어 안전)
    function applyStickyMod(data) {
      if (_ctrlArmed && data) {
        _setCtrlArmed(false);   // 입력이 오면 sticky 상태 소비(해제)
        return VTKeySeq.applyCtrlToInput(data);
      }
      return data;
    }

    function _focusActiveTerm() {
      const t = sessions[activeId] && sessions[activeId].term;
      if (t) { try { t.focus(); } catch (_) {} }
    }

    function initKeybar() {
      const bar = document.getElementById('keybar');
      if (!bar) return;
      // 물리 키보드가 없는 터치 기기에서만 노출 (데스크톱은 CSS로도 숨기지만 이중 방어).
      // 강제 오버라이드: ?keybar=1 또는 localStorage vt_keybar='on' (터치 노트북/테스트용).
      const coarse = window.matchMedia && window.matchMedia('(pointer:coarse)').matches;
      let _force = false;
      try { _force = _urlParams.get('keybar') === '1' || localStorage.getItem('vt_keybar') === 'on'; } catch (_) {}
      if (!coarse && !_force) return;
      // 강제 노출 시엔 CSS의 pointer:fine 숨김을 이기도록 클래스 부여.
      if (_force) bar.classList.add('force-show');
      bar.hidden = false;

      // pointerdown에서 preventDefault → 터미널 textarea 포커스를 뺏지 않아
      // 소프트 키보드가 내려가지 않는다. (버튼 탭마다 키보드가 닫히면 못 씀)
      bar.addEventListener('pointerdown', (e) => {
        // 접기/펴기 토글 — .kb가 아니므로 먼저 가로챈다.
        const toggle = e.target.closest('#keybar-toggle');
        if (toggle) {
          e.preventDefault();
          _setKeybarCollapsed(!bar.classList.contains('collapsed'));
          _focusActiveTerm();
          return;
        }
        const btn = e.target.closest('.kb');
        if (!btn) return;
        e.preventDefault();
        if (btn.dataset.mod === 'ctrl') { _setCtrlArmed(!_ctrlArmed); _focusActiveTerm(); return; }
        // armed면 keybarSeq가 Ctrl+화살표(단어 이동)·Ctrl+문자를 조합해 준다.
        const out = VTKeySeq.keybarSeq({ key: btn.dataset.key, seq: btn.dataset.seq, ctrl: _ctrlArmed });
        if (!out) return;
        if (_ctrlArmed) _setCtrlArmed(false);
        sendToPty(activeId, out);
        _focusActiveTerm();
      });

      // 키보드 위로 띄우기 — visualViewport로 소프트 키보드 높이를 추정해 transform.
      const positionBar = () => {
        const vv = window.visualViewport;
        if (!vv) return;
        const overlap = Math.max(0, window.innerHeight - (vv.height + vv.offsetTop));
        bar.style.transform = overlap > 0 ? `translateY(${-overlap}px)` : '';
      };
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', positionBar);
        window.visualViewport.addEventListener('scroll', positionBar);
      }
      window.addEventListener('resize', positionBar);

      // 접기/펴기 — 상태를 localStorage에 기억. 접으면 우하단 알약만 남고
      // 터미널 하단 여백이 줄어 화면을 더 쓴다. (함수 선언이라 위 핸들러에서 참조 가능)
      function _setKeybarCollapsed(collapsed) {
        // armed Ctrl이 접힘 상태로 넘어가면 하이라이트가 숨겨진 채 다음 입력이
        // Ctrl 조합으로 나가버린다(놀람). 접기/펴기 시 항상 해제.
        _setCtrlArmed(false);
        bar.classList.toggle('collapsed', collapsed);
        document.body.classList.toggle('kb-collapsed', collapsed);
        const tg = document.getElementById('keybar-toggle');
        if (tg) {
          tg.textContent = collapsed ? '▴' : '▾';
          tg.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
          tg.setAttribute('aria-label', collapsed ? '특수키 바 펴기' : '특수키 바 접기');
        }
        try { localStorage.setItem('vt_keybar_collapsed', collapsed ? '1' : '0'); } catch (_) {}
        // 하단 여백이 바뀌었으니 xterm 칸 수 재계산.
        if (typeof fitAndResize === 'function' && activeId) setTimeout(() => fitAndResize(activeId), 60);
        positionBar();
      }

      // 초기 상태 복원 (기본: 펼침)
      let _startCollapsed = false;
      try { _startCollapsed = localStorage.getItem('vt_keybar_collapsed') === '1'; } catch (_) {}
      if (_startCollapsed) _setKeybarCollapsed(true);

      positionBar();
    }
    initKeybar();
