'use strict';
// 폰 파일 편집(layout/mobile-edit.js, 시안 files.html M-F5) — 편집 중엔 아래 탭을 숨기고 키보드 위에 보조 키 줄
// (Tab ← → ↑ ↓ Esc 저장)을 띄운다. 끝내거나 칸이 사라지거나 폭이 넓어지면 원래대로.
// 편집 시작·끝 알림은 panels/viewer/edit.js의 'vt:viewer-edit'(+ 칸의 vt-vw-editing)이다 — 그 계약도 여기서 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MOBILE_EDIT_JS = path.join(__dirname, '../js/layout/mobile-edit.js');
const EDIT_JS = path.join(__dirname, '../js/panels/viewer/edit.js');
const CSS = fs.readFileSync(path.join(__dirname, '../../styles/screens/98-mobile.css'), 'utf8');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const tick = () => new Promise((r) => setTimeout(r, 0));

const PAGE = `<!doctype html><html><body>
  <nav id="vt-mnav-bottom"></nav>
  <div class="vt-viewer-backdrop vt-file-compact" id="viewer">
    <div class="vt-viewer-card">
      <div class="vt-vw-bar"><span class="vt-vw-edit-tools"><button class="vt-vw-save" type="button">저장</button></span></div>
      <div class="vt-vw-code-pane" id="pane"><div class="cm-editor"><div class="cm-content" contenteditable="true" tabindex="0"></div></div></div>
    </div>
  </div>
</body></html>`;

function _vv(window, { height, offsetTop = 0 }) {
  const listeners = new Map();
  const vv = {
    height, offsetTop,
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    _emit(type) { for (const fn of listeners.get(type) || []) fn(); },
  };
  window.visualViewport = vv;
  return vv;
}

async function build({ compact = true, vv = null } = {}) {
  const env = createDomEnv(PAGE);
  _doms.push(env.dom);
  const { window } = env;
  const mq = { compact, listeners: new Set() };
  window.matchMedia = (query) => ({
    get matches() { return query.includes('max-width') ? mq.compact : false; },
    media: query,
    addEventListener(_, fn) { mq.listeners.add(fn); }, removeEventListener() {}, addListener() {}, removeListener() {},
  });
  // jsdom은 배치를 안 하므로 getClientRects가 늘 비어 있다 — 붙어 있고 display:none 조상이 없으면 보인다고 본다.
  window.Element.prototype.getClientRects = function () {
    for (let n = this; n; n = n.parentElement) if (n.hidden || n.style?.display === 'none') return [];
    return this.isConnected ? [{}] : [];
  };
  Object.defineProperty(window, 'innerHeight', { value: 844, configurable: true });
  const fakeVv = vv ? _vv(window, vv) : null;
  const cache = new Map();
  const mod = await importFresh(MOBILE_EDIT_JS, env.context, cache);
  const doc = window.document;
  const pane = doc.getElementById('pane');
  const setCompact = (on) => { mq.compact = on; for (const fn of mq.listeners) fn(); };
  const startEditing = () => { pane.classList.add('vt-vw-editing'); doc.dispatchEvent(new window.CustomEvent('vt:viewer-edit', { detail: { on: true } })); };
  const stopEditing = () => { pane.classList.remove('vt-vw-editing'); doc.dispatchEvent(new window.CustomEvent('vt:viewer-edit', { detail: { on: false } })); };
  const row = () => doc.getElementById('vt-edit-keys');
  const on = () => doc.documentElement.classList.contains('vt-mobile-editing');
  return { ...env, mod, doc, pane, fakeVv, setCompact, startEditing, stopEditing, row, on };
}

test('편집을 시작하면 켜진다 — 클래스 · 키 줄(Tab ← → ↑ ↓ Esc 저장), 끝내면 걷힌다', async () => {
  const { startEditing, stopEditing, row, on, mod } = await build();
  assert.strictEqual(row(), null, '편집 전에는 줄을 만들지도 않는다');
  startEditing();
  assert.ok(on());
  assert.strictEqual(mod.isMobileEditing(), true);
  assert.strictEqual(row().hidden, false);
  assert.strictEqual(row().getAttribute('role'), 'toolbar');
  assert.deepStrictEqual([...row().querySelectorAll('button')].map((b) => b.textContent), ['Tab', '←', '→', '↑', '↓', 'Esc', '저장']);
  assert.strictEqual(row().querySelector('[data-key="ArrowLeft"]').getAttribute('aria-label'), '왼쪽 화살표');
  for (const b of row().querySelectorAll('button')) assert.ok(b.classList.contains('kb'), '키바 키 모양을 쓴다');
  stopEditing();
  assert.ok(!on());
  assert.strictEqual(row().hidden, true);
});

test('넓은 화면(폭 720 이상)에서는 켜지지 않고, 편집 중 폭이 넓어지면 꺼졌다가 좁아지면 다시 켜진다', async () => {
  const wide = await build({ compact: false });
  wide.startEditing();
  assert.ok(!wide.on());
  assert.strictEqual(wide.row(), null);
  const { startEditing, setCompact, on } = await build();
  startEditing();
  setCompact(false);
  assert.ok(!on());
  setCompact(true);
  assert.ok(on());
});

