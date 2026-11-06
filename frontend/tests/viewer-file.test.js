'use strict';
// 파일 본문 렌더(panels/viewer/file.js) — 파일 개편 단계 2(CodeMirror 6, 읽기 전용)와 단계 3(읽기 API 2판 연결).
// CM 자체는 레이아웃이 없는 jsdom에서 돌리지 않는다. 실제 그리기와 성능은 frontend/tests/perf/(실브라우저)와
// e2e 스모크가 맡고, 여기서는 file.js·file-load.js가 정하는 규칙을 본다.
// - 편집기 청크를 못 받으면 일반 텍스트로라도 보인다(이 하네스는 `@codemirror/*` 맨 이름 import를 풀지 못해
//   editor.js import가 실패한다 — 바로 그 경로다). 이어 받기·다시 읽기도 그 대체 표시에서 같은 길로 간다.
// - 1MB 구간으로 앞 4MB까지 열고, 이어 받는 구간은 첫 응답의 인코딩·rev를 고정해 보낸다.
// - 다시 읽기는 첫 구간을 If-None-Match로 묻고 304면 아무것도 하지 않는다. 200이면 바뀐 부분만 바꾼다.
// - 409가 3번 연속이면 「계속 바뀌는 중」으로 멈추고 옛 내용을 둔다.
// - 「더 보기」·「끝부분 보기」·인코딩 고르기.
// - 바뀐 부분 계산(editor-text.js textChange), 조각 이어 붙이기(file-load.js joinPiece·eolKinds).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const FILE_JS = path.join(__dirname, '../js/panels/viewer/file.js');
const LOAD_JS = path.join(__dirname, '../js/panels/viewer/file-load.js');
const TEXT_JS = path.join(__dirname, '../js/panels/viewer/editor-text.js');
const MB = 1024 * 1024;

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// 서버 /api/fs/file 2판 흉내(ASCII 내용만 — 바이트 = 글자). 구간은 창 안의 마지막 `\n`에서 자른다.
function fakeServer(initial) {
  const srv = { text: initial, rev: 1, encoding: 'utf-8', confident: true, reason: null, changingFor: 0, binary: false };
  srv.handle = (url, headers) => {
    const q = new URL(url, 'http://x').searchParams;
    const rev = `r${srv.rev}`;
    const enc = q.get('encoding') || 'auto';
    const etag = `W/"${rev}|${enc}|${q.get('offset')}|${q.get('length')}|${q.get('tail')}"`;
    if (q.get('expected_rev') && q.get('expected_rev') !== rev) return { status: 409, body: { error: 'changed', rev } };
    if (srv.changingFor > 0) {
      srv.changingFor -= 1;
      srv.rev += 1;
      if (q.get('expected_rev')) return { status: 409, body: { error: 'changed', rev: `r${srv.rev}` } };
    }
    if (headers && headers['If-None-Match'] === etag) return { status: 304, etag };
    const base = {
      path: q.get('path'), size: srv.text.length, rev, binary: srv.binary, image: false,
      encoding: enc === 'auto' ? srv.encoding : enc, encoding_confident: enc === 'auto' ? srv.confident : true, bom: false,
    };
    if (srv.reason && enc === 'auto') base.encoding_reason = srv.reason;
    if (srv.binary) return { status: 200, etag, body: { ...base, content: '', eof: true, next_offset: null } };
    if (q.get('tail')) {
      const n = Number(q.get('tail'));
      let start = Math.max(0, srv.text.length - n);
      let lt = false;
      if (start > 0) {
        const i = srv.text.indexOf('\n', start - 1);
        if (i >= 0 && i + 1 < srv.text.length) start = i + 1; else lt = true;
      }
      return { status: 200, etag, body: { ...base, content: srv.text.slice(start), offset: start, eof: true, next_offset: null, line_truncated: lt, tail: true } };
    }
    const off = Number(q.get('offset') || 0);
    const len = Number(q.get('length') || MB);
    let end = Math.min(srv.text.length, off + len);
    let lt = false;
    if (end < srv.text.length) {
      const i = srv.text.lastIndexOf('\n', end - 1);
      if (i >= off) end = i + 1; else lt = true;
    }
    const eof = end >= srv.text.length;
    return {
      status: 200, etag,
      body: { ...base, content: srv.text.slice(off, end), offset: off, length: end - off, next_offset: eof ? null : end, eof, line_truncated: lt },
    };
  };
  return srv;
}

