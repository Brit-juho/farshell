// 모달형 패널(코드 뷰어/포트/큐)의 공용 뼈대. 구 js/panel.js (F2에서 이관).
// 세 패널이 토글 진입 · backdrop 클릭 닫기 · Esc 닫기 · 닫을 때 fitAndResize ·
// "패널이 열려 있는 동안만 도는 자가정리 폴링" 을 각자 복붙해서 갖고 있었다.
// 껍데기(.vt-viewer-backdrop/.vt-viewer-card)는 이미 CSS에서 공유되고 있었으니
// 그걸 만드는 JS도 한 곳에 둔다.
//
// 이제 ES 모듈이라 activeId/fitAndResize를 classic script 공유 스코프로 못
// 읽는다. fitAndResize는 top-level 함수 선언이라 원래도 window에 자동으로
// 걸려 있고(terminal.js), activeId는 F3(b)부터 core/store.js가 window.activeId로
// 브리지해준다.

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
// D7: 포커스 트랩 대상 셀렉터 — MDN의 "포커스 가능 요소" 표준 목록에서
// 이 앱이 실제로 헤더/바디에 쓰는 것만 추렸다.
const FOCUSABLE_SEL = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

// picker.js의 세션 시트도 같은 트랩 로직을 쓴다(별도 모달 구현이라 openPanel을
// 못 쓰지만, "포커스가 배경으로 새면 안 된다"는 규칙은 같다).
export function _focusables(el) {
  return Array.from(el.querySelectorAll(FOCUSABLE_SEL))
    .filter((n) => n.offsetParent !== null); // 화면에 실제로 보이는 것만
}

export function openPanel(opts) {
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
      // D7: 닫을 때 열기 전 포커스로 복귀 — 트리거 버튼(⋯ 메뉴 항목 등)을 놓치지 않는다.
      el._vtTriggerEl = document.activeElement;
      el.querySelector('.vt-vw-x').addEventListener('click', () => closePanel(opts.id));
      el.addEventListener('click', (ev) => { if (ev.target === el) closePanel(opts.id); });

      const keyHandler = (ev) => {
        if (ev.key === 'Escape') {
          if (opts.onKey && opts.onKey(ev) === true) return;
          ev.stopPropagation();
          closePanel(opts.id);
          return;
        }
        // D7: 포커스 트랩 — Tab이 배경(터미널 등)으로 새지 않게 첫/끝 사이를 순환시킨다.
        if (ev.key !== 'Tab') return;
        const items = _focusables(el);
        if (items.length === 0) return;
        const first = items[0], last = items[items.length - 1];
        if (ev.shiftKey && document.activeElement === first) {
          ev.preventDefault(); last.focus();
        } else if (!ev.shiftKey && document.activeElement === last) {
          ev.preventDefault(); first.focus();
        } else if (!el.contains(document.activeElement)) {
          // 포커스가 이미 트랩 밖으로 샌 상태(자동완성 등) — 안으로 되돌린다.
          ev.preventDefault(); first.focus();
        }
      };
      el._vtKeyHandler = keyHandler;
      document.addEventListener('keydown', keyHandler);
      document.body.appendChild(el);

      // D7: 초기 포커스 — 첫 포커스 가능 요소(대개 닫기 버튼)로. 패널 자신의
      // 검색창 등 더 적절한 대상이 있으면 opts.onOpen()이 openPanel() 리턴 직후
      // 그 위에 다시 focus()를 걸면 된다(quickopen.js가 이렇게 한다).
      const toFocus = _focusables(el)[0];
      if (toFocus) toFocus.focus();

      return { el, body: document.getElementById(opts.bodyId) };
    }

export function closePanel(id) {
      const el = document.getElementById(id);
      if (!el) return;
      if (el._vtTimer) clearInterval(el._vtTimer);
      document.removeEventListener('keydown', el._vtKeyHandler);
      el.remove();
      if (el._vtOnClose) el._vtOnClose();
      // D7: 트리거로 포커스 복귀 — DOM에서 사라졌을 수 있으니(탭 닫힘 등) 방어.
      if (el._vtTriggerEl && el._vtTriggerEl.isConnected && typeof el._vtTriggerEl.focus === 'function') {
        el._vtTriggerEl.focus();
      }
      // 패널이 레이아웃을 건드렸을 수 있으므로 터미널 크기를 다시 맞춘다.
      try { setTimeout(() => window.fitAndResize(window.activeId), 60); } catch (_) {}
    }

    // 패널이 열려 있는 동안만 도는 폴링 — 닫히면 스스로 정리한다.
export function setPanelPoll(id, ms, fn) {
      const el = document.getElementById(id);
      if (!el) return;
      el._vtTimer = setInterval(() => {
        if (document.getElementById(id)) fn(); else closePanel(id);
      }, ms);
    }


window.openPanel = openPanel;
window.closePanel = closePanel;
window.setPanelPoll = setPanelPoll;
