// 인라인 SVG 아이콘 — D5: Lucide 웹폰트(vendor/lucide.min.css 64KB +
// lucide.woff2 221KB, 도합 285KB)로 아이콘 11개만 쓰자고 폰트 전체를 실어
// 오던 것과, 플랫폼마다 렌더가 갈리는 이모지(🟢🖥️💤🗑🛡🔧✅✓✎)를 여기 하나로
// 통일한다. 실제 쓰는 아이콘이 20개 안팎이라 별도 빌드 스텝/트리셰이킹 없이
// 이 map 하나로 충분하다 — 새 아이콘이 필요하면 여기 한 줄만 추가한다.
// path는 Lucide(ISC 라이선스, 기존에 이미 폰트로 번들하던 것과 같은 세트)의
// 24x24 stroke 좌표를 그대로 옮겼다.
const PATHS = {
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  'square-terminal': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="m7 9 3 3-3 3"/><path d="M12 15h5"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
  'clipboard-copy': '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-2"/><path d="M16 4h2a2 2 0 0 1 2 2v4"/><path d="M21 14H11"/><path d="m15 10-4 4 4 4"/>',
  'file-up': '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M4.5 22H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v3.5"/><path d="M12 12v6"/><path d="m9 15 3-3 3 3"/>',
  'monitor-smartphone': '<path d="M18 8V4H6v4"/><path d="M14 20H2v-8h12"/><rect width="8" height="14" x="14" y="10" rx="2"/>',
  palette: '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
  'layout-grid': '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  'wifi-off': '<path d="M12 20h.01"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/><path d="M5 12.859a10 10 0 0 1 5.17-2.69"/><path d="M19 12.859a10 10 0 0 0-2.007-1.523"/><path d="M2 8.82a15 15 0 0 1 4.177-2.643"/><path d="M22 8.82a15 15 0 0 0-11.288-3.764"/><path d="m2 2 20 20"/>',
  'trash-2': '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  pencil: '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
  // L3 3단계: pane 헤더의 분할 버튼(→/↓ 방향).
  'columns-2': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M12 3v18"/>',
  'rows-2': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 12h18"/>',
  // L3 5단계: 빈 pane 세션 선택 시트의 "+ 새 세션".
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',

  // 2026-09-16 — 이모지로 남아 있던 자리를 메운다. DESIGN.md §0이 "No emoji as
  // a UI asset"을 이미 규칙으로 두고 있었는데 키바(📎🎤)·상단바(🔍)·파일 패널
  // (⬇📋⌨🔗🗑)·큐(⏸)·설정(⚙)·경고(⚠)·확인(✓)이 그 규칙 밖에 있었다.
  // 이모지는 OS·폰트마다 모양과 색이 갈리고 currentColor를 안 따라 스킨 전환에서
  // 혼자 튄다 — 크기·광학 정렬도 통제가 안 된다. 여기 들어오면 셋 다 해결된다.
  paperclip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  keyboard: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="M6 8h.01"/><path d="M10 8h.01"/><path d="M14 8h.01"/><path d="M18 8h.01"/><path d="M8 12h.01"/><path d="M12 12h.01"/><path d="M16 12h.01"/><path d="M7 16h10"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  pause: '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
  'alert-triangle': '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  // 방향은 이 하나를 CSS로 돌려 만든다(chevron-up/left/right를 따로 두지
  // 않는다) — 여는/접는 토글에서 회전이 상태 전이를 그대로 보여준다.
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  // 2.1.6 — 레일 바닥의 「더보기」. 지금까지 문자 `⋯`(U+22EF)였는데, 글꼴마다
  // 점 크기와 세로 위치가 달라 옆 아이콘과 광학 정렬이 안 맞았다.
  'more-horizontal': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',

  // dock 탭 5종(2026-09-17). 접힌 dock이 한글 라벨을 writing-mode:vertical-rl로
  // 세우고 있었는데, 한글은 세로쓰기에서 **음절이 한 자씩 쌓여** 35px 폭에
  // "소/스/컨/트/롤"이 세로로 늘어섰다. 모노스페이스까지 겹쳐 더 나빴다.
  // 세로쓰기 자체는 영문 라벨을 전제한 패턴이라 한글에는 성립하지 않는다.
  'git-branch': '<line x1="6" x2="6" y1="3" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  // 팔레트 섹션 마크(2.1.6). 목업 4a의 파일 줄 아이콘과 같은 형태 — 모서리가
  // 접힌 문서. 'file-up'(업로드)과 역할이 다르므로 따로 둔다.
  file: '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M4.5 22H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2Z"/>',
  list: '<path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M3 6h.01"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M8 6h13"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  plug: '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
  gauge: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',

  // D3(20-design-system.md §4) — 에이전트 마크. 탭·rail 행이 "어떤 CLI가
  // 떠 있는가"를 이모지 대신 이 세트로 그린다(icons.js가 D5에서 이미 이모지를
  // 걷어낸 원칙과 같다 — 플랫폼마다 렌더가 갈리지 않는다). 미지 agent 이름은
  // agent-shell로 떨어진다(문서 §4 매핑 규칙). gemini·aider는 2.1.0에서
  // agent-shell로 두고(문서가 "2.1.1에서 결정 — 디자인에 없음"이라 명시)
  // 2.1.1에서 전용 마크를 만들지 결정한다.
  'agent-claude': '<path d="M12 3l1.8 6.2L20 11l-6.2 1.8L12 19l-1.8-6.2L4 11l6.2-1.8z"/>',
  'agent-codex': '<path d="M8 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h3"/><path d="M16 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3"/>',
  'agent-local': '<rect x="6" y="6" width="12" height="12" rx="1"/><line x1="9" y1="2" x2="9" y2="6"/><line x1="15" y1="2" x2="15" y2="6"/><line x1="9" y1="18" x2="9" y2="22"/><line x1="15" y1="18" x2="15" y2="22"/>',
  'agent-ssh': '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><line x1="3" y1="12" x2="21" y2="12"/>',
  'agent-shell': '<polyline points="6 8 10 12 6 16"/><line x1="12" y1="16" x2="18" y2="16"/>',
};

