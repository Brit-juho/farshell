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
import { renderItems } from './settings/controls.js';
import { renderKeymapSection } from './settings/sections/keymap.js';
import { renderVoiceSection, cleanupVoiceClients } from './settings/sections/voice.js';
import { renderAppearanceSection } from './settings/sections/appearance.js';
import { renderAgentsSection } from './settings/sections/agents.js';
import { renderSecuritySection } from './settings/sections/security.js';
import { renderScrollbackSection } from './settings/sections/scrollback.js';
import { renderAboutSection } from './settings/sections/about.js';
import { getTabs, getActiveTabId } from '../layout/store.js';

const PANEL_ID = 'vt-settings';

// 섹션 정의 — 계획서(50-settings-keymap.md §3)의 9개 섹션 중, 이번 범위에서
// 실제로 조작 가능한 것만 넣는다. 「음성」·「알림」·「세션」은 각자 자기 패널이
// 이미 있거나(푸시 토글·음성 바) 서버 설정이라 여기서 중복 노출하지 않는다 —
// 빈 섹션을 보여주는 것보다 없는 편이 낫다(§4 "빈 패널을 보여주지 않는다").
const SECTIONS = [
  {
    id: 'terminal', label: '터미널',
    items: [
      { key: 'terminal.fontSize', label: '글자 크기', kind: 'range' },
      { key: 'terminal.cursorStyle', label: '커서 모양', kind: 'select',
        labels: { block: '블록', underline: '밑줄', bar: '막대' } },
      { key: 'terminal.cursorBlink', label: '커서 깜빡임', kind: 'bool' },
      { key: 'terminal.scrollback', label: '스크롤백 줄 수', kind: 'range', step: 500 },
    ],
  },
  {
    id: 'mouse', label: '마우스 · 선택',
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
    id: 'a11y', label: '접근성',
    items: [
      { key: 'a11y.screenReader', label: '스크린 리더 모드', kind: 'select',
        labels: { auto: '자동 (터치 기기에서 켬)', on: '항상 켬', off: '항상 끔' },
        help: '새로 여는 탭부터 적용됩니다 — 터미널 내부 구조가 달라져 실행 중에는 바꿀 수 없습니다.' },
    ],
  },
  // N42(60-settings-palette.md §7) — 알림·음성 진단. 다섯 항목 전부 서버
  // API는 이미 있고(push.py·voice.py·system.py) 이 섹션은 그 배선일 뿐이다.
  { id: 'voice', label: '음성', custom: renderVoiceSection },
  // N9/N45(80-multihost-agents.md §2) — CLI별 승인 대기 감지 커버리지 표.
  { id: 'agents', label: '에이전트', custom: renderAgentsSection },
  { id: 'keymap', label: '키맵', custom: () => renderKeymapSection(rerender) },
  // N14 — Ghostty/Warp 테마 가져오기. 기본 6스킨을 고르는 칩은 예전부터
  // 다른 자리(테마 줄·팔레트)에 있고, 여기는 "가져오기"만 다룬다.
  { id: 'appearance', label: '모양', custom: renderAppearanceSection },
  { id: 'mcp', label: 'MCP', custom: renderMcpSection },
  { id: 'security', label: '보안', custom: renderSecuritySection },
  // N13(80-multihost-agents.md §3) — 토글 자체는 스키마 항목이지만, 디스크
  // 사용량은 서버에 물어봐야 하는 값이라(보안 섹션과 같은 이유로) custom.
  { id: 'scrollback', label: '스크롤백', custom: renderScrollbackSection },
  { id: 'about', label: '정보', custom: renderAboutSection },
];

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

function rerender() {
  const body = document.getElementById('vt-set-body');
  if (!body) return;
  // 「음성」 섹션을 떠나면 clients.js 폴링 타이머를 반드시 끊는다(rail.js의
  // _clientsCleanup과 같은 규칙 — 안 그러면 패널을 여닫을 때마다 쌓인다).
  if (_activeSection !== 'voice') cleanupVoiceClients();
  body.innerHTML = '';
  const nav = document.createElement('div');
  nav.className = 'vt-set-nav';
  for (const s of SECTIONS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-set-navitem' + (s.id === _activeSection ? ' active' : '');
    b.textContent = s.label;
    b.addEventListener('click', () => { _activeSection = s.id; rerender(); });
    nav.appendChild(b);
  }
  const content = document.createElement('div');
  content.className = 'vt-set-content';
  const section = SECTIONS.find((s) => s.id === _activeSection) || SECTIONS[0];
  content.appendChild(section.custom ? section.custom() : renderItems(section));
  body.append(nav, content);
}

export function showSettings() {
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: '설정',
    headHTML: '<div class="vt-vw-title">설정</div>',
    bodyId: 'vt-set-body',
    extraClass: 'vt-settings',
    onClose: cleanupVoiceClients,
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  rerender();
}

// `settings.show` 액션과 `Mod+,` 바인딩 등록은 panels/settings-lazy.js에 있다
// — 부팅 시점에 등록돼야 하므로 지연 청크 안으로 들어가면 안 된다.
