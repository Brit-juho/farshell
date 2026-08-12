// 모달형 패널(코드 뷰어/포트/큐)의 공용 뼈대.
// 세 패널이 토글 진입 · backdrop 클릭 닫기 · Esc 닫기 · 닫을 때 fitAndResize ·
// "패널이 열려 있는 동안만 도는 자가정리 폴링" 을 각자 복붙해서 갖고 있었다.
// 껍데기(.vt-viewer-backdrop/.vt-viewer-card)는 이미 CSS에서 공유되고 있었으니
// 그걸 만드는 JS도 한 곳에 둔다.
//
// grid.js 뒤에 로드되므로 activeId / fitAndResize 를 그대로 참조한다
// (classic script 최상위 스코프 공유 — viewer.js 원래 주석과 동일한 계약).

    // opts:
    //   id             — 패널 루트 엘리먼트 id ('vt-viewer' 등)
    //   ariaLabel      — dialog aria-label
    //   headHTML       — 헤더 내부 HTML. 닫기 버튼(.vt-vw-x)은 여기서 자동으로 덧붙는다.
    //   extraHTML      — 헤더와 body 사이에 끼울 내용(큐의 작성 바 등). 없으면 생략.
    //   bodyId         — body 엘리먼트 id
    //   bodyHTML       — 초기 body 내용. 생략하면 "불러오는 중…"
    //   extraClass     — 루트 엘리먼트에 덧붙일 클래스(공백구분). 코드 뷰어의 표시 모드용.
    //                     (배경 클릭으로 닫는 것을 막고 싶은 레이아웃이면 CSS에서 그 클래스에
    //                     pointer-events:none 을 주고 카드에만 auto를 되돌리면 된다 — 그러면
    //                     클릭이 el까지 도달하지 않아 별도 JS 플래그 없이 막힌다. 코드 뷰어의
    //                     도킹 모드가 이 방식이다.)
    //   onKey(ev)      — Escape 처리를 가로채고 싶을 때. true를 반환하면 기본 닫기를 건너뛴다
    //                     (큐의 "입력 중이던 텍스트만 지우고 패널은 유지" 케이스, 도킹 모드의
    //                     "Esc는 터미널/vim에 쓰이므로 패널을 닫지 않는다" 케이스 등).
    //   onClose()      — 패널이 실제로 닫힐 때(X · 배경 클릭 · Esc · 재호출 토글 전부) 호출된다.
    //                     showViewer()가 직접 부르는 게 아니라 X/배경/Esc는 panel.js가 스스로
    //                     closePanel()을 부르므로, "닫힐 때 정리할 것"은 여기로 등록해야
    //                     모든 닫힘 경로에서 빠짐없이 실행된다(토글 버튼 active 해제 등).
    //
    // 반환: 이미 열려 있어서 토글-닫기만 했으면 null, 새로 열었으면 { el, body }.
    function openPanel(opts) {
      if (document.getElementById(opts.id)) { closePanel(opts.id); return null; }

      const el = document.createElement('div');
      el.id = opts.id;
      el.className = 'vt-viewer-backdrop' + (opts.extraClass ? ' ' + opts.extraClass : '');
      el.innerHTML = `
        <div class="vt-viewer-card" role="dialog" aria-modal="true" aria-label="${opts.ariaLabel}">
          <div class="vt-viewer-head">${opts.headHTML}<button class="vt-vw-x" aria-label="닫기">✕</button></div>
          ${opts.extraHTML || ''}
          <div class="vt-vw-body" id="${opts.bodyId}">${opts.bodyHTML || '<div class="vt-vw-loading">불러오는 중…</div>'}</div>
        </div>
      `;
      el._vtOnClose = opts.onClose;
      el.querySelector('.vt-vw-x').addEventListener('click', () => closePanel(opts.id));
      el.addEventListener('click', (ev) => { if (ev.target === el) closePanel(opts.id); });

      const keyHandler = (ev) => {
        if (ev.key !== 'Escape') return;
        if (opts.onKey && opts.onKey(ev) === true) return;
        ev.stopPropagation();
        closePanel(opts.id);
      };
      el._vtKeyHandler = keyHandler;
      document.addEventListener('keydown', keyHandler);
      document.body.appendChild(el);

      return { el, body: document.getElementById(opts.bodyId) };
    }

    function closePanel(id) {
      const el = document.getElementById(id);
      if (!el) return;
      if (el._vtTimer) clearInterval(el._vtTimer);
      document.removeEventListener('keydown', el._vtKeyHandler);
      el.remove();
      if (el._vtOnClose) el._vtOnClose();
      // 패널이 레이아웃을 건드렸을 수 있으므로 터미널 크기를 다시 맞춘다.
      try { setTimeout(() => fitAndResize(activeId), 60); } catch (_) {}
    }

    // 패널이 열려 있는 동안만 도는 폴링 — 닫히면 스스로 정리한다.
    function setPanelPoll(id, ms, fn) {
      const el = document.getElementById(id);
      if (!el) return;
      el._vtTimer = setInterval(() => {
        if (document.getElementById(id)) fn(); else closePanel(id);
      }, ms);
    }
