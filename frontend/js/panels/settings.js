// S4 — 설정 화면. 진입: `Mod+,`(키맵 레지스트리) 또는 rail ⚙.
//
// 지금까지 설정 표면은 **없었다**. 값은 있는데 UI가 없는 항목이 여럿이었고
// (`screenReaderMode`는 콘솔로만 바꿀 수 있었다), 있는 것도 ⋯ 메뉴 체크박스와
// 헤더 버튼으로 흩어져 있었다. 이 화면이 그 단일 표면이 된다.
//
// 렌더링 원칙:
//   - **스키마에서 그린다.** 항목을 여기 하드코딩하면 core/settings.js 스키마와
//     두 벌이 되어 어긋난다. 섹션 정의는 "어떤 키를 어떤 컨트롤로 보여줄지"만
//     정하고, 값·범위·기본값은 전부 스토어에서 읽는다.
//   - **변경 즉시 반영.** 저장 버튼이 없다(스토어가 즉시 반영 + 서버 저장).
//   - 못 하는 것은 **숨기지 말고 이유와 함께 보여준다** — 일반 브라우저 탭에서
//     쓸 수 없는 키 바인딩, 새 탭부터 적용되는 항목 등.
import { openPanel, closePanel } from './panel.js';
import { renderItems, group } from './settings/controls.js';
import { renderKeymapSection } from './settings/sections/keymap.js';
import { renderVoiceSection, cleanupVoiceClients } from './settings/sections/voice.js';
import { renderAppearanceSection } from './settings/sections/appearance.js';
import { renderAgentsSection } from './settings/sections/agents.js';
import { renderSecuritySection } from './settings/sections/security.js';
import { renderScrollbackSection } from './settings/sections/scrollback.js';
import { renderAboutSection } from './settings/sections/about.js';
import { getTabs, getActiveTabId } from '../layout/store.js';
import { getAction } from '../core/dom.js';
import { subscribePersistence } from '../core/settings.js';
import { vtFetch } from '../core/api.js';

const PANEL_ID = 'vt-settings';

function renderScrollbackGroup() {
  const frag = document.createDocumentFragment();
  frag.appendChild(renderItems({ items: [
    { key: 'terminal.scrollback', label: '스크롤백 줄 수', kind: 'range', step: 500, unit: '줄', presets: [2000, 5000, 10000] },
  ] }));
  frag.appendChild(renderScrollbackSection());
  return frag;
}

// 섹션 정의 — 자주 함께 쓰는 항목을 7개 범주로 묶는다. 「세션」은 rail 자체가
// 그 화면이라 여기서 중복 노출하지 않는다. 「음성」의 푸시 구독 토글·음성 전용 모드는 원래
// 이 설명대로 "다른 자리"(⋯ 메뉴 → legacy rail 플라이아웃)에 있었지만,
// 2026-09-18 레일 교체로 그 자리 자체가 도달 불가능해져 이 섹션들로 옮겼다
// (voice.js/appearance.js 참고) — 지금은 중복이 아니라 유일한 자리다.
const TERMINAL_GROUPS = [
  {
    label: '표시',
    items: [
      { key: 'terminal.fontSize', label: '글자 크기', kind: 'range', unit: 'px' },
      { key: 'terminal.cursorStyle', label: '커서 모양', kind: 'select',
        labels: { block: '블록', underline: '밑줄', bar: '막대' } },
      { key: 'terminal.cursorBlink', label: '커서 깜빡임', kind: 'bool' },
      // 2026-09-18 — ui/settings-toggles.js가 진작 스토어에 묶어뒀지만
      // (E2), 그 체크박스가 살던 자리(legacy rail 플라이아웃)가 오늘
      // 도달 불가능해지며 이 키를 끄고 켤 화면이 아예 없어졌다.
      { key: 'session.openOnMac', label: '새 세션 생성 시 맥에서도 iTerm 열기', kind: 'bool' },
    ],
  },
  {
    label: '입력 및 선택',
    items: [
      { key: 'mouse.forwardToApp', label: '앱에 마우스 이벤트 전달',
        // S1 spike로 구현 가능함이 확인된 항목. 끄면 iTerm2 기본 동작이 된다.
        help: '끄면 vim·tmux가 마우스를 잡아도 항상 드래그로 선택할 수 있습니다 (iTerm2 기본 동작).',
        kind: 'bool' },
      { key: 'mouse.autocopyOnSelect', label: '드래그 시 자동 복사', kind: 'bool' },
      { key: 'mouse.touchTapToApp', label: '터치 탭을 앱에 전달',
        help: '터치 기기에서 짧은 탭을 앱으로 넘겨 커서를 옮깁니다.', kind: 'bool' },
    ],
  },
  {
    label: '접근성',
    items: [
      { key: 'a11y.screenReader', label: '스크린 리더 모드', kind: 'select',
        labels: { auto: '자동 (터치 기기에서 켬)', on: '항상 켬', off: '항상 끔' },
        help: '새로 여는 탭부터 적용됩니다 — 터미널 내부 구조가 달라져 실행 중에는 바꿀 수 없습니다.' },
    ],
  },
  {
    label: '스크롤백', custom: renderScrollbackGroup,
  },
];

