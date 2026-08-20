// D13: theme.js — 스킨 전환이 (1) 열려 있는 모든 xterm 인스턴스에 반영되는지,
// (2) localStorage/DOM data-skin 속성에 저장되는지, (3) 칩 UI가 동기화되는지 검증한다.
// theme.js는 document/localStorage만 있으면 되고 xterm 자체(Terminal 클래스)는 몰라도
// 된다 — _applyXtermToOpen이 건드리는 건 term.options/term.setOption뿐이라 그 모양만
// 갖춘 가짜 세션으로 충분하다(진짜 렌더링은 이 파일의 관심사가 아니다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const THEME_JS = fs.readFileSync(path.join(__dirname, '../js/theme.js'), 'utf8');

const _windows = [];
after(() => { for (const w of _windows) { try { w.close(); } catch (_) {} } });

function buildThemeWindow() {
  const dom = new JSDOM('<!doctype html><html data-skin="macos"><body></body></html>', {
    url: 'http://localhost/', runScripts: 'dangerously',
  });
  const { window } = dom;
  _windows.push(window);
  const script = window.document.createElement('script');
  script.textContent = THEME_JS;
  window.document.body.appendChild(script);
  return window;
}

// xterm 5.x 스타일(옵션 객체 직접 대입) 가짜 세션.
function fakeSession5x() {
  return { term: { options: { theme: null, fontFamily: null } }, fitAddon: { fit() { this.fitted = (this.fitted || 0) + 1; } } };
}

// xterm 4.x 스타일(setOption 메서드) 가짜 세션.
function fakeSession4x() {
  const opts = {};
  return {
    term: { setOption(key, value) { opts[key] = value; }, _opts: opts },
    fitAddon: { fit() {} },
  };
}

test('getVtSkin: data-skin 속성을 읽고, 미지의 값이면 macos로 폴백한다', () => {
  const window = buildThemeWindow();
  assert.strictEqual(window.getVtSkin(), 'macos');

  window.document.documentElement.setAttribute('data-skin', 'catppuccin');
  assert.strictEqual(window.getVtSkin(), 'catppuccin');

  window.document.documentElement.setAttribute('data-skin', 'not-a-real-skin');
  assert.strictEqual(window.getVtSkin(), 'macos');
});

test('getVtXtermTheme: 스킨별로 다른 팔레트를 반환하고, 미지의 스킨은 macos로 폴백한다', () => {
  const window = buildThemeWindow();
  const macos = window.getVtXtermTheme('macos');
  const notepad = window.getVtXtermTheme('notepad');
  assert.notStrictEqual(macos.background, notepad.background);
  assert.strictEqual(window.getVtXtermTheme('nonexistent').background, macos.background);
});

test('setVtSkin: 미지의 스킨을 넘기면 macos로 강제된다', () => {
  const window = buildThemeWindow();
  window.setVtSkin('totally-bogus');
  assert.strictEqual(window.document.documentElement.getAttribute('data-skin'), 'macos');
});

test('setVtSkin: data-skin 속성과 localStorage를 함께 갱신한다', () => {
  const window = buildThemeWindow();
  window.setVtSkin('vscode');
  assert.strictEqual(window.document.documentElement.getAttribute('data-skin'), 'vscode');
  assert.strictEqual(window.localStorage.getItem('vt-skin'), 'vscode');
});

test('setVtSkin: 열려 있는 모든 xterm 세션(5.x 스타일)에 테마+폰트를 반영한다', () => {
  const window = buildThemeWindow();
  const s1 = fakeSession5x();
  const s2 = fakeSession5x();
  window.sessions = { a: s1, b: s2 };

  window.setVtSkin('windows');

  const expectedTheme = window.getVtXtermTheme('windows');
  assert.deepStrictEqual(s1.term.options.theme, expectedTheme);
  assert.deepStrictEqual(s2.term.options.theme, expectedTheme);
  assert.strictEqual(s1.term.options.fontFamily, window.getVtXtermFont('windows'));
  assert.strictEqual(s2.term.options.fontFamily, window.getVtXtermFont('windows'));
});

test('setVtSkin: 세션마다 fitAddon.fit()을 다시 불러 셀 폭 변경을 반영한다', () => {
  const window = buildThemeWindow();
  const s1 = fakeSession5x();
  window.sessions = { a: s1 };

  window.setVtSkin('catppuccin');
  assert.strictEqual(s1.fitAddon.fitted, 1);

  window.setVtSkin('notepad');
  assert.strictEqual(s1.fitAddon.fitted, 2, '스킨을 또 바꾸면 다시 fit()이 불려야 한다');
});

test('setVtSkin: xterm 4.x(setOption) 세션도 지원한다', () => {
  const window = buildThemeWindow();
  const s1 = fakeSession4x();
  window.sessions = { a: s1 };

  window.setVtSkin('vscode');
  assert.deepStrictEqual(s1.term._opts.theme, window.getVtXtermTheme('vscode'));
  assert.strictEqual(s1.term._opts.fontFamily, window.getVtXtermFont('vscode'));
});

test('setVtSkin: 터미널이 없는(로드 전) 세션은 조용히 건너뛴다', () => {
  const window = buildThemeWindow();
  window.sessions = { a: { term: null, fitAddon: null } };
  assert.doesNotThrow(() => window.setVtSkin('windows'));
});

test('setVtSkin: sessions가 아직 정의 안 됐어도(terminal.js 로드 전) 죽지 않는다', () => {
  const window = buildThemeWindow();
  // sessions를 아예 안 만든 상태 — theme.js는 terminal.js보다 먼저 로드되므로
  // 이 시점엔 실제로 sessions가 없을 수 있다(index.html 주석 참고).
  assert.doesNotThrow(() => window.setVtSkin('macos'));
});

test('setVtSkin: .theme-chip에 선택 상태를 동기화한다', () => {
  const window = buildThemeWindow();
  const { document } = window;
  ['macos', 'catppuccin', 'windows'].forEach((skin) => {
    const chip = document.createElement('button');
    chip.className = 'theme-chip';
    chip.dataset.skin = skin;
    document.body.appendChild(chip);
  });

  window.setVtSkin('catppuccin');

  const selected = Array.from(document.querySelectorAll('.theme-chip.sel')).map((c) => c.dataset.skin);
  assert.deepStrictEqual(selected, ['catppuccin']);
});

test('setVtSkin: theme-color 메타 태그를 스킨의 상태바 색으로 갱신한다', () => {
  const window = buildThemeWindow();
  const meta = window.document.createElement('meta');
  meta.id = 'theme-color-meta';
  meta.setAttribute('content', '#000000');
  window.document.head.appendChild(meta);

  window.setVtSkin('notepad');
  assert.strictEqual(meta.getAttribute('content'), '#f1efe7');
});
