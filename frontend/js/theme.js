// VT 테마 엔진 — UI 스킨 + xterm.js 터미널 테마(ANSI 16색)를 함께 전환. F5에서
// classic script에서 ES 모듈로 전환.
import { set as setSetting, get as setting, has as hasSetting, subscribe as onSettings } from './core/settings.js';
import { allSessions } from './core/store.js';
import { registerAction } from './core/dom.js';

// D1(ADR-3): farshell이 새 기본 스킨 — 목록 순서가 곧 설정 화면 칩 순서이자
// "미지의 스킨" 폴백 우선순위 의도를 나타내므로 맨 앞에 둔다.
// 2026-09-24(2.1 D6) — warp/linear/vercel 3개 추가. 사용자가 직접 고른
// 색 구성(그라디언트 액센트 · 무광 인디고 · 흑백+단색)을
// 그대로 새 스킨으로 얹었다 — 기존 6개처럼
// "이미 있던 색의 재매핑"이 아니라 값 자체가 새로 고른 것들이다.
const VT_SKINS = ['farshell', 'macos', 'catppuccin', 'windows', 'vscode', 'notepad', 'warp', 'linear', 'vercel'];

// 칩·팔레트 행에 쓰는 표시 이름 — 두 소비처(설정 「모양」 칩, 커맨드 팔레트)가
// 각자 표를 갖던 게 이번 사고(테마 칩이 DOM에서 사라지자 팔레트도 같이
// 말라버린 것)의 근본 원인 중 하나였다. 단일 소스로 둔다.
export const VT_SKIN_LABELS = {
  farshell: 'FarShell', macos: 'macOS', catppuccin: 'Catppuccin',
  windows: 'Windows', vscode: 'VS Code', notepad: 'Notepad',
  warp: 'Warp', linear: 'Linear', vercel: 'Vercel/Geist',
};

export function vtSkins() {
  return VT_SKINS.slice();
}

export function vtSkinLabel(skin) {
  return VT_SKIN_LABELS[skin] || skin;
}