// server/agent_detector.py의 KNOWN_AGENTS 키 → 마크. 없는 이름(gemini·aider
// 포함, 위 주석 참고)은 agent-shell로 떨어진다.
const AGENT_MARK = {
  claude: 'agent-claude',
  codex: 'agent-codex',
};
export function agentMarkFor(agentName) {
  return AGENT_MARK[agentName] || 'agent-shell';
}

// icon(name, size?) → SVG 마크업 문자열. innerHTML/템플릿 리터럴에 그대로
// 꽂아 쓴다 — path 데이터가 사용자 입력과 무관한 고정 맵이라 별도 sanitize가
// 필요 없다. stroke-width는 2로 고정(기존 코드가 2/2.5를 혼용했다).
export function icon(name, size, strokeWidth) {
  const body = PATHS[name];
  if (!body) {
    console.warn('[icons] 등록되지 않은 아이콘:', name);
    return '';
  }
  const s = size || 16;
  const sw = strokeWidth || 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 24 24" `
    + `fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" `
    + `aria-hidden="true" focusable="false">${body}</svg>`;
}

// 에이전트 마크 전용 — 문서 §4: "14px 정사각, stroke 1.75".
export function agentIcon(agentName) {
  return icon(agentMarkFor(agentName), 14, 1.75);
}

// 마크 옆에 붙는 사람이 읽는 이름(title·aria-label용). 마크는 도형이라 그
// 자체로는 무엇인지 말하지 않는다 — 데스크톱 탭은 title="Claude"를 이미
// 달고 있었고(layout/tabbar.js), 레일·플릿 행도 같은 이름을 쓰게 한다.
const AGENT_LABEL = {
  claude: 'Claude',
  codex: 'Codex',
};
export function agentLabel(agentName) {
  if (!agentName) return '';
  return AGENT_LABEL[agentName] || String(agentName);
}
