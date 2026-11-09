// 파일 개편 단계 5 — 사이드바 「파일」 트리(frontend/js/files/).
// 1부: tree-model.ts(평탄화·캐시·경로 키 비교·필터·가상 스크롤 창·키보드·글자 이동).
// 2부: tree-store.ts(받기 규칙 — 처음 depth=2, 펼치기 depth=1, 더 불러오기 after=, `tree` 감시는 바뀐 폴더만).
// 3부: FileTree.tsx를 jsdom에 마운트 — 가상 행 수, aria-tree, 키보드, 행 메뉴, 필터, 행 높이.
//   node --experimental-vm-modules --test frontend/tests/files-tree.test.js
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

// 번들은 jsdom vm 문맥에서 돈다 — 그쪽 배열·객체는 프로토타입이 달라 deepStrictEqual이 늘 실패하므로 JSON으로 옮겨 비교한다.
const plain = (x) => (x === undefined ? x : JSON.parse(JSON.stringify(x)));
const deq = (a, b, msg) => assert.deepStrictEqual(plain(a), plain(b), msg);

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });
async function fresh() {
  const env = await loadSolidBuild();
  _doms.push(env);
  return env;
}

const H = 'local';

function fixtureDirs(m, { files = 3, subs = 2 } = {}) {
  // /r ─ a/ (펼침) ─ a/x.txt …, b/ (접힘), README.md
  const dirs = new Map();
  const set = (p, entries, extra = {}) => dirs.set(m.keyOf(H, p), { ...m.emptyDir(), entries, total: entries.length, pages: 1, ...extra });
  const top = [];
  for (let i = 0; i < subs; i += 1) top.push({ name: String.fromCharCode(97 + i), dir: true, size: 0 });
  top.push({ name: 'README.md', dir: false, size: 10 });
  set('/r', top);
  set('/r/a', Array.from({ length: files }, (_, i) => ({ name: `x${i}.txt`, dir: false, size: i })));
  set('/r/b', [{ name: 'deep', dir: true, size: 0 }]);
  return dirs;
}

const flat = (m, dirs, expanded, extra = {}) => m.flatten({
  host: H, root: '/r', dirs, expanded: new Set(expanded.map((p) => m.keyOf(H, p))),
  changed: new Set(), changedDirs: new Set(), ...extra,
});

// ── 1부: tree-model ─────────────────────────────────────────────────────

test('경로 도우미 — joinPath · parentOf · relToAbs는 위로 벗어나는 상대경로를 버린다', async () => {
  const m = await fresh();
  assert.strictEqual(m.joinPath('/', 'a'), '/a');
  assert.strictEqual(m.joinPath('/r/', 'a'), '/r/a');
  assert.strictEqual(m.parentOf('/r/a/b'), '/r/a');
  assert.strictEqual(m.parentOf('/r'), '/');
  assert.ok(m.isUnder('/r/a', '/r') && !m.isUnder('/ra', '/r') && !m.isUnder('/r', '/r'));
  assert.strictEqual(m.relToAbs('/r', ''), '/r');
  assert.strictEqual(m.relToAbs('/r', 'a/b'), '/r/a/b');
  for (const bad of ['..', 'a/../b', '/a', 'a//b', './a']) assert.strictEqual(m.relToAbs('/r', bad), null, bad);
});

test('평탄화 — 펼친 폴더만 내려가고 깊이·위치·크기가 붙는다', async () => {
  const m = await fresh();
  const { rows } = flat(m, fixtureDirs(m), ['/r/a']);
  deq(rows.map((r) => [r.name, r.depth, r.kind]), [
    ['a', 0, 'dir'], ['x0.txt', 1, 'file'], ['x1.txt', 1, 'file'], ['x2.txt', 1, 'file'],
    ['b', 0, 'dir'], ['README.md', 0, 'file'],
  ]);
  assert.strictEqual(rows[0].open, true);
  assert.strictEqual(rows[4].open, false);
  deq([rows[1].pos, rows[1].setSize], [1, 3]);
  assert.strictEqual(rows[0].key, m.keyOf(H, '/r/a'));
});

