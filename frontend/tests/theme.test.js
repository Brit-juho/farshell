// D13: theme.js — 스킨 전환이 (1) 열려 있는 모든 xterm 인스턴스에 반영되는지,
// (2) localStorage/DOM data-skin 속성에 저장되는지, (3) 칩 UI가 동기화되는지 검증한다.
// theme.js는 document/localStorage만 있으면 되고 xterm 자체(Terminal 클래스)는 몰라도
// 된다 — _applyXtermToOpen이 건드리는 건 term.options/term.setOption뿐이라 그 모양만
// 갖춘 가짜 세션으로 충분하다(진짜 렌더링은 이 파일의 관심사가 아니다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

// 테스트 하네스 재설계(F5 백로그): stripEsm 대신 vm.SourceTextModule로 실제
// js/theme.js와 그 실제 의존성(core/store.js·core/dom.js)을 그대로 링크·평가한다.
// theme.js가 F5에서 window 브리지를 걷어냈으므로(getVtSkin 등은 이제 순수 export),
// 아래 테스트는 window.getVtSkin() 대신 실제 export된 함수를 그대로 쓴다.
const THEME_JS = path.join(__dirname, '../js/theme.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function buildThemeWindow(skin = 'macos') {
  const env = createDomEnv(`<!doctype html><html data-skin="${skin}"><body></body></html>`);
  _doms.push(env.dom);
  // theme.js가 실제 import하는 core/store.js가 window.sessions를 소유한다 —
  // 매 테스트 새 cache(Map)로 importFresh하므로 store 상태(sessions/activeId)도
  // 테스트마다 새로 시작한다.
  const ns = await importFresh(THEME_JS, env.context, new Map());
  return { window: env.window, ...ns };
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

test('getVtSkin: data-skin 속성을 읽고, 미지의 값이면 farshell로 폴백한다 (D1: 기본 스킨 macos→farshell)', async () => {
  const { window, getVtSkin } = await buildThemeWindow();
  assert.strictEqual(getVtSkin(), 'macos');

  window.document.documentElement.setAttribute('data-skin', 'catppuccin');
  assert.strictEqual(getVtSkin(), 'catppuccin');

  window.document.documentElement.setAttribute('data-skin', 'not-a-real-skin');
  assert.strictEqual(getVtSkin(), 'farshell');
});

test('getVtXtermTheme: 스킨별로 다른 팔레트를 반환하고, 미지의 스킨은 farshell로 폴백한다', async () => {
  const { getVtXtermTheme } = await buildThemeWindow();
  const farshell = getVtXtermTheme('farshell');
  const notepad = getVtXtermTheme('notepad');
  assert.notStrictEqual(farshell.background, notepad.background);
  assert.strictEqual(getVtXtermTheme('nonexistent').background, farshell.background);
});

test('setVtSkin: 미지의 스킨을 넘기면 farshell로 강제된다', async () => {
  const { window, setVtSkin } = await buildThemeWindow();
  setVtSkin('totally-bogus');
  assert.strictEqual(window.document.documentElement.getAttribute('data-skin'), 'farshell');
});

test('setVtSkin: data-skin 속성과 localStorage를 함께 갱신한다', async () => {
  const { window, setVtSkin } = await buildThemeWindow();
  setVtSkin('vscode');
  assert.strictEqual(window.document.documentElement.getAttribute('data-skin'), 'vscode');
  assert.strictEqual(window.localStorage.getItem('vt-skin'), 'vscode');
});

test('setVtSkin: 열려 있는 모든 xterm 세션(5.x 스타일)에 테마+폰트를 반영한다', async () => {
  const { window, setVtSkin, getVtXtermTheme, getVtXtermFont } = await buildThemeWindow();
  const s1 = fakeSession5x();
  const s2 = fakeSession5x();
  // core/store.js가 소유한 window.sessions는 재할당이 아니라 mutate로만 채운다
  // (재할당하면 theme.js가 실제 import한 allSessions()의 참조와 어긋난다).
  window.sessions.a = s1;
  window.sessions.b = s2;

  setVtSkin('windows');

  const expectedTheme = getVtXtermTheme('windows');
  assert.deepStrictEqual(s1.term.options.theme, expectedTheme);
  assert.deepStrictEqual(s2.term.options.theme, expectedTheme);
  assert.strictEqual(s1.term.options.fontFamily, getVtXtermFont('windows'));
  assert.strictEqual(s2.term.options.fontFamily, getVtXtermFont('windows'));
});

test('setVtSkin: 세션마다 fitAddon.fit()을 다시 불러 셀 폭 변경을 반영한다', async () => {
  const { window, setVtSkin } = await buildThemeWindow();
  const s1 = fakeSession5x();
  window.sessions.a = s1;

  setVtSkin('catppuccin');
  assert.strictEqual(s1.fitAddon.fitted, 1);

  setVtSkin('notepad');
  assert.strictEqual(s1.fitAddon.fitted, 2, '스킨을 또 바꾸면 다시 fit()이 불려야 한다');
});

test('setVtSkin: xterm 4.x(setOption) 세션도 지원한다', async () => {
  const { window, setVtSkin, getVtXtermTheme, getVtXtermFont } = await buildThemeWindow();
  const s1 = fakeSession4x();
  window.sessions.a = s1;

  setVtSkin('vscode');
  assert.deepStrictEqual(s1.term._opts.theme, getVtXtermTheme('vscode'));
  assert.strictEqual(s1.term._opts.fontFamily, getVtXtermFont('vscode'));
});

test('setVtSkin: 터미널이 없는(로드 전) 세션은 조용히 건너뛴다', async () => {
  const { window, setVtSkin } = await buildThemeWindow();
  window.sessions.a = { term: null, fitAddon: null };
  assert.doesNotThrow(() => setVtSkin('windows'));
});

test('setVtSkin: 세션이 하나도 없어도(빈 store) 죽지 않는다', async () => {
  const { setVtSkin } = await buildThemeWindow();
  assert.doesNotThrow(() => setVtSkin('macos'));
});

test('setVtSkin: .theme-chip에 선택 상태를 동기화한다', async () => {
  const { window, setVtSkin } = await buildThemeWindow();
  const { document } = window;
  ['macos', 'catppuccin', 'windows'].forEach((skin) => {
    const chip = document.createElement('button');
    chip.className = 'theme-chip';
    chip.dataset.skin = skin;
    document.body.appendChild(chip);
  });

  setVtSkin('catppuccin');

  const selected = Array.from(document.querySelectorAll('.theme-chip.sel')).map((c) => c.dataset.skin);
  assert.deepStrictEqual(selected, ['catppuccin']);
});

test('setVtSkin: theme-color 메타 태그를 스킨의 상태바 색으로 갱신한다', async () => {
  const { window, setVtSkin } = await buildThemeWindow();
  const meta = window.document.createElement('meta');
  meta.id = 'theme-color-meta';
  meta.setAttribute('content', '#000000');
  window.document.head.appendChild(meta);

  setVtSkin('notepad');
  assert.strictEqual(meta.getAttribute('content'), '#f1efe7');
});