function renderTerminalSection() {
  const frag = document.createDocumentFragment();
  for (const part of TERMINAL_GROUPS) {
    frag.appendChild(group(part.label, part.custom ? part.custom() : renderItems(part)));
  }
  return frag;
}

function renderAgentSection() {
  const frag = document.createDocumentFragment();
  frag.appendChild(group('감지 상태', renderAgentsSection()));
  frag.appendChild(group('MCP 서버', renderMcpSection()));
  return frag;
}

const SECTIONS = [
  { id: 'terminal', label: '터미널', description: '표시, 입력, 접근성, 기록 방식을 설정합니다.', custom: renderTerminalSection },
  // N42(60-settings-palette.md §7) — 알림·음성 진단. 다섯 항목 전부 서버
  // API는 이미 있고(push.py·voice.py·system.py) 이 섹션은 그 배선일 뿐이다.
  { id: 'voice', label: '음성 및 알림', description: '알림과 음성 입력 엔진을 관리합니다.', custom: renderVoiceSection },
  // N9/N45(80-multihost-agents.md §2) — CLI별 승인 대기 감지 커버리지 표.
  { id: 'agents', label: '에이전트', description: '에이전트 감지 상태와 MCP 서버를 확인합니다.', custom: renderAgentSection },
  { id: 'keymap', label: '키맵', description: '단축키와 터미널 전달 여부를 바꿉니다.', custom: () => renderKeymapSection(rerender) },
  // N14 — Ghostty/Warp 테마 가져오기 + 기본 6스킨 칩(2026-09-18부터 이 탭이
  // 유일한 자리 — 위 주석 참고).
  { id: 'appearance', label: '모양', description: '스킨을 고르거나 터미널 테마를 가져옵니다.', custom: renderAppearanceSection },
  { id: 'security', label: '계정 및 보안', description: '로그인, OTP, 등록 기기와 현재 세션을 확인합니다.', custom: renderSecuritySection },
  // N13(80-multihost-agents.md §3) — 토글 자체는 스키마 항목이지만, 디스크
  // 사용량은 서버에 물어봐야 하는 값이라(보안 섹션과 같은 이유로) custom.
  { id: 'about', label: '정보 및 진단', description: '훅과 사용량 공급자의 연결 상태를 진단합니다.', custom: renderAboutSection },
];

const SEARCH_ITEMS = [
  ['terminal', '글자 크기'], ['terminal', '커서 모양'], ['terminal', '커서 깜빡임'],
  ['terminal', '새 세션 생성 시 맥에서도 iTerm 열기'], ['terminal', '앱에 마우스 이벤트 전달'],
  ['terminal', '드래그 시 자동 복사'], ['terminal', '터치 탭을 앱에 전달'],
  ['terminal', '스크린 리더 모드'], ['terminal', '스크롤백 줄 수'], ['terminal', '스크롤백 저장'],
  ['voice', '웹 푸시 알림'], ['voice', '완료 알림'], ['voice', '음성 모델'],
  ['voice', '로컬 음성 입력'], ['voice', '음성 전용 모드'],
  ['agents', '에이전트 감지 상태'], ['agents', 'MCP 서버'],
  ['keymap', '단축키와 터미널 전달'],
  ['appearance', '스킨'], ['appearance', '테마 가져오기'],
  ['security', '로그인과 OTP'], ['security', '등록 기기'], ['security', '로그아웃'],
  ['about', '에이전트 훅'], ['about', '사용량 공급자'], ['about', '버전과 진단'],
].map(([sectionId, label]) => ({ sectionId, label }));