// 각 스킨의 xterm.js 테마 — foreground/background/cursor/selection + ANSI 16색.
// "iTerm2 vs 윈도우 터미널 느낌"의 핵심은 이 팔레트다.
const VT_XTERM_THEMES = {
  // 2026-11 리디자인 3단계 — 시안 팔레트(파랑 액센트). 이 값이 styles/theme/
  // tokens.css의 --color-term-*와 같아야 한다(theme.test.js가 9스킨 전부 본다):
  // background=term-bg, foreground=term-fg, brightWhite=term-bright,
  // brightBlack=term-dim, red/green/yellow/blue/magenta/cyan=term-red/green/
  // amber/blue/mag/cyan, selectionBackground=term-sel. bright 계열은 같은 색상각에서
  // 밝기만 올렸다. black은 hover 면(bg-3), white는 보조 글자(sub)와 같다.
  farshell: {
    background:'#121316', foreground:'#d9d7d2', cursor:'#7aa2ff', cursorAccent:'#121316',
    selectionBackground:'rgba(122,162,255,0.32)',
    black:'#25272d', red:'#f07070', green:'#7fd18b', yellow:'#e8c46a',
    blue:'#7aa2ff', magenta:'#c49cf0', cyan:'#6fc8d6', white:'#a9a7a2',
    brightBlack:'#77756f', brightRed:'#ff9090', brightGreen:'#a2e3ab', brightYellow:'#f2d68e',
    brightBlue:'#9dbbff', brightMagenta:'#d8bcf6', brightCyan:'#94dbe5', brightWhite:'#ffffff',
  },
  // 2026-11 — brightBlack #5a5a60 → #626268. 흐린 글자(자동 제안·주석)가 바탕에서
  // 2.78:1이라 3:1(--color-term-dim 기준)에 못 미쳤다. 같은 회색에서 밝기만 올렸다.
  macos: {
    background:'#101012', foreground:'#e6e6ea', cursor:'#0a84ff', cursorAccent:'#101012',
    selectionBackground:'rgba(10,132,255,0.32)',
    black:'#2a2a2e', red:'#ff453a', green:'#32d74b', yellow:'#ffd60a',
    blue:'#0a84ff', magenta:'#bf5af2', cyan:'#5ac8fa', white:'#d0d0d5',
    brightBlack:'#626268', brightRed:'#ff6961', brightGreen:'#6ee787', brightYellow:'#ffe066',
    brightBlue:'#64a5ff', brightMagenta:'#da8fff', brightCyan:'#7fdbff', brightWhite:'#ffffff',
  },
  // 2026-11 — brightBlack을 surface2(#585b70, 2.46:1)에서 같은 Mocha 팔레트의
  // overlay0(#6c7086, 3.36:1)으로. 흐린 글자가 3:1을 넘는 가장 가까운 공식 색이다.
  catppuccin: {
    background:'#1e1e2e', foreground:'#cdd6f4', cursor:'#f5e0dc', cursorAccent:'#1e1e2e',
    selectionBackground:'rgba(88,91,112,0.55)',
    black:'#45475a', red:'#f38ba8', green:'#a6e3a1', yellow:'#f9e2af',
    blue:'#89b4fa', magenta:'#f5c2e7', cyan:'#94e2d5', white:'#bac2de',
    brightBlack:'#6c7086', brightRed:'#f38ba8', brightGreen:'#a6e3a1', brightYellow:'#f9e2af',
    brightBlue:'#89b4fa', brightMagenta:'#f5c2e7', brightCyan:'#94e2d5', brightWhite:'#a6adc8',
  },
  // VS Code Dark+ 통합 터미널 공식 팔레트. 2026-11 — brightBlack만 #666666(2.90:1)에서
  // #6b6b6b(3.13:1)로 — 흐린 글자 3:1 기준(--color-term-dim).
  vscode: {
    background:'#1e1e1e', foreground:'#cccccc', cursor:'#ffffff', cursorAccent:'#1e1e1e',
    selectionBackground:'rgba(38,79,120,0.60)',
    black:'#000000', red:'#cd3131', green:'#0dbc79', yellow:'#e5e510',
    blue:'#2472c8', magenta:'#bc3fbc', cyan:'#11a8cd', white:'#e5e5e5',
    brightBlack:'#6b6b6b', brightRed:'#f14c4c', brightGreen:'#23d18b', brightYellow:'#f5f543',
    brightBlue:'#3b8eea', brightMagenta:'#d670d6', brightCyan:'#29b8db', brightWhite:'#e5e5e5',
  },
  // 공식 Campbell 팔레트 (Windows Terminal 기본). 2026-11 — brightMagenta만 #b4009e에서
  // #e000c4로 밝혔다: magenta(#881798 2.44:1)와 brightMagenta(3.20:1)가 둘 다 바탕에서
  // 4.5에 못 미쳐 --color-term-mag로 쓸 색이 없었다. 같은 색상각에서 밝기만 올린 값.
  windows: {
    background:'#0c0c0c', foreground:'#cccccc', cursor:'#ffffff', cursorAccent:'#0c0c0c',
    selectionBackground:'rgba(255,255,255,0.28)',
    black:'#0c0c0c', red:'#c50f1f', green:'#13a10e', yellow:'#c19c00',
    blue:'#0037da', magenta:'#881798', cyan:'#3a96dd', white:'#cccccc',
    brightBlack:'#767676', brightRed:'#e74856', brightGreen:'#16c60c', brightYellow:'#f9f1a5',
    brightBlue:'#3b78ff', brightMagenta:'#e000c4', brightCyan:'#61d6d6', brightWhite:'#f2f2f2',
  },
  // 메모장 — 흰 종이 위 잉크. 밝은 배경이라 ANSI 밝기 관계가 다크 테마와 반대로 뒤집힌다.
  // 2026-11 — yellow #b08800(3.27:1)→#917000, cyan #0598bc(3.35:1)→#047e9c. 종이 위에서
  // 노랑·청록 글자가 4.5에 못 미쳤다(bright 쪽은 더 밝아 더 낮다). 같은 색상각에서 어둡게.
  notepad: {
    background:'#fffefb', foreground:'#2b2a25', cursor:'#0060df', cursorAccent:'#fffefb',
    selectionBackground:'rgba(0,96,223,0.18)',
    black:'#24292e', red:'#d73a49', green:'#22863a', yellow:'#917000',
    blue:'#0366d6', magenta:'#5a32a3', cyan:'#047e9c', white:'#6a737d',
    brightBlack:'#959da5', brightRed:'#cb2431', brightGreen:'#28a745', brightYellow:'#dbab09',
    brightBlue:'#005cc5', brightMagenta:'#8250df', brightCyan:'#3192aa', brightWhite:'#2b2a25',
  },
  // 2026-09-24(2.1 D6) — Warp 터미널 느낌. black/white는 styles/theme/
  // skins.css의 warp 블록(--color-bg-3/--color-txt)과 같은 값, red/green/
  // yellow/blue는 상태 램프(st-error/working/waiting/done)와 맞췄다(다른
  // 6스킨과 같은 관행) — magenta/cyan은 그라디언트 액센트 계열(라벤더·하늘)
  // 에서 골랐다.
  warp: {
    background:'#0d0b14', foreground:'#f3f1fb', cursor:'#c4a6ff', cursorAccent:'#0d0b14',
    selectionBackground:'rgba(196,166,255,0.28)',
    black:'#221c30', red:'#ff6b81', green:'#5be3a8', yellow:'#ffb37a',
    blue:'#7fdcff', magenta:'#c4a6ff', cyan:'#7fdcff', white:'#a49cc4',
    brightBlack:'#8078a0', brightRed:'#ff8fa0', brightGreen:'#7eeac0', brightYellow:'#ffc79a',
    brightBlue:'#a3e6ff', brightMagenta:'#dcc4ff', brightCyan:'#a3e6ff', brightWhite:'#ffffff',
  },
  // Linear — 거의 무채색, 액센트는 인디고 하나. magenta/cyan은 다른 6스킨과
  // 같은 이유로 그 넷과 안 부딪히는 보색을 새로 골랐다(Linear 실제 브랜드에
  // ANSI 팔레트가 없어 참고할 "공식값"이 없다).
  linear: {
    background:'#08090a', foreground:'#f2f3f5', cursor:'#6e79f2', cursorAccent:'#08090a',
    selectionBackground:'rgba(110,121,242,0.28)',
    black:'#17181c', red:'#e5484d', green:'#4cb782', yellow:'#e0a72e',
    blue:'#6e79f2', magenta:'#c168e0', cyan:'#4fc8e0', white:'#9599a1',
    brightBlack:'#767a82', brightRed:'#ff6369', brightGreen:'#66d69a', brightYellow:'#f5c052',
    brightBlue:'#8891f5', brightMagenta:'#d98cf0', brightCyan:'#6fe0f5', brightWhite:'#ffffff',
  },
  // Vercel/Geist — 흑백 + 단색 파랑. magenta도 팔레트엔 있어야 하는 ANSI
  // 계약이라 모노톤을 깨지 않는 보라 하나만 더한다(UI 어디에도 쓰지 않는다,
  // 터미널 색만).
  vercel: {
    background:'#000000', foreground:'#ffffff', cursor:'#0084ff', cursorAccent:'#000000',
    selectionBackground:'rgba(0,132,255,0.28)',
    black:'#171717', red:'#f31260', green:'#17c964', yellow:'#f5a524',
    blue:'#0084ff', magenta:'#9750dd', cyan:'#17c1c9', white:'#a0a0a0',
    brightBlack:'#7a7a7a', brightRed:'#ff5c8a', brightGreen:'#3ddb82', brightYellow:'#ffc14d',
    brightBlue:'#3d9bff', brightMagenta:'#b57ee8', brightCyan:'#4fd8de', brightWhite:'#ffffff',
  },
};

