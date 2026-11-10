'use strict';
// 파일 개편 단계 7 — 미리보기와 다운로드(panels/viewer/markdown.js·preview.js·download.js, file.js 연결).
// - 마크다운 정화: 실제 marked + DOMPurify를 jsdom 창에 붙여(markdown-lib.js와 같은 조합) XSS 시도를 넣어 본다.
//   원문 HTML은 글자로, javascript:·data: 링크는 href 없음, onerror·srcset·id·style 없음, 외부·data:·루트(/) 이미지는
//   자리표시, 상대 이미지는 문서 폴더 기준 /api/fs/raw, 링크는 http·https·mailto + noopener noreferrer.
// - DOMPurify 단계만 따로(파서가 HTML을 그대로 넘겼다고 가정)도 막는지 — 두 겹 중 하나가 뚫려도.
// - CSV 파서·표, 뷰어의 렌더/원문 전환, SVG는 <img>, PDF·바이너리는 다운로드만, 미디어는 <video>/<audio>.
// - 다운로드: 원격(호스트 전환·다른 호스트 파일)은 요청하지 않고 이유, 409·413 알림, 받은 바이트로 저장, 큰 파일은
//   브라우저 다운로드로 넘김.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MD_JS = path.join(__dirname, '../js/panels/viewer/markdown.js');
const PREVIEW_JS = path.join(__dirname, '../js/panels/viewer/preview.js');
const FILE_JS = path.join(__dirname, '../js/panels/viewer/file.js');
const DL_JS = path.join(__dirname, '../js/panels/viewer/download.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function libs() {
  const { Marked } = await import('marked');
  const createDOMPurify = (await import('dompurify')).default;
  return { Marked, createDOMPurify };
}

async function mdEnv() {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const md = await importFresh(MD_JS, e.context, new Map());
  const { Marked, createDOMPurify } = await libs();
  const purify = createDOMPurify(e.window);
  const render = md.createMarkdown({ Marked, purify });
  const raws = [];
  const go = (text, docPath = '/repo/docs/README.md') => {
    const box = e.document.createElement('div');
    box.appendChild(render(text, { docPath, rawUrl: (p) => { raws.push(p); return `/api/fs/raw?path=${encodeURIComponent(p)}`; } }));
    return box;
  };
  return { ...e, md, purify, render, go, raws, Marked };
}