async function build(srv) {
  const e = createDomEnv('<!doctype html><html><body><div id="scroll"></div></body></html>');
  _doms.push(e.dom);
  const { window } = e;
  window.API_BASE = '';
  window._tokenQuery = '';
  const toasts = [];
  window.showToast = (m) => toasts.push(m);
  const fetched = [];
  window.fetch = async (url, opts) => {
    const headers = (opts && opts.headers) || {};
    fetched.push({ url: String(url), headers });
    const r = srv.handle(String(url), headers);
    return {
      ok: r.status >= 200 && r.status < 300, status: r.status,
      headers: { get: (k) => (k.toLowerCase() === 'etag' ? r.etag || null : null) },
      json: async () => r.body,
    };
  };
  window.console.warn = () => {};   // 편집기 청크 실패 경고(의도된 경로)
  const cache = new Map();
  const file = await importFresh(FILE_JS, e.context, cache);
  const load = await importFresh(LOAD_JS, e.context, cache);
  return { ...e, file, load, fetched, toasts };
}

function mountHost(document) {
  const scroll = document.getElementById('scroll');
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  scroll.appendChild(host);
  return { scroll, host };
}

const params = (f) => new URL(f.url, 'http://x').searchParams;

function lines(n, width = 63) {
  const row = `${'x'.repeat(width)}\n`;
  return row.repeat(n);
}

test('편집기 청크를 못 받으면 일반 텍스트로 보이고, CRLF는 LF로 바뀐다. 요청은 1MB 구간이다', async () => {
  const srv = fakeServer('a\r\nb<script>x</script>\r\n');
  const { document, file, fetched } = await build(srv);
  const { host } = mountHost(document);
  await file.renderFile(host, '/repo/a.js');
  const pre = host.querySelector('pre.vt-vw-plain');
  assert.ok(pre, '대체 표시가 없다');
  assert.strictEqual(pre.textContent, 'a\nb<script>x</script>\n');
  assert.strictEqual(host.querySelector('script'), null, '내용은 텍스트로만 들어간다');
  assert.strictEqual(host._vtEditor, null);
  assert.strictEqual(fetched.length, 1);
  assert.strictEqual(params(fetched[0]).get('offset'), '0');
  assert.strictEqual(params(fetched[0]).get('length'), String(MB));
  assert.strictEqual(params(fetched[0]).get('encoding'), 'auto');
  assert.strictEqual(host.querySelector('.vt-vw-bar'), null, '알릴 것이 없으면 위 띠가 없다');
  assert.strictEqual(host.querySelector('.vt-vw-trunc'), null);
});

test('4MB를 넘는 파일 — 앞 4MB를 이어 받고(인코딩·rev 고정), 「더 보기」로 나머지를 붙인다', async () => {
  const text = lines(6 * 16384);   // 6MB, 64바이트 줄
  const srv = fakeServer(text);
  srv.encoding = 'unknown';
  srv.confident = false;
  srv.reason = 'not_utf8';
  const { document, file, fetched } = await build(srv);
  const { host } = mountHost(document);
  await file.renderFile(host, '/repo/big.log');
  assert.strictEqual(fetched.length, 4, '1MB 구간 4개');
  for (const f of fetched.slice(1)) {
    assert.strictEqual(params(f).get('encoding'), 'utf-8', 'unknown이면 이어 받기는 utf-8로 고정');
    assert.strictEqual(params(f).get('expected_rev'), 'r1');
  }
  const pre = host.querySelector('pre.vt-vw-plain');
  assert.strictEqual(pre.textContent.length, 4 * MB);
  const note = host.querySelector('.vt-vw-trunc');
  assert.match(note.textContent, /앞 4\.0MB만 불러왔습니다 \(전체 6\.0MB\)/);
  assert.match(note.textContent, /찾기와 줄 이동은 불러온 범위 안에서만/);
  // D9 — 인코딩을 모르면 읽기 전용 이유와 인코딩 고르기를 띄운다.
  const bar = host.querySelector('.vt-vw-bar');
  assert.match(bar.textContent, /읽기 전용 · UTF-8이 아닌 바이트가 있어/);
  assert.ok(bar.querySelector('select'), '인코딩 고르기');
  assert.strictEqual(bar.querySelector('select').value, 'auto');

  const moreBtn = [...note.querySelectorAll('button')].find((b) => b.textContent === '더 보기');
  assert.ok(moreBtn);
  await file.more(host);
  assert.strictEqual(pre.textContent, text, '붙인 결과가 파일 전체와 같다');
  assert.strictEqual(host.querySelector('.vt-vw-trunc'), null, '끝까지 받으면 아래 띠를 거둔다');
});

