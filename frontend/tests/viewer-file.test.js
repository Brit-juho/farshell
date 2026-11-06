'use strict';
// 파일 본문 렌더(panels/viewer/file.js) — 파일 개편 단계 2(CodeMirror 6, 읽기 전용).
// CM 자체는 레이아웃이 없는 jsdom에서 돌리지 않는다. 실제 그리기와 성능은 frontend/tests/perf/(실브라우저)와
// e2e 스모크가 맡고, 여기서는 file.js가 정하는 규칙을 본다.
// - 편집기 청크를 못 받으면 일반 텍스트로라도 보인다(이 하네스는 `@codemirror/*` 맨 이름 import를 풀지 못해
//   editor.js import가 실패한다 — 바로 그 경로다).
// - 다시 읽기는 떠 있는 편집기의 바뀐 부분만 바꾸고 호스트를 갈아 끼우지 않는다.
// - 바뀐 부분 계산(editor-text.js textChange).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const FILE_JS = path.join(__dirname, '../js/panels/viewer/file.js');
const TEXT_JS = path.join(__dirname, '../js/panels/viewer/editor-text.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build(respond) {
  const e = createDomEnv('<!doctype html><html><body><div id="scroll"></div></body></html>');
  _doms.push(e.dom);
  const { window } = e;
  window.API_BASE = '';
  window._tokenQuery = '';
  window.showToast = () => {};
  const fetched = [];
  window.fetch = async (url) => {
    fetched.push(String(url));
    return { ok: true, status: 200, json: async () => respond(String(url)) };
  };
  window.console.warn = () => {};   // 편집기 청크 실패 경고(의도된 경로)
  const file = await importFresh(FILE_JS, e.context, new Map());
  return { ...e, file, fetched };
}

test('편집기 청크를 못 받으면 일반 텍스트로 보이고, CRLF는 LF로 바뀐다', async () => {
  const { document, file } = await build(() => ({ content: 'a\r\nb<script>x</script>\r\n', size: 20 }));
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  document.getElementById('scroll').appendChild(host);
  await file.renderFile(host, '/repo/a.js');
  const pre = host.querySelector('pre.vt-vw-plain');
  assert.ok(pre, '대체 표시가 없다');
  assert.strictEqual(pre.textContent, 'a\nb<script>x</script>\n');
  assert.strictEqual(host.querySelector('script'), null, '내용은 텍스트로만 들어간다');
  assert.strictEqual(host._vtEditor, null);
});

test('잘린 파일은 경고 줄을 붙인다. 이미지·바이너리는 편집기를 쓰지 않는다', async () => {
  let resp = { content: 'x', truncated: true, size: 600 * 1024 };
  const { document, file } = await build(() => resp);
  const host = document.createElement('div');
  await file.renderFile(host, '/repo/big.js');
  assert.match(host.querySelector('.vt-vw-trunc').textContent, /600KB/);

  resp = { binary: true, size: 2048 };
  await file.renderFile(host, '/repo/a.bin');
  assert.match(host.textContent, /바이너리 파일 \(2KB\)/);
  assert.strictEqual(host.querySelector('.vt-vw-plain'), null);

  resp = { image: true, mime: 'image/png', size: 10 };
  await file.renderFile(host, '/repo/a.png');
  assert.match(host.querySelector('img').getAttribute('src'), /\/api\/fs\/raw\?path=%2Frepo%2Fa\.png/);
});

