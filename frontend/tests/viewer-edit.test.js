'use strict';
// 뷰어 편집·저장(panels/viewer/edit.js · fs-write.js) — 파일 개편 단계 8.
// CM 자체는 jsdom에서 돌리지 않는다(viewer-file.test.js와 같은 이유). 편집기 핸들은 같은 계약의 가짜로 바꾸고,
// edit.js가 정하는 규칙을 본다: 편집 가능 판정(D7·D9·2MB), 저장 본문(쿠키만, 중계 없음, expected_rev, save_id),
// 승격 요구 → 비밀번호 → 같은 save_id로 재시도, 409 → 충돌 배너, 503 → 같은 save_id 재시도, 바깥 변경 따라가기,
// 고친 채 닫으면 보관 → 다시 열면 이어 열기, 줄 비교.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const EDIT_JS = path.join(__dirname, '../js/panels/viewer/edit.js');
const WRITE_JS = path.join(__dirname, '../js/panels/viewer/fs-write.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

function fakeHandle(text) {
  const h = {
    text, editable: false, onSave: null, changed: null, jumped: null,
    view: { dom: { isConnected: true } },
    getText() { return h.text; },
    setText(next) { const c = next !== h.text; h.text = next; if (c && h.changed) h.changed(); return c; },
    setEditable(on, o = {}) { h.editable = on; h.onSave = on ? o.onSave : null; },
    onChange(fn) { h.changed = fn; },
    focus() {},
    jumpTo(line, col) { h.jumped = [line, col]; return true; },
    type(next) { h.setText(next); },
  };
  return h;
}

async function build(server) {
  const e = createDomEnv('<!doctype html><html><body><div id="c" class="vt-vw-code-pane"></div></body></html>');
  _doms.push(e.dom);
  const { window } = e;
  window.API_BASE = '';
  window._tokenQuery = '?token=machine';   // 쓰기 요청에는 실리면 안 된다
  window.VT_TOKEN = 'machine';
  window.fitAndResize = () => {};
  const toasts = [];
  window.showToast = (m, k) => toasts.push([m, k]);
  const calls = [];
  window.fetch = async (url, opts = {}) => {
    const u = String(url);
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url: u, method: opts.method || 'GET', headers: opts.headers || {}, body, credentials: opts.credentials });
    const r = await server(u, opts.method || 'GET', body, calls.length);
    return {
      ok: r.status >= 200 && r.status < 300, status: r.status,
      headers: { get: (k) => (k.toLowerCase() === 'etag' ? r.etag || null : null) },
      json: async () => r.body,
    };
  };
  const cache = new Map();
  const edit = await importFresh(EDIT_JS, e.context, cache);
  const write = await importFresh(WRITE_JS, e.context, cache);
  return { ...e, edit, write, calls, toasts };
}

function doc(over = {}) {
  return {
    path: '/repo/a.txt', host: null, mode: 'head', preview: false, changing: false, etag: 'W/"x"',
    meta: { size: 10, rev: '1:2:10:100', encoding: 'utf-8', pinned: 'utf-8', confident: true, eof: true, eol: { lf: true, crlf: false, cr: false }, binary: false, image: false },
    ...over,
  };
}