const SEARCH_ALIASES = {
  '글자 크기': '폰트 글꼴 확대 축소',
  '커서 모양': '캐럿',
  '스킨': '테마 색상 컬러',
  '웹 푸시 알림': '브라우저 알림 구독',
  'MCP 서버': '도구 플러그인',
  '등록 기기': '디바이스 장치',
  '에이전트 훅': 'hook codex claude',
};

// ── 「MCP」 ───────────────────────────────────────────────────────────────
// 97번 — 조회·토글. 실제 화면은 지연 청크(panels/mcp.js)다. 워크트리 id는 여기서
// 구해 넘긴다 — 지연 청크가 layout/store.js를 직접 import하면 탭 상태가 복제된다
// (실측: 싱글톤이 통째로 panels.js로 옮겨갔다).
function renderMcpSection() {
  const host = document.createElement('div');
  host.className = 'vt-set-sechost';
  const tab = getTabs().find((t) => t.id === getActiveTabId());
  import('./mcp.js')
    .then((m) => m.mount(host, (tab && tab.worktreeId) || null))
    .catch(() => { host.textContent = '불러오지 못했습니다'; });
  return host;
}

// ── 패널 ──────────────────────────────────────────────────────────────────
let _activeSection = 'terminal';
let _mobileHome = true;
let _persistenceUnsub = null;

function isCompact() { return window.matchMedia('(max-width:719px)').matches; }

function openTool(action) {
  closePanel(PANEL_ID);
  getAction(action)?.();
}

function renderSearch() {
  const root = document.createElement('div'); root.className = 'vt-set-search';
  const input = document.createElement('input');
  input.type = 'search'; input.className = 'vt-set-search-input';
  input.placeholder = '설정 검색'; input.setAttribute('aria-label', '설정 검색');
  const results = document.createElement('div'); results.className = 'vt-set-search-results';
  const update = () => {
    const query = input.value.trim().toLocaleLowerCase();
    results.innerHTML = '';
    if (!query) { results.hidden = true; return; }
    const matches = SEARCH_ITEMS.filter((item) => {
      const section = SECTIONS.find((s) => s.id === item.sectionId);
      return `${item.label} ${SEARCH_ALIASES[item.label] || ''} ${section?.label || ''} ${section?.description || ''}`.toLocaleLowerCase().includes(query);
    });
    results.hidden = false;
    if (!matches.length) {
      const empty = document.createElement('div'); empty.className = 'vt-set-search-empty'; empty.textContent = '일치하는 설정이 없습니다.';
      results.appendChild(empty); return;
    }
    for (const item of matches.slice(0, 8)) {
      const section = SECTIONS.find((s) => s.id === item.sectionId);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'vt-set-search-result';
      button.innerHTML = `<strong>${item.label}</strong><small>${section.label}</small>`;
      button.addEventListener('click', () => {
        _activeSection = item.sectionId; _mobileHome = false;
        rerender(item.label);
      });
      results.appendChild(button);
    }
  };
  input.addEventListener('input', update);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && input.value) { input.value = ''; update(); e.stopPropagation(); }
  });
  root.append(input, results);
  return root;
}

function renderMobileHome(body, focusSearch = false) {
  body.classList.add('mobile-home');
  const home = document.createElement('div');
  home.className = 'vt-set-mobile-home';
  home.appendChild(renderSearch());
  const addList = (title, items) => {
    const h = document.createElement('h2'); h.className = 'vt-set-mobile-group'; h.textContent = title; home.appendChild(h);
    for (const item of items) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'vt-set-mobile-link';
      b.innerHTML = `<span><strong>${item.label}</strong>${item.description ? `<small>${item.description}</small>` : ''}</span><span aria-hidden="true">›</span>`;
      b.addEventListener('click', () => {
        if (item.action) openTool(item.action);
        else { _activeSection = item.id; _mobileHome = false; rerender(''); }
      });
      home.appendChild(b);
    }
  };
  addList('도구', [
    { label: '파일', description: '업로드와 공유 링크', action: 'files.show' },
    { label: '포트', description: '실행 중인 로컬 서비스', action: 'ports.show' },
    { label: '사용량', description: 'AI 도구 한도와 누적 사용량', action: 'usage.open' },
  ]);
  addList('설정', SECTIONS);
  body.appendChild(home);
  if (focusSearch) Promise.resolve().then(() => home.querySelector('.vt-set-search-input')?.focus());
}