// 어떤 요소에도 실행 가능한 속성·식별 속성이 남지 않는다.
function assertInert(box) {
  for (const el of box.querySelectorAll('*')) {
    for (const name of el.getAttributeNames()) {
      assert.ok(!/^on/i.test(name), `${el.tagName}에 ${name}가 남았다`);
      assert.ok(!['id', 'name', 'style', 'srcset', 'formaction', 'xlink:href', 'action'].includes(name.toLowerCase()), `${el.tagName}에 ${name}가 남았다`);
    }
    const href = el.getAttribute('href');
    if (href != null) assert.match(href, /^(?:https?:|mailto:|#)/i, `위험한 href: ${href}`);
    const src = el.getAttribute('src');
    if (src != null) assert.match(src, /^\/api\/fs\/raw\?path=/, `허용 밖 src: ${src}`);
  }
  for (const tag of ['script', 'iframe', 'object', 'embed', 'svg', 'math', 'style', 'form', 'link', 'meta', 'base', 'video', 'audio']) {
    assert.strictEqual(box.querySelector(tag), null, `<${tag}>가 남았다`);
  }
}

test('마크다운 — 원문 HTML은 글자로 보인다(script·img onerror·svg script·iframe)', async () => {
  const { go } = await mdEnv();
  const box = go([
    '<script>alert(1)</script>',
    '',
    'inline <img src=x onerror=alert(1)> and <a href="javascript:alert(1)">x</a>',
    '',
    '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(2)</script></svg>',
    '',
    '<iframe src="https://evil.example"></iframe>',
    '<style>body{display:none}</style>',
  ].join('\n'));
  assertInert(box);
  assert.strictEqual(box.querySelector('img'), null);
  assert.match(box.textContent, /<script>alert\(1\)<\/script>/);
  assert.match(box.textContent, /<img src=x onerror=alert\(1\)>/);
});

test('마크다운 — 링크 프로토콜: javascript:·data:·vbscript:·상대·//호스트는 href 없음, http·https·mailto는 새 탭 + noopener', async () => {
  const { go } = await mdEnv();
  const bad = [
    'javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'javas&#x63;ript:alert(1)', '&#106;avascript:alert(1)',
    'data:text/html,<script>alert(1)</script>', 'vbscript:msgbox(1)', 'docs/x.md', '//evil.example/x', '/api/tmux/kill/a',
  ];
  for (const href of bad) {
    const box = go(`[click](${href})`);
    const a = box.querySelector('a');
    assert.ok(a, href);
    assert.strictEqual(a.getAttribute('href'), null, `${href}의 href가 남았다`);
    assertInert(box);
  }
  const ok = go('[a](https://example.com/x) [b](http://example.com) [c](mailto:a@example.com)');
  const links = [...ok.querySelectorAll('a')];
  assert.strictEqual(links.length, 3);
  for (const a of links) {
    assert.strictEqual(a.getAttribute('target'), '_blank');
    assert.strictEqual(a.getAttribute('rel'), 'noopener noreferrer');
  }
  const anchor = go('[go](#Section-Two)').querySelector('a');
  assert.strictEqual(anchor.getAttribute('href'), '#Section-Two');
  assert.strictEqual(anchor.getAttribute('target'), null, '문서 안 앵커는 이 칸에서');
});

test('마크다운 — 이미지: 외부·data:·//·/ 주소는 자리표시, 상대 경로는 문서 폴더 기준 /api/fs/raw', async () => {
  const { go, raws } = await mdEnv();
  for (const src of ['https://img.shields.io/badge/x.svg', 'http://tracker.example/p.gif', '//evil.example/p.png',
    '/api/tmux/kill/x', 'data:image/svg+xml;base64,PHN2Zz48c2NyaXB0PmFsZXJ0KDEpPC9zY3JpcHQ+PC9zdmc+', 'javascript:alert(1)']) {
    const box = go(`![alt](${src})`);
    assert.strictEqual(box.querySelector('img'), null, src);
    const ph = box.querySelector('.vt-md-blocked');
    assert.ok(ph, `${src} 자리표시가 없다`);
    assert.match(ph.textContent, /외부 이미지를 막았습니다/);
    assertInert(box);
  }
  const ext = go('![a](https://img.shields.io/badge/x.svg)');
  assert.strictEqual(ext.querySelector('.vt-md-copy').dataset.copy, 'https://img.shields.io/badge/x.svg');
  assert.strictEqual(go('![a](data:image/png;base64,AAAA)').querySelector('.vt-md-copy'), null, 'data: 주소는 복사 단추가 없다');

  raws.length = 0;
  const rel = go('![shot](img/shot.png) ![up](../assets/a%20b.png "t") ![q](x.png?v=1#frag)');
  const imgs = [...rel.querySelectorAll('img')];
  assert.strictEqual(imgs.length, 3);
  assert.deepStrictEqual(raws, ['/repo/docs/img/shot.png', '/repo/assets/a b.png', '/repo/docs/x.png']);
  for (const img of imgs) {
    assert.strictEqual(img.getAttribute('srcset'), null);
    assert.strictEqual(img.getAttribute('loading'), 'lazy');
    assert.strictEqual(img.getAttribute('referrerpolicy'), 'no-referrer');
  }
  assertInert(rel);
});

test('마크다운 — 이미지 제목·대체 글에 따옴표를 넣어 속성을 끊으려 해도 속성이 생기지 않는다', async () => {
  const { go } = await mdEnv();
  const box = go('![a" onerror="alert(1)](x.png "t\\" onload=\\"alert(2)") [l](https://e.example "x\\" onmouseover=\\"alert(3)")');
  assertInert(box);
});

test('마크다운 — 체크 상자는 비활성 checkbox만, 표의 align은 left/center/right만', async () => {
  const { go } = await mdEnv();
  const box = go('- [x] done\n- [ ] todo\n\n| a | b |\n|:-:|--:|\n| 1 | 2 |\n');
  const boxes = [...box.querySelectorAll('input')];
  assert.strictEqual(boxes.length, 2);
  for (const b of boxes) {
    assert.strictEqual(b.getAttribute('type'), 'checkbox');
    assert.ok(b.hasAttribute('disabled'));
  }
  assert.ok(boxes[0].hasAttribute('checked') && !boxes[1].hasAttribute('checked'));
  const th = box.querySelectorAll('th');
  assert.strictEqual(th[0].getAttribute('align'), 'center');
  assert.strictEqual(th[1].getAttribute('align'), 'right');
  assertInert(box);
});

test('DOMPurify 단계만으로도 막는다 — 파서가 원문 HTML을 그대로 넘긴 경우(두 겹 중 하나가 뚫려도)', async () => {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const md = await importFresh(MD_JS, e.context, new Map());
  const { createDOMPurify } = await libs();
  class PassThrough { parse(s) { return s; } }
  const render = md.createMarkdown({ Marked: PassThrough, purify: createDOMPurify(e.window) });
  const box = e.document.createElement('div');
  box.appendChild(render([
    '<p id="x" name="y" style="color:red" onclick="alert(1)">p</p>',
    '<img src="x" onerror="alert(1)" srcset="https://evil.example/a.png 2x">',
    '<a href="javascript:alert(1)">j</a><a href="https://ok.example" target="_self">ok</a>',
    '<svg><script>alert(1)</script><a xlink:href="javascript:alert(1)">s</a></svg>',
    '<math><mi xlink:href="javascript:alert(1)">m</mi></math>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe><object data="x"></object><embed src="x">',
    '<form action="https://evil.example"><input type="text" name="q"><button formaction="javascript:alert(1)">b</button></form>',
    '<details open ontoggle="alert(1)">d</details><video><source onerror="alert(1)"></video>',
    '<input type="checkbox" onclick="alert(1)" checked><input type="image" src="x">',
    '<base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=https://evil.example">',
    '<a href="&#x6A;avascript:alert(1)">e</a><img src="data:image/svg+xml,<svg onload=alert(1)>">',
  ].join('\n'), { docPath: '/repo/a.md', rawUrl: (p) => `/api/fs/raw?path=${encodeURIComponent(p)}` }));
  assertInert(box);
  const ok = [...box.querySelectorAll('a')].find((a) => a.getAttribute('href') === 'https://ok.example');
  assert.strictEqual(ok.getAttribute('target'), '_blank');
  assert.strictEqual(ok.getAttribute('rel'), 'noopener noreferrer');
  const inputs = [...box.querySelectorAll('input')];
  assert.ok(inputs.every((i) => i.getAttribute('type') === 'checkbox' && i.hasAttribute('disabled')));
});

test('resolveRelative·slugify', async () => {
  const { md } = await mdEnv();
  assert.strictEqual(md.resolveRelative('/a/b/c.md', 'x.png'), '/a/b/x.png');
  assert.strictEqual(md.resolveRelative('/a/b/c.md', './x/../y.png'), '/a/b/y.png');
  assert.strictEqual(md.resolveRelative('/a/b/c.md', '../../../../z.png'), '/z.png');
  assert.strictEqual(md.resolveRelative('/a/b/c.md', '/etc/passwd'), null);
  assert.strictEqual(md.resolveRelative('/a/b/c.md', 'https://x'), null);
  assert.strictEqual(md.resolveRelative('/a/b/c.md', '\\\\host\\x'), null);
  assert.strictEqual(md.resolveRelative('/a/b/c.md', ''), null);
  assert.strictEqual(md.slugify('  Section Two! '), 'section-two');
  assert.strictEqual(md.slugify('토큰 규칙'), '토큰-규칙');
});

// --- CSV --------------------------------------------------------------------------------------

test('CSV 파서 — 따옴표 안의 구분자·줄바꿈·"", CRLF, 끝 빈 줄', async () => {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const p = await importFresh(PREVIEW_JS, e.context, new Map());
  const j = (v) => JSON.parse(JSON.stringify(v));   // vm 창의 배열 → 이 쪽 배열
  assert.deepStrictEqual(j(p.parseCsv('a,b\r\n1,"x, ""y""\nz"\n2,\n')), [['a', 'b'], ['1', 'x, "y"\nz'], ['2', '']]);
  assert.deepStrictEqual(j(p.parseCsv('a\tb\n1\t2', '\t')), [['a', 'b'], ['1', '2']]);
  assert.ok(p.isNumeric('1,204') && p.isNumeric('-0.5') && p.isNumeric('12%') && !p.isNumeric('2026-11-01') && !p.isNumeric(''));
  assert.deepStrictEqual(j(p.csvWindow(0, 240, 1000, 24, 0)), [0, 10]);
  assert.deepStrictEqual(j(p.csvWindow(2400, 240, 1000, 24, 5)), [95, 115]);
  assert.deepStrictEqual(j(p.csvWindow(999999, 240, 50, 24, 5)), [50, 50]);
  assert.strictEqual(p.previewKind('/a/B.MD'), 'md');
  assert.strictEqual(p.previewKind('/a/x.tsv'), 'csv');
  assert.strictEqual(p.previewKind('/a/x.svg'), 'svg');
  assert.strictEqual(p.previewKind('/a/x.pdf'), 'pdf');
  assert.strictEqual(p.previewKind('/a/x.js'), null);
});

// --- 뷰어 연결(file.js) -------------------------------------------------------------------------

function fakeFs(files) {
  return (url) => {
    const q = new URL(url, 'http://x').searchParams;
    const f = files[q.get('path')];
    if (!f) return { status: 404, body: { error: 'not found' } };
    const base = { path: q.get('path'), size: f.size ?? (f.text || '').length, rev: f.rev || 'r1', encoding: 'utf-8', encoding_confident: true, bom: false };
    if (f.binary) return { status: 200, body: { ...base, binary: true, image: false, media: f.media, mime: f.mime, content: '', eof: true, next_offset: null } };
    return { status: 200, body: { ...base, binary: false, image: false, content: f.text, offset: 0, length: f.text.length, next_offset: null, eof: true, line_truncated: false } };
  };
}

async function viewerEnv(files, { relay } = {}) {
  const e = createDomEnv('<!doctype html><html><body><div id="scroll"></div></body></html>');
  _doms.push(e.dom);
  const { window } = e;
  if (relay) window.localStorage.setItem('vt.relayHost', relay);
  window.API_BASE = '';
  window._tokenQuery = '';
  const toasts = [];
  window.showToast = (m, kind) => toasts.push([m, kind]);
  const handle = fakeFs(files);
  const fetched = [];
  window.fetch = async (url) => {
    fetched.push(String(url));
    const r = handle(String(url));
    return { ok: r.status < 300, status: r.status, headers: { get: () => null }, json: async () => r.body };
  };
  window.console.warn = () => {};
  const cache = new Map();
  const file = await importFresh(FILE_JS, e.context, cache);
  const md = await importFresh(MD_JS, e.context, cache);
  const { Marked, createDOMPurify } = await libs();
  const render = md.createMarkdown({ Marked, purify: createDOMPurify(window) });
  file._setMarkdownLoader(() => ({ renderMarkdown: render }));
  window.showToast = (m, kind) => toasts.push([m, kind]);   // 그래프 안의 ui/toast.js가 덮어쓴 것을 되돌린다
  const scroll = e.document.getElementById('scroll');
  const host = e.document.createElement('div');
  host.className = 'vt-vw-code-pane';
  scroll.appendChild(host);
  return { ...e, file, host, scroll, toasts, fetched };
}

test('뷰어 — 마크다운은 기본 렌더(정화), 위 띠에서 원문으로 바꾸면 편집기(대체 표시)로 다시 연다', async () => {
  const text = '# 제목\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1)) ![i](https://t.example/p.png)\n\n[아래](#끝)\n\n## 끝\n';
  const { file, host } = await viewerEnv({ '/repo/README.md': { text } });
  await file.renderFile(host, '/repo/README.md');
  const doc = host.querySelector('.vt-md');
  assert.ok(doc, '렌더가 없다');
  assert.strictEqual(doc.querySelector('h1').textContent, '제목');
  assertInert(doc);
  assert.ok(doc.querySelector('.vt-md-blocked'));
  const seg = host.querySelector('.vt-vw-bar .vt-vw-seg');
  assert.ok(seg, '렌더/원문 전환이 없다');
  const [renderBtn, sourceBtn] = seg.querySelectorAll('button');
  assert.strictEqual(renderBtn.getAttribute('aria-pressed'), 'true');
  assert.strictEqual(host.querySelector('pre.vt-vw-plain'), null);
  sourceBtn.click();
  await new Promise((r) => setTimeout(r, 30));
  assert.strictEqual(host.querySelector('.vt-md'), null);
  assert.strictEqual(host.querySelector('pre.vt-vw-plain').textContent, text);
  assert.strictEqual(host.querySelector('.vt-vw-seg button[aria-pressed="true"]').textContent, '원문');
});

test('뷰어 — 줄 이동으로 연 마크다운은 원문으로 연다(그 줄로 가야 하므로)', async () => {
  const { file, host } = await viewerEnv({ '/repo/b.md': { text: '# a\n\nb\n' } });
  await file.renderFile(host, '/repo/b.md', { line: 2 });
  assert.strictEqual(host.querySelector('.vt-md'), null);
  assert.ok(host.querySelector('pre.vt-vw-plain'));
});

test('뷰어 — 1MB를 넘는 마크다운·CSV는 원문으로 열고 이유를 적는다', async () => {
  const big = `${'x'.repeat(100)}\n`.repeat(11000);   // 1.1MB, 첫 구간 안
  const { file, host } = await viewerEnv({ '/repo/big.csv': { text: big } });
  await file.renderFile(host, '/repo/big.csv');
  assert.strictEqual(host.querySelector('.vt-csv'), null);
  assert.ok(host.querySelector('pre.vt-vw-plain'));
  assert.match(host.querySelector('.vt-vw-bar').textContent, /1MB를 넘어 표 대신 원문으로/);
  assert.ok(host.querySelector('.vt-vw-seg button').disabled, '표 단추는 막힌다');
});

test('뷰어 — 열이 512개를 넘거나 행이 10만을 넘는 CSV는 표를 만들지 않고 원문으로 열고 이유를 적는다', async () => {
  const wide = `${','.repeat(100000)}\n1\n`;              // 1MB 안, 열 10만 개 — 표로 그리면 탭이 멈춘다
  const tall = 'a\n' + '1\n'.repeat(100001);
  const ok = `${Array.from({ length: 512 }, (_, i) => `c${i}`).join(',')}\n${'1,'.repeat(511)}1\n`;
  const { file, host, document } = await viewerEnv({
    '/repo/wide.csv': { text: wide }, '/repo/tall.csv': { text: tall }, '/repo/ok.csv': { text: ok },
  });
  for (const p of ['/repo/wide.csv', '/repo/tall.csv']) {
    const h = document.createElement('div');
    h.className = 'vt-vw-code-pane';
    document.body.appendChild(h);
    await file.renderFile(h, p);
    assert.strictEqual(h.querySelector('.vt-csv'), null, `${p}: 표를 만들었다`);
    assert.strictEqual(h.querySelectorAll('th').length, 0);
    assert.ok(h.querySelector('pre.vt-vw-plain'), `${p}: 원문이 없다`);
    assert.match(h.querySelector('.vt-vw-bar').textContent, /표가 너무 커서\(최대 512열/);
    assert.ok(h.querySelector('.vt-vw-seg button').disabled, '표 단추는 막힌다');
  }
  await file.renderFile(host, '/repo/ok.csv');
  assert.strictEqual(host.querySelectorAll('.vt-csv-table thead th').length, 513, '512열은 표로 그린다');
});

test('CSV 표 — 상한을 넘으면 아무것도 붙이지 않는다', async () => {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const p = await importFresh(PREVIEW_JS, e.context, new Map());
  const box = e.document.createElement('div');
  const r = p.mountCsvTable(box, `${','.repeat(p.CSV_MAX_COLS)}\n`, '/a.csv');
  assert.ok(r.tooBig && r.cols === p.CSV_MAX_COLS + 1);
  assert.strictEqual(box.childNodes.length, 0);
});

test('뷰어 — CSV는 표(머리 고정, 칸은 글자로만), SVG는 <img>로만 그린다', async () => {
  const csv = 'name,n\n"<img src=x onerror=alert(1)>",1\nb,2\n';
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>';
  const { file, host, document } = await viewerEnv({ '/repo/m.csv': { text: csv }, '/repo/logo.svg': { text: svg } });
  await file.renderFile(host, '/repo/m.csv');
  const table = host.querySelector('.vt-csv-table');
  assert.ok(table);
  assert.deepStrictEqual([...table.querySelectorAll('thead th')].map((x) => x.textContent), ['#', 'name', 'n']);
  assert.strictEqual(table.querySelector('img'), null, '칸 내용은 HTML로 해석되지 않는다');
  assert.match(table.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.ok(table.querySelector('td.n'), '숫자 열은 오른쪽 맞춤');

  const h2 = document.createElement('div');
  h2.className = 'vt-vw-code-pane';
  document.body.appendChild(h2);
  await file.renderFile(h2, '/repo/logo.svg');
  const img = h2.querySelector('.vt-vw-svg img');
  assert.ok(img, 'SVG는 <img>');
  const u = new URL(img.getAttribute('src'), 'http://localhost');
  assert.strictEqual(u.pathname + u.search, '/api/fs/raw?path=%2Frepo%2Flogo.svg');
  assert.strictEqual(h2.querySelector('svg script'), null);
  assert.strictEqual(h2.querySelectorAll('script').length, 0);
  assert.match(h2.querySelector('.vt-vw-bar').textContent, /격리된 그림/);
  assert.ok(h2.querySelector('.vt-vw-svg .vt-vw-zoom-bar'), 'SVG 그림에도 맞춤 / 100%');
  assert.strictEqual(h2.querySelectorAll('.vt-vw-svg img').length, 1, '그림은 여전히 <img> 하나');
});

test('이미지 — 맞춤 / 100% 전환, 100%에서 ⌘/Ctrl + 휠로 확대(10%–800%), 그냥 휠은 스크롤', async () => {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const p = await importFresh(PREVIEW_JS, e.context, new Map());
  const box = e.document.createElement('div');
  e.document.body.appendChild(box);
  const v = p.mountImageView(box, '/api/fs/raw?path=%2Fr%2Fa.png', 'a.png', { note: 'image/png · 4KB' });
  Object.defineProperty(v.img, 'naturalWidth', { value: 1000 });
  Object.defineProperty(v.img, 'clientWidth', { value: 250 });
  v.img.dispatchEvent(new e.window.Event('load'));
  const wrap = box.querySelector('.vt-vw-image');
  const [fit, actual] = wrap.querySelectorAll('.vt-vw-seg-btn');
  assert.strictEqual(wrap.dataset.zoom, 'fit', '처음은 맞춤');
  assert.strictEqual(fit.getAttribute('aria-pressed'), 'true');
  assert.match(wrap.querySelector('.vt-vw-zoom-pct').textContent, /맞춤 · 25%/);
  assert.strictEqual(wrap.querySelector('.vt-vw-note').textContent, 'image/png · 4KB');
  actual.click();
  assert.strictEqual(wrap.dataset.zoom, 'actual');
  assert.strictEqual(v.img.style.width, '1000px');
  assert.strictEqual(wrap.querySelector('.vt-vw-zoom-pct').textContent, '100%');
  const zbox = wrap.querySelector('.vt-vw-zoom-box');
  const plain = new e.window.WheelEvent('wheel', { deltaY: -100, cancelable: true });
  zbox.dispatchEvent(plain);
  assert.strictEqual(v.scale(), 1, '그냥 휠은 확대하지 않는다');
  assert.strictEqual(plain.defaultPrevented, false);
  const zoom = new e.window.WheelEvent('wheel', { deltaY: -100, ctrlKey: true, cancelable: true });
  zbox.dispatchEvent(zoom);
  assert.ok(zoom.defaultPrevented);
  assert.ok(v.scale() > 2.5 && v.scale() < 3, `e^1 ≈ 2.72배: ${v.scale()}`);
  for (let i = 0; i < 10; i += 1) zbox.dispatchEvent(new e.window.WheelEvent('wheel', { deltaY: -500, metaKey: true, cancelable: true }));
  assert.strictEqual(v.scale(), p.ZOOM_MAX);
  assert.strictEqual(v.img.style.width, '8000px');
  fit.click();
  assert.strictEqual(v.img.style.width, '', '맞춤으로 돌아가면 폭 지정을 푼다');
  // 맞춤에서 확대를 시작하면 보이는 배율(25%)에서 이어 간다.
  zbox.dispatchEvent(new e.window.WheelEvent('wheel', { deltaY: -10, ctrlKey: true, cancelable: true }));
  assert.strictEqual(wrap.dataset.zoom, 'actual');
  assert.ok(v.scale() > 0.25 && v.scale() < 0.3, `25%에서 이어 간다: ${v.scale()}`);
  assert.strictEqual(p.zoomStep(0.1, 10000), p.ZOOM_MIN);
  assert.strictEqual(p.fitScale(2000, 1000), 1, '맞춤은 원래보다 크게 늘리지 않는다');
  assert.strictEqual(p.fitScale(100, 0), null);
});

test('뷰어 — PDF는 미리 보지 않고 다운로드 하나, 그 밖 바이너리도 다운로드, 동영상·소리는 플레이어', async () => {
  const { file, host, document } = await viewerEnv({
    '/repo/a.pdf': { binary: true, size: 1234, mime: null },
    '/repo/t.pdf': { text: '%PDF-1.4\n1 0 obj<</JS(app.alert(1))>>endobj\n' },
    '/repo/a.bin': { binary: true, size: 10 },
    '/repo/a.mp4': { binary: true, size: 99, media: 'video', mime: 'video/mp4' },
    '/repo/a.m4a': { binary: true, size: 99, media: 'audio', mime: 'audio/mp4' },
  });
  await file.renderFile(host, '/repo/a.pdf');
  assert.match(host.textContent, /PDF는 미리 보지 않습니다/);
  assert.strictEqual(host.querySelectorAll('.vt-vw-dl-btn').length, 1);
  assert.strictEqual(host.querySelector('iframe, object, embed'), null);
  assert.strictEqual(host.querySelector('.vt-vw-dl-btn').getAttribute('aria-disabled'), null);

  const mk = () => { const h = document.createElement('div'); h.className = 'vt-vw-code-pane'; document.body.appendChild(h); return h; };
  // NUL이 없어 서버가 글자로 판정한 PDF도 원문으로 그리지 않는다.
  const tp = mk();
  await file.renderFile(tp, '/repo/t.pdf');
  assert.match(tp.textContent, /PDF는 미리 보지 않습니다/);
  assert.strictEqual(tp.querySelector('pre.vt-vw-plain'), null);
  const b = mk();
  await file.renderFile(b, '/repo/a.bin');
  assert.ok(b.querySelector('.vt-vw-dl-btn'));
  const v = mk();
  await file.renderFile(v, '/repo/a.mp4');
  const vu = new URL(v.querySelector('video').getAttribute('src'), 'http://localhost');
  assert.strictEqual(vu.pathname + vu.search, '/api/fs/raw?path=%2Frepo%2Fa.mp4');
  const au = mk();
  await file.renderFile(au, '/repo/a.m4a');
  assert.ok(au.querySelector('audio'));
});

test('뷰어 — 다른 호스트의 파일은 다운로드 단추가 흐리고, 눌러도 요청하지 않고 이유를 알린다', async () => {
  const { file, host, toasts, fetched } = await viewerEnv({ '/repo/a.pdf': { binary: true, size: 5 } });
  // 다른 호스트 파일은 중계 경로로 읽힌다 — fake 서버는 경로의 path만 본다.
  await file.renderFile(host, '/repo/a.pdf', { host: 'm5' });
  const btn = host.querySelector('.vt-vw-dl-btn');
  assert.strictEqual(btn.getAttribute('aria-disabled'), 'true');
  assert.match(btn.dataset.tip, /이 컴퓨터의 파일만/);
  const before = fetched.length;
  btn.click();
  await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(fetched.length, before, '요청하지 않는다');
  assert.match(toasts.at(-1)[0], /이 컴퓨터의 파일만/);
});

// --- download.js --------------------------------------------------------------------------------

async function dlEnv(respond, { relay } = {}) {
  const e = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(e.dom);
  const { window } = e;
  if (relay) window.localStorage.setItem('vt.relayHost', relay);
  window.API_BASE = '';
  window._tokenQuery = '';
  const toasts = [];
  window.showToast = (m, kind, opts) => toasts.push([m, kind, opts]);
  const calls = [];
  window.fetch = async (url, opts) => { calls.push({ url: String(url), opts }); return respond(String(url), opts); };
  const saved = [];
  window.HTMLAnchorElement.prototype.click = function click() { saved.push({ href: this.getAttribute('href'), name: this.download }); };
  window.URL.createObjectURL = () => 'blob:fake';
  window.URL.revokeObjectURL = () => {};
  const dl = await importFresh(DL_JS, e.context, new Map());
  return { ...e, dl, toasts, calls, saved };
}

function bodyOf(bytes) {
  let sent = false;
  return {
    getReader: () => ({
      read: async () => {
        if (sent) return { done: true };
        sent = true;
        return { done: false, value: bytes };
      },
    }),
  };
}

test('다운로드 — 호스트 전환 중이면 요청하지 않고 이유를 알린다(D7)', async () => {
  const { dl, toasts, calls } = await dlEnv(() => { throw new Error('should not fetch'); }, { relay: 'm5' });
  assert.strictEqual(await dl.downloadFsPath('/repo/a.txt'), 'blocked');
  assert.strictEqual(calls.length, 0);
  assert.match(toasts[0][0], /이 컴퓨터의 파일만/);
  assert.match(dl.downloadBlockedReason(null), /이 컴퓨터의 파일만/);
});

test('다운로드 — 다른 호스트 파일(터미널 링크)도 요청하지 않는다', async () => {
  const { dl, calls } = await dlEnv(() => { throw new Error('x'); });
  assert.strictEqual(dl.downloadBlockedReason(null), null);
  assert.strictEqual(await dl.downloadFsPath('/repo/a.txt', { host: 'm5' }), 'blocked');
  assert.strictEqual(calls.length, 0);
});

test('다운로드 — rev를 보내고, 받은 바이트를 파일 이름 그대로 저장시킨다', async () => {
  const bytes = new Uint8Array([1, 2, 3, 4]);
  const { dl, calls, saved, toasts } = await dlEnv(() => ({
    ok: true, status: 200, headers: { get: (k) => (k === 'content-length' ? '4' : null) }, body: bodyOf(bytes),
  }));
  assert.strictEqual(await dl.downloadFsPath('/repo/보고서 1.txt', { rev: '1:2:4:9' }), 'done');
  const q = new URL(calls[0].url, 'http://x').searchParams;
  assert.strictEqual(new URL(calls[0].url, 'http://x').pathname, '/api/fs/download');
  assert.strictEqual(q.get('path'), '/repo/보고서 1.txt');
  assert.strictEqual(q.get('rev'), '1:2:4:9');
  assert.deepStrictEqual(saved, [{ href: 'blob:fake', name: '보고서 1.txt' }]);
  assert.strictEqual(toasts.at(-1)[1], 'success');
});

test('다운로드 — 409는 「바뀌어 받지 않았다」, 413은 상한, 도중에 끊기면 알린다', async () => {
  let mode = 409;
  const { dl, toasts, saved } = await dlEnv(() => {
    if (mode === 409) return { ok: false, status: 409, headers: { get: () => null }, json: async () => ({ error: 'changed', rev: 'r2' }) };
    if (mode === 413) return { ok: false, status: 413, headers: { get: () => null }, json: async () => ({ error: 'too_large', size: 3 * 1024 * 1024, max: 1024 * 1024 }) };
    return { ok: true, status: 200, headers: { get: (k) => (k === 'content-length' ? '10' : null) }, body: bodyOf(new Uint8Array(4)) };
  });
  assert.strictEqual(await dl.downloadFsPath('/r/a.txt', { rev: 'r1' }), 'error');
  assert.match(toasts.at(-1)[0], /파일이 바뀌어 받지 않았습니다/);
  assert.strictEqual(toasts.at(-1)[1], 'warn');
  mode = 413;
  await dl.downloadFsPath('/r/a.txt');
  assert.match(toasts.at(-1)[0], /너무 커서 받을 수 없습니다 \(3\.0MB, 상한 1\.0MB\)/);
  mode = 200;
  assert.strictEqual(await dl.downloadFsPath('/r/a.txt'), 'error', '덜 받은 파일을 저장하지 않는다');
  assert.match(toasts.at(-1)[0], /받기가 끊겼습니다/);
  assert.deepStrictEqual(saved, []);
});

test('다운로드 — 64MB를 넘으면 메모리에 담지 않고 브라우저 다운로드로 넘긴다(같은 rev 주소)', async () => {
  let aborted = false;
  const { dl, saved, calls } = await dlEnv((url, opts) => {
    opts.signal.addEventListener('abort', () => { aborted = true; });
    return { ok: true, status: 200, headers: { get: (k) => (k === 'content-length' ? String(100 * 1024 * 1024) : null) }, body: bodyOf(new Uint8Array(1)) };
  });
  assert.strictEqual(await dl.downloadFsPath('/r/big.iso', { rev: 'r9' }), 'handoff');
  assert.ok(aborted, '머리만 보고 끊는다');
  assert.strictEqual(saved.length, 1);
  assert.strictEqual(saved[0].href, calls[0].url);
  assert.strictEqual(saved[0].name, 'big.iso');
});

// 끝나지 않는 몸통 — 시험이 조각을 하나씩 밀어 넣고, 신호(signal)가 끊으면 읽기가 AbortError로 끝난다.
function pendingBody(signal) {
  const waiters = [];
  const queue = [];
  let ended = false;
  let failed = null;
  const flush = () => {
    while (waiters.length && (queue.length || ended || failed)) {
      const w = waiters.shift();
      if (failed) w.reject(failed);
      else if (queue.length) w.resolve({ done: false, value: queue.shift() });
      else w.resolve({ done: true });
    }
  };
  if (signal) signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; failed = e; flush(); });
  return {
    push(bytes) { queue.push(bytes); flush(); },
    end() { ended = true; flush(); },
    body: { getReader: () => ({ read: () => new Promise((resolve, reject) => { waiters.push({ resolve, reject }); flush(); }), cancel: async () => {} }) },
  };
}

const tick = () => new Promise((r) => { setTimeout(r, 0); });

test('다운로드 — 동시에 3개까지, 넷째는 「대기 중」으로 줄을 서고 앞의 것이 끝나면 시작한다', async () => {
  const bodies = [];
  const { dl, toasts, calls, saved } = await dlEnv((url, opts) => {
    const b = pendingBody(opts.signal);
    bodies.push(b);
    return { ok: true, status: 200, headers: { get: (k) => (k === 'content-length' ? '2' : null) }, body: b.body };
  });
  const runs = ['/r/a', '/r/b', '/r/c', '/r/d'].map((p) => dl.downloadFsPath(p));
  await tick();
  assert.strictEqual(calls.length, 3, '넷째는 아직 요청하지 않는다');
  assert.deepStrictEqual({ ...dl.downloadQueueState() }, { active: 3, queued: 1 });
  const wait = toasts.find((x) => /^d 대기 중/.test(x[0]));
  assert.ok(wait, '줄 선 것은 「대기 중」 카드');
  assert.strictEqual(wait[2].actions[0].label, '취소');
  assert.match(wait[2].detail, /동시에 3개까지/);
  bodies[0].push(new Uint8Array(2)); bodies[0].end();
  assert.strictEqual(await runs[0], 'done');
  await tick();
  assert.strictEqual(calls.length, 4, '자리가 나면 줄의 다음 것을 시작한다');
  assert.strictEqual(new URL(calls[3].url, 'http://x').searchParams.get('path'), '/r/d');
  for (const i of [1, 2, 3]) { bodies[i].push(new Uint8Array(2)); bodies[i].end(); }
  assert.deepStrictEqual(await Promise.all(runs.slice(1)), ['done', 'done', 'done']);
  assert.strictEqual(saved.length, 4);
  assert.deepStrictEqual({ ...dl.downloadQueueState() }, { active: 0, queued: 0 });
});

test('다운로드 — 줄 선 것을 취소하면 요청을 보내지 않고, 받는 중인 것을 취소하면 끊고 저장하지 않는다', async () => {
  const bodies = [];
  const signals = [];
  const { dl, toasts, calls, saved } = await dlEnv((url, opts) => {
    const b = pendingBody(opts.signal);
    bodies.push(b);
    signals.push(opts.signal);
    return { ok: true, status: 200, headers: { get: (k) => (k === 'content-length' ? String(4 * 1024 * 1024) : null) }, body: b.body };
  });
  const runs = ['/r/a', '/r/b', '/r/c', '/r/d'].map((p) => dl.downloadFsPath(p));
  await tick();
  // 넷째(줄 선 것) 취소 — 요청 없이 끝난다.
  toasts.find((x) => /^d 대기 중/.test(x[0]))[2].actions[0].onClick();
  assert.strictEqual(await runs[3], 'cancelled');
  assert.match(toasts.at(-1)[0], /^d 받기를 취소했습니다/);
  // 첫째는 1MB가 넘어 처음부터 진행 카드(취소 단추)가 있다. 조각을 조금 받다가 취소한다.
  bodies[0].push(new Uint8Array(1024));
  await tick();
  const prog = toasts.find((x) => /^a 받는 중/.test(x[0]));
  assert.ok(prog && prog[2].duration === 0 && prog[2].actions[0].label === '취소', '진행 카드에 취소');
  prog[2].actions[0].onClick();
  assert.ok(signals[0].aborted, 'AbortController로 요청을 끊는다');
  assert.strictEqual(await runs[0], 'cancelled');
  assert.match(toasts.at(-1)[0], /^a 받기를 취소했습니다/);
  for (const i of [1, 2]) { bodies[i].push(new Uint8Array(4 * 1024 * 1024)); bodies[i].end(); }
  assert.deepStrictEqual(await Promise.all(runs.slice(1, 3)), ['done', 'done']);
  assert.strictEqual(calls.length, 3, '취소한 넷째는 끝내 요청하지 않았다');
  assert.strictEqual(saved.length, 2, '취소한 것은 저장하지 않는다');
});
