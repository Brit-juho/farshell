// 빠른 열기 = 커맨드 팔레트 (L6) — 세션 · 최근 파일 · 명령을 한 입력창에서 찾는다.
// L1/L8과 같은 문제(진입 동선)를 없애는 목적. 새 서버 엔드포인트 없이 이미 있는
// 데이터(열린 세션, 코드 뷰어의 최근 파일 목록, 고정 명령 리스트)만으로 만든다 —
// 파일시스템 전체를 훑는 진짜 fuzzy 검색은 별도 서버 API가 필요해 스코프 밖으로 뺐다.
//
// F5에서 classic script에서 ES 모듈로 전환 — _loadRecent/showViewer/_selectFile을
// panels/viewer/{tree,shell}.js에서 진짜 import로 받는다. main.js가 이 파일들을
// 전부 정적 import하므로(뷰어를 실제로 연 적 없어도) 항상 준비돼 있어, 예전
// `typeof _loadRecent === 'function'` 방어 체크는 더 이상 필요 없다.
//
// L6: ADR-8("⋯ 메뉴의 전 항목이 팔레트 명령으로 존재해야 한다")에 따라 옛 ⋯ 메뉴
// (index.html #more-menu, 원래 15항목)를 전부 여기서 커버한다. `/`(검색)·`:`(포트)·
// `>`(설정) 접두사는 30-layout-shell.md §5 입력 표를 그대로 구현한다. 상단바와
// 완전히 겹치던 3항목(빠른 열기·터미널 내 검색·코드 뷰어)은 L1 잔여 항목에서
// ⋯ 메뉴 쪽을 제거했다 — 이 파일의 명령 목록(팔레트 자신 + 기본 목록의 "코드
// 뷰어 열기"/"터미널 내 검색")이 그 안전망이다.
import { register as registerKey, getBinding, displayCombo } from './core/keymap.js';
import { openPanel, closePanel } from './panels/panel.js';
import { getAction, registerAction } from './core/dom.js';
import { allSessions, getSession } from './core/store.js';
import { apiFetch, vtFetch } from './core/api.js';
import { API_BASE } from './core/env.js';
import { _loadRecent, _selectFile } from './panels/viewer/tree.js';
import { showViewer } from './panels/viewer/shell.js';
import { switchTo } from './term/session.js';
import { setVtSkin } from './theme.js';
import { buildSessionCard, updateSessionCard, ensurePreviewWs } from './agent/preview.js';

function closeQuickOpen() { closePanel('vt-qopen'); }

// capability 게이팅 — agent/status.js가 /api/capabilities 응답에 따라 `.needs-*`
// 엘리먼트에 직접 style.display='none'을 건다(F4). 여기서 새로 캡을 물어보는 대신
// 그 결과를 재활용한다 — 그 클래스를 가진 대표 엘리먼트가 이미 DOM에 있으므로
// (⋯ 메뉴 항목들), 숨겨져 있으면 이 명령도 숨긴다. 대표 엘리먼트 자체가 없으면
// (해당 안 되는 gate) 기본은 표시.
function _gateOk(gate) {
  if (!gate) return true;
  const el = document.querySelector(`.needs-${gate}`);
  return !el || el.style.display !== 'none';
}

// S3: 하드코딩된 힌트 문자열 대신 키맵 레지스트리에서 **현재** 바인딩을 읽는다.
// 사용자가 재바인딩하면 팔레트 표시도 같이 바뀐다(Warp 패리티: "각 명령 옆에
// 현재 키 바인딩 표시"). 옛날처럼 문자열을 여기 적어두면 재바인딩 후 팔레트가
// 거짓말을 하게 된다.
function _hint(actionId) {
  const b = getBinding(actionId);
  if (!b || b.unavailable) return '';
  return displayCombo(b.combo);
}

// "명령" 섹션(접두사 없음) — ⋯ 메뉴의 「음성 · 파일」+「보기」 그룹 대응.
function _quickOpenCommands() {
  const cmds = [
    { label: '코드 뷰어 열기', hint: _hint('viewer'), action: 'viewer.show', gate: 'fs' },
    { label: '프롬프트 큐', hint: '', action: 'queue.show' },
    { label: '프롬프트 스니펫', hint: '', action: 'snippets.show' },
    { label: '포트 대시보드', hint: '', action: 'ports.show', gate: 'ports' },
    // U2: rail과 1:1 패리티(ADR-8). 사용량 소스가 없으면 gate가 숨긴다.
    { label: '사용량', hint: '', action: 'usage.open', gate: 'usage' },
    { label: '터미널 내 검색', hint: _hint('search'), action: 'search.toggle' },
    { label: '음성 전용 모드', hint: '', action: 'voice.only-toggle', gate: 'voice' },
    { label: '파일 업로드', hint: '', run: () => document.getElementById('file-input')?.click() },
    { label: '새 세션', hint: '', action: 'session.add-menu' },
  ];
  return cmds.filter(c => _gateOk(c.gate) && (c.run || typeof getAction(c.action) === 'function'));
}