function rerender(focusTarget = null) {
  const body = document.getElementById('vt-set-body');
  if (!body) return;
  // 「음성」 섹션을 떠나면 clients.js 폴링 타이머를 반드시 끊는다(rail.js의
  // _clientsCleanup과 같은 규칙 — 안 그러면 패널을 여닫을 때마다 쌓인다).
  if (_activeSection !== 'voice') cleanupVoiceClients();
  body.innerHTML = '';
  body.classList.remove('mobile-home');
  if (isCompact() && _mobileHome) { renderMobileHome(body, focusTarget === 'home'); return; }
  const nav = document.createElement('nav');
  nav.className = 'vt-set-nav';
  nav.setAttribute('aria-label', '설정 항목');
  nav.appendChild(renderSearch());
  for (const s of SECTIONS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-menu-item sm vt-set-navitem' + (s.id === _activeSection ? ' active' : '');
    if (s.id === _activeSection) b.setAttribute('aria-current', 'page');
    b.textContent = s.label;
    b.addEventListener('click', () => { _activeSection = s.id; rerender(''); });
    nav.appendChild(b);
  }
  const content = document.createElement('div');
  content.className = 'vt-set-content';
  const section = SECTIONS.find((s) => s.id === _activeSection) || SECTIONS[0];
  const intro = document.createElement('div'); intro.className = 'vt-set-intro';
  if (isCompact()) {
    const back = document.createElement('button'); back.type = 'button'; back.className = 'vt-btn sm quiet vt-set-back'; back.textContent = '‹ 더보기';
    back.addEventListener('click', () => { _mobileHome = true; rerender('home'); }); intro.appendChild(back);
  }
  const h = document.createElement('h2'); h.className = 'vt-set-title'; h.tabIndex = -1; h.textContent = section.label;
  const p = document.createElement('p'); p.className = 'vt-set-description'; p.textContent = section.description;
  const device = document.createElement('span'); device.className = 'vt-chip sm vt-set-device'; device.textContent = '이 기기';
  vtFetch('/api/devices').then((r) => {
    const current = ((r && r.devices) || []).find((d) => d.current);
    if (current && current.label && device.isConnected) device.textContent = `이 기기 · ${current.label}`;
  }).catch(() => {});
  intro.append(h, p, device);
  content.appendChild(intro);
  content.appendChild(section.custom ? section.custom() : renderItems(section));
  body.append(nav, content);
  if (focusTarget !== null) Promise.resolve().then(() => {
    const target = focusTarget && Array.from(content.querySelectorAll('.vt-set-label'))
      .find((el) => el.firstChild?.textContent === focusTarget);
    if (target) { target.tabIndex = -1; target.focus(); target.scrollIntoView?.({ block: 'center' }); }
    else h.focus();
  });
}

export function showSettings() {
  _mobileHome = true;
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: '설정',
    headHTML: '<div class="vt-vw-title">설정</div><div class="vt-set-save" id="vt-set-save" role="status" aria-live="polite" data-state="saved">변경 사항 자동 저장</div>',
    bodyId: 'vt-set-body',
    extraClass: 'vt-settings',
    onClose: () => { cleanupVoiceClients(); if (_persistenceUnsub) { _persistenceUnsub(); _persistenceUnsub = null; } },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  _persistenceUnsub = subscribePersistence(({ status }) => {
    const el = document.getElementById('vt-set-save'); if (!el) return;
    el.dataset.state = status;
    el.textContent = status === 'saving' ? '저장 중…' : status === 'failed' ? '서버 저장 실패 · 다시 열면 되돌아갈 수 있음' : '저장됨';
  });
  rerender();
}

// `settings.show` 액션과 `Mod+,` 바인딩 등록은 panels/settings-lazy.js에 있다
// — 부팅 시점에 등록돼야 하므로 지연 청크 안으로 들어가면 안 된다.
