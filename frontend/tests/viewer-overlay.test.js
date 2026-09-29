'use strict';
// ADR-44(docs/plan-2.3/95-viewer-surface.md §10.1) — 파일 뷰어 오버레이.
// 핵심 불변식은 "pane 트리를 건드리지 않는다"와 "터미널 입력을 가로채지 않는다"다.
// 터미널 cols×rows가 실제로 안 바뀌는지는 레이아웃이 없는 jsdom으로는 못 재므로
// 실브라우저 스모크(tests/e2e/test_smoke.py)가 맡는다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');
const LAYOUT_STORE_JS = path.join(__dirname, '../js/layout/store.js');
const OVERLAY_JS = path.join(__dirname, '../js/panels/viewer/overlay.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build({ width = 1280, surface, rect } = {}) {
  const env = createDomEnv(
    '<!doctype html><html><body><div id="terminal-container"><textarea id="term-input"></textarea></div></body></html>');
  _doms.push(env.dom);
  const { window, document } = env;
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
  window.fitAndResize = () => {};
  window.activeId = null;
  window.API_BASE = '';
  window._tokenQuery = '';
  window.showToast = () => {};
  window.VTDiffLex = { normalize: (s) => s, langForPath: () => null };
  // 파일을 열면 자동 갱신 구독(/ws-fs-watch)이 붙는다 — 실제 소켓을 열지 않게.
  window.WebSocket = class { send() {} close() {} };
  const fetches = [];
  window.fetch = async (url, opts) => {
    const u = String(url);
    fetches.push({ url: u, method: opts && opts.method });
    if (u.includes('/api/fs/file')) {
      const p = decodeURIComponent(u.split('path=')[1].split('&')[0]);
      return { ok: true, status: 200, json: async () => ({ content: `// ${p}\nline2` }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const cache = new Map();
  const settings = await importFresh(SETTINGS_JS, env.context, cache);
  const layout = await importFresh(LAYOUT_STORE_JS, env.context, cache);
  const overlay = await importFresh(OVERLAY_JS, env.context, cache);
  if (surface) await settings.set('viewer.surface', surface);
  if (rect !== undefined) await settings.set('viewer.float.rect', rect);
  const container = document.getElementById('terminal-container');
  const fileFetches = () => fetches.filter((f) => f.url.includes('/api/fs/file')).length;
  return { window, document, settings, layout, overlay, container, fileFetches };
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const esc = (window, target) => target.dispatchEvent(
  new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

test('#terminal-container 안에 비모달로 붙고 pane 트리는 그대로다', async () => {
  const { document, layout, overlay, container } = await build();
  const before = layout.getTree();
  overlay.openFileOverlay('/repo/src/a.js');
  const el = document.getElementById('vt-file');
  assert.ok(el, '오버레이가 열린다');
  assert.strictEqual(el.parentElement, container, '레일·dock·topbar를 안 가리게 터미널 영역에만 붙는다');
  assert.ok(el.classList.contains('vt-file-overlay'));
  assert.ok(el.classList.contains('surface-sheet'), '기본 표면은 sheet');
  assert.strictEqual(el.querySelector('.vt-viewer-card').getAttribute('aria-modal'), 'false');
  assert.strictEqual(layout.getTree(), before, 'pane을 쪼개지 않는다 — 이게 리플로우가 없는 이유다');
});

test('다른 파일을 열면 닫히지 않고 내용만 바뀐다', async () => {
  const { document, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  await flush();
  const el = document.getElementById('vt-file');
  overlay.openFileOverlay('/repo/b.js');
  await flush();
  assert.strictEqual(document.getElementById('vt-file'), el, '같은 엘리먼트 — 토글로 닫히면 안 된다');
  assert.strictEqual(el.dataset.viewerFile, '/repo/b.js');
  assert.strictEqual(el.querySelector('.vt-file-name').textContent, 'b.js');
  assert.match(el.querySelector('.vt-vw-body').textContent, /\/\/ \/repo\/b\.js/);
});

test('같은 파일을 다시 열면 다시 불러오지 않는다(스크롤이 튀지 않게)', async () => {
  const { overlay, fileFetches } = await build();
  overlay.openFileOverlay('/repo/a.js');
  await flush();
  const n = fileFetches();
  overlay.openFileOverlay('/repo/a.js');
  await flush();
  assert.strictEqual(fileFetches(), n);
});

test('Esc — 포커스가 터미널에 있으면 안 닫힌다(vim이 받아야 한다), 뷰어 안이면 닫힌다', async () => {
  const { window, document, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  const term = document.getElementById('term-input');
  term.focus();
  esc(window, term);
  assert.ok(document.getElementById('vt-file'), '터미널 포커스에서 Esc는 터미널 몫');

  const body = document.querySelector('#vt-file .vt-vw-body');
  body.focus();
  esc(window, body);
  assert.strictEqual(document.getElementById('vt-file'), null);
});

test('열리면 뷰어 본문이 포커스를 받는다 — 바로 키보드로 스크롤하고 Esc로 닫을 수 있다', async () => {
  const { document, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  assert.strictEqual(document.activeElement, document.querySelector('#vt-file .vt-vw-body'));
});

test('바깥 클릭으로는 안 닫힌다(코드를 띄운 채 터미널을 쓴다)', async () => {
  const { window, document, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  const el = document.getElementById('vt-file');
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.ok(document.getElementById('vt-file'));
});

test('시트 높이 — 클램프는 스키마(20~85)를 따른다', async () => {
  const { overlay } = await build();
  assert.strictEqual(overlay.clampSheetPct(5), 20);
  assert.strictEqual(overlay.clampSheetPct(99), 85);
  assert.strictEqual(overlay.clampSheetPct(50), 50);
});

test('시트 높이 드래그 — 놓을 때 한 번 저장되고 범위 밖이면 잘린다', async () => {
  const { window, document, settings, overlay, container } = await build();
  Object.defineProperty(container, 'clientHeight', { value: 1000, configurable: true });
  overlay.openFileOverlay('/repo/a.js');
  const el = document.getElementById('vt-file');
  const grip = el.querySelector('.vt-file-grip');
  const ev = (type, y) => new window.MouseEvent(type, { clientY: y, bubbles: true });
  grip.dispatchEvent(ev('pointerdown', 450));
  document.dispatchEvent(ev('pointermove', 600));
  assert.strictEqual(el.style.getPropertyValue('--vt-sheet-h'), '60%', '드래그 중에는 CSS 변수만 바뀐다');
  assert.strictEqual(settings.get('viewer.sheet.height'), 45, '드래그 중에는 저장하지 않는다');
  document.dispatchEvent(ev('pointermove', 1500));
  document.dispatchEvent(ev('pointerup', 1500));
  assert.strictEqual(settings.get('viewer.sheet.height'), 85);
});

test('창 사각형 — 깨진 저장값은 기본값, 화면 밖 저장값은 안으로 당겨진다', async () => {
  const { overlay } = await build();
  assert.strictEqual(overlay.parseRect('{broken'), null);
  assert.strictEqual(overlay.parseRect('{}'), null);
  assert.strictEqual(overlay.parseRect('{"x":1,"y":1,"w":-5,"h":10}'), null);
  assert.strictEqual(overlay.parseRect('{"x":"1","y":1,"w":5,"h":10}'), null);
  // vm 컨텍스트의 객체라 프로토타입이 다르다 — 펼쳐서 이 realm 객체로 비교한다.
  assert.deepStrictEqual({ ...overlay.parseRect('{"x":1,"y":2,"w":3,"h":4}') }, { x: 1, y: 2, w: 3, h: 4 });

  // 외부 모니터 오른쪽 끝에 두고 닫았다가 노트북만으로 연 경우.
  const r = overlay.clampRect({ x: 2400, y: 1300, w: 900, h: 700 }, 1000, 600);
  assert.deepStrictEqual({ ...r }, { x: 1000 - 48, y: 600 - 28, w: 900, h: 600 });
  const tiny = overlay.clampRect({ x: -50, y: -50, w: 10, h: 10 }, 1000, 600);
  assert.deepStrictEqual({ ...tiny }, { x: 0, y: 0, w: 320, h: 200 }, '최소 크기 320×200');

  const d = overlay.defaultRect(1000, 600);
  assert.strictEqual(d.w, 550);
  assert.strictEqual(d.x, 1000 - 550 - 24, '오른쪽 위 — 프롬프트가 있는 왼쪽 아래를 덜 가린다');
});

test('float — 저장된 창이 화면 밖이면 열 때 안으로 당긴다', async () => {
  const { document, overlay, container } = await build({
    surface: 'float', rect: '{"x":5000,"y":5000,"w":400,"h":300}',
  });
  Object.defineProperty(container, 'clientWidth', { value: 1000, configurable: true });
  Object.defineProperty(container, 'clientHeight', { value: 600, configurable: true });
  overlay.openFileOverlay('/repo/a.js');
  const el = document.getElementById('vt-file');
  assert.ok(el.classList.contains('surface-float'));
  assert.strictEqual(el.style.getPropertyValue('--vt-file-x'), '952px');
  assert.strictEqual(el.style.getPropertyValue('--vt-file-y'), '572px');
});

test('float — 헤더 드래그로 옮기고 놓을 때 저장한다. 버튼 위에서 시작하면 안 움직인다', async () => {
  const { window, document, settings, overlay, container } = await build({
    surface: 'float', rect: '{"x":100,"y":50,"w":400,"h":300}',
  });
  Object.defineProperty(container, 'clientWidth', { value: 1000, configurable: true });
  Object.defineProperty(container, 'clientHeight', { value: 600, configurable: true });
  overlay.openFileOverlay('/repo/a.js');
  const el = document.getElementById('vt-file');
  const head = el.querySelector('.vt-viewer-head');
  const ev = (type, x, y) => new window.MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true });

  el.querySelector('.vt-file-topane').dispatchEvent(ev('pointerdown', 0, 0));
  document.dispatchEvent(ev('pointermove', 300, 300));
  assert.strictEqual(el.style.getPropertyValue('--vt-file-x'), '100px', '버튼에서 시작한 드래그는 무시');

  head.dispatchEvent(ev('pointerdown', 10, 10));
  document.dispatchEvent(ev('pointermove', 60, 30));
  document.dispatchEvent(ev('pointerup', 60, 30));
  assert.strictEqual(el.style.getPropertyValue('--vt-file-x'), '150px');
  assert.deepStrictEqual(JSON.parse(settings.get('viewer.float.rect')), { x: 150, y: 70, w: 400, h: 300 });
});

test('표면을 바꿔도 같은 엘리먼트·같은 파일이 유지된다', async () => {
  const { document, settings, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  const el = document.getElementById('vt-file');
  await settings.set('viewer.surface', 'float');
  assert.strictEqual(document.getElementById('vt-file'), el);
  assert.ok(el.classList.contains('surface-float'));
  assert.ok(!el.classList.contains('surface-sheet'));
  assert.strictEqual(el.dataset.viewerFile, '/repo/a.js');
});

test('표면을 pane으로 바꾸면 오버레이를 닫고 그 파일을 페인으로 옮긴다', async () => {
  const { document, settings, layout, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  await settings.set('viewer.surface', 'pane');
  assert.strictEqual(document.getElementById('vt-file'), null);
  const tree = layout.getTree();
  assert.strictEqual(tree.kind, 'viewer');
  assert.strictEqual(tree.file, '/repo/a.js');
});

test('「페인으로」 — 오버레이를 닫고 같은 파일을 페인으로 연다', async () => {
  const { window, document, layout, overlay } = await build();
  overlay.openFileOverlay('/repo/a.js');
  document.querySelector('#vt-file .vt-file-topane').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.strictEqual(document.getElementById('vt-file'), null);
  assert.strictEqual(layout.getTree().file, '/repo/a.js');
});

test('compact(<720px) — 표면과 무관하게 body에 모달 시트로 붙고 뒤로가기 route가 된다', async () => {
  const { window, document, overlay, container } = await build({ width: 390, surface: 'float' });
  overlay.openFileOverlay('/repo/a.js');
  const el = document.getElementById('vt-file');
  assert.strictEqual(el.parentElement, document.body);
  assert.ok(el.classList.contains('vt-viewer-backdrop'));
  assert.ok(el.classList.contains('vt-file-compact'));
  assert.ok(el.classList.contains('vt-mobile-route'));
  assert.strictEqual(window.history.state.vtPanel, 'vt-file');
  assert.strictEqual(container.querySelector('#vt-file'), null);
});

test('헤더 경로는 textContent로만 들어간다(파일 이름에 마크업이 있어도 실행되지 않는다)', async () => {
  const { document, overlay } = await build();
  overlay.openFileOverlay('/repo/<img src=x onerror=alert(1)>.js');
  const el = document.getElementById('vt-file');
  assert.strictEqual(el.querySelector('.vt-file-head img, .vt-viewer-head img'), null);
  assert.strictEqual(el.querySelector('.vt-file-name').textContent, '<img src=x onerror=alert(1)>.js');
});

test('헤더 디렉터리는 끝 두 단계만 보이고 전체 경로는 툴팁에 있다', async () => {
  const { document, overlay } = await build();
  overlay.openFileOverlay('/Users/me/repo/frontend/js/panels/viewer/overlay.js');
  const el = document.getElementById('vt-file');
  assert.strictEqual(el.querySelector('.vt-file-dir').textContent, '…/panels/viewer');
  assert.strictEqual(el.querySelector('.vt-file-title').dataset.tip, '/Users/me/repo/frontend/js/panels/viewer/overlay.js');
  overlay.openFileOverlay('/etc/hosts');
  assert.strictEqual(el.querySelector('.vt-file-dir').textContent, '/etc');
});
