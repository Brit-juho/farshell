// 720px 이상에서 #topbar의 남은 버튼(신호등·키바·캡션)이 제목 막대로 옮겨 앉고, compact에서는 원래 순서로 돌아온다
// (layout/menubar-slots.js). 진짜 index.html로 본다 — 자리 id(#vt-mb-lead/#vt-mb-tools)가 제목 막대 마크업에 있어야 한다.
// ⌘K 버튼은 옮기지 않는다 — 제목 막대 가운데 팔레트 입력(TitleBar.tsx)이 같은 일을 한다(리디자인 2단계).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// matchMedia 대역 — 바꿀 수 있는 하나짜리 리스트.
function fakeMatchMedia(window, wide) {
  const listeners = new Set();
  const mq = {
    matches: wide,
    addEventListener: (_, fn) => listeners.add(fn),
    removeEventListener: (_, fn) => listeners.delete(fn),
  };
  window.matchMedia = () => mq;
  return (next) => { mq.matches = next; for (const fn of listeners) fn({ matches: next }); };
}

async function setup(wide) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const set = fakeMatchMedia(env.window, wide);
  await importFresh(J('layout/menubar-slots.js'), env.context, new Map());
  return { doc: env.document, set };
}

const parentId = (doc, sel) => doc.querySelector(sel).parentElement.id;

test('넓은 화면: 신호등은 왼쪽 자리, 키바·캡션은 오른쪽 도구 자리로 옮겨 앉고 ⌘K는 #topbar에 남는다', async () => {
  const { doc } = await setup(true);
  assert.strictEqual(parentId(doc, '.traffic'), 'vt-mb-lead');
  for (const sel of ['#keybar-open-btn', '.caption']) {
    assert.strictEqual(parentId(doc, sel), 'vt-mb-tools', sel);
  }
  assert.strictEqual(parentId(doc, '#palette-toggle'), 'topbar', '팔레트 입력이 대신한다');
  assert.strictEqual(doc.getElementById('vt-wtabs'), null, '뷰 탭 줄은 없다');
});

test('두 자리는 제목 막대 안에 있고, 상태 막대(#vt-hud)는 비어 있는 별도 막대다', async () => {
  const { doc } = await setup(true);
  for (const id of ['vt-mb-lead', 'vt-mb-tools']) {
    assert.ok(doc.getElementById(id).closest('#vt-titlebar'), id);
  }
  const hud = doc.getElementById('vt-hud');
  assert.ok(hud && !hud.closest('#vt-titlebar'), '상태 막대는 제목 막대 밖');
  assert.strictEqual(hud.children.length, 0, '내용은 Hud.tsx·toast.js가 채운다');
  assert.strictEqual(doc.getElementById('vt-workspace-chip-slot'), null, '옛 워크스페이스 칩 자리는 제목 막대로 합쳐졌다');
});

test('복제가 아니라 이동이다 — id가 하나뿐이다', async () => {
  const { doc } = await setup(true);
  assert.strictEqual(doc.querySelectorAll('#palette-toggle').length, 1);
  assert.strictEqual(doc.querySelectorAll('#keybar-open-btn').length, 1);
});

test('compact: 옮기지 않고 #topbar에 원래 순서(신호등 … 키바 · 스니펫 · ⌘K · 캡션)로 남는다', async () => {
  const { doc } = await setup(false);
  const ids = [...doc.getElementById('topbar').children].map((e) => e.id || e.className.split(' ')[0]);
  const at = (k) => ids.indexOf(k);
  assert.ok(at('traffic') === 0, ids.join());
  assert.ok(at('keybar-open-btn') < at('snippets-open-btn') && at('snippets-open-btn') < at('palette-toggle'), ids.join());
  assert.strictEqual(at('caption'), ids.length - 1, ids.join());
});

test('창 폭이 넘나들면 따라 움직이고, 돌아오면 원래 순서로 복원한다', async () => {
  const { doc, set } = await setup(false);
  const before = [...doc.getElementById('topbar').children].map((e) => e.id || e.className.split(' ')[0]).join();
  set(true);
  assert.strictEqual(parentId(doc, '#keybar-open-btn'), 'vt-mb-tools');
  set(false);
  const after = [...doc.getElementById('topbar').children].map((e) => e.id || e.className.split(' ')[0]).join();
  assert.strictEqual(after, before, '복원 순서가 처음과 같다');
});
