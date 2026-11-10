// 사용 가이드 본문 — term/guide.js(core)가 열 때 import()한다. `panel` 청크(모달 뼈대와 같은 곳)에 실린다.
// "⋯ → 가이드 보기" — 언제든 열고/닫을 수 있는 서비스 전체 사용 가이드(첫 사용자용).
// F4에서 terminal.js(구 :1761-1840)에서 분리. 온보딩(showOnboarding, boot.js)은
// 첫 실행 시 세션 생성을 강제하는 화면이라 재사용하지 않고 별도로 둔다.
import { openPanel } from '../panels/panel.js';
import { icon } from '../ui/icons.js';
import { t } from '../core/i18n.js';

export function showGuide() {
  // 섹션 정의 — {icon, title, rows:[{key, desc}]}. key는 왼쪽 라벨, desc는 설명(HTML).
  // icon은 이제 Lucide 웹폰트 클래스명이 아니라 ui/icons.js 맵의 키(D5).
  const kbdCtrlShiftV = '<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd>';
  const sections = [
    { icon: 'rocket', title: t('시작하기'), rows: [
      { key: t('{key} 버튼', { key: '<kbd>+</kbd>' }), desc: t('새 세션 생성 (tmux 세션 또는 일반 터미널)') },
      { key: t('탭'), desc: t('더블클릭 → 이름 변경 · {x} → 닫기(tmux는 detach만 됨, 완전 종료는 ⋯ 메뉴)', { x: icon('x', 12) }) },
      { key: t('pane 분할'), desc: t('pane 헤더의 분할 버튼 → 화면을 나눠 여러 세션을 동시에 표시') },
    ]},
    { icon: 'mic', title: t('음성 입력'), rows: [
      { key: t('마이크 버튼'), desc: t('상단 {mic} 탭 → 말하면 STT로 텍스트 입력', { mic: icon('mic', 14) }) },
      { key: t('맥 핫키'), desc: t('{cmd} 실행 후 {combo} 토글', { cmd: '<code>fsh voice</code>', combo: kbdCtrlShiftV }) },
      { key: t('핸즈프리'), desc: t('모바일 반복 버튼 → 녹음·인식 연속 자동 반복') },
      { key: t('음성 전용'), desc: t('⋯ 메뉴 → 터미널 숨기고 큰 마이크만 표시(이어폰용)') },
    ]},
    { icon: 'clipboard-copy', title: t('복사 · 붙여넣기'), rows: [
      { key: t('복사'), desc: t('텍스트 드래그 → {auto} · 또는 선택 후 우클릭', { auto: `<b>${t('자동 복사')}</b>` }) },
      { key: t('드래그해도 선택이 안 될 때'), desc: t('tmux 마우스 모드({cmd})가 켜져 있으면 드래그를 tmux가 먼저 가로챔 → {shift}+드래그(맥은 {option}+드래그도 가능)로 강제 선택', { cmd: '<code>mouse on</code>', shift: '<kbd>Shift</kbd>', option: '<kbd>Option</kbd>' }) },
      { key: t('붙여넣기'), desc: t('{paste} · {combo} · 선택 없이 우클릭', { paste: '<kbd>Cmd</kbd>/<kbd>Ctrl</kbd>+<kbd>V</kbd>', combo: kbdCtrlShiftV }) },
      { key: t('이미지'), desc: t('이미지를 복사해 붙여넣으면 서버에 {auto} + 경로가 명령줄에 삽입', { auto: `<b>${t('자동 업로드')}</b>` }) },
      { key: t('자동 복사 켬/끔'), desc: t('⋯ 메뉴 → 설정에서 토글. {on}: 드래그·tmux copy-mode 복사가 클립보드에 즉시 반영. {off}: 선택만 되고 클립보드는 그대로 — 우클릭으로 원할 때만 복사', { on: `<b>${t('켬')}</b>`, off: `<b>${t('끔')}</b>` }) },
      { key: t('맥↔웹 동기화'), desc: t('tmux copy-mode 복사는 자동(OSC52). 그 밖(Safari 등)은 맥에서 {cmd} 실행', { cmd: '<code>fsh clip</code>' }) },
    ]},
    { icon: 'square-terminal', title: t('tmux 세션'), rows: [
      { key: t('tmux 세션'), desc: t('⋯ 메뉴 → 기존 세션 목록 확인·attach') },
      { key: t('새 세션을 맥에서도 열기'), desc: t('앞으로 만들 세션마다 맥 터미널 창도 함께 열림(토글). 지금 보고 있는 세션을 여는 것은 「지금 이 세션 맥에서 열기」') },
      { key: t('이 세션 열기'), desc: t('지금 보는 세션을 맥 터미널 새 창으로 열기') },
    ]},
    { icon: 'file-up', title: t('파일 · 검색'), rows: [
      { key: t('파일 업로드'), desc: t('⋯ 메뉴 → 업로드(경로 자동 삽입). 이미지는 붙여넣기로도 업로드') },
      { key: t('검색'), desc: t('{combo} 로 터미널 출력 검색', { combo: '<kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>F</kbd>' }) },
    ]},
    { icon: 'monitor-smartphone', title: t('모바일 · 원격'), rows: [
      { key: t('모바일 접속'), desc: t('{cmd} → QR/URL로 폰 접속(같은 세션 이어쓰기)', { cmd: '<code>fsh mobile</code>' }) },
      { key: t('핸드오프'), desc: t('{mobile} / {desktop} 으로 폰↔맥 전환', { mobile: '<code>fsh handoff mobile</code>', desktop: '<code>desktop</code>' }) },
    ]},
    { icon: 'palette', title: t('테마'), rows: [
      { key: t('스킨 변경'), desc: t('⋯ 메뉴 → macOS · Catppuccin · Windows · VS Code · Notepad') },
    ]},
  ];

  const secHtml = sections.map(s => `
    <div class="vt-guide-sec">
      <div class="vt-gs-title">${icon(s.icon, 15)}${s.title}</div>
      ${s.rows.map(r => `
        <div class="vt-guide-row">
          <div class="vt-gr-key">${r.key}</div>
          <div class="vt-gr-desc">${r.desc}</div>
        </div>`).join('')}
    </div>`).join('');

  // 모달 뼈대(backdrop/card/닫기 버튼/Esc/배경 클릭)는 openPanel() 공용 구현에
  // 맡긴다 — D6: 예전엔 이 모달만 .vt-guide-* 전용 CSS로 직접 구현하고 있었다.
  openPanel({
    id: 'vt-guide',
    ariaLabel: t('사용 가이드'),
    headHTML: `
      <div class="vt-gh-icon">${icon('terminal', 20)}</div>
      <div class="vt-guide-title-wrap">
        <h2>FarShell</h2>
        <p class="vt-guide-sub">${t('브라우저로 tmux 터미널을 — 웹·폰 어디서든 이어서')}</p>
      </div>
    `,
    bodyId: 'vt-guide-body',
    bodyHTML: `<div class="vt-guide-scroll">${secHtml}</div>`,
  });
}
