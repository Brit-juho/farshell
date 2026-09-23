// ADR-26/N35 — panels/viewer/*를 지연 청크로 뺀 뒤 실제로 재현됐던 버그의
// 회귀 테스트: Vite lib 모드는 entry를 항상 완전히 인라인하므로, 지연 로드된
// 모듈이 core/store.js·layout/store.js 등을 그 청크 안으로 복제해 가면 앱과
// **별개 싱글톤 두 벌**이 된다(옛 증상: 뷰어가 자기만의 빈 sessions={}를 봐서
// 조용히 아무 세션도 못 찾았다).
//
// §6에서 모달 코드 뷰어가 사라지고 파일이 **페인**으로 열리면서, 그 위험이
// 옮겨간 자리가 여기다: viewer-lazy.js의 openFileInPane()이 layout/store.js를
// 정적 import한다 — 앱 본체(app.js)에 남는 파일이라 이건 복제가 아니라 공유가
// 맞다. 이 테스트는 "그 호출이 진짜 앱 트리를 바꾼다"를 실제로 확인한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const LAYOUT_STORE_JS = path.join(__dirname, '../js/layout/store.js');
const VIEWER_LAZY_JS = path.join(__dirname, '../js/panels/viewer-lazy.js');
const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(bodyHTML = '') {
  const env = createDomEnv(`<!doctype html><html><body>${bodyHTML}</body></html>`);
  _doms.push(env.dom);
  const { window } = env;
  window.fetch = async () => ({ ok: true, status: 200, json: async () => ({ content: '' }) });
  window.showToast = () => {};
  window.fitAndResize = () => {};
  window.API_BASE = '';
  window._tokenQuery = '';
  window.VTDiffLex = { normalize: (s) => String(s ?? ''), langForPath: () => null };

  const cache = new Map();
  const core = await importFresh(CORE_STORE_JS, env.context, cache);
  const layout = await importFresh(LAYOUT_STORE_JS, env.context, cache);
  const settings = await importFresh(SETTINGS_JS, env.context, cache);
  const lazy = await importFresh(VIEWER_LAZY_JS, env.context, cache);
  return { window, document: env.document, core, layout, settings, lazy };
}

async function waitFor(fn, ms = 2000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 5));
  }
  return fn();
}

test('openFileInPane — 앱과 같은 레이아웃 스토어의 트리를 바꾼다(싱글톤 공유)', async () => {
  const { layout, lazy } = await load();

  const before = layout.getTree();
  assert.strictEqual(before.t, 'leaf');
  assert.strictEqual(before.kind, 'terminal');

  lazy.openFileInPane('/tmp/example.txt');

  const after = layout.getTree();
  assert.strictEqual(after.t, 'leaf', '빈 pane이었으므로 그 자리에서 열린다(분할 없음)');
  assert.strictEqual(after.kind, 'viewer');
  assert.strictEqual(after.file, '/tmp/example.txt');
  assert.strictEqual(layout.getActivePaneId(), after.id);
});

test('openFileInPane — 터미널이 들어 있던 pane은 덮지 않고 쪼갠다', async () => {
  const { core, layout, lazy } = await load();

  core.registerSession('sess-1', { wsHandle: null, tabEl: null, tmuxName: 'dev' });
  layout.setPaneSession('sess-1');

  lazy.openFileInPane('/tmp/example.txt');

  const tree = layout.getTree();
  assert.strictEqual(tree.t, 'split', '보고 있던 터미널을 파일이 덮으면 작업이 끊긴다');
  assert.strictEqual(tree.a.session, 'sess-1');
  assert.strictEqual(tree.b.kind, 'viewer');
  assert.strictEqual(tree.b.file, '/tmp/example.txt');
});

test('빈 경로는 아무것도 하지 않는다', async () => {
  const { layout, lazy } = await load();
  const before = layout.getTree();
  lazy.openFileInPane('');
  assert.strictEqual(layout.getTree(), before);
});

// ADR-44 — 파일 열기의 기본 진입점이 openFile로 갈렸다. openFileInPane은 의미 그대로
// (위 테스트들이 계속 그걸 지킨다), openFile은 표면 설정을 따른다.
test('openFile — 기본 표면(sheet)은 pane 트리를 건드리지 않고 오버레이로 연다', async () => {
  const { document, core, layout, lazy } = await load('<div id="terminal-container"></div>');
  core.registerSession('sess-1', { wsHandle: null, tabEl: null, tmuxName: 'dev' });
  layout.setPaneSession('sess-1');
  const before = layout.getTree();

  lazy.openFile('/tmp/example.txt');
  const el = await waitFor(() => document.getElementById('vt-file'));

  assert.ok(el, '오버레이가 열린다');
  assert.strictEqual(el.dataset.viewerFile, '/tmp/example.txt');
  assert.strictEqual(layout.getTree(), before, '터미널 pane을 쪼개지 않는다 — 리플로우가 없다');
});

test('openFile — 표면이 pane이면 지금까지처럼 터미널 옆을 쪼갠다', async () => {
  const { document, core, layout, settings, lazy } = await load('<div id="terminal-container"></div>');
  await settings.set('viewer.surface', 'pane');
  core.registerSession('sess-1', { wsHandle: null, tabEl: null, tmuxName: 'dev' });
  layout.setPaneSession('sess-1');

  lazy.openFile('/tmp/example.txt');

  const tree = layout.getTree();
  assert.strictEqual(tree.t, 'split');
  assert.strictEqual(tree.b.file, '/tmp/example.txt');
  assert.strictEqual(document.getElementById('vt-file'), null);
});

test('window.openFile·window.openFileInPane 브리지가 둘 다 등록된다', async () => {
  const { window, lazy } = await load();
  assert.strictEqual(window.openFile, lazy.openFile);
  assert.strictEqual(window.openFileInPane, lazy.openFileInPane, 'e2e 스모크가 이 이름으로 pane 경로를 검증한다');
});
