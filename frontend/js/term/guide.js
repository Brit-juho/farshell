// "⋯ → 가이드 보기" — 언제든 열고/닫을 수 있는 서비스 전체 사용 가이드(첫 사용자용).
// F4에서 terminal.js(구 :1761-1840)에서 분리. 온보딩(showOnboarding, boot.js)은
// 첫 실행 시 세션 생성을 강제하는 화면이라 재사용하지 않고 별도로 둔다.
import { registerAction } from '../core/dom.js';

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

registerAction('guide.show', () => showGuide());