test('안내 행 — 불러오는 중 · 빈 폴더 · 오류 · 더 불러오기 · 스캔 상한', async () => {
  const m = await fresh();
  const dirs = fixtureDirs(m);
  dirs.set(m.keyOf(H, '/r/a'), { ...m.emptyDir(), loading: true });
  dirs.set(m.keyOf(H, '/r/b'), { ...m.emptyDir() });
  let { rows } = flat(m, dirs, ['/r/a', '/r/b']);
  deq(rows.map((r) => r.kind), ['dir', 'loading', 'dir', 'empty', 'file']);
  dirs.set(m.keyOf(H, '/r/a'), { ...m.emptyDir(), error: '권한 없음' });
  dirs.set(m.keyOf(H, '/r/b'), { ...m.emptyDir(), entries: [{ name: 'q', dir: false, size: 1 }], total: 2140, next: 'CUR', pages: 1 });
  ({ rows } = flat(m, dirs, ['/r/a', '/r/b']));
  deq(rows.map((r) => r.kind), ['dir', 'error', 'dir', 'file', 'more', 'file']);
  const more = rows[4];
  deq([more.loaded, more.total, more.path], [1, 2140, '/r/b']);
  assert.strictEqual(rows[3].setSize, 2140, 'aria-setsize는 서버가 센 전체 수');
  dirs.set(m.keyOf(H, '/r/b'), { ...m.emptyDir(), entries: [{ name: 'q', dir: false, size: 1 }], total: 50000, capped: true, pages: 1 });
  ({ rows } = flat(m, dirs, ['/r/b']));
  assert.ok(rows.some((r) => r.kind === 'capped'));
});

test('조각 캐시 — 결과는 캐시 없는 평탄화와 같고, 폴더 하나가 바뀌면 나머지 행 객체는 그대로다', async () => {
  const m = await fresh();
  const dirs = fixtureDirs(m, { files: 4, subs: 3 });
  dirs.set(m.keyOf(H, '/r/c'), { ...m.emptyDir(), entries: [{ name: 'z.md', dir: false, size: 5 }], total: 1, pages: 1 });
  const cache = new m.FlattenCache();
  let ever = 0;
  const run = (exp) => flat(m, dirs, exp, { cache, marks: 0, expandVer: () => ever }).rows;
  const shape = (rows) => rows.map((r) => [r.key, r.kind, r.depth, r.open, r.size, r.pos, r.setSize, r.changed].join('|'));
  const a = run(['/r/a', '/r/c']);
  deq(shape(a), shape(flat(m, dirs, ['/r/a', '/r/c']).rows));
  // /r/c 목록만 새 객체(다시 받음) — /r/a 행들은 같은 객체여야 한다.
  dirs.set(m.keyOf(H, '/r/c'), { ...m.emptyDir(), entries: [{ name: 'z.md', dir: false, size: 6 }], total: 1, pages: 1 });
  const b = run(['/r/a', '/r/c']);
  deq(shape(b), shape(flat(m, dirs, ['/r/a', '/r/c']).rows));
  const byKey = (rows, p) => rows.find((r) => r.key === m.keyOf(H, p));
  assert.strictEqual(byKey(b, '/r/a/x2.txt'), byKey(a, '/r/a/x2.txt'));
  assert.strictEqual(byKey(b, '/r/a'), byKey(a, '/r/a'));
  assert.notStrictEqual(byKey(b, '/r/c/z.md'), byKey(a, '/r/c/z.md'));
  // 펼침이 바뀌면(expandVer 증가) 항목을 다시 훑어 반영한다.
  ever += 1;
  const c = run(['/r/a']);
  deq(shape(c), shape(flat(m, dirs, ['/r/a']).rows));
  assert.strictEqual(byKey(c, '/r/a/x0.txt'), byKey(a, '/r/a/x0.txt'));
});

test('경로 키 비교(reuseRows) — 키와 내용이 같으면 이전 객체, 다르면 새 객체', async () => {
  const m = await fresh();
  const dirs = fixtureDirs(m);
  const r1 = m.reuseRows(new Map(), flat(m, dirs, ['/r/a']).rows);
  assert.strictEqual(r1.changed, r1.rows.length);
  dirs.set(m.keyOf(H, '/r/a'), { ...dirs.get(m.keyOf(H, '/r/a')), entries: [
    { name: 'x0.txt', dir: false, size: 0 }, { name: 'x1.txt', dir: false, size: 99 }, { name: 'x2.txt', dir: false, size: 2 }] });
  const r2 = m.reuseRows(r1.byKey, flat(m, dirs, ['/r/a']).rows);
  assert.strictEqual(r2.changed, 1, '크기가 바뀐 x1.txt 하나만');
  assert.strictEqual(r2.rows[1], r1.rows[1]);
  assert.notStrictEqual(r2.rows[2], r1.rows[2]);
});

