import { icon } from '../ui/icons.js';
// N38 — dock이 CSS로 숨는 문턱과 정확히 같은 조건이어야 한다(styles/layers/
// legacy.css의 `#vt-dock`는 `@media (min-width:720px)`에서만 display:flex —
// pointer는 안 본다). 그래서 여기서도 폭만 본다 — isCompactMode()(L3
// 2단계, pointer:coarse까지 요구)를 쓰면 "폭은 좁은데 마우스 포인터인"
// 창에서 dock CSS는 이미 숨었는데 이 판정만 아니라고 답해, scm/queue
// 패널이 보이지 않는 dock 안에 계속 조용히 붙어버린다(실제로 그렇게
// 재현됨). layout/compact.js를 통째로 import하지 않는 이유는 그 파일이
// 딸고 있는 core/store.js 등 상태 싱글톤이 panels.js(지연 청크) 안에
// 복제되기 때문이다(build-output.test.js, ADR-26) — breakpoints.js는
// 상수뿐이라 안전하다(Rail.tsx의 같은 예외 주석 참고).
import { COMPACT_MAX } from '../layout/breakpoints.js';

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

const MOBILE_ROUTE_IDS = new Set([
  'vt-dock-scm', 'vt-queue', 'vt-settings', 'vt-files', 'vt-ports', 'vt-usage',
]);

function _withoutPanelState(state) {
  if (!state || typeof state !== 'object') return state;
  const next = { ...state };
  delete next.vtPanel;
  return next;
}

// picker.js의 세션 시트도 같은 트랩 로직을 쓴다(별도 모달 구현이라 openPanel을
// 못 쓰지만, "포커스가 배경으로 새면 안 된다"는 규칙은 같다).
export function _focusables(el) {
  return Array.from(el.querySelectorAll(FOCUSABLE_SEL))
    .filter((n) => n.offsetParent !== null); // 화면에 실제로 보이는 것만
}

// N35 §6 — dock 호스트. 큐·포트·사용량은 2.1.0부터 backdrop 모달이 아니라
// dock 탭 안에서 산다. 세 패널의 렌더러(queue.js·ports.js·panels/usage.js)를
// 다시 쓰지 않기 위해, "어디에 붙일지"만 여기서 갈라준다 — 호출부는 그대로
// openPanel()을 부르고, dock이 마운트돼 있으면 그 본문에, 아니면 예전처럼
// backdrop에 붙는다(compact·dock 로드 실패 시 그대로 동작한다).
//
// window.vtDockHost는 shell/Dock.tsx가 등록한다. 여기서 직접 import 하지
// 않는 이유는 그 파일이 지연 청크라서다(ADR-26).
function _dockHost(id) {
  // N38(70-mobile.md §1) — compact에서는 dock 자체가 CSS로 숨는다(하단 내비가
  // 그 자리를 대신한다, 10-shell-layout.md §3 표). Dock.tsx는 뷰포트와
  // 무관하게 항상 마운트돼 있어 window.vtDockHost도 항상 등록돼 있으므로,
  // 여기서 거르지 않으면 「변경」·「큐」 액션이 화면에 안 보이는 dock 안에
  // 조용히 붙어버린다(실제로 재현됨). compact에서는 dock 호스트를 아예
  // 묻지 않고 바로 아래 backdrop 모달 경로로 폴백한다 — 그 경로는 이미
  // 작은 화면에서 풀스크린 시트로 보이도록 스타일돼 있다(2.0부터).
  if (window.innerWidth < COMPACT_MAX) return null;
  const fn = window.vtDockHost;
  if (typeof fn !== 'function') return null;
  try { return fn(id) || null; } catch (_) { return null; }
}

