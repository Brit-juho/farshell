// 폰 세션 타일(shell/Fleet.tsx, 세션 로직 점검 단계 7 — 시안 T1 저장소 묶음 2열).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
async function mod() { const env = await loadSolidBuild(); _envs.push(env); return env; }
const plain = (v) => JSON.parse(JSON.stringify(v));
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', '..', ...p), 'utf8');
const row = (over) => ({
  sessionId: '', tmuxName: over.name, name: over.name, open: true, status: 'idle', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, origin: 'local', viewLabel: null,
  otherScreens: 0, otherClients: 0, ...over,
});

test('tilesOf — 메인 가지 → 워크트리 가지, 위임 하위는 부모 바로 뒤(부모 이름을 단다), 워크트리 세션은 브랜치', async () => {
  const m = await mod();
  const p = row({ name: 'p' });
  const g = { lanes: [
    { worktree: null, sessions: [{ row: p, kids: [row({ name: 'k' })] }] },
    { worktree: { key: '/r/wt', name: 'wt', branch: 'fix/x' }, sessions: [{ row: row({ name: 'w' }), kids: [] }] },
  ] };
  const tiles = m.tilesOf(g);
  assert.deepStrictEqual(plain(tiles.map((e) => [e.row.name, e.worktree, e.parent && e.parent.name])), [['p', null, null], ['k', null, 'p'], ['w', 'fix/x', null]]);
});

function mount(m, entry, calls) {
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountFleetTile(root, { entry, onOpen: () => calls.push('open'), onLongPress: () => calls.push('menu'), onSeen: () => calls.push('seen') });
  return root;
}

test('타일 — 이름·경과, 둘째 줄은 대기 유형 + 요약, 답 버튼은 없다, 워크트리면 브랜치', async () => {
  const m = await mod();
  const calls = [];
  const root = mount(m, { row: row({ name: '결제 버그', status: 'waiting', kind: 'approval', summary: 'npm', since: Date.now() / 1000 - 120 }), worktree: 'fix/x', parent: null }, calls);
  const tile = root.querySelector('.vt-tile');
  assert.strictEqual(tile.dataset.state, 'waiting');
  assert.strictEqual(tile.querySelector('.vt-tile-name').textContent, '결제 버그');
  assert.match(tile.querySelector('.vt-tile-tm').textContent, /2분/);
  assert.match(tile.querySelector('.vt-tile-line').textContent, /승인 요청\s*npm/);
  assert.match(tile.querySelector('.vt-tile-wt').textContent, /fix\/x/);
  assert.strictEqual(tile.querySelectorAll('button').length, 0, '타일 안에 답 버튼이 없다');
  tile.click();
  assert.deepStrictEqual(calls, ['seen', 'open']);
});

test('타일 — 할 말이 없으면 변경량, 「유휴」는 쓰지 않는다, 완료·확인 안 함은 누르면 확인만', async () => {
  const m = await mod();
  const calls = [];
  const root = mount(m, { row: row({ name: 'a', changed: { files: 1, add: 3, del: 1 } }), worktree: null, parent: null }, calls);
  assert.match(root.querySelector('.vt-tile-line').textContent, /\+3\s*−1/);
  assert.ok(!/유휴/.test(root.textContent));
  const calls2 = [];
  const done = mount(m, { row: row({ name: 'd', status: 'done', unseen: true }), worktree: null, parent: null }, calls2);
  assert.ok(done.querySelector('.vt-tile-unseen'));
  done.querySelector('.vt-tile').click();
  assert.deepStrictEqual(calls2, ['seen'], '확인만 하고 이동하지 않는다');
});

test('타일 — 위임 하위는 「↳ 부모 · 보고 도착」', async () => {
  const m = await mod();
  const lin = { delegation: 'd', parent: 'p', state: 'reported', outcome: 'succeeded', createdAt: 1, task: '', summary: '', reportUnread: true };
  const root = mount(m, { row: row({ name: 'k', lineage: lin }), worktree: null, parent: row({ name: '부모 세션' }) }, []);
  assert.match(root.querySelector('.vt-tile-sub').textContent, /↳ 부모 세션 · 보고 도착/);
});