test('바뀜 표시 — 파일은 점, 조상 폴더는 빈 원, 받지 않은 폴더 안의 바뀜도 원', async () => {
  const m = await fresh();
  const dirs = fixtureDirs(m);
  const { rows } = flat(m, dirs, ['/r/a'], {
    changed: new Set([m.keyOf(H, '/r/a/x1.txt')]), changedDirs: new Set([m.keyOf(H, '/r/b/deep')]),
  });
  const by = Object.fromEntries(rows.map((r) => [r.name, r.changed]));
  assert.strictEqual(by['x1.txt'], 'dot');
  assert.strictEqual(by['x0.txt'], '');
  assert.strictEqual(by.a, 'ring');
  assert.strictEqual(by.b, 'ring');
  assert.strictEqual(by['README.md'], '');
});

test('이름 필터 — 부분 일치(대소문자 무시, 강조 구간)와 glob, 받아 둔 접힌 폴더까지 본다', async () => {
  const m = await fresh();
  const dirs = fixtureDirs(m);
  dirs.set(m.keyOf(H, '/r/b'), { ...m.emptyDir(), entries: [{ name: 'Rail.tsx', dir: false, size: 1 }, { name: 'rail-zones.ts', dir: false, size: 1 }], total: 2, pages: 1 });
  let out = flat(m, dirs, [], { filter: m.compileFilter('RAIL') });
  deq(out.rows.map((r) => r.name), ['b', 'Rail.tsx', 'rail-zones.ts']);
  assert.strictEqual(out.matches, 2);
  assert.strictEqual(out.rows[0].open, true, '조상은 펼친 것처럼 그린다');
  deq(out.rows[1].match, [0, 4]);
  out = flat(m, dirs, [], { filter: m.compileFilter('*.tsx') });
  deq(out.rows.map((r) => r.name), ['b', 'Rail.tsx']);
  out = flat(m, dirs, [], { filter: m.compileFilter('x?.txt') });
  deq(out.rows.map((r) => r.name), ['a', 'x0.txt', 'x1.txt', 'x2.txt']);
  // 정규식 문자는 글자 그대로다.
  assert.strictEqual(m.compileFilter('a.b')('axb'), null);
  deq(m.compileFilter('(x')('a(x'), [1, 3]);
  assert.strictEqual(m.compileFilter('  '), null);
  out = flat(m, dirs, [], { filter: m.compileFilter('없는이름') });
  deq([out.rows.length, out.matches], [0, 0]);
});

test('가상 스크롤 창 — 보이는 행 + 위아래 OVERSCAN, 끝에서 잘린다', async () => {
  const m = await fresh();
  assert.strictEqual(m.OVERSCAN, 10);
  deq(m.windowRange(0, 280, 28, 5000), { start: 0, end: 21 });
  deq(m.windowRange(28 * 1000, 280, 28, 5000), { start: 990, end: 1021 });
  deq(m.windowRange(28 * 4995, 280, 28, 5000), { start: 4985, end: 5000 });
  deq(m.windowRange(0, 280, 28, 0), { start: 0, end: 0 });
  deq(m.windowRange(-50, 280, 28, 3), { start: 0, end: 3 });
  assert.strictEqual(m.scrollToReveal(50, 0, 280, 28), 50 * 28 + 28 - 280);
  assert.strictEqual(m.scrollToReveal(2, 280, 280, 28), 56);
  assert.strictEqual(m.scrollToReveal(12, 280, 280, 28), 280);
  deq([m.rowHeight(false, false), m.rowHeight(true, false), m.rowHeight(true, true)], [28, 22, 44]);
});

