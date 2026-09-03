// VT 테마 엔진 — UI 스킨 + xterm.js 터미널 테마(ANSI 16색)를 함께 전환. F5에서
// classic script에서 ES 모듈로 전환.
import { allSessions } from './core/store.js';
import { registerAction } from './core/dom.js';

const VT_SKINS = ['macos', 'catppuccin', 'windows', 'vscode', 'notepad'];

// 각 스킨의 xterm.js 테마 — foreground/background/cursor/selection + ANSI 16색.
// "iTerm2 vs 윈도우 터미널 느낌"의 핵심은 이 팔레트다.
const VT_XTERM_THEMES = {
  macos: {
    background:'#101012', foreground:'#e6e6ea', cursor:'#0a84ff', cursorAccent:'#101012',
    selectionBackground:'rgba(10,132,255,0.32)',
    black:'#2a2a2e', red:'#ff453a', green:'#32d74b', yellow:'#ffd60a',
    blue:'#0a84ff', magenta:'#bf5af2', cyan:'#5ac8fa', white:'#d0d0d5',
    brightBlack:'#5a5a60', brightRed:'#ff6961', brightGreen:'#6ee787', brightYellow:'#ffe066',
    brightBlue:'#64a5ff', brightMagenta:'#da8fff', brightCyan:'#7fdbff', brightWhite:'#ffffff',
  },
  catppuccin: {
    background:'#1e1e2e', foreground:'#cdd6f4', cursor:'#f5e0dc', cursorAccent:'#1e1e2e',
    selectionBackground:'rgba(88,91,112,0.55)',
    black:'#45475a', red:'#f38ba8', green:'#a6e3a1', yellow:'#f9e2af',
    blue:'#89b4fa', magenta:'#f5c2e7', cyan:'#94e2d5', white:'#bac2de',
    brightBlack:'#585b70', brightRed:'#f38ba8', brightGreen:'#a6e3a1', brightYellow:'#f9e2af',
    brightBlue:'#89b4fa', brightMagenta:'#f5c2e7', brightCyan:'#94e2d5', brightWhite:'#a6adc8',
  },
  // VS Code Dark+ 통합 터미널 공식 팔레트
  vscode: {
    background:'#1e1e1e', foreground:'#cccccc', cursor:'#ffffff', cursorAccent:'#1e1e1e',
    selectionBackground:'rgba(38,79,120,0.60)',
    black:'#000000', red:'#cd3131', green:'#0dbc79', yellow:'#e5e510',
    blue:'#2472c8', magenta:'#bc3fbc', cyan:'#11a8cd', white:'#e5e5e5',
    brightBlack:'#666666', brightRed:'#f14c4c', brightGreen:'#23d18b', brightYellow:'#f5f543',
    brightBlue:'#3b8eea', brightMagenta:'#d670d6', brightCyan:'#29b8db', brightWhite:'#e5e5e5',
  },
  // 공식 Campbell 팔레트 (Windows Terminal 기본)
  windows: {
    background:'#0c0c0c', foreground:'#cccccc', cursor:'#ffffff', cursorAccent:'#0c0c0c',
    selectionBackground:'rgba(255,255,255,0.28)',
    black:'#0c0c0c', red:'#c50f1f', green:'#13a10e', yellow:'#c19c00',
    blue:'#0037da', magenta:'#881798', cyan:'#3a96dd', white:'#cccccc',
    brightBlack:'#767676', brightRed:'#e74856', brightGreen:'#16c60c', brightYellow:'#f9f1a5',
    brightBlue:'#3b78ff', brightMagenta:'#b4009e', brightCyan:'#61d6d6', brightWhite:'#f2f2f2',
  },
  // 메모장 — 흰 종이 위 잉크. 밝은 배경이라 ANSI 밝기 관계가 다크 테마와 반대로 뒤집힌다.
  notepad: {
    background:'#fffefb', foreground:'#2b2a25', cursor:'#0060df', cursorAccent:'#fffefb',
    selectionBackground:'rgba(0,96,223,0.18)',
    black:'#24292e', red:'#d73a49', green:'#22863a', yellow:'#b08800',
    blue:'#0366d6', magenta:'#5a32a3', cyan:'#0598bc', white:'#6a737d',
    brightBlack:'#959da5', brightRed:'#cb2431', brightGreen:'#28a745', brightYellow:'#dbab09',
    brightBlue:'#005cc5', brightMagenta:'#8250df', brightCyan:'#3192aa', brightWhite:'#2b2a25',
  },
};

// theme-color 메타(모바일 상태바)용 — --bar 값과 일치
const VT_BAR_COLOR = { macos:'#2c2c2e', catppuccin:'#181825', windows:'#2b2b2b', vscode:'#2d2d2d', notepad:'#f1efe7' };

export function getVtSkin() {
  const s = document.documentElement.getAttribute('data-skin');
  return VT_SKINS.indexOf(s) >= 0 ? s : 'macos';
}

export function getVtXtermTheme(skin) {
  return VT_XTERM_THEMES[skin || getVtSkin()] || VT_XTERM_THEMES.macos;
}

// 테마별 터미널 폰트 — Windows는 Cascadia Code(WT 정체성), 나머지는 IBM Plex Mono
const VT_XTERM_FONTS = {
  windows: "'Cascadia Code', 'Cascadia Mono', 'IBM Plex Mono', ui-monospace, Consolas, monospace",
  _default: "'IBM Plex Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace",
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
  if (meta && VT_BAR_COLOR[skin]) meta.setAttribute('content', VT_BAR_COLOR[skin]);
}

export function setVtSkin(skin) {
  if (VT_SKINS.indexOf(skin) < 0) skin = 'macos';
  document.documentElement.setAttribute('data-skin', skin);
  try { localStorage.setItem('vt-skin', skin); } catch (_) {}
  _syncThemeChips(skin);
  _applyXtermToOpen(skin);
}

// 초기 칩/메타 동기화 (부팅 인라인 스크립트가 이미 data-skin은 설정함)
(function initSkinUI() {
  const apply = () => _syncThemeChips(getVtSkin());
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, { once: true });
  else apply();
})();

// 번들 폰트(IBM Plex Mono) 로드 완료 후 열린 터미널 refit — swap로 인한 셀 폭 오차 보정
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => {
    const map = allSessions();
    for (const id of Object.keys(map)) {
      try { map[id].fitAddon && map[id].fitAddon.fit(); } catch (_) {}
    }
  });
}

// F3(c): data-action 위임용 등록. 테마 칩은 data-skin 속성을 그대로 읽는다
// (이미 CSS 선택자용으로 붙어 있던 값 재사용 — F3(d)에서 추가 속성을 만들지 않았다).
registerAction('theme.set', (el) => setVtSkin(el.dataset.skin));