test('다시 읽기 — 304면 그대로, 200이면 바뀐 부분만, 같은 내용이면 아무것도 바꾸지 않는다', async () => {
  const srv = fakeServer('v1\n');
  const { document, file, fetched } = await build(srv);
  const { scroll, host } = mountHost(document);
  await file.renderFile(host, '/repo/a.js');
  // CM 대신 같은 모양의 핸들을 붙인다(view.dom이 문서에 붙어 있으면 "떠 있는" 편집기다).
  const dom = document.createElement('div');
  host.appendChild(dom);
  const got = [];
  let doc = 'v1\n';
  host._vtEditor = {
    view: { dom }, setText: (t) => { const ch = t !== doc; doc = t; got.push(t); return ch; }, append: (t) => { doc += t; }, destroy() {},
  };
  host._vtPlain = null;

  await file.refreshFile(scroll, '/repo/a.js', () => true);
  assert.strictEqual(fetched.length, 2);
  assert.ok(fetched[1].headers['If-None-Match'], '첫 구간을 조건부로 묻는다');
  assert.deepStrictEqual(got, [], '304면 아무것도 바꾸지 않는다');

  srv.text = 'v2\r\n';
  srv.rev += 1;
  await file.refreshFile(scroll, '/repo/a.js', () => true);
  assert.deepStrictEqual(got, ['v2\n']);
  assert.strictEqual(scroll.querySelector('.vt-vw-code-pane'), host, '호스트를 갈아 끼우면 스크롤·선택이 날아간다');

  srv.rev += 1;   // 시각만 바뀐 저장(같은 바이트)
  await file.refreshFile(scroll, '/repo/a.js', () => true);
  assert.deepStrictEqual(got, ['v2\n', 'v2\n'], '내용 비교는 setText가 한다(같으면 그리지 않음)');
});

test('계속 바뀌는 파일 — 409가 3번 연속이면 옛 내용을 두고 알린다. 다음에 그대로면 알림을 거둔다', async () => {
  const text = lines(32768);   // 2MB → 이어 받기가 있다
  const srv = fakeServer(text);
  const { document, file } = await build(srv);
  const { scroll, host } = mountHost(document);
  await file.renderFile(host, '/repo/live.log');
  const pre = host.querySelector('pre.vt-vw-plain');
  assert.strictEqual(pre.textContent, text);

  srv.rev += 1;
  srv.changingFor = 99;
  await file.refreshFile(scroll, '/repo/live.log', () => true);
  assert.strictEqual(pre.textContent, text, '옛 내용을 둔다');
  assert.match(host.querySelector('.vt-vw-bar').textContent, /파일이 계속 바뀌는 중/);

  srv.changingFor = 0;
  srv.text = `${text}tail\n`;
  srv.rev += 1;
  await file.refreshFile(scroll, '/repo/live.log', () => true);
  assert.strictEqual(pre.textContent, `${text}tail\n`);
  assert.strictEqual(host.querySelector('.vt-vw-bar'), null);
});

test('끝부분 보기 — tail 구간을 받고, 다시 읽기는 조건부, 「처음부터 보기」로 돌아온다', async () => {
  const text = lines(6 * 16384);
  const srv = fakeServer(text);
  const { document, file, fetched } = await build(srv);
  const { scroll, host } = mountHost(document);
  await file.renderFile(host, '/repo/big.log');
  const tailBtn = [...host.querySelectorAll('.vt-vw-trunc button')].find((b) => b.textContent === '끝부분 보기');
  // jsdom에는 jumpToEnd가 없는 대체 표시라 그대로 눌러도 된다.
  fetched.length = 0;
  tailBtn.click();
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(params(fetched[0]).get('tail'), String(MB));
  assert.strictEqual(host.querySelector('pre.vt-vw-plain').textContent, text.slice(text.length - MB));
  assert.match(host.querySelector('.vt-vw-bar').textContent, /끝부분만 보는 중/);
  assert.strictEqual(host.querySelector('.vt-vw-trunc'), null);

  fetched.length = 0;
  await file.refreshFile(scroll, '/repo/big.log', () => true);
  assert.strictEqual(fetched.length, 1);
  assert.ok(fetched[0].headers['If-None-Match']);
  assert.strictEqual(params(fetched[0]).get('tail'), String(MB));

  const back = [...host.querySelectorAll('.vt-vw-bar button')].find((b) => b.textContent === '처음부터 보기');
  back.click();
  await new Promise((r) => setTimeout(r, 50));
  assert.strictEqual(host.querySelector('pre.vt-vw-plain').textContent.length, 4 * MB);
});