test('키보드 — ↑↓ · → 펼침/첫 자식 · ← 접기/부모 · Home/End · ↵ · ⌥↵ · ⇧F10 · /', async () => {
  const m = await fresh();
  const dirs = fixtureDirs(m);
  dirs.set(m.keyOf(H, '/r/b'), { ...m.emptyDir(), entries: [{ name: 'q', dir: false, size: 1 }], total: 3000, next: 'C', pages: 1 });
  const { rows } = flat(m, dirs, ['/r/a', '/r/b']);
  // a, x0, x1, x2, b, q, more, README
  const k = (i, key, extra = {}) => m.treeKey(rows, i, { key, ...extra });
  deq(k(-1, 'ArrowDown'), { type: 'focus', index: 0 });
  deq(k(0, 'ArrowDown'), { type: 'focus', index: 1 });
  deq(k(0, 'ArrowUp'), { type: 'focus', index: 0 });
  deq(k(5, 'ArrowDown'), { type: 'focus', index: 6 }, '「더 불러오기」도 초점을 받는다');
  deq(k(0, 'ArrowRight'), { type: 'focus', index: 1 });
  deq(k(0, 'ArrowLeft'), { type: 'collapse', index: 0 });
  deq(k(2, 'ArrowLeft'), { type: 'focus', index: 0 });
  deq(k(6, 'ArrowLeft'), { type: 'focus', index: 4 });
  deq(k(1, 'ArrowRight'), { type: 'none' });
  deq(k(3, 'Home'), { type: 'focus', index: 0 });
  deq(k(0, 'End'), { type: 'focus', index: 7 });
  deq(k(1, 'Enter'), { type: 'activate', index: 1 });
  deq(k(1, 'Enter', { altKey: true }), { type: 'insert', index: 1 });
  deq(k(6, 'Enter', { altKey: true }), { type: 'none' });
  deq(k(1, 'F10', { shiftKey: true }), { type: 'menu', index: 1 });
  deq(k(1, 'F10'), { type: 'none' });
  deq(k(1, 'ContextMenu'), { type: 'menu', index: 1 });
  deq(k(1, '/'), { type: 'filter' });
  const closed = flat(m, dirs, []).rows;
  deq(m.treeKey(closed, 0, { key: 'ArrowRight' }), { type: 'expand', index: 0 });
});

test('글자 입력 이동 — 다음 일치로 돌고, 여러 글자는 지금 행부터 본다', async () => {
  const m = await fresh();
  const { rows } = flat(m, fixtureDirs(m), ['/r/a']);
  assert.strictEqual(m.typeahead(rows, 0, 'x'), 1);
  assert.strictEqual(m.typeahead(rows, 1, 'x'), 2);
  assert.strictEqual(m.typeahead(rows, 3, 'x'), 1, '끝에서 처음으로 돈다');
  assert.strictEqual(m.typeahead(rows, 2, 'x2'), 3);
  assert.strictEqual(m.typeahead(rows, 0, 'readme'), 5);
  assert.strictEqual(m.typeahead(rows, 0, 'zzz'), -1);
});

test('목록 비교 — 새로 생김 · 크기 · 수정 시각이 바뀐 이름, 사라진 하위 폴더', async () => {
  const m = await fresh();
  const prev = [{ name: 'a', dir: false, size: 1, mtime: 1 }, { name: 'b', dir: false, size: 1, mtime: 1 }, { name: 'd', dir: true, size: 0 }, { name: 'gone', dir: true, size: 0 }];
  const next = [{ name: 'a', dir: false, size: 1, mtime: 1 }, { name: 'b', dir: false, size: 1, mtime: 2 }, { name: 'd', dir: true, size: 0 }, { name: 'new', dir: false, size: 0 }];
  deq(m.changedNames(prev, next), ['b', 'new']);
  deq(m.removedDirs(prev, next), ['gone']);
  const d = m.appendPage(m.dirFromListing({ entries: [{ name: 'a', dir: false, size: 1 }], next: 'X', total: 3 }),
    { entries: [{ name: 'a', dir: false, size: 1 }, { name: 'b', dir: false, size: 1 }, { name: '.DS_Store', dir: false, size: 1 }], next: null, total: 3 });
  deq(d.entries.map((e) => e.name), ['a', 'b'], '겹친 이름은 한 번, OS 잡음은 뺀다');
  deq([d.pages, d.next], [2, null]);
});

// ── 2부: tree-store ─────────────────────────────────────────────────────