function _toggleCheckbox(id) {
  const cb = document.getElementById(id);
  if (!cb) return;
  cb.checked = !cb.checked;
  // moreMenu.js의 initAutoCopy/initAutoMac이 이 이벤트로 localStorage에 반영한다 —
  // 여기서 직접 localStorage를 건드리면 그 값 소유권이 두 곳으로 갈린다.
  cb.dispatchEvent(new Event('change'));
}

// "설정" 섹션(`>` 접두사) — ⋯ 메뉴의 「설정」 그룹 대응. 테마 6종은 index.html의
// .theme-chip을 그대로 읽는다(라벨을 여기 다시 하드코딩하면 칩을 늘릴 때 또
// 어긋난다 — API.md/CLAUDE.md 드리프트와 같은 종류의 실수).
function _quickOpenSettingsCommands() {
  const themeCmds = Array.from(document.querySelectorAll('.theme-chip')).map(chip => ({
    label: `테마 · ${chip.textContent.trim()}`,
    hint: '',
    run: () => setVtSkin(chip.dataset.skin),
  }));
  const cmds = [
    ...themeCmds,
    { label: '푸시 알림', hint: '', action: 'push.toggle', gate: 'push' },
    { label: '드래그 시 자동 복사', hint: '', run: () => _toggleCheckbox('autocopy-checkbox') },
    { label: '맥에서도 열기', hint: '', run: () => _toggleCheckbox('auto-mac-checkbox') },
    { label: '이어폰 미디어키', hint: '', action: 'voice.mediakey-toggle', gate: 'voice' },
    { label: '가이드 보기', hint: '', action: 'guide.show' },
  ];
  return cmds.filter(c => _gateOk(c.gate) && (c.run || typeof getAction(c.action) === 'function'));
}

function _runCommand(c) {
  closeQuickOpen();
  if (c.run) { c.run(); return; }
  const fn = getAction(c.action);
  if (typeof fn === 'function') fn();
}

function _quickOpenSessionItems() {
  return Object.keys(allSessions()).map(id => {
    const nameEl = getSession(id).tabEl?.querySelector('.tab-name');
    return { id, name: (nameEl?.textContent || id).trim() };
  });
}

function _fuzzyMatch(hay, needle) {
  if (!needle) return true;
  return hay.toLowerCase().includes(needle.toLowerCase());
}