// theme-color 메타(모바일 상태바) — CSS의 --bar를 그대로 읽는다.
//
// 예전엔 스킨별 hex를 여기 표로 갖고 있었다. 값은 정확히 --bar와 같아야 하는데
// 두 곳에 적어두면 언젠가 한쪽만 바뀐다(2026-09-08 기준으로는 6스킨 다 일치했지만,
// 그건 아직 안 어긋났다는 뜻이지 안 어긋난다는 보장이 아니다). data-skin을 이미
// 세운 뒤에 계산값을 읽으면 표 자체가 필요 없어진다.
//
// 폴백: 부팅 아주 초반이나 CSS 미로드 시 계산값이 빈 문자열로 온다. 그때는
// 메타를 건드리지 않는다 — 잘못된 색을 넣느니 브라우저 기본을 두는 게 낫다.
function _barColor() {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue('--bar').trim();
  } catch (_) {
    return '';
  }
}

export function getVtSkin() {
  const s = document.documentElement.getAttribute('data-skin');
  return vtSkins().indexOf(s) >= 0 ? s : 'farshell';
}

export function getVtXtermTheme(skin) {
  const name = skin || getVtSkin();
  return VT_XTERM_THEMES[name] || VT_XTERM_THEMES.farshell;
}

// 테마별 터미널 폰트 — Windows는 Cascadia Code(WT 정체성, 시스템 설치 폰트를
// 찾는 의도적 선택 — D4에서 legacy.css의 --mono 번들 @font-face는 제거했지만
// 이건 별개), 나머지는 D4: 1순위를 JetBrains Mono로(시스템에 있으면 사용,
// 없으면 번들된 IBM Plex Mono로 자연히 넘어감). 예전 4순위 'Cascadia Code'는
// IBM Plex Mono/ui-monospace/SF Mono가 항상 먼저 걸려 로드될 일이 없던
// 죽은 값이라 제거.
// 'Symbols Nerd Font Mono'는 번들 글리프 전용 폴백(프롬프트 아이콘/Powerline) —
// 기기에 Nerd Font가 없어도 깨진 네모가 안 뜨게 한다. 일반 글자엔 쓰이지 않는다.
const VT_XTERM_FONTS = {
  windows: "'Cascadia Code', 'Cascadia Mono', 'IBM Plex Mono', ui-monospace, Consolas, 'Symbols Nerd Font Mono', monospace",
  _default: "'JetBrains Mono', 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, Consolas, 'Symbols Nerd Font Mono', monospace",
};
export function getVtXtermFont(skin) {
  skin = skin || getVtSkin();
  return VT_XTERM_FONTS[skin] || VT_XTERM_FONTS._default;
}