function fileReply(text, rev) {
  return { status: 200, etag: `W/"${rev}"`, body: { path: '/repo/a.txt', size: text.length, rev, binary: false, image: false, encoding: 'utf-8', encoding_confident: true, content: text, offset: 0, eof: true, next_offset: null } };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('편집 가능 판정 — 원격 · 2MB · 섞인 줄 끝 · 모르는 인코딩 · 끝부분 · 렌더 보기 · 편집기 없음', async () => {
  const { edit, document } = await build(async () => ({ status: 500, body: {} }));
  const c = document.getElementById('c');
  c._vtEditor = fakeHandle('x');
  assert.strictEqual(edit.editability(doc(), c).ok, true);
  const cases = [
    [doc({ host: 'm5' }), /원격 호스트/],
    [doc({ meta: { ...doc().meta, size: 3 * 1024 * 1024 } }), /2 MB/],
    [doc({ meta: { ...doc().meta, eol: { lf: true, crlf: true, cr: false } } }), /줄 끝이 섞여/],
    [doc({ meta: { ...doc().meta, encoding: 'unknown', confident: false } }), /인코딩을 확신/],
    [doc({ mode: 'tail' }), /끝부분 보기/],
    [doc({ preview: true }), /원문 보기/],
    [doc({ meta: { ...doc().meta, eof: false } }), /파일 전체/],
    [doc({ changing: true }), /계속 바뀌는/],
  ];
  for (const [st, re] of cases) {
    const r = edit.editability(st, c);
    assert.strictEqual(r.ok, false);
    assert.match(r.reason, re);
  }
  c._vtEditor = null;
  assert.match(edit.editability(doc(), c).reason, /편집기를 불러오지 못해/);
});

test('저장 — 쿠키만 · 중계 없음 · expected_rev · save_id, 성공하면 저장됨', async () => {
  const { edit, document, calls } = await build(async (u, m, body) => {
    if (m === 'PUT') return { status: 200, body: { ok: true, rev: '1:3:12:200', size: 12, durable: true } };
    return { status: 500, body: {} };
  });
  const c = document.getElementById('c');
  const h = fakeHandle('one\ntwo\n');
  c._vtEditor = h;
  const st = doc();
  c._vtDoc = st;
  let synced = 0;
  assert.ok(edit.startEdit(c, st, () => { synced += 1; }));
  assert.strictEqual(h.editable, true);
  h.type('one\nTWO\n');
  assert.strictEqual(edit.isDirty(c), true);
  assert.strictEqual(await edit.saveEdit(c, st), true);
  const put = calls.find((x) => x.method === 'PUT');
  assert.strictEqual(put.url, '/api/fs/file', '토큰 쿼리·호스트 중계가 붙지 않는다');
  assert.strictEqual(put.headers.Authorization, undefined);
  assert.strictEqual(put.credentials, 'same-origin');
  assert.deepStrictEqual({ ...put.body, save_id: 'x' }, {
    path: '/repo/a.txt', content: 'one\nTWO\n', encoding: 'utf-8', eol: 'lf', expected_rev: '1:2:10:100', save_id: 'x',
  });
  assert.match(put.body.save_id, /^[A-Za-z0-9_-]{8,64}$/);
  assert.strictEqual(edit.isDirty(c), false);
  assert.strictEqual(c._vtEdit.rev, '1:3:12:200');
  assert.strictEqual(st.meta.rev, '1:3:12:200');
  assert.strictEqual(c._vtEdit.status, 'saved');
  assert.ok(synced > 0);
});

test('승격이 없으면 비밀번호를 묻고 같은 save_id로 다시 보낸다', async () => {
  let n = 0;
  const { edit, document, calls } = await build(async (u, m) => {
    if (u.startsWith('/api/auth/elevate')) return { status: 200, body: { ok: true } };
    if (m === 'PUT') {
      n += 1;
      return n === 1 ? { status: 401, body: { error: 'elevation_required' } } : { status: 200, body: { ok: true, rev: 'r2', size: 3 } };
    }
    return { status: 500, body: {} };
  });
  const c = document.getElementById('c');
  const h = fakeHandle('a\n');
  c._vtEditor = h;
  const st = doc();
  edit.startEdit(c, st, () => {});
  h.type('b\n');
  const p = edit.saveEdit(c, st);
  for (let i = 0; i < 20 && !document.querySelector('.vt-dialog-input'); i += 1) await tick();
  const input = document.querySelector('.vt-dialog-input');
  assert.ok(input, '비밀번호 창이 떠야 한다');
  assert.strictEqual(input.type, 'password');
  input.value = 'pw';
  document.querySelector('.vt-dialog-ok').click();
  assert.strictEqual(await p, true);
  const puts = calls.filter((x) => x.method === 'PUT');
  assert.strictEqual(puts.length, 2);
  assert.strictEqual(puts[0].body.save_id, puts[1].body.save_id, '재시도는 같은 save_id');
});

test('409 충돌 → 배너(비교 · 다시 불러오기 · 덮어쓰기), 덮어쓰기는 디스크의 지금 판을 expected_rev로', async () => {
  let disk = 'one\nX\n';
  const { edit, document, calls } = await build(async (u, m, body) => {
    if (m === 'PUT') {
      if (body.expected_rev === 'disk-2') return { status: 200, body: { ok: true, rev: 'r3', size: 9 } };
      return { status: 409, body: { error: 'conflict', rev: 'disk-2' } };
    }
    if (u.startsWith('/api/fs/file')) return fileReply(disk, 'disk-2');
    return { status: 500, body: {} };
  });
  const c = document.getElementById('c');
  const bar = document.createElement('div');
  bar.className = 'vt-vw-bar';
  c.appendChild(bar);
  const h = fakeHandle('one\ntwo\n');
  c._vtEditor = h;
  const st = doc();
  c._vtDoc = st;
  edit.startEdit(c, st, () => edit.syncConflict(c, st));
  h.type('one\nmine\n');
  assert.strictEqual(await edit.saveEdit(c, st), false);
  assert.strictEqual(c._vtEdit.conflict.rev, 'disk-2');
  assert.strictEqual(c._vtEdit.conflict.text, 'one\nX\n');
  const ban = c.querySelector('.vt-vw-conflict');
  assert.ok(ban);
  const labels = [...ban.querySelectorAll('button')].map((b) => b.textContent);
  assert.deepStrictEqual(labels, ['비교', '다시 불러오기', '내 것으로 덮어쓰기']);
  // 충돌 중 ⌘S는 저장하지 않는다(덮어쓰기는 배너에서만)
  const before = calls.filter((x) => x.method === 'PUT').length;
  assert.strictEqual(await edit.saveEdit(c, st), false);
  assert.strictEqual(calls.filter((x) => x.method === 'PUT').length, before);
  // 비교
  ban.querySelector('button').click();
  const cmp = c.querySelector('.vt-vw-compare');
  assert.ok(cmp);
  assert.match(cmp.textContent, /X/);
  assert.match(cmp.textContent, /mine/);
  // 덮어쓰기
  [...c.querySelectorAll('.vt-vw-conflict button')].find((b) => b.textContent === '내 것으로 덮어쓰기').click();
  for (let i = 0; i < 20 && !document.querySelector('.vt-dialog-ok'); i += 1) await tick();
  document.querySelector('.vt-dialog-ok').click();
  for (let i = 0; i < 40 && c._vtEdit.conflict; i += 1) await tick();
  const last = calls.filter((x) => x.method === 'PUT').pop();
  assert.strictEqual(last.body.expected_rev, 'disk-2');
  assert.strictEqual(c._vtEdit.conflict, null);
  assert.strictEqual(c.querySelector('.vt-vw-conflict'), null);
  disk = '';
});

test('503 · 시간 초과면 같은 save_id로 한 번 더 — 서버가 첫 결과(replayed)를 준다', async () => {
  let n = 0;
  const { edit, document, calls, toasts } = await build(async (u, m) => {
    if (m !== 'PUT') return { status: 500, body: {} };
    n += 1;
    return n === 1 ? { status: 503, body: { error: 'fs_unavailable', reason: 'slow' } } : { status: 200, body: { ok: true, rev: 'r2', size: 2, replayed: true } };
  });
  const c = document.getElementById('c');
  const h = fakeHandle('a\n');
  c._vtEditor = h;
  const st = doc();
  edit.startEdit(c, st, () => {});
  h.type('b\n');
  assert.strictEqual(await edit.saveEdit(c, st), true);
  const puts = calls.filter((x) => x.method === 'PUT');
  assert.strictEqual(puts.length, 2);
  assert.strictEqual(puts[0].body.save_id, puts[1].body.save_id);
  assert.ok(toasts.some(([m]) => /두 번 쓰지 않고/.test(m)));
});

test('실패 계약 — 422 unencodable은 그 줄로 옮기고, 실패해도 편집 내용은 그대로', async () => {
  const { edit, document, toasts } = await build(async (u, m) => (m === 'PUT'
    ? { status: 422, body: { error: 'unencodable', line: 2, col: 3, char: 'ő' } } : { status: 500, body: {} }));
  const c = document.getElementById('c');
  const h = fakeHandle('a\n');
  c._vtEditor = h;
  const st = doc({ meta: { ...doc().meta, encoding: 'cp949' } });
  edit.startEdit(c, st, () => {});
  h.type('a\nabőc\n');
  assert.strictEqual(await edit.saveEdit(c, st), false);
  assert.deepStrictEqual(h.jumped, [2, 3]);
  assert.strictEqual(c._vtEdit.status, 'failed');
  assert.strictEqual(h.text, 'a\nabőc\n');
  assert.ok(toasts.some(([m]) => /CP949로 바꿀 수 없는 글자.*2줄 3열/.test(m)));
});

test('바깥 변경 — 저장한 판이면 무시, 고친 것이 없으면 따라가고, 있으면 충돌', async () => {
  let disk = { text: 'v2\n', rev: 'r2' };
  const { edit, document } = await build(async (u) => (u.startsWith('/api/fs/file') ? fileReply(disk.text, disk.rev) : { status: 500, body: {} }));
  const c = document.getElementById('c');
  const h = fakeHandle('v1\n');
  c._vtEditor = h;
  const st = doc({ meta: { ...doc().meta, rev: 'r1' } });
  c._vtDoc = st;
  edit.startEdit(c, st, () => {});
  assert.strictEqual(await edit.refreshEditing(c, st), 'ok');
  assert.strictEqual(h.text, 'v2\n');
  assert.strictEqual(c._vtEdit.rev, 'r2');
  assert.strictEqual(await edit.refreshEditing(c, st), 'same');
  h.type('mine\n');
  disk = { text: 'v3\n', rev: 'r3' };
  assert.strictEqual(await edit.refreshEditing(c, st), 'same');
  assert.strictEqual(h.text, 'mine\n', '고친 내용을 덮지 않는다');
  assert.strictEqual(c._vtEdit.conflict.rev, 'r3');
});

test('고친 채 칸이 사라지면 보관하고, 같은 파일을 다시 열면 편집 모드로 이어 연다', async () => {
  const { edit, document } = await build(async () => ({ status: 500, body: {} }));
  const c = document.getElementById('c');
  const h = fakeHandle('base\n');
  c._vtEditor = h;
  const st = doc();
  c._vtDoc = st;
  edit.startEdit(c, st, () => {});
  h.type('draft\n');
  edit.stash(c);
  assert.strictEqual(edit._draftCount(), 1);
  assert.strictEqual(edit.hasUnsaved(), true);
  const c2 = document.createElement('div');
  const h2 = fakeHandle('base\n');
  c2._vtEditor = h2;
  const st2 = doc();
  c2._vtDoc = st2;
  assert.strictEqual(edit.restore(c2, st2, () => {}), true);
  assert.strictEqual(h2.text, 'draft\n');
  assert.strictEqual(h2.editable, true);
  assert.strictEqual(edit.isDirty(c2), true);
  assert.strictEqual(edit._draftCount(), 0);
});

test('원격 호스트 — 쓰기 요청을 보내지 않고 이유를 알린다(D7)', async () => {
  const { write, calls, toasts } = await build(async () => ({ status: 200, body: { ok: true } }));
  assert.strictEqual(await write.createFileDialog('/repo', 'm5'), null);
  assert.strictEqual(await write.renameDialog('/repo/a.txt', 'm5'), null);
  assert.strictEqual(await write.trashDialog('/repo/a.txt', 'm5'), false);
  assert.strictEqual(calls.length, 0);
  assert.ok(toasts.every(([m]) => /원격 호스트 파일은 읽기만/.test(m)));
});

test('휴지통 확인 창은 「휴지통으로 이동」이고 기본 초점은 취소다', async () => {
  const { write, document, calls } = await build(async () => ({ status: 200, body: { ok: true } }));
  const p = write.trashDialog('/repo/a.txt', null, 'r1');
  for (let i = 0; i < 20 && !document.querySelector('.vt-dialog-ok'); i += 1) await tick();
  const ok = document.querySelector('.vt-dialog-ok');
  assert.strictEqual(ok.textContent, '휴지통으로 이동');
  assert.ok(document.activeElement.classList.contains('vt-dialog-cancel'));
  ok.click();
  assert.strictEqual(await p, true);
  assert.deepStrictEqual(calls[0].body, { path: '/repo/a.txt', expected_rev: 'r1' });
  assert.strictEqual(calls[0].url, '/api/fs/delete');
});

test('이름 규칙(서버와 같다)', async () => {
  const { write } = await build(async () => ({ status: 200, body: {} }));
  for (const bad of ['', ' ', 'a/b', 'a\\b', '.', '..', 'a\nb']) assert.ok(write.nameProblem(bad), bad);
  for (const good of ['a.txt', '.gitignore', '한글.md']) assert.strictEqual(write.nameProblem(good), null);
});

test('줄 비교 — 공통 앞뒤를 빼고 바뀐 곳만, 크면 null', async () => {
  const { edit } = await build(async () => ({ status: 200, body: {} }));
  const rows = edit.lineDiff('a\nb\nc\nd', 'a\nB\nc\nd\ne');
  assert.strictEqual(JSON.stringify(rows.map((r) => `${r.kind}:${r.text}`)), JSON.stringify(['same:a', 'del:b', 'add:B', 'same:c', 'same:d', 'add:e']));
  const h = edit.diffHunks(edit.lineDiff('1\n2\n3\n4\n5\n6\n7\n8\n9\n10', '1\n2\n3\n4\n5\n6\n7\n8\n9\nX'), 1);
  assert.strictEqual(h[0].kind, 'gap');
  assert.strictEqual(edit.lineDiff('x\n'.repeat(2000), 'y\n'.repeat(2000)), null);
});