test('키 — 편집기 본문(.cm-content)에 keydown을 보낸다(편집기 자신의 키맵이 처리), 누름은 초점을 뺏지 않는다', async () => {
  const { doc, window, startEditing, row } = await build();
  startEditing();
  const content = doc.querySelector('.cm-content');
  const got = [];
  content.addEventListener('keydown', (e) => got.push([e.key, e.keyCode]));
  for (const k of ['Tab', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Escape']) row().querySelector(`[data-key="${k}"]`).click();
  assert.deepStrictEqual(got, [['Tab', 9], ['ArrowLeft', 37], ['ArrowRight', 39], ['ArrowUp', 38], ['ArrowDown', 40], ['Escape', 27]]);
  assert.strictEqual(doc.activeElement, content, '편집기에 초점이 간다(소프트 키보드)');
  const down = new window.PointerEvent('pointerdown', { bubbles: true, cancelable: true });
  row().querySelector('[data-key="Tab"]').dispatchEvent(down);
  assert.strictEqual(down.defaultPrevented, true);
});

test('저장 — 같은 편집 화면 위 띠의 「저장」을 누른다(저장 경로는 하나), 그 버튼이 막혀 있으면 키도 막힌다', async () => {
  const { doc, startEditing, row, mod } = await build();
  let saved = 0;
  const top = doc.querySelector('.vt-vw-save');
  top.addEventListener('click', () => { saved += 1; });
  startEditing();
  const save = row().querySelector('.vt-edit-keys-save');
  save.click();
  assert.strictEqual(saved, 1);
  top.disabled = true;
  await tick();   // 위 띠를 지켜보다가 따라 막는다
  assert.strictEqual(save.disabled, true);
  assert.strictEqual(mod.pressSave(), false);
  assert.strictEqual(saved, 1);
  // 띠가 다시 그려져도(저장 중 → 저장됨) 새 버튼 상태를 따라간다
  const tools = doc.querySelector('.vt-vw-edit-tools');
  tools.replaceChildren(Object.assign(doc.createElement('button'), { className: 'vt-vw-save', type: 'button' }));
  await tick();
  assert.strictEqual(save.disabled, false);
});

test('소프트 키보드 — 덮은 높이만큼 줄을 올리고 편집 화면 바닥도 올린다(--vt-edit-overlap)', async () => {
  const { doc, startEditing, row, fakeVv } = await build({ vv: { height: 844 } });
  startEditing();
  assert.strictEqual(row().style.transform, '');
  assert.strictEqual(doc.documentElement.style.getPropertyValue('--vt-edit-overlap'), '0px');
  fakeVv.height = 500;
  fakeVv._emit('resize');
  assert.strictEqual(row().style.transform, 'translateY(-344px)');
  assert.strictEqual(doc.documentElement.style.getPropertyValue('--vt-edit-overlap'), '344px');
  fakeVv.offsetTop = 40;   // iOS — 화면이 밀려도 키보드 위에 붙는다
  fakeVv._emit('scroll');
  assert.strictEqual(row().style.transform, 'translateY(-304px)');
});

test('편집 칸이 통째로 사라지면(닫기 · 다른 파일) 알림 없이도 꺼진다', async () => {
  const { doc, startEditing, on } = await build();
  startEditing();
  assert.ok(on());
  doc.getElementById('viewer').remove();
  await tick();
  assert.ok(!on());
  assert.strictEqual(doc.documentElement.style.getPropertyValue('--vt-edit-overlap'), '');
});

test('CSS — 편집 중엔 아래 탭을 숨기고, 편집 화면 바닥을 줄+키보드 위로, 대화상자가 뜨면 줄을 걷는다', () => {
  assert.match(CSS, /html\.vt-mobile-editing #vt-mnav-bottom \{ display:none; \}/);
  assert.match(CSS, /html\.vt-mobile-editing \.vt-viewer-backdrop\.vt-file-compact \{\s*bottom:calc\(var\(--vt-edit-keys-h, 44px\) \+ var\(--vt-edit-overlap, 0px\)\);/);
  assert.match(CSS, /html\.vt-mobile-editing #vt-edit-keys:not\(\[hidden\]\) \{ display:flex; \}/);
  assert.match(CSS, /html\.vt-mobile-editing body:has\(\.vt-viewer-backdrop\.vt-dialog\) #vt-edit-keys \{ display:none; \}/);
  assert.match(CSS, /\n#vt-edit-keys \{ display:none; \}/, '넓은 화면에서는 숨는다');
});

// --- edit.js의 알림 계약 -------------------------------------------------------------------
function fakeHandle(text) {
  const h = {
    text, editable: false, changed: null, view: { dom: { isConnected: true } },
    getText() { return h.text; },
    setText(next) { const c = next !== h.text; h.text = next; if (c && h.changed) h.changed(); return c; },
    setEditable(on) { h.editable = on; },
    onChange(fn) { h.changed = fn; },
    focus() {},
  };
  return h;
}

test('edit.js — startEdit/stopEdit가 칸에 vt-vw-editing을 달고 떼며 vt:viewer-edit를 쏜다', async () => {
  const env = createDomEnv('<!doctype html><html><body><div id="c" class="vt-vw-code-pane"></div></body></html>');
  _doms.push(env.dom);
  env.window.showToast = () => {};
  const edit = await importFresh(EDIT_JS, env.context, new Map());
  const c = env.document.getElementById('c');
  c._vtEditor = fakeHandle('a\n');
  const st = {
    path: '/repo/a.txt', host: null, mode: 'head', preview: false, changing: false,
    meta: { size: 2, rev: '1', encoding: 'utf-8', pinned: 'utf-8', confident: true, eof: true, eol: { lf: true, crlf: false, cr: false }, binary: false, image: false },
  };
  const seen = [];
  env.document.addEventListener('vt:viewer-edit', (e) => seen.push([e.detail.on, e.detail.container === c]));
  assert.ok(edit.startEdit(c, st, () => {}));
  assert.ok(c.classList.contains('vt-vw-editing'));
  assert.strictEqual(await edit.stopEdit(c), true);
  assert.ok(!c.classList.contains('vt-vw-editing'));
  assert.deepStrictEqual(seen, [[true, true], [false, true]]);
});