function fakeServer(tree) {
  // tree: path → entries. depth·after를 서버처럼 흉내 낸다(페이지 2개짜리 /r/big).
  const urls = [];
  const watchers = [];
  const list = (path, after) => {
    const all = tree[path];
    if (!all) throw new Error('not found');
    if (path === '/r/big') {
      const start = after ? Number(after) : 0;
      const page = all.slice(start, start + 2);
      const next = start + 2 < all.length ? String(start + 2) : null;
      return { path, entries: page.map((e) => ({ ...e })), total: all.length, next, capped: false };
    }
    return { path, entries: all.map((e) => ({ ...e })), total: all.length, next: null, capped: false };
  };
  const fetchJSON = async (u) => {
    urls.push(u);
    const q = new URLSearchParams(u.split('?')[1]);
    const path = q.get('path');
    const depth = Number(q.get('depth'));
    const fill = (l, base, left) => {
      if (left <= 0) return l;
      for (const e of l.entries) if (e.dir && tree[`${base}/${e.name}`]) e.children = fill(list(`${base}/${e.name}`), `${base}/${e.name}`, left - 1);
      return l;
    };
    return fill(list(path, q.get('after')), path, q.get('after') ? 0 : depth);
  };
  const watch = (kind, path, fn) => { const w = { kind, path, fn, off: false }; watchers.push(w); return () => { w.off = true; }; };
  return { urls, watchers, fetchJSON, watch };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('받기 규칙 — 처음 depth=2, 미리 받은 폴더는 요청 없이, 나머지는 depth=1, 더 불러오기는 after=', async () => {
  const { TreeStore, keyOf } = await fresh();
  const srv = fakeServer({
    '/r': [{ name: 'a', dir: true, size: 0 }, { name: 'big', dir: true, size: 0 }],
    '/r/a': [{ name: 'b', dir: true, size: 0 }],
    '/r/a/b': [{ name: 'c', dir: true, size: 0 }],
    '/r/a/b/c': [{ name: 'f.txt', dir: false, size: 1 }],
    '/r/big': Array.from({ length: 5 }, (_, i) => ({ name: `n${i}`, dir: false, size: i })),
  });
  const s = new TreeStore({ host: H, fetchJSON: srv.fetchJSON, watch: srv.watch });
  await s.open('/r');
  deq(srv.urls, ['/api/fs/tree?path=%2Fr&depth=2']);
  deq(srv.watchers.map((w) => [w.kind, w.path]), [['tree', '/r']], '감시는 트리 루트 하나');
  await s.expand('/r/a');
  await s.expand('/r/a/b');
  assert.strictEqual(srv.urls.length, 1, '두 단계까지는 미리 받았다');
  await s.expand('/r/a/b/c');
  assert.strictEqual(srv.urls[1], '/api/fs/tree?path=%2Fr%2Fa%2Fb%2Fc&depth=1');
  await s.expand('/r/big');
  const big = () => s.dirs.get(keyOf(H, '/r/big'));
  // depth=2 미리 받기에 /r/big 첫 페이지가 들어 있다.
  deq([big().entries.length, big().next], [2, '2']);
  await s.loadMore('/r/big');
  await s.loadMore('/r/big');
  deq(srv.urls.slice(-2), ['/api/fs/tree?path=%2Fr%2Fbig&depth=0&after=2', '/api/fs/tree?path=%2Fr%2Fbig&depth=0&after=4']);
  deq([big().entries.length, big().next, big().pages], [5, null, 3]);
});

test('tree 감시 — 보이는 바뀐 폴더만 depth=0으로(받은 페이지 수만큼), 안 보이는 폴더는 표시만, 넘치면 보이는 것 전부', async () => {
  const { TreeStore, keyOf } = await fresh();
  const tree = {
    '/r': [{ name: 'a', dir: true, size: 0 }, { name: 'z', dir: true, size: 0 }, { name: 'big', dir: true, size: 0 }],
    '/r/a': [{ name: 'f.txt', dir: false, size: 1, mtime: 1 }, { name: 'sub', dir: true, size: 0 }],
    '/r/a/sub': [{ name: 'g', dir: false, size: 1 }],
    '/r/z': [{ name: 'q', dir: false, size: 1 }],
    '/r/big': Array.from({ length: 5 }, (_, i) => ({ name: `n${i}`, dir: false, size: i })),
  };
  const srv = fakeServer(tree);
  const s = new TreeStore({ host: H, fetchJSON: srv.fetchJSON, watch: srv.watch });
  await s.open('/r');
  await s.expand('/r/a');
  await s.expand('/r/big');
  await s.loadMore('/r/big');
  srv.urls.length = 0;
  const notify = srv.watchers[0].fn;
  // a/f.txt 수정 + a/sub 삭제, 접힌 z 안 변경.
  tree['/r/a'] = [{ name: 'f.txt', dir: false, size: 1, mtime: 2 }, { name: 'new.md', dir: false, size: 0 }];
  notify({ dirs: ['a', 'z', '../etc'] });
  await tick(); await tick();
  deq(srv.urls, ['/api/fs/tree?path=%2Fr%2Fa&depth=0'], '보이는 바뀐 폴더 하나만, 위로 벗어나는 값은 버린다');
  assert.ok(s.changed.has(keyOf(H, '/r/a/f.txt')) && s.changed.has(keyOf(H, '/r/a/new.md')));
  assert.ok(!s.dirs.has(keyOf(H, '/r/a/sub')), '사라진 하위 폴더의 받아 둔 목록을 버린다');
  assert.ok(!s.dirs.has(keyOf(H, '/r/z')) && s.changedDirs.has(keyOf(H, '/r/z')), '접힌 폴더는 버리고 「안이 바뀜」만');
  // 페이지 2개를 받아 둔 폴더는 다시 받을 때도 2페이지.
  srv.urls.length = 0;
  notify({ dirs: ['big'] });
  await tick(); await tick(); await tick();
  deq(srv.urls, ['/api/fs/tree?path=%2Fr%2Fbig&depth=0', '/api/fs/tree?path=%2Fr%2Fbig&depth=0&after=2']);
  // 넘침·재연결 — 보이는 받아 둔 폴더 전부(루트 · a · big).
  srv.urls.length = 0;
  notify({ dirs: [], overflow: true });
  await tick(); await tick(); await tick();
  const paths = srv.urls.map((u) => new URLSearchParams(u.split('?')[1]).get('path'));
  deq(Array.from(new Set(paths)).sort(), ['/r', '/r/a', '/r/big']);
  s.markSeen('/r/a/f.txt');
  assert.ok(!s.changed.has(keyOf(H, '/r/a/f.txt')));
});

test('루트를 바꾸면 늦게 온 옛 응답은 버리고, 감시를 옮긴다', async () => {
  const { TreeStore, keyOf } = await fresh();
  let release;
  const gate = new Promise((r) => { release = r; });
  const urls = [];
  const watchers = [];
  const s = new TreeStore({
    host: 'm5',
    fetchJSON: async (u) => {
      urls.push(u);
      if (u.includes('%2Fslow')) { await gate; return { entries: [{ name: 'late', dir: false, size: 1 }], total: 1, next: null }; }
      return { entries: [{ name: 'ok', dir: false, size: 1 }], total: 1, next: null };
    },
    watch: (kind, path) => { const w = { kind, path, off: false }; watchers.push(w); return () => { w.off = true; }; },
  });
  const slow = s.open('/slow');
  await s.open('/fast');
  release();
  await slow;
  assert.strictEqual(s.root, '/fast');
  assert.ok(s.dirs.has(keyOf('m5', '/fast')) && !s.dirs.has(keyOf('m5', '/slow')));
  assert.ok(Array.from(s.dirs.keys()).every((k) => k.startsWith('m5\n')), '키에 호스트가 들어 있다');
  deq(watchers.map((w) => w.path), ['/fast']);
  await s.open('/slow2').catch(() => {});
  s.dispose();
  assert.ok(watchers.every((w) => w.off), 'dispose가 감시를 푼다');
});

// ── 3부: FileTree 마운트 ────────────────────────────────────────────────

async function mountWith(env, tree, extra = {}) {
  const srv = fakeServer(tree);
  const calls = { open: [], insert: [], share: [], copy: [] };
  const host = env.document.createElement('div');
  host.style.height = '400px';
  env.document.body.appendChild(host);
  const mounted = env.mountFileTree(host, {
    host: H, remote: false, fetchJSON: srv.fetchJSON, watch: srv.watch, fetchHostLabel: async () => 'Mac',
    openFile: (p) => calls.open.push(p), insertPath: (p) => { calls.insert.push(p); return true; },
    copyPath: async (p) => { calls.copy.push(p); return true; }, sharePath: (p) => calls.share.push(p),
    terminalCwd: async () => null, sessionName: () => 'dev', recent: () => [], clearRecent: () => {},
    toast: () => {}, getDense: () => !!extra.dense, subscribeSettings: () => () => {},
    ...(extra.deps || {}),
  });
  await mounted.store.open('/r');
  await tick();
  return { host, srv, calls, mounted, scroll: host.querySelector('.vt-ft-scroll') };
}

const bigTree = (n) => ({ '/r': Array.from({ length: n }, (_, i) => ({ name: `f${String(i).padStart(5, '0')}.txt`, dir: false, size: i })) });

test('마운트 — 행이 수천 개여도 DOM에는 보이는 구간만, 위치는 transform, aria-tree 속성', async () => {
  const env = await fresh();
  const tree = { '/r': [{ name: 'src', dir: true, size: 0 }, ...bigTree(3000)['/r']], '/r/src': [{ name: 'a.ts', dir: false, size: 1 }] };
  const { host, scroll } = await mountWith(env, tree);
  const rows = host.querySelectorAll('.vt-ft-row');
  assert.ok(rows.length > 0 && rows.length <= 60, `DOM 행 ${rows.length}개`);
  assert.strictEqual(scroll.getAttribute('role'), 'tree');
  assert.strictEqual(host.querySelector('.vt-ft-spacer').style.height, `${3001 * 28}px`, '전체 높이는 행 수 × 28');
  const dir = host.querySelector('.vt-ft-row.k-dir');
  deq([dir.getAttribute('role'), dir.getAttribute('aria-level'), dir.getAttribute('aria-expanded'),
    dir.getAttribute('aria-posinset'), dir.getAttribute('aria-setsize')], ['treeitem', '1', 'false', '1', '3001']);
  assert.strictEqual(rows[1].style.transform, 'translateY(28px)');
  assert.ok(host.querySelector('.vt-ft-host').textContent.includes('Mac'));
});

test('마운트 — 키보드로 초점이 움직이고(aria-activedescendant), → 가 펼치고, ↵ 가 파일을 연다', async () => {
  const env = await fresh();
  const tree = { '/r': [{ name: 'src', dir: true, size: 0 }, { name: 'z.md', dir: false, size: 1 }], '/r/src': [{ name: 'a.ts', dir: false, size: 1 }] };
  const { host, scroll, calls } = await mountWith(env, tree);
  const key = (k, extra = {}) => scroll.dispatchEvent(new env.window.KeyboardEvent('keydown', { key: k, bubbles: true, ...extra }));
  scroll.dispatchEvent(new env.window.FocusEvent('focus'));
  await tick();
  const active = () => env.document.getElementById(scroll.getAttribute('aria-activedescendant'));
  assert.strictEqual(active().dataset.key, `${H}\n/r/src`);
  key('ArrowRight');
  await tick(); await tick();
  assert.strictEqual(host.querySelector('.vt-ft-row.k-dir').getAttribute('aria-expanded'), 'true');
  key('ArrowDown');
  await tick();
  assert.strictEqual(active().dataset.key, `${H}\n/r/src/a.ts`);
  assert.strictEqual(active().getAttribute('aria-level'), '2');
  key('Enter');
  deq(calls.open, ['/r/src/a.ts']);
  key('Enter', { altKey: true });
  deq(calls.insert, ['/r/src/a.ts']);
  key('ArrowLeft');
  await tick();
  assert.strictEqual(active().dataset.key, `${H}\n/r/src`);
});

test('마운트 — 행 메뉴(⇧F10·오른쪽 클릭): 열기 · 경로 삽입 · 경로 복사 · 공유 · 새로고침, 다운로드·새 파일은 없다', async () => {
  const env = await fresh();
  const tree = { '/r': [{ name: 'a$(id).txt', dir: false, size: 3 }, { name: 'd', dir: true, size: 0 }], '/r/d': [] };
  const { host, scroll, calls } = await mountWith(env, tree);
  scroll.dispatchEvent(new env.window.FocusEvent('focus'));
  await tick();
  // 초점은 첫 행(가짜 서버 순서대로 파일이 먼저)에 있다.
  scroll.dispatchEvent(new env.window.KeyboardEvent('keydown', { key: 'F10', shiftKey: true, bubbles: true }));
  await tick();
  const menu = env.document.querySelector('.vt-ft-menu[role="menu"]');
  assert.ok(menu, '메뉴가 열린다');
  const labels = Array.from(menu.querySelectorAll('[role="menuitem"]'), (b) => b.textContent.replace(/[↵⌥]/g, '').trim());
  deq(labels, ['열기', '터미널에 경로 삽입', '경로 복사', '공유 링크 만들기…', '새로고침']);
  assert.ok(!/다운로드|새 파일/.test(menu.textContent));
  menu.querySelectorAll('[role="menuitem"]')[1].click();
  deq(calls.insert, ['/r/a$(id).txt'], '경로 삽입은 tree.js의 insertPathToTerminal(인용·제어문자 거부)로 간다');
  assert.ok(!env.document.querySelector('.vt-ft-menu'), '고르면 닫힌다');
  // 폴더 행 오른쪽 클릭 — 공유 없음.
  const dirRow = host.querySelector('.vt-ft-row.k-dir');
  dirRow.dispatchEvent(new env.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 10, clientY: 10 }));
  await tick();
  const dl = Array.from(env.document.querySelectorAll('.vt-ft-menu [role="menuitem"]'), (b) => b.textContent);
  assert.ok(dl.some((x) => x.includes('펼치기')) && !dl.some((x) => x.includes('공유')));
  env.document.querySelector('.vt-ft-menu').dispatchEvent(new env.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await tick();
  assert.ok(!env.document.querySelector('.vt-ft-menu'));
});