// ── N4: pane 인스턴스 ──────────────────────────────────────────────────────
//
// 2026-09-20 — 패널을 pane 리프(`kind:'panel'`)로도 띄울 수 있게 됐다. 그런데
// 이 파일의 기존 계약은 **전역 id 하나 = 인스턴스 하나**를 전제한다:
// `closePanel(id)`·`setPanelPoll(id, …)`이 `document.getElementById(id)`로
// 대상을 찾고, `openPanel`은 같은 id가 이미 있으면 "토글"로 해석해 닫는다.
// dock/모달 한 벌만 있을 때는 그게 맞았다.
//
// **기존 경로는 건드리지 않는다.** dock·모달 인스턴스는 지금까지와 똑같이
// 전역 id를 갖고, 팔레트·키맵·dock 탭의 토글 의미도 그대로다. pane 인스턴스만
// 다르게 다룬다:
//   - 루트 id를 `${id}--pane-${paneId}`로 유일하게 만든다 → `getElementById(id)`는
//     여전히 dock/모달 것만 찾는다(토글이 pane 것을 닫아버리는 사고가 없다).
//   - 안쪽 id(`vt-q-body` 등)도 같은 접미사를 붙여 **문서에서 유일**하게 만든다.
//     중복 id를 남기면 `getElementById`가 먼저 나온 것만 돌려줘 두 벌이 서로의
//     DOM을 갱신하는 조용한 버그가 된다.
//   - 대신 패널 코드에는 `ctx.byId('vt-q-body')`를 준다 — 접미사를 패널이 알
//     필요가 없고, dock/모달에서는 그냥 `getElementById`다.
function _scopeIds(rootEl, suffix) {
  const map = new Map();
  const walk = (el) => {
    if (el.id) { map.set(el.id, `${el.id}${suffix}`); el.id = `${el.id}${suffix}`; }
    for (const c of el.children) walk(c);
  };
  // 루트는 건너뛴다 — 호출자가 이미 유일한 id를 붙여 뒀다. 여기서 또 붙이면
  // `vt-queue--pane-p3--pane-p3`가 된다(실측).
  for (const c of rootEl.children) walk(c);
  // `for`/`aria-*`가 가리키던 id도 같이 옮긴다 — 안 옮기면 라벨 연결이 끊겨
  // 스크린리더에서 이름 없는 컨트롤이 된다.
  for (const el of rootEl.querySelectorAll('[for],[aria-labelledby],[aria-describedby],[aria-controls]')) {
    for (const attr of ['for', 'aria-labelledby', 'aria-describedby', 'aria-controls']) {
      const v = el.getAttribute(attr);
      if (v && map.has(v)) el.setAttribute(attr, map.get(v));
    }
  }
  return map;
}

/** 패널 코드가 자기 인스턴스 안에서만 DOM을 찾도록 하는 손잡이.
 * dock/모달에서는 전역 조회 그대로 — 기존 동작과 1:1이다. */
function _makeCtx(rootEl, idMap) {
  if (!idMap) {
    return {
      root: rootEl,
      byId: (id) => document.getElementById(id),
      query: (sel) => (rootEl || document).querySelector(sel),
      queryAll: (sel) => Array.from((rootEl || document).querySelectorAll(sel)),
    };
  }
  return {
    root: rootEl,
    // `#id` 대신 속성 셀렉터를 쓴다 — id 문자열을 셀렉터 문법으로 이스케이프할
    // 필요가 없어지고(`CSS.escape`는 jsdom 환경에 없다, 실측), 우리가 만든
    // 접미사에 어떤 문자가 들어와도 안전하다.
    byId: (id) => rootEl.querySelector(`[id="${idMap.get(id) || id}"]`),
    query: (sel) => rootEl.querySelector(sel),
    queryAll: (sel) => Array.from(rootEl.querySelectorAll(sel)),
  };
}

