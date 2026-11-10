// 설정 › 정보 및 진단 「캐시 지우기」 — 공용 빌더(row/group)로 그려지고, 기존 app.clear-cache 액션을 부르며,
// 폰 44px / 데스크톱 28px 대상 크기를 CSS 가 지키는지, 검색 색인에 한·영 키워드가 있는지 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const ROOT = path.join(__dirname, '..', '..');
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const js = (p) => path.join(__dirname, '../js', p);

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = () => new Promise((r) => setImmediate(r));

async function build() {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = ''; window._tokenQuery = ''; window.fitAndResize = () => {};
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  await importFresh(js('ui/toast.js'), env.context, cache);
  await importFresh(js('core/settings.js'), env.context, cache);
  await importFresh(js('core/keymap.js'), env.context, cache);
  const dom = await importFresh(js('core/dom.js'), env.context, cache);
  const P = await importFresh(js('panels/settings.js'), env.context, cache);
  const D = await importFresh(js('panels/settings/index-data.js'), env.context, cache);
  return { document: window.document, P, dom, D };
}

async function openAbout(ctx) {
  ctx.P.showSettings();
  Array.from(ctx.document.querySelectorAll('.vt-set-navitem')).find((b) => b.textContent === '정보 및 진단').click();
  await flush(); await flush();
}

test('앱 파일 구역: 공용 group/row 로 그려지고 단추가 app.clear-cache 액션을 부른다', async () => {
  const ctx = await build();
  let calls = 0;
  ctx.dom.registerAction('app.clear-cache', () => { calls++; });
  await openAbout(ctx);
  const sec = Array.from(ctx.document.querySelectorAll('.vt-set-group'))
    .find((g) => g.querySelector('.vt-set-group-title').textContent === '앱 파일');
  assert.ok(sec, '「앱 파일」 구역이 없다');
  const row = sec.querySelector(':scope > .vt-set-row');
  assert.ok(row, '공용 row() 빌더의 .vt-set-row 여야 한다');
  assert.match(row.querySelector('.vt-set-label').textContent, /설정과 로그인은 그대로입니다\.$/);
  const btn = row.querySelector(':scope > button.vt-btn.sm');
  assert.ok(btn);
  assert.strictEqual(btn.textContent, '캐시 지우기');
  btn.click();
  assert.strictEqual(calls, 1);
});

test('앱 파일 단추: 액션이 등록되지 않았어도 던지지 않는다', async () => {
  const ctx = await build();
  await openAbout(ctx);
  ctx.document.querySelector('.vt-set-clearcache').click();
});

test('앱 파일 단추 크기: 폰은 44px, 데스크톱 설정 .vt-btn.sm 은 28px (CSS 원문)', () => {
  const mobile = fs.readFileSync(path.join(ROOT, 'styles/screens/98-mobile.css'), 'utf8');
  assert.match(mobile, /\.vt-set-clearcache\s*\{[^}]*--btn-h:\s*44px[^}]*height:\s*44px/);
  const all = fs.readdirSync(path.join(ROOT, 'styles'), { recursive: true })
    .filter((f) => String(f).endsWith('.css'))
    .map((f) => fs.readFileSync(path.join(ROOT, 'styles', String(f)), 'utf8')).join('\n');
  assert.match(all, /\.vt-settings \.vt-set-content \.vt-btn\.sm\s*\{[^}]*--btn-h:\s*28px/);
});

test('검색 색인: 캐시 지우기는 정보 및 진단 항목이고 한·영 키워드가 있다', async () => {
  const { D } = await build();
  const item = D.SEARCH_ITEMS.find((s) => s.label === '캐시 지우기');
  assert.ok(item);
  assert.strictEqual(item.sectionId, 'about');
  const alias = D.SEARCH_ALIASES['캐시 지우기'];
  for (const w of ['캐시', '앱 파일', '새로고침', '오래됨', 'cache', 'update', 'clear']) assert.ok(alias.includes(w), w);
});