test('다시 읽기 — 편집기가 떠 있으면 그 자리에서 내용만 바꾸고 호스트는 그대로다', async () => {
  let content = 'v1\r\n';
  let truncated = true;
  const { document, file, fetched } = await build(() => ({ content, truncated, size: 700 * 1024 }));
  const scroll = document.getElementById('scroll');
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  scroll.appendChild(host);
  // CM 대신 같은 모양의 핸들을 붙인다(view.dom이 문서에 붙어 있으면 "떠 있는" 편집기다).
  const dom = document.createElement('div');
  host.appendChild(dom);
  const got = [];
  host._vtEditor = { view: { dom }, setText: (t) => { got.push(t); return true; }, destroy() {} };
  const oldNote = document.createElement('div');
  oldNote.className = 'vt-vw-note warn vt-vw-trunc';
  host.appendChild(oldNote);

  content = 'v2\r\n';
  truncated = false;
  await file.refreshFile(scroll, '/repo/a.js', () => true);
  assert.strictEqual(fetched.length, 1);
  assert.deepStrictEqual(got, ['v2\n']);
  assert.strictEqual(scroll.querySelector('.vt-vw-code-pane'), host, '호스트를 갈아 끼우면 스크롤·선택이 날아간다');
  assert.strictEqual(host.querySelector('.vt-vw-trunc'), null, '더 이상 잘리지 않으면 경고 줄을 거둔다');
});

test('다시 읽기 — 텍스트가 바이너리가 되면 새 호스트로 바꾸고, 다른 파일로 바뀐 뒤 도착한 응답은 버린다', async () => {
  let resp = { binary: true, size: 1 };
  const { document, file } = await build(() => resp);
  const scroll = document.getElementById('scroll');
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  scroll.appendChild(host);
  const dom = document.createElement('div');
  host.appendChild(dom);
  const got = [];
  host._vtEditor = { view: { dom }, setText: (t) => { got.push(t); return true; }, destroy() {} };
  await file.refreshFile(scroll, '/repo/a.js', () => true);
  assert.deepStrictEqual(got, []);
  const now = scroll.querySelector('.vt-vw-code-pane');
  assert.notStrictEqual(now, host);
  assert.match(now.textContent, /바이너리 파일/);

  resp = { content: 'late' };
  await file.refreshFile(scroll, '/repo/a.js', () => false);
  assert.strictEqual(scroll.querySelector('.vt-vw-code-pane'), now);
});

test('textChange — 같으면 null, 다르면 앞뒤 공통 부분을 뺀 한 덩어리', async () => {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const { textChange, editorLangKey } = await importFresh(TEXT_JS, e.context, new Map());
  assert.strictEqual(textChange('abc', 'abc'), null);
  assert.deepStrictEqual({ ...textChange('l1\nl2\n', 'l1\nl2\nl3\n') }, { from: 6, to: 6, insert: 'l3\n' });
  assert.deepStrictEqual({ ...textChange('abXcd', 'abYYcd') }, { from: 2, to: 3, insert: 'YY' });
  assert.deepStrictEqual({ ...textChange('aa', 'aaa') }, { from: 2, to: 2, insert: 'a' });
  assert.deepStrictEqual({ ...textChange('abc', '') }, { from: 0, to: 3, insert: '' });
  // 서로게이트 쌍(😀 = D83D DE00 / 😁 = D83D DE01)을 가르지 않는다.
  const c = textChange('x😀y', 'x😁y');
  assert.deepStrictEqual({ ...c }, { from: 1, to: 3, insert: '😁' });
  // 적용하면 새 내용이 된다(무작위 짝).
  const apply = (s, ch) => s.slice(0, ch.from) + ch.insert + s.slice(ch.to);
  const pool = ['', 'a', 'ab', 'aab', 'abab', 'ba', '😀', 'a😀b', '\n\n', 'a\nb'];
  for (const p of pool) for (const n of pool) {
    const ch = textChange(p, n);
    assert.strictEqual(ch ? apply(p, ch) : p, n, `${JSON.stringify(p)} → ${JSON.stringify(n)}`);
  }
  assert.strictEqual(editorLangKey('/a/b.tsx', 'typescript'), 'tsx');
  assert.strictEqual(editorLangKey('/a/b.ts', 'typescript'), 'typescript');
  assert.strictEqual(editorLangKey('/a/b.jsx', 'javascript'), 'javascript');
  assert.strictEqual(editorLangKey('/a/b.py', 'python'), 'python');
  assert.strictEqual(editorLangKey('/a/VERSION', null), null);
});