// tmux 세션 목록 + 에이전트 배지 — L3 5단계(layout/pane-picker.js)와 동일한
// fetch 조합. 이미 탭인 세션에 한해 라이브 프리뷰 썸네일(ADR-7)을 붙이려고 쓴다.
async function _fetchTmuxCandidates() {
  try {
    const [sessRes, agentsRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/tmux/sessions`),
      apiFetch(`${API_BASE}/api/agents`).catch(() => null),
    ]);
    const tmuxSessions = await sessRes.json();
    const agents = agentsRes ? await agentsRes.json().catch(() => ({})) : {};
    const byWebId = {};
    for (const s of tmuxSessions) { if (s.web_session_id) byWebId[s.web_session_id] = s; }
    return { byWebId, agents };
  } catch (_) {
    return { byWebId: {}, agents: {} };
  }
}

function _openTerminalSearch(query) {
  const fn = getAction('search.toggle');
  if (typeof fn === 'function') fn();
  if (!query) return;
  const searchInput = document.getElementById('search-input');
  if (!searchInput) return;
  searchInput.value = query;
  // search.js가 searchInput 자신에게 건 keydown 리스너(Enter→searchNext)를 그대로
  // 재활용한다 — searchNext()를 다시 import해 결합을 늘리는 대신 기존 DOM 계약을 탄다.
  searchInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
}


function openQuickOpen() {
      const panel = openPanel({
        id: 'vt-qopen',
        ariaLabel: '빠른 열기',
        headHTML: `<div class="vt-vw-title">빠른 열기</div>`,
        extraHTML: `<input class="vt-vw-path" id="vt-qo-input" type="text" spellcheck="false"
          autocapitalize="off" autocomplete="off" placeholder="세션 · 최근 파일 · 명령 검색… ( / 검색 · : 포트 · > 설정 )">`,
        bodyId: 'vt-qo-body',
        bodyHTML: `<div class="vt-vw-loading">불러오는 중…</div>`,
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      const input = document.getElementById('vt-qo-input');
      const body = document.getElementById('vt-qo-body');

      // 최근 파일은 코드 뷰어의 로컬 저장소 목록을 그대로 가져온다 — 뷰어를
      // 실제로 연 적이 없으면 빈 배열이다(localStorage에 저장된 게 없을 뿐).
      const recentFiles = _loadRecent();
      // tmux 후보는 비동기로 채워진다 — 도착 전까지는 세션이 평문 행으로만
      // 보이다가, 도착하면 같은 쿼리로 다시 그려 썸네일이 얹힌다(깜빡임 최소화).
      let tmuxCandidates = { byWebId: {}, agents: {} };
      _fetchTmuxCandidates().then((c) => { tmuxCandidates = c; render(input.value); });

      const section = (title, items, renderRow) => {
        if (!items.length) return;
        const head = document.createElement('div');
        head.className = 'vt-qo-section-head';
        head.textContent = title;
        body.appendChild(head);
        items.forEach(item => body.appendChild(renderRow(item)));
      };

      const emptyState = () => {
        const empty = document.createElement('div');
        empty.className = 'vt-vw-empty';
        empty.textContent = '일치하는 항목이 없습니다.';
        body.appendChild(empty);
      };

      const cmdRow = (c) => {
        const row = document.createElement('div');
        row.className = 'vt-vw-row vt-qo-row';
        const label = document.createElement('div');
        label.className = 'vt-vw-name';
        label.textContent = c.label;
        row.appendChild(label);
        if (c.hint) {
          const hint = document.createElement('div');
          hint.className = 'vt-qo-hint';
          hint.textContent = c.hint;
          row.appendChild(hint);
        }
        row.addEventListener('click', () => _runCommand(c));
        return row;
      };

      const sessionRow = (s) => {
        const tmuxSess = tmuxCandidates.byWebId[s.id];
        if (tmuxSess) {
          // ADR-7: 프리뷰 카드는 썸네일로 축소돼 세션 시트·팔레트에서 재사용된다 —
          // pane-picker.js(L3 5단계)와 완전히 같은 빌더를 쓴다.
          const card = buildSessionCard(tmuxSess, () => { switchTo(s.id); closeQuickOpen(); });
          card.classList.add('vt-qo-row', 'vt-qo-session-card');
          updateSessionCard(card, tmuxSess, tmuxCandidates.agents[tmuxSess.name]);
          ensurePreviewWs(tmuxSess.name);
          return card;
        }
        const row = document.createElement('div');
        row.className = 'vt-vw-row vt-qo-row';
        row.textContent = s.name;
        row.addEventListener('click', () => { switchTo(s.id); closeQuickOpen(); });
        return row;
      };

      function renderDefault(q) {
        const sessionItems = _quickOpenSessionItems().filter(s => _fuzzyMatch(s.name, q));
        const fileItems = recentFiles.filter(p => _fuzzyMatch(p, q));
        const cmdItems = _quickOpenCommands().filter(c => _fuzzyMatch(c.label, q));

        body.innerHTML = '';
        if (!sessionItems.length && !fileItems.length && !cmdItems.length) { emptyState(); return; }

        section('세션', sessionItems, sessionRow);

        section('최근 파일', fileItems, (p) => {
          const row = document.createElement('div');
          // vt-vw-recent-row가 세로 배치(파일명 위 · 경로 아래)를 준다 — 기본
          // vt-vw-row는 가로 flex라 두 줄이 나란히 눌려버린다.
          row.className = 'vt-vw-row vt-vw-recent-row vt-qo-row';
          const name = document.createElement('div');
          name.className = 'vt-vw-name';
          name.textContent = p.split('/').pop() || p;
          const dir = document.createElement('div');
          dir.className = 'vt-vw-recent-dir';
          dir.textContent = p.split('/').slice(0, -1).join('/') || '.';
          row.appendChild(name);
          row.appendChild(dir);
          row.addEventListener('click', async () => {
            closeQuickOpen();
            await showViewer();
            _selectFile(p, null);
          });
          return row;
        });

        section('명령', cmdItems, cmdRow);
      }

      // `/` 접두사 — 터미널 내 검색으로 바로 넘긴다(입력 표: §5). 세션·파일·명령
      // 목록은 감추고 "이 문자열로 검색"이라는 단일 행만 보여준다.
      function renderSearchMode(q) {
        body.innerHTML = '';
        const row = document.createElement('div');
        row.className = 'vt-vw-row vt-qo-row';
        row.textContent = q ? `"${q}" 터미널에서 검색` : '터미널 내 검색 열기';
        row.addEventListener('click', () => { closeQuickOpen(); _openTerminalSearch(q); });
        body.appendChild(row);
      }

      // `:` 접두사 — 포트. 킬은 여기서 하지 않는다(오조작 방지) — 행을 고르면
      // 포트 대시보드를 열어 거기서 확인 후 조작하게 한다.
      // rawQ: 접두사를 포함한 원본(트리밍만 된) 쿼리 — 비동기 응답이 도착했을 때
      // "그 사이 입력이 바뀌었는지"를 정확히 비교하기 위한 스냅샷이다(q 자체는
      // 접두사·앞뒤 공백을 제거한 필터링용 값이라 이 비교엔 못 쓴다).
      function renderPortMode(q, rawQ) {
        if (!_gateOk('ports')) {
          body.innerHTML = '<div class="vt-vw-empty">포트 대시보드를 사용할 수 없는 환경입니다.</div>';
          return;
        }
        body.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
        vtFetch('/api/ports').then((d) => {
          if (input.value.trim() !== rawQ) return; // 그 사이 쿼리가 바뀌었으면 버림
          const ports = (d.ports || []).filter(p => _fuzzyMatch(`${p.port} ${p.cmd || ''}`, q));
          body.innerHTML = '';
          if (!ports.length) { body.innerHTML = '<div class="vt-vw-empty">일치하는 포트가 없습니다.</div>'; return; }
          section('포트', ports, (p) => {
            const row = document.createElement('div');
            row.className = 'vt-vw-row vt-qo-row';
            const label = document.createElement('div');
            label.className = 'vt-vw-name';
            label.textContent = `:${p.port} · ${p.cmd || ''}`;
            row.appendChild(label);
            row.addEventListener('click', () => {
              closeQuickOpen();
              const fn = getAction('ports.show');
              if (typeof fn === 'function') fn();
            });
            return row;
          });
        }).catch((e) => {
          if (input.value.trim() !== rawQ) return;
          body.innerHTML = `<div class="vt-vw-empty">${e.message}</div>`;
        });
      }

      // `>` 접두사 — 설정 항목(⋯ 메뉴 「설정」 그룹 대응).
      function renderSettingsMode(q) {
        const items = _quickOpenSettingsCommands().filter(c => _fuzzyMatch(c.label, q));
        body.innerHTML = '';
        if (!items.length) { emptyState(); return; }
        section('설정', items, cmdRow);
      }

      function render(query) {
        const q = query.trim();
        if (q.startsWith('/')) return renderSearchMode(q.slice(1).trim());
        if (q.startsWith(':')) return renderPortMode(q.slice(1).trim(), q);
        if (q.startsWith('>')) return renderSettingsMode(q.slice(1).trim());
        renderDefault(q);
      }

      input.addEventListener('input', () => render(input.value));
      input.addEventListener('keydown', (ev) => {
        // Enter는 목록의 첫 항목을 연다 — 소프트 키보드에서 화면 스크롤 없이 바로 실행.
        if (ev.key !== 'Enter') return;
        const first = body.querySelector('.vt-qo-row');
        if (first) first.click();
      });
      render('');
      // 패널이 DOM에 붙은 다음 포커스해야 소프트 키보드가 뜬다(레이아웃 전에 focus하면
      // iOS Safari가 무시하는 경우가 있다).
      requestAnimationFrame(() => input.focus());
}

// F3(c): data-action 위임용 등록.
registerAction('quickopen.open', () => openQuickOpen());

// L6: Mod+K 전역 단축키. search.js의 Ctrl/Cmd+F와 같은 패턴(입력 필드 포커스
// 여부를 가리지 않는다 — 기존 관행과 일치, 그리고 openPanel 자체가 토글이라
// 실수로 눌러도 다시 누르면 닫힌다). 브라우저 기본 동작(주소창 포커스 등)을
// 막기 위해 항상 preventDefault.
// S3: 키맵 레지스트리 경유(하드코딩 제거). 팔레트가 각 명령 옆에 현재 바인딩을
// 표시하는 것도 같은 레지스트리를 읽는다.
registerKey('palette', () => openQuickOpen());