export function openPanel(opts) {
      // pane 호스트: 토글이 아니다. 같은 패널을 여러 칸에 띄울 수 있고, dock에
      // 떠 있는 것과도 공존한다(사용자 결정, 2026-09-20).
      if (!opts.paneHost && document.getElementById(opts.id)) { closePanel(opts.id); return null; }

      const host = opts.paneHost || _dockHost(opts.id);
      // 'stale' = dock이 "이 패널은 이제 필요 없다"고 말한 것(요청과 도착 사이에
      // 탭이 바뀌었다). 모달로 폴백하면 안 된다 — 사용자가 안 누른 화면이 뜬다.
      if (host === 'stale') return null;
      const el = document.createElement('div');
      // pane 인스턴스는 루트 id가 유일해야 한다(위 _scopeIds 주석).
      el.id = opts.paneHost ? `${opts.id}--pane-${opts.paneId}` : opts.id;
      const mobileRoute = !host && window.innerWidth < COMPACT_MAX && MOBILE_ROUTE_IDS.has(opts.id);
      // pane 인스턴스도 "끼워 넣는 자리"라 dock과 같은 껍데기 벗기기가 필요하다
      // — 같은 규칙을 다시 쓰되(`.vt-dock-panel`) 자리를 구분할 수 있게 표시
      // 클래스를 하나 더 붙인다(닫기 X는 pane에서는 살려둔다, 아래 CSS).
      el.className = (opts.paneHost ? 'vt-dock-panel vt-pane-panel-root' : host ? 'vt-dock-panel' : 'vt-viewer-backdrop')
        + (mobileRoute ? ' vt-mobile-route' : '')
        + (opts.extraClass ? ' ' + opts.extraClass : '');
      el.innerHTML = `
        <div class="vt-viewer-card" role="dialog" aria-modal="true" aria-label="${opts.ariaLabel}">
          <div class="vt-viewer-head">${opts.headHTML}<button class="vt-icon-btn vt-vw-x" aria-label="닫기">${icon('x', 14)}</button></div>
          ${opts.extraHTML || ''}
          <div class="vt-vw-body" id="${opts.bodyId}">${opts.bodyHTML || '<div class="vt-vw-loading">불러오는 중…</div>'}</div>
        </div>
      `;
      // 안쪽 id도 인스턴스별로 옮긴다. 순서가 중요하다 — innerHTML을 채운
      // **뒤**, 어떤 조회보다 **앞**이어야 한다.
      const idMap = opts.paneHost ? _scopeIds(el, `--pane-${opts.paneId}`) : null;
      const ctx = _makeCtx(el, idMap);
      el._vtCtx = ctx;
      el._vtPanelId = opts.id;
      el._vtOnClose = opts.onClose;
      // D7: 닫을 때 열기 전 포커스로 복귀 — 트리거 버튼(⋯ 메뉴 항목 등)을 놓치지 않는다.
      el._vtTriggerEl = document.activeElement;
      el.querySelector('.vt-vw-x').addEventListener('click', () => closePanel(el.id));

      // dock 안에서는 모달의 세 가지 관용구를 전부 뺀다: 배경 클릭 닫기(배경이
      // 없다), Esc 닫기(터미널에 가야 할 키다 — 도킹 모드 뷰어가 이미 같은
      // 판단을 했다), 포커스 트랩(터미널로 Tab이 나갈 수 있어야 한다).
      if (host) {
        // dock과 pane 둘 다 "끼워 넣는 자리"다 — 모달의 세 관용구(배경 클릭
        // 닫기·Esc 닫기·포커스 트랩)를 쓰지 않는다는 판단이 그대로 적용된다.
        host.appendChild(el);
        return { el, body: ctx.byId(opts.bodyId), ctx };
      }

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

      if (mobileRoute) {
        el._vtMobileHistory = true;
        try {
          history.pushState({ ...(history.state || {}), vtPanel: opts.id }, '', location.href);
        } catch (_) { /* history를 쓸 수 없는 임베디드 브라우저 */ }
      }
      document.dispatchEvent(new CustomEvent('vt:panel-open', { detail: { id: opts.id, mobileRoute } }));

      // D7: 초기 포커스 — 첫 포커스 가능 요소(대개 닫기 버튼)로. 패널 자신의
      // 검색창 등 더 적절한 대상이 있으면 opts.onOpen()이 openPanel() 리턴 직후
      // 그 위에 다시 focus()를 걸면 된다(quickopen.js가 이렇게 한다).
      const toFocus = _focusables(el)[0];
      if (toFocus) toFocus.focus();

      return { el, body: ctx.byId(opts.bodyId), ctx };
    }