// 열린 터미널에 스킨의 테마(색) + 폰트를 함께 적용하고 refit.
function _applyXtermToOpen(skin) {
  const map = allSessions();
  const theme = getVtXtermTheme(skin);
  const font = getVtXtermFont(skin);
  for (const id of Object.keys(map)) {
    const s = map[id];
    const term = s && s.term;
    if (!term) continue;
    try {
      if (term.options) { term.options.theme = theme; term.options.fontFamily = font; }  // xterm 5.x
      else if (term.setOption) { term.setOption('theme', theme); term.setOption('fontFamily', font); } // xterm 4.x
      if (s.fitAddon) s.fitAddon.fit();  // 폰트 셀 폭 변경 반영
    } catch (_) {}
  }
}

function _syncThemeChips(skin) {
  document.querySelectorAll('.theme-chip').forEach((c) => {
    c.classList.toggle('sel', c.dataset.skin === skin);
  });
  const meta = document.getElementById('theme-color-meta');
  // data-skin은 setVtSkin이 이 함수보다 먼저 세운다 — 그래서 계산값이 이미 새 스킨 것이다.
  const bar = _barColor();
  if (meta && bar) meta.setAttribute('content', bar);
}

function applyVtSkin(skin) {
  if (vtSkins().indexOf(skin) < 0) skin = 'farshell';
  document.documentElement.setAttribute('data-skin', skin);
  // S2: 스킨도 설정 스토어로 — 기기마다 다른 스킨이 뜨던 것을 없앤다.
  // 옛 `vt-skin` 키도 계속 쓴다(theme.js는 classic script 시절부터 부팅 아주
  // 초반에 이 키를 읽어 FOUC를 막아왔다. 그 경로를 깨지 않는다).
  try { localStorage.setItem('vt-skin', skin); } catch (_) {}
  _syncThemeChips(skin);
  _applyXtermToOpen(skin);
}

// 복원은 저장 요청을 만들지 않는다. 서버 값 도착과 사용자 변경 모두 같은 적용 경로.
export function setVtSkin(skin) {
  if (!vtSkins().includes(skin)) skin = 'farshell';
  if (setting('theme.skin') === skin) applyVtSkin(skin);
  return setSetting('theme.skin', skin);
}
onSettings((changed) => {
  if ('theme.skin' in changed) applyVtSkin(setting('theme.skin'));
});
if (hasSetting('theme.skin')) applyVtSkin(setting('theme.skin'));

// 초기 칩/메타 동기화 (부팅 인라인 스크립트가 이미 data-skin은 설정함)
(function initSkinUI() {
  const apply = () => _syncThemeChips(getVtSkin());
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, { once: true });
  else apply();
})();

// 번들 폰트 로드 완료 후 열린 터미널 refit — swap로 인한 셀 폭 오차 보정.
// 아이콘 폴백(Symbols Nerd Font Mono)은 글리프가 처음 나올 때 늦게 받아지므로
// ready 이후에도 loadingdone마다 텍스처 아틀라스를 비우고 다시 그린다.
function _refitAfterFonts() {
  const map = allSessions();
  for (const id of Object.keys(map)) {
    const s = map[id];
    try {
      if (s.term && s.term.clearTextureAtlas) s.term.clearTextureAtlas();
      if (s.term && s.term.refresh) s.term.refresh(0, s.term.rows - 1);
      s.fitAddon && s.fitAddon.fit();
    } catch (_) {}
  }
}
if (document.fonts) {
  if (document.fonts.ready) document.fonts.ready.then(_refitAfterFonts);
  if (document.fonts.addEventListener) document.fonts.addEventListener('loadingdone', _refitAfterFonts);
}

// F3(c): data-action 위임용 등록. 테마 칩은 data-skin 속성을 그대로 읽는다
// (이미 CSS 선택자용으로 붙어 있던 값 재사용 — F3(d)에서 추가 속성을 만들지 않았다).
registerAction('theme.set', (el) => setVtSkin(el.dataset.skin));