test('마운트 — 다운로드(단계 7): 파일 행 메뉴에 크기와 함께, 원격 호스트면 부르지 않고 이유를 알린다(D7)', async () => {
  const tree = { '/r': [{ name: 'a.bin', dir: false, size: 2048 }, { name: 'd', dir: true, size: 0 }], '/r/d': [] };
  for (const remote of [false, true]) {
    const env = await fresh();
    const got = [];
    const toasts = [];
    const { host, scroll } = await mountWith(env, tree, {
      deps: { remote, downloadPath: (p, size) => got.push([p, size]), toast: (m, k) => toasts.push([m, k]) },
    });
    scroll.dispatchEvent(new env.window.FocusEvent('focus'));
    await tick();
    scroll.dispatchEvent(new env.window.KeyboardEvent('keydown', { key: 'F10', shiftKey: true, bubbles: true }));
    await tick();
    const items = Array.from(env.document.querySelectorAll('.vt-ft-menu [role="menuitem"]'));
    const dl = items.find((b) => b.textContent.includes('다운로드'));
    assert.ok(dl, '파일 행에는 다운로드가 있다');
    assert.match(dl.textContent, remote ? /이 컴퓨터에서만/ : /2\.0K/);
    dl.click();
    if (remote) {
      deq(got, []);
      assert.match(toasts[0][0], /이 컴퓨터의 파일만/);
    } else {
      deq(got, [['/r/a.bin', 2048]]);
    }
    // 폴더 행에는 없다.
    const dirRow = host.querySelector('.vt-ft-row.k-dir');
    dirRow.dispatchEvent(new env.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 10, clientY: 10 }));
    await tick();
    assert.ok(!env.document.querySelector('.vt-ft-menu').textContent.includes('다운로드'));
  }
});