// `id` 대신 **엘리먼트**를 받아도 된다. pane 인스턴스는 루트 id가
// `vt-queue--pane-p3`처럼 인스턴스마다 다르므로, 부르는 쪽이 그 문자열을
// 조립하게 두는 것보다 핸들을 그대로 넘기는 편이 덜 깨진다.
export function closePanel(idOrEl, options = {}) {
      const el = typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
      if (!el || !el.isConnected) return;
      const id = el._vtPanelId || el.id;
      if (el._vtMobileHistory && options.history !== 'replace' && options.fromHistory !== true) {
        try {
          if (history.state?.vtPanel === id) { history.back(); return; }
        } catch (_) { /* 즉시 닫기 폴백 */ }
      }
      if (el._vtMobileHistory && options.history === 'replace') {
        try { history.replaceState(_withoutPanelState(history.state), '', location.href); } catch (_) {}
      }
      if (el._vtTimer) clearInterval(el._vtTimer);
      // 폴링과 짝인 visibilitychange 리스너 — 같이 떼지 않으면 닫힌 패널의
      // 핸들러가 document에 남는다(탭을 오갈 때마다 쌓인다).
      if (el._vtOnVisible) document.removeEventListener('visibilitychange', el._vtOnVisible);
      document.removeEventListener('keydown', el._vtKeyHandler);
      el.remove();
      if (el._vtOnClose) el._vtOnClose();
      document.dispatchEvent(new CustomEvent('vt:panel-close', { detail: { id, mobileRoute: Boolean(el._vtMobileHistory) } }));
      // D7: 트리거로 포커스 복귀 — DOM에서 사라졌을 수 있으니(탭 닫힘 등) 방어.
      if (el._vtTriggerEl && el._vtTriggerEl.isConnected && typeof el._vtTriggerEl.focus === 'function') {
        el._vtTriggerEl.focus();
      }
      // 패널이 레이아웃을 건드렸을 수 있으므로 터미널 크기를 다시 맞춘다.
      try { setTimeout(() => window.fitAndResize(window.activeId), 60); } catch (_) {}
    }

window.addEventListener('popstate', () => {
  const el = document.querySelector('.vt-viewer-backdrop.vt-mobile-route');
  if (el) closePanel(el.id, { fromHistory: true });
});

    // 패널이 열려 있는 동안만 도는 폴링 — 닫히면 스스로 정리한다.
    //
    // 2026-09-18 — **탭이 숨어 있으면 건너뛴다.** 포트 패널은 2초, 큐는 5초
    // 주기라, 패널을 열어 둔 채 탭을 백그라운드로 보내면 아무도 안 보는
    // 화면을 위해 시간당 1,800건이 나간다(ngrok 무료 요청 한도를 그렇게
    // 태웠다). 레일·플릿·dock·모바일 내비는 이미 같은 가드를 갖고 있었고
    // 여기만 빠져 있었다.
    //
    // 돌아왔을 때는 다음 tick을 기다리지 않고 한 번 즉시 따라잡는다 — 안
    // 그러면 포트 패널이 최대 2초, 파일 패널이 10초 동안 낡은 값을 보여준다.
export function setPanelPoll(idOrEl, ms, fn) {
      const el = typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
      if (!el) return;
      // 살아있는지는 **그 엘리먼트 자체**로 본다(id 재조회가 아니라) — pane
      // 인스턴스가 여럿이면 id 조회는 남의 것을 찾아와 "아직 열려 있다"고
      // 오판한다. isConnected는 DOM에서 떨어진 순간 false가 된다.
      const tick = () => {
        if (!el.isConnected) { closePanel(el); return false; }
        if (document.hidden) return true;
        fn();
        return true;
      };
      el._vtTimer = setInterval(tick, ms);
      el._vtOnVisible = () => { if (!document.hidden) tick(); };
      document.addEventListener('visibilitychange', el._vtOnVisible);
    }


window.openPanel = openPanel;
window.closePanel = closePanel;
window.setPanelPoll = setPanelPoll;