test('길게 누르기 — 터치 500ms면 메뉴, 뒤따르는 click은 타일을 열지 않는다, 마우스는 무시', async () => {
  const m = await mod();
  const calls = [];
  const root = mount(m, { row: row({ name: 'a' }), worktree: null, parent: null }, calls);
  const tile = root.querySelector('.vt-tile');
  const ev = (type, pointerType = 'touch') => new m.window.PointerEvent(type, { bubbles: true, pointerType, clientX: 10, clientY: 10 });
  tile.dispatchEvent(ev('pointerdown', 'mouse'));
  await new Promise((r) => setTimeout(r, 560));
  assert.deepStrictEqual(calls, []);
  tile.dispatchEvent(ev('pointerdown'));
  await new Promise((r) => setTimeout(r, 560));
  assert.deepStrictEqual(calls, ['menu']);
  tile.click();
  assert.deepStrictEqual(calls, ['menu'], '메뉴를 띄운 손가락을 떼도 열리지 않는다');
});

test('폰 목록 — 저장소 묶음·「종료 N」 시트·보관함, 정렬·답 버튼·위임 레인은 없다, 시트는 대화상자', () => {
  const src = read('frontend', 'js', 'shell', 'Fleet.tsx');
  assert.match(src, /buildRepoTree\(/);
  assert.match(src, /class="vt-tile-grid"/);
  assert.match(src, /openEnded\(g\(\)\.key\)/);
  assert.match(src, /<ArchiveView remote=/);
  assert.doesNotMatch(src, /AnswerButtons|answerSessionPrompt|vt-rail-sort|vt-lin-kid|ORDER_KEY/);
  assert.match(src, /class="vt-session-sheet vt-fleet-menu-sheet" role="dialog" aria-modal="true"/);
  assert.match(src, /e\.key === 'Escape'\) \{ e\.preventDefault\(\); closeMenu\(\);/);
});

test('카탈로그 쪽 합치기 — 더 불러온 뒤쪽은 폴링에도 남고 커서도 유지된다', async () => {
  const m = await mod();
  const snap = (ids, next, acts) => ({ host: 'h', revision: 1, projects: [], workspaces: [], next, sessions: ids.map((id, i) => ({ id, last_activity: acts[i] })) });
  const first = snap(['a', 'b'], 'c1', [90, 80]);
  const more = m.mergeCatalogPage(first, snap(['b', 'c'], null, [80, 10]), true, false);
  assert.deepStrictEqual(Array.from(more.sessions, (x) => x.id), ['a', 'b', 'c'], '겹친 id는 한 번');
  assert.strictEqual(more.next, null);
  const polled = m.mergeCatalogPage(more, snap(['n', 'a'], 'c2', [99, 90]), false, true);
  assert.deepStrictEqual(Array.from(polled.sessions, (x) => x.id), ['n', 'a', 'b', 'c'], '첫 쪽보다 오래된 받아 둔 뒤쪽은 남는다');
  assert.strictEqual(polled.next, null, '받아 둔 끝 자리 커서');
  const plainPoll = m.mergeCatalogPage(more, snap(['n'], 'c3', [99]), false, false);
  assert.deepStrictEqual(Array.from(plainPoll.sessions, (x) => x.id), ['n']);
});

test('시트 키 — Esc는 닫고, Tab은 시트 안에서 돈다', async () => {
  const m = await mod();
  const doc = m.document || globalThis.document;
  const sheet = doc.createElement('div');
  sheet.innerHTML = '<button id="x1">1</button><button id="x2">2</button>';
  doc.body.appendChild(sheet);
  const [b1, b2] = sheet.querySelectorAll('button');
  const key = (k, shift = false) => { let prevented = false; return { e: { key: k, shiftKey: shift, preventDefault() { prevented = true; }, stopPropagation() {} }, get prevented() { return prevented; } }; };
  let closed = 0;
  b2.focus();
  const tab = key('Tab');
  m.onSheetKeyDown(tab.e, sheet, () => { closed++; });
  assert.ok(tab.prevented); assert.strictEqual(doc.activeElement, b1, '끝에서 Tab → 처음');
  const back = key('Tab', true);
  m.onSheetKeyDown(back.e, sheet, () => {});
  assert.strictEqual(doc.activeElement, b2, '처음에서 Shift+Tab → 끝');
  m.onSheetKeyDown(key('Escape').e, sheet, () => { closed++; });
  assert.strictEqual(closed, 1);
  sheet.remove();
});
