// 파일 개편 단계 5 — 폰 아래 탭 「첨부」 → 「파일」(사용자 선택 1 「나」).
// 탭을 누르면 마지막에 본 쪽(ui.mobile.filesSegment, 기본 트리)을 열고, 탭 화면으로 열린 트리·첨부 패널 머리 아래에
// 「파일 | 첨부」 전환을 단다. 올리는 중 배지(#vt-mnav-badge-files)는 탭에 남고 전환의 「첨부」 쪽에도 같은 수가 붙는다.
//   node --experimental-vm-modules --test frontend/tests/mobile-nav-files.test.js
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = path.join(__dirname, '../js');
const INDEX = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build() {
  // 하단 내비 마크업만 index.html에서 그대로 가져온다(이 파일이 고친 그 마크업이다).
  const nav = INDEX.match(/<nav id="vt-mnav-bottom"[\s\S]*?<\/nav>/)[0];
  const env = createDomEnv(`<!doctype html><html><body>${nav}<div id="vt-fleet-slot" hidden></div></body></html>`);
  _doms.push(env.dom);
  // 폭 판정은 데스크톱으로 둔다 — 세션 탭 청크(Fleet.tsx)를 받지 않게. 전환 장착은 panel-open의 mobileRoute로 정해진다.
  env.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  env.window.fitAndResize = () => {};
  const cache = new Map();
  const dom = await importFresh(path.join(JS, 'core/dom.js'), env.context, cache);
  const settings = await importFresh(path.join(JS, 'core/settings.js'), env.context, cache);
  const nav_ = await importFresh(path.join(JS, 'layout/mobile-nav.js'), env.context, cache);
  const opened = [];
  dom.registerAction('dir.show', () => opened.push('dir.show'));
  dom.registerAction('files.show', () => opened.push('files.show'));
  const closed = [];
  env.window.closePanel = (id) => { closed.push(id); env.document.getElementById(id)?.remove(); };
  return { ...env, dom, settings, nav: nav_, opened, closed };
}

function fakePanel(document, id) {
  const el = document.createElement('div');
  el.id = id;
  el.innerHTML = '<div class="vt-viewer-card"><div class="vt-viewer-head"><b>x</b></div><div class="vt-vw-body"></div></div>';
  document.body.appendChild(el);
  return el;
}

test('index.html — 넷째 탭은 「파일」(폴더 아이콘, mfiles.show)이고 올리는 중 배지를 그대로 갖는다', () => {
  const nav = INDEX.match(/<nav id="vt-mnav-bottom"[\s\S]*?<\/nav>/)[0];
  const btn = nav.match(/<button[^>]*data-mnav="files"[\s\S]*?<\/button>/)[0];
  assert.match(btn, /data-action="mfiles\.show"/);
  assert.match(btn, /aria-label="파일"/);
  assert.match(btn, /data-icon="folder"/);
  assert.match(btn, /<span class="vt-mnav-label" data-i18n>파일<\/span>/);
  assert.match(btn, /id="vt-mnav-badge-files"/);
  assert.doesNotMatch(nav, />첨부</, '아래 탭에 「첨부」 칸은 더 없다');
  const order = Array.from(nav.matchAll(/data-mnav="([a-z]+)"/g), (m) => m[1]);
  assert.deepStrictEqual(order, ['fleet', 'terminal', 'changes', 'files', 'more']);
});

test('「파일」 탭은 마지막에 본 쪽을 연다 — 기본은 트리, 첨부로 바꿨으면 첨부', async () => {
  const { dom, settings, opened } = await build();
  assert.strictEqual(settings.SCHEMA['ui.mobile.filesSegment'].scope, 'device');
  assert.strictEqual(settings.SCHEMA['ui.mobile.filesSegment'].def, 'tree');
  dom.getAction('mfiles.show')();
  settings.set('ui.mobile.filesSegment', 'attach');
  dom.getAction('mfiles.show')();
  assert.deepStrictEqual(opened, ['dir.show', 'files.show']);
});

test('탭 화면으로 열린 트리 패널에 「파일 | 첨부」 전환이 머리 바로 아래 붙고, 전환하면 기억하고 다른 쪽을 연다', async () => {
  const { document, settings, nav, opened, closed } = await build();
  const el = fakePanel(document, 'vt-dir');
  document.dispatchEvent(new document.defaultView.CustomEvent('vt:panel-open', { detail: { id: 'vt-dir', mobileRoute: true } }));
  const bar = el.querySelector('.vt-viewer-card > .vt-mfiles-seg');
  assert.ok(bar, '전환이 붙는다');
  assert.strictEqual(bar.previousElementSibling.className, 'vt-viewer-head');
  assert.strictEqual(bar.getAttribute('role'), 'tablist');
  const tabs = Array.from(bar.querySelectorAll('[role="tab"]'));
  assert.deepStrictEqual(tabs.map((b) => b.textContent.trim()), ['파일', '첨부']);
  assert.deepStrictEqual(tabs.map((b) => b.getAttribute('aria-selected')), ['true', 'false']);
  tabs[1].click();
  assert.strictEqual(settings.get('ui.mobile.filesSegment'), 'attach');
  assert.deepStrictEqual(closed, ['vt-dir']);
  assert.deepStrictEqual(opened, ['files.show']);
  // 데스크톱(사이드바·칸)으로 열린 패널에는 달지 않는다.
  const desk = fakePanel(document, 'vt-files');
  document.dispatchEvent(new document.defaultView.CustomEvent('vt:panel-open', { detail: { id: 'vt-files', mobileRoute: false } }));
  assert.strictEqual(desk.querySelector('.vt-mfiles-seg'), null);
  // 첨부 쪽에서는 「첨부」가 선택돼 있다.
  const seg = nav.mountFilesSegment(desk);
  assert.deepStrictEqual(Array.from(seg.querySelectorAll('[role="tab"]'), (b) => b.getAttribute('aria-selected')), ['false', 'true']);
});

test('올리는 중 배지 — 탭 배지(#vt-mnav-badge-files)와 전환의 「첨부」 쪽에 같은 수', async () => {
  const { document, window } = await build();
  const el = fakePanel(document, 'vt-dir');
  document.dispatchEvent(new window.CustomEvent('vt:panel-open', { detail: { id: 'vt-dir', mobileRoute: true } }));
  window.vtUploadJobs = () => [{ phase: 'uploading' }, { phase: 'queued' }, { phase: 'done' }];
  document.dispatchEvent(new window.CustomEvent('vt:upload-progress'));
  const tabBadge = document.getElementById('vt-mnav-badge-files');
  assert.strictEqual(tabBadge.hidden, false);
  assert.strictEqual(tabBadge.textContent, '2');
  const segBadge = el.querySelector('.vt-mfiles-seg-badge');
  assert.strictEqual(segBadge.hidden, false);
  assert.strictEqual(segBadge.textContent, '2');
  window.vtUploadJobs = () => [];
  document.dispatchEvent(new window.CustomEvent('vt:upload-progress'));
  assert.strictEqual(tabBadge.hidden, true);
  assert.strictEqual(segBadge.hidden, true);
});