test('인코딩 고르기 — 고른 값으로 다시 열고, (호스트, 경로)마다 기억한다', async () => {
  const srv = fakeServer('abc\n');
  srv.encoding = 'unknown';
  srv.confident = false;
  srv.reason = 'not_utf8';
  const { document, file, fetched } = await build(srv);
  const { host } = mountHost(document);
  await file.renderFile(host, '/repo/k.txt');
  const sel = host.querySelector('.vt-vw-bar select');
  sel.value = 'cp949';
  sel.dispatchEvent(new document.defaultView.Event('change'));
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(params(fetched[fetched.length - 1]).get('encoding'), 'cp949');
  // 고른 상태면 확신해도 띠가 남아 되돌릴 수 있다.
  assert.strictEqual(host.querySelector('.vt-vw-bar select').value, 'cp949');
  assert.doesNotMatch(host.querySelector('.vt-vw-bar').textContent, /읽기 전용/);

  const other = document.createElement('div');
  await file.renderFile(other, '/repo/k.txt', { host: 'peer-b' });
  assert.strictEqual(params(fetched[fetched.length - 1]).get('encoding'), 'auto', '다른 호스트의 같은 경로는 따로');
  assert.match(fetched[fetched.length - 1].url, /^\/api\/hosts\/peer-b\/relay\/api\/fs\/file\?/, '그 호스트로 중계');
});

test('줄 이동 — 받지 않은 줄이면 더 받고, 상한 밖이면 알린다(대체 표시에는 편집기가 없어 이동하지 않는다)', async () => {
  const srv = fakeServer('a\nb\n');
  const { document, file, toasts } = await build(srv);
  const { host } = mountHost(document);
  await file.renderFile(host, '/repo/a.js', { line: 2, col: 1 });
  assert.deepStrictEqual(toasts, [], '편집기가 없으면 조용히 넘어간다');
});

test('다시 읽기 — 텍스트가 바이너리가 되면 새 호스트로 바꾸고, 다른 파일로 바뀐 뒤 도착한 응답은 버린다', async () => {
  const srv = fakeServer('x\n');
  const { document, file } = await build(srv);
  const { scroll, host } = mountHost(document);
  await file.renderFile(host, '/repo/a.js');
  srv.binary = true;
  srv.rev += 1;
  await file.refreshFile(scroll, '/repo/a.js', () => true);
  const now = scroll.querySelector('.vt-vw-code-pane');
  assert.notStrictEqual(now, host);
  assert.match(now.textContent, /바이너리 파일/);

  srv.binary = false;
  srv.rev += 1;
  await file.refreshFile(scroll, '/repo/a.js', () => false);
  assert.strictEqual(scroll.querySelector('.vt-vw-code-pane'), now);
});

test('joinPiece·eolKinds — 구간 끝의 \\r은 다음 조각으로 넘기고, 줄 끝 종류를 센다', async () => {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  e.window.API_BASE = '';
  const { joinPiece, eolKinds, mixedEol, canLoadMore, MAX_LOADED } = await importFresh(LOAD_JS, e.context, new Map());
  let r = joinPiece('', 'a\r\nb\r', false);
  assert.deepStrictEqual({ ...r }, { text: 'a\nb', carry: '\r' });
  r = joinPiece(r.carry, '\nc', true);
  assert.deepStrictEqual({ ...r }, { text: '\nc', carry: '' }, 'CRLF가 한 줄로');
  assert.deepStrictEqual({ ...joinPiece('', 'x\r', true) }, { text: 'x\n', carry: '' }, '끝이면 넘기지 않는다');
  assert.strictEqual(mixedEol(eolKinds('a\r\nb\r\n')), false);
  assert.strictEqual(mixedEol(eolKinds('a\nb\n')), false);
  assert.strictEqual(mixedEol(eolKinds('a\r\nb\n')), true);
  assert.strictEqual(mixedEol(eolKinds('a\rb\n')), true);
  const k = eolKinds('a\r\nb\r');
  eolKinds('\r\nc\r\n', k);   // 넘겨받은 \r이 붙은 다음 조각
  assert.strictEqual(mixedEol(k), false);
  assert.strictEqual(canLoadMore({ mode: 'head', eof: false, nextOffset: MAX_LOADED - 1 }), true);
  assert.strictEqual(canLoadMore({ mode: 'head', eof: false, nextOffset: MAX_LOADED }), false);
  assert.strictEqual(canLoadMore({ mode: 'tail', eof: true, nextOffset: null }), false);
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