test('마운트 — 이름 필터가 행을 줄이고 수를 적는다, 촘촘 설정이면 행 22px', async () => {
  const env = await fresh();
  const tree = { '/r': [{ name: 'Rail.tsx', dir: false, size: 1 }, { name: 'panes.js', dir: false, size: 1 }, { name: 'rail-zones.ts', dir: false, size: 1 }] };
  const { host } = await mountWith(env, tree, { dense: true });
  assert.strictEqual(host.querySelector('.vt-ft').style.getPropertyValue('--ft-row'), '22px');
  const inp = host.querySelector('.vt-ft-filter input');
  inp.value = 'rail';
  inp.dispatchEvent(new env.window.Event('input', { bubbles: true }));
  await tick();
  const names = Array.from(host.querySelectorAll('.vt-ft-row .vt-ft-nm'), (n) => n.textContent);
  deq(names, ['Rail.tsx', 'rail-zones.ts']);
  assert.strictEqual(host.querySelector('.vt-ft-row mark').textContent, 'Rail');
  assert.strictEqual(host.querySelector('.vt-ft-cnt').textContent, '2개');
  inp.value = 'nothing';
  inp.dispatchEvent(new env.window.Event('input', { bubbles: true }));
  await tick();
  assert.ok(host.querySelector('.vt-ft-empty'));
});

test('마운트 — 클릭은 스크롤 상자 하나가 받는다(행 DOM에 리스너 없음), 행 동작 버튼은 위임으로 처리', async () => {
  const env = await fresh();
  const tree = { '/r': [{ name: 'a.txt', dir: false, size: 1 }] };
  const { host, calls } = await mountWith(env, tree);
  const row = host.querySelector('.vt-ft-row.k-file');
  row.querySelector('[data-act="share"]').click();
  deq(calls.share, ['/r/a.txt']);
  row.querySelector('[data-act="insert"]').click();
  deq(calls.insert, ['/r/a.txt']);
  deq(calls.open, [], '행 동작 버튼은 파일을 열지 않는다');
  row.click();
  deq(calls.open, ['/r/a.txt']);
  assert.ok(row.classList.contains('sel'));
});
