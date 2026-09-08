// L4 — 좌측 rail. ADR-8("⋯ 폐지 → 좌측 rail(포인터) + 커맨드 팔레트(키보드)")의
// 포인터 경로 절반. 팔레트(quickopen.js)와 "내용은 동일"해야 한다(문서
// 30-layout-shell.md §3) — 그래서 파일/큐/스니펫/포트는 이 파일이 새로 뭔가를
// 그리지 않는다. 그 네 버튼은 index.html에서 이미 `data-action="viewer.show"`
// 등을 달고 있어 core/dom.js의 전역 클릭 위임이 알아서 기존 모달을 연다.
// 이 파일이 직접 관리하는 건 "세션"(진짜 새 임베디드 패널)과 "설정"
// (⋯ 메뉴의 마지막 남은 그룹을 그대로 옮긴 정적 마크업)뿐이다.
import { apiFetch, vtFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { allSessions, getSession, activeSessionId, subscribe } from '../core/store.js';
import { getAction } from '../core/dom.js';
import { switchTo, removeSession, renameSession, createSession } from '../term/session.js';
import { buildSessionCard, updateSessionCard, ensurePreviewWs } from '../agent/preview.js';
import { wireRatioResizer } from './resizer.js';
import { COMPACT_MAX, REGULAR_MAX } from './breakpoints.js';
import { icon } from '../ui/icons.js';
import { whenAuthed } from '../agent/status.js';
import { getStatus, onStatusChange, applyStatusDot, sortByUrgency } from '../agent/state.js';
import { mountClients } from './clients.js';

const MIN_W = 240, MAX_W = 480, DEFAULT_W = 280;
// 'file'/'queue'/'snippets'/'ports'는 data-action 버튼이라 여기서 다루지 않는다(위 헤더 주석).
const PANEL_ITEMS = ['session', 'settings'];

const rail = document.getElementById('vt-rail');
const panel = document.getElementById('vt-rail-panel');
const panelTitle = document.getElementById('vt-rail-panel-title');
const panelBody = document.getElementById('vt-rail-panel-body');
const resizer = document.getElementById('vt-rail-resizer');
// 설정 패널의 정적 컨텐츠 — 한 번만 참조를 잡아둔다. body를 비울 때마다
// (innerHTML='' 대신 replaceChildren 등으로) DOM에서 떨어져 나가지만
// 이 변수 자체는 살아있는 노드를 계속 가리키므로 다음에 다시 붙일 수 있다.
const settingsTpl = document.getElementById('vt-rail-settings-tpl');

if (!rail || !panel) {
  // 테스트 등 index.html 마크업이 없는 환경 — 조용히 아무 것도 안 한다.
  // (다른 모듈들의 top-level 방어 패턴과 동일)
} else {
  initRail();
}

function initRail() {
  let openItem = null;   // null | 'session' | 'settings'
  let _clientsCleanup = null;   // C3 — 「연결된 화면」 폴링 정리
  let panelW = DEFAULT_W;

  function isOverlayWidth() {
    return window.innerWidth < REGULAR_MAX; // regular(720~1023) = 오버레이, wide = 밀어냄
  }

  function applyPanelWidth(w) {
    panelW = Math.max(MIN_W, Math.min(MAX_W, Math.round(w)));
    document.documentElement.style.setProperty('--vt-rail-panel-w', panelW + 'px');
  }

  function saveState() {
    vtFetch('/api/workspace', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ui: { rail: { open: openItem, width: panelW } } }),
    }).catch(() => {}); // 저장 실패는 조용히 무시 — ADR-5: 실패해도 UI는 캐시(현재 상태)로 계속 동작
  }

  function setActiveBtn(item) {
    rail.querySelectorAll('.vt-rail-btn[data-rail]').forEach((b) => {
      const isThis = b.dataset.rail === item;
      b.classList.toggle('active', isThis);
      if (PANEL_ITEMS.includes(b.dataset.rail)) b.setAttribute('aria-pressed', isThis ? 'true' : 'false');
    });
  }

  function closePanel({ persist = true } = {}) {
    if (_clientsCleanup) { _clientsCleanup(); _clientsCleanup = null; }
    if (!openItem) return;
    openItem = null;
    panel.hidden = true;
    document.body.classList.remove('vt-rail-panel-open', 'vt-rail-overlay');
    setActiveBtn(null);
    if (persist) saveState();
  }

  async function openPanelItem(item, { persist = true } = {}) {
    if (openItem === item) { closePanel(); return; }
    openItem = item;
    panel.hidden = false;
    document.body.classList.add('vt-rail-panel-open');
    document.body.classList.toggle('vt-rail-overlay', isOverlayWidth());
    setActiveBtn(item);
    if (item === 'session') {
      panelTitle.textContent = '세션';
      await renderSessionPanel();
    } else if (item === 'settings') {
      panelTitle.textContent = '설정';
      panelBody.replaceChildren(settingsTpl);
      settingsTpl.hidden = false;
    }
    if (persist) saveState();
  }

  // ── 세션 패널 ────────────────────────────────────────────────────────────
  // quickopen.js의 세션 썸네일과 완전히 같은 조합(tmux 세션 목록 + 라이브
  // 프리뷰 카드) — ADR-7 "프리뷰 카드는 썸네일로 축소돼 재사용된다"의 또
  // 다른 소비처. 팔레트와 달리 이 패널은 "열어둔 채 작업"하는 게 목적이라
  // 세션 선택 후에도 패널을 닫지 않는다.
  function _sessionLabel(id) {
    const s = getSession(id);
    return s?.tabEl?.querySelector('.tab-name')?.textContent?.trim() || id.slice(0, 8);
  }

  async function _fetchTmuxByWebId() {
    try {
      const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
      const list = await res.json();
      const map = {};
      for (const s of list) if (s.web_session_id) map[s.web_session_id] = s;
      return map;
    } catch (_) { return {}; }
  }

  async function renderSessionPanel() {
    panelBody.replaceChildren();
    const listEl = document.createElement('div');
    listEl.className = 'vt-rail-session-list';
    panelBody.appendChild(listEl);

    const newBtn = document.createElement('button');
    newBtn.type = 'button';
    newBtn.className = 'vt-rail-session-new';
    newBtn.innerHTML = `${icon('plus', 14)}새 세션`;
    newBtn.addEventListener('click', () => createSession());
    panelBody.insertBefore(newBtn, listEl);

    // C3: 활성 세션이 tmux면 그 세션에 붙은 화면 목록을 보여준다. 세션이
    // 바뀌면 이전 블록의 폴링을 반드시 끊어야 한다(안 그러면 패널을 열고 닫을
    // 때마다 타이머가 쌓인다) — 아래 _clientsCleanup이 그 역할.
    if (_clientsCleanup) { _clientsCleanup(); _clientsCleanup = null; }
    const activeSess = getSession(activeSessionId());
    const activeTmux = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
    if (activeTmux) _clientsCleanup = mountClients(panelBody, activeTmux);

    const footer = document.createElement('div');
    footer.className = 'vt-rail-session-footer';
    // L1(과도기)~picker.js가 임시로 들고 있던 세션 전용 동작 2개의 최종 착지점.
    const macBtn = document.createElement('button');
    macBtn.type = 'button'; macBtn.className = 'vt-rail-session-footer-item';
    macBtn.textContent = '이 세션 맥에서 열기';
    macBtn.addEventListener('click', () => getAction('session.open-on-mac')?.());
    const tmuxBtn = document.createElement('button');
    tmuxBtn.type = 'button'; tmuxBtn.className = 'vt-rail-session-footer-item';
    tmuxBtn.textContent = 'tmux 세션 목록';
    tmuxBtn.addEventListener('click', () => getAction('session.tmux-list')?.());
    footer.append(macBtn, tmuxBtn);
    panelBody.appendChild(footer);

    const entries = Object.entries(allSessions());
    if (!entries.length) {
      listEl.innerHTML = '<p class="vt-rail-session-empty">열려 있는 세션이 없습니다.</p>';
      return;
    }
    const tmuxByWebId = await _fetchTmuxByWebId();
    if (openItem !== 'session') return; // 그 사이 패널이 바뀌었으면 그리지 않는다
    const activeIdNow = activeSessionId();
    // A5(2-6): 내 개입이 필요한 것이 맨 위 — waiting > done > working > idle.
    // **탭 순서는 정렬하지 않는다**(사용자가 드래그로 정한 의도라 건드리면
    // 안 된다). 정렬은 rail·팔레트·세션 시트처럼 "찾아 들어가는" 목록에만.
    const sorted = sortByUrgency(entries, ([id]) => tmuxByWebId[id]?.name);
    for (const [id, s] of sorted) {
      const tmuxSess = tmuxByWebId[id];
      let row;
      if (tmuxSess) {
        // ADR-7 재사용 — quickopen.js/layout/pane-picker.js와 동일한 빌더.
        row = buildSessionCard(tmuxSess, () => switchTo(id));
        row.classList.add('vt-rail-session-row', 'vt-rail-session-card');
        updateSessionCard(row, tmuxSess, undefined);
        ensurePreviewWs(tmuxSess.name);
      } else {
        row = document.createElement('div');
        row.className = 'vt-rail-session-row';
        const select = document.createElement('button');
        select.type = 'button'; select.className = 'vt-rail-session-select';
        select.textContent = _sessionLabel(id);
        select.addEventListener('click', () => switchTo(id));
        row.appendChild(select);
      }
      row.classList.toggle('active', id === activeIdNow);
      const st = getStatus(tmuxSess?.name);
      applyStatusDot(row, st);
      row.dataset.state = st;
      const actions = document.createElement('div');
      actions.className = 'vt-rail-session-actions';
      const renameBtn = document.createElement('button');
      renameBtn.type = 'button'; renameBtn.className = 'vt-rail-session-action';
      renameBtn.innerHTML = icon('pencil', 13);
      renameBtn.setAttribute('aria-label', `${_sessionLabel(id)} 이름 변경`);
      renameBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const next = window.prompt('새 세션 이름', _sessionLabel(id));
        if (next === null) return;
        if (await renameSession(id, next) && openItem === 'session') renderSessionPanel();
      });
      const closeBtn = document.createElement('button');
      closeBtn.type = 'button'; closeBtn.className = 'vt-rail-session-action';
      closeBtn.innerHTML = icon('x', 13);
      closeBtn.setAttribute('aria-label', `${_sessionLabel(id)} 닫기`);
      closeBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        await removeSession(id);
        if (openItem === 'session') renderSessionPanel();
      });
      actions.append(renameBtn, closeBtn);
      row.appendChild(actions);
      listEl.appendChild(row);
    }
  }

  // ── 클릭 배선 ────────────────────────────────────────────────────────────
  // file/queue/ports 버튼은 data-action을 갖고 있어 core/dom.js의 document
  // 위임이 별도로 처리한다(이 리스너보다 나중에, stopPropagation 안 함) —
  // 여기서는 오직 'session'/'settings'(data-action이 없는 두 버튼)만 다룬다.
  rail.addEventListener('click', (e) => {
    const btn = e.target.closest('.vt-rail-btn[data-rail]');
    if (!btn || btn.hasAttribute('disabled')) return;
    const item = btn.dataset.rail;
    if (!PANEL_ITEMS.includes(item)) return; // file/queue/ports/usage
    openPanelItem(item);
  });
  panel.querySelector('.vt-rail-panel-close')?.addEventListener('click', () => closePanel());
  // 오버레이 모드에서만 바깥 클릭으로 닫는다 — wide(밀어냄)에서는 열어둔 채
  // 작업하는 게 목적이라 바깥을 클릭해도 안 닫혀야 한다.
  document.addEventListener('click', (e) => {
    if (!openItem || !document.body.classList.contains('vt-rail-overlay')) return;
    if (panel.contains(e.target) || rail.contains(e.target)) return;
    closePanel();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && openItem && document.body.classList.contains('vt-rail-overlay')) closePanel();
  });

  // 리사이저 — layout/resizer.js의 wireRatioResizer를 "컨테이너 크기=1"로 불러
  // ratio 값 자체를 그대로 픽셀로 쓴다(L3 3단계 주석이 예고한 재사용 방식 —
  // 나눗셈을 항등으로 만들어 절대 픽셀 드래그에 그대로 쓴다).
  if (resizer) {
    wireRatioResizer(resizer, {
      dir: 'row',
      getContainerSize: () => 1,
      getStartRatio: () => panelW,
      onRatio: (px) => applyPanelWidth(px),
      onEnd: () => saveState(),
    });
  }

  // 창 크기가 바뀌어 regular↔wide 경계를 넘으면 오버레이/밀어냄 상태를
  // 다시 판정한다(L3 2단계의 compact 렌더 모드 재판정과 같은 이유 — 트리/패널
  // 상태는 안 바뀌어도 렌더 방식은 뷰포트에 달려 있다).
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!openItem) return;
      document.body.classList.toggle('vt-rail-overlay', isOverlayWidth());
    }, 120);
  });

  // ── 배지(큐 대기 수 · 포트 수) ───────────────────────────────────────────
  // "큐 기능이 안 쓰이는 진짜 이유가 이 가시성 부재다"(문서 §3) — 패널을 열지
  // 않아도 보여야 의미가 있으므로 상시(탭이 보이는 동안만) 가볍게 폴링한다.
  // L7: 큐 배지는 rail(regular/wide)과 keybar(compact, #keybar-badge-queue)
  // 둘 다 같은 값을 보여준다 — 폴링을 두 번 하지 않고 한 fetch 결과를 두 id에
  // 동시에 반영한다(_setBadge가 없는 id는 조용히 건너뛰므로 어느 쪽이 실제로
  // DOM에 있든 안전).
  function _setBadge(id, n) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!n) { el.hidden = true; return; }
    el.textContent = n > 99 ? '99+' : String(n);
    el.hidden = false;
  }
  async function refreshBadges() {
    if (document.hidden) return;
    try {
      const q = await vtFetch('/api/queue');
      const n = (q.items || []).length;
      _setBadge('vt-rail-badge-queue', n);
      _setBadge('keybar-badge-queue', n);
    } catch (_) { /* 다음 주기 재시도 */ }
    // agent/status.js가 !caps.ports일 때 .needs-ports 엘리먼트에 직접
    // style.display='none'을 건다 — 그 결과만 재사용(새로 capabilities를 안 물어봄).
    // rail의 포트 버튼 기준으로만 게이팅한다(compact엔 포트 keybar 슬롯이
    // 없어 배지도 없음 — §3 6항목 중 포트는 rail 전용이라 keybar엔 없다).
    const portsBtn = document.getElementById('vt-rail-ports');
    if (portsBtn && portsBtn.style.display !== 'none') {
      try {
        const p = await vtFetch('/api/ports');
        _setBadge('vt-rail-badge-ports', (p.ports || []).filter((x) => !x.protected).length);
      } catch (_) { /* 다음 주기 재시도 */ }
    }
  }
  whenAuthed(() => {
    refreshBadges();
    setInterval(refreshBadges, 15000);
  });

  // ── 부팅 시 상태 복원 (ADR-5: /api/workspace가 단일 진실) ─────────────────
  whenAuthed(async () => {
    try {
      const ws = await vtFetch('/api/workspace');
      const railState = ws?.ui?.rail;
      if (railState?.width) applyPanelWidth(railState.width);
      else applyPanelWidth(DEFAULT_W);
      // compact(<720px)에서는 rail 자체가 안 보이므로 패널을 미리 열어두지
      // 않는다 — 다시 넓어지면 그때 사용자가 rail을 눌러 연다(상태 자체는
      // 보존되지만 자동으로 튀어나오지 않음, L8 모바일 케이스와 같은 원칙:
      // "숨겨진 표면이 화면 전환만으로 갑자기 나타나지 않는다").
      if (railState?.open && PANEL_ITEMS.includes(railState.open) && window.innerWidth >= COMPACT_MAX) {
        openPanelItem(railState.open, { persist: false });
      }
    } catch (_) {
      applyPanelWidth(DEFAULT_W);
    }
  });

  // core/store.js의 subscribe(구독)로 세션 추가/삭제/전환 시 열려 있으면
  // 다시 그린다 — 다른 화면(pane-picker 등)에서 세션이 바뀌어도 패널이
  // stale 상태로 남지 않게.
  subscribe(() => { if (openItem === 'session') renderSessionPanel(); });
  // A5: 상태가 바뀌면 dot뿐 아니라 **정렬 순서**도 달라지므로 통째로 다시 그린다.
  onStatusChange(() => { if (openItem === 'session') renderSessionPanel(); });
}
