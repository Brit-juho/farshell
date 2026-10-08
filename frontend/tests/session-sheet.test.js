// 모바일 세션 시트의 평소 목록 — 뷰 순서대로 묶음(라벨 헤더 + 구성원)/단독(행 하나), 열려 있지 않은 tmux 세션은
// 이름순 별도 구역. 목록 렌더링은 지연 청크(layout/session-sheet.js)라 picker.js는 틀만 먼저 그린다.
// 진짜 index.html + 실제 모듈 그래프로 돌린다(session-view-edit.test.js와 같은 방식).
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

class FakeTerminal {
  constructor(opts) { this.options = opts; this.cols = 80; this.rows = 24; }
  loadAddon() {} open(el) { this.element = el; } focus() {} reset() {} write() {}
  onData() {} getSelection() { return ''; } attachCustomKeyEventHandler() {} dispose() {}
}
class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = 0; }
  addEventListener() {} send() {} close() {}
}
const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = () => new Promise((r) => setTimeout(r, 20));

// 뷰 3개: [a] · [b | c](묶음) · [d]. b·c만 tmux 세션이라 표시 이름이 tmux 이름(tb·tc)이고 완전 종료 버튼도 거기에만 있다.
async function setup({ tmux = [] } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.innerWidth = 1024; // 저장된 PC 묶음을 만든 뒤 모바일에서 복원한다.
  window.WebSocket = FakeWebSocket;
  window.Terminal = FakeTerminal;
  window.FitAddon = { FitAddon: class { fit() {} } };
  window.SearchAddon = { SearchAddon: class {} };
  const state = { tmux, pending: null };
  window.fetch = (url) => {
    if (String(url).includes('/api/tmux/sessions')) {
      if (state.pending) return state.pending;
      return ok(state.tmux);
    }
    return ok([]);
  };
  const cache = new Map();
  await importFresh(J('lib/keyseq.js'), env.context, cache);
  await importFresh(J('ui/toast.js'), env.context, cache);
  await importFresh(J('lib/ansilex.js'), env.context, cache);
  const sess = await importFresh(J('term/session.js'), env.context, cache);
  await importFresh(J('layout/panes.js'), env.context, cache);
  const dom = await importFresh(J('core/dom.js'), env.context, cache);
  const S = await importFresh(J('layout/store.js'), env.context, cache);
  const core = await importFresh(J('core/store.js'), env.context, cache);
  const picker = await importFresh(J('picker.js'), env.context, cache);
  sess.addSession('a', 'A');
  const t1 = S.getActiveTabId();
  sess.addSession('b', 'B', { tmuxName: 'tb' });
  const t2 = S.getActiveTabId();
  S.splitActivePane('row', null);
  sess.addSession('c', 'C', { place: 'pane', tmuxName: 'tc' });
  sess.addSession('d', 'D');
  const t3 = S.getActiveTabId();
  window.innerWidth = 500;
  return { env, window, doc: window.document, sess, core, dom, S, picker, state, t1, t2, t3 };
}

async function openSheet(ctx) {
  ctx.dom.getAction('session.manager')();
  await flush();
  return ctx.doc.getElementById('session-manager');
}
const names = (root) => [...root.querySelectorAll('.vt-session-select')].map((b) => b.textContent);

test('뷰 순서대로 — 단독 행, 묶음은 라벨 헤더 + 구성원 한 덩어리, 단독 행', async () => {
  const ctx = await setup();
  const m = await openSheet(ctx);
  const kids = [...m.querySelector('.vt-session-list').children];
  assert.deepStrictEqual(kids.map((k) => k.className.split(' ')[0]), ['vt-session-row', 'vt-session-group', 'vt-session-row']);
  assert.strictEqual(kids[0].querySelector('.vt-session-select').textContent, 'A');
  assert.strictEqual(kids[1].querySelector('.vt-session-group-label').textContent, '묶음 뷰');
  assert.deepStrictEqual(names(kids[1]), ['tb', 'tc']);
  assert.strictEqual(kids[2].querySelector('.vt-session-select').textContent, 'D');
  assert.deepStrictEqual(names(m), ['A', 'tb', 'tc', 'D']);
});

test('뷰에 이름이 있으면 묶음 헤더에 그 이름', async () => {
  const ctx = await setup();
  ctx.S.setTabLabel(ctx.t2, '배포 작업');
  const m = await openSheet(ctx);
  assert.strictEqual(m.querySelector('.vt-session-group-label').textContent, '배포 작업');
  assert.strictEqual(m.querySelector('.vt-session-group').getAttribute('aria-label'), '배포 작업');
});

test('묶음 제목 편집은 세션 이름을 바꾸지 않고 닫힌 묶음에도 저장된다', async () => {
  const { answerPrompt } = require('./helpers/dialog-stub');
  const ctx = await setup();
  const m = await openSheet(ctx);
  m.querySelector('.vt-view-group-rename').click();
  await answerPrompt(ctx.doc, { value: '검토 작업' });
  await flush();
  assert.strictEqual(ctx.S.getTabsWithTrees().find((t) => t.id === ctx.t2).label, '검토 작업');
  assert.deepStrictEqual(names(m.querySelector('.vt-session-group')), ['tb', 'tc']);
  // 같은 저장소의 실제 닫힌 스냅샷도 제목 편집 대상이다.
  ctx.S.rememberClosedView({ id: 'closed', label: '검토 작업', tree: { t: 'split', dir: 'row', ratio: .5,
    a: { t: 'leaf', session: { tmux: 'e' } }, b: { t: 'leaf', session: { tmux: 'f' } } } });
  assert.strictEqual(ctx.S.setTabLabel('closed', '다음 작업'), true);
  assert.strictEqual(ctx.S.getClosedViews().find((v) => v.id === 'closed').label, '다음 작업');
});

test('모바일 목록에도 현재 디렉터리/브랜치가 보이고 조회 실패는 기존 목록과 재시도를 보존한다', async () => {
  const ctx = await setup({ tmux: [{ name: 'tb', cwd: '/repo/src', context: { cwd: '/repo/src', status: 'ready', branch: 'feature' } },
    { name: 'outside', cwd: '/elsewhere', context: { cwd: '/elsewhere', status: 'nonrepo' } }] });
  let m = await openSheet(ctx);
  assert.match(m.querySelector('.vt-session-meta').textContent, /src · feature/);
  ctx.window.fetch = () => Promise.reject(new Error('temporary'));
  ctx.S.setTabLabel(ctx.t2, '갱신');
  await flush();
  assert.ok(names(m).includes('outside'));
  assert.match(m.querySelector('.vt-session-error').textContent, /다시 시도/);
  assert.strictEqual(m.querySelector('.vt-session-empty'), null);
});

test('뷰 탭을 재정렬하면 목록이 따라오고 n/m과 같은 순서다', async () => {
  const ctx = await setup();
  ctx.S.moveLayoutTab(ctx.t3, 0);
  const m = await openSheet(ctx);
  assert.deepStrictEqual(names(m), ['D', 'A', 'tb', 'tc']);
  const active = ctx.doc.querySelector('.vt-session-row.active .vt-session-select').textContent;
  const total = names(m).length;
  const pos = names(m).indexOf(active) + 1;
  assert.match(ctx.doc.querySelector('.vt-sp-meta').textContent, new RegExp(`${pos}/${total}$`));
});

test('활성 표시 — 활성 세션 행 하나만 active + aria-current', async () => {
  const ctx = await setup();
  const m = await openSheet(ctx);
  const act = [...m.querySelectorAll('.vt-session-row.active')];
  assert.strictEqual(act.length, 1);
  assert.strictEqual(act[0].querySelector('.vt-session-select').textContent, 'D');
  assert.strictEqual(act[0].querySelector('.vt-session-select').getAttribute('aria-current'), 'true');
  assert.strictEqual(m.querySelector('.vt-session-group .vt-session-row.in-group.active'), null);
});

test('열려 있지 않은 세션 — 열린 것(tb·tc)은 빼고 이름순 별도 구역, 목록 아래', async () => {
  const ctx = await setup({ tmux: [{ name: 'zeta' }, { name: 'tb' }, { name: 'alpha' }, { name: 'tc' }] });
  const m = await openSheet(ctx);
  const list = m.querySelector('.vt-session-list');
  const heading = list.querySelector('.vt-session-section');
  assert.strictEqual(heading.textContent, '열려 있지 않은 세션');
  const sleeping = [...list.querySelectorAll('.vt-session-row.sleeping .vt-session-select')].map((b) => b.textContent);
  assert.deepStrictEqual(sleeping, ['alpha', 'zeta']);
  // 구역 제목은 모든 열린 행·묶음 뒤에 온다.
  const order = [...list.children];
  const h = order.indexOf(heading);
  assert.ok(order.slice(0, h).every((n) => !n.classList.contains('sleeping')));
  assert.ok(order.slice(h + 1).every((n) => n.classList.contains('sleeping')));
});

test('열려 있지 않은 세션이 없으면 구역 제목도 없다', async () => {
  const ctx = await setup();
  const m = await openSheet(ctx);
  assert.strictEqual(m.querySelector('.vt-session-section'), null);
});

test('행 클릭 이동 — 묶음 안 구성원을 누르면 그 뷰로 가서 시트가 닫힌다(배정 아님)', async () => {
  const ctx = await setup();
  const m = await openSheet(ctx);
  const before = JSON.stringify(ctx.S.getTabsWithTrees().map((t) => t.tree));
  [...m.querySelectorAll('.vt-session-group .vt-session-select')][1].click();
  assert.strictEqual(ctx.S.getActiveTabId(), ctx.t2);
  assert.strictEqual(ctx.core.activeSessionId(), 'c');
  assert.strictEqual(ctx.doc.getElementById('session-manager'), null);
  assert.strictEqual(JSON.stringify(ctx.S.getTabsWithTrees().map((t) => t.tree)), before, '트리 불변');
});

test('행 메뉴 — tmux 세션 행에만 완전 종료, 모든 행에 이름 변경·닫기, 닫으면 목록이 다시 그려진다', async () => {
  const ctx = await setup();
  let m = await openSheet(ctx);
  const rows = [...m.querySelectorAll('.vt-session-row')];
  assert.deepStrictEqual(rows.map((r) => r.querySelectorAll('.vt-session-action').length), [2, 3, 3, 2]);
  assert.ok([...rows[1].querySelectorAll('.vt-session-action')].some((b) => /완전 종료/.test(b.getAttribute('aria-label'))));
  rows[0].querySelectorAll('.vt-session-action')[1].click();       // A 닫기
  await flush();
  m = ctx.doc.getElementById('session-manager');
  assert.deepStrictEqual(names(m), ['tb', 'tc', 'D']);
});

test('틀이 먼저 — 청크가 오기 전엔 한 줄 로딩, 다음 그림부터는 이전 목록이 남는다', async () => {
  const ctx = await setup();
  ctx.dom.getAction('session.manager')();
  const m = ctx.doc.getElementById('session-manager');
  assert.ok(m.querySelector('.vt-session-loading'), '청크 전 로딩 한 줄');
  assert.strictEqual(m.querySelector('.vt-session-sheet').getAttribute('aria-busy'), 'true');
  await flush();
  assert.strictEqual(m.querySelector('.vt-session-loading'), null);
  assert.strictEqual(m.querySelector('.vt-session-sheet').getAttribute('aria-busy'), null);
  // tmux 목록 응답이 늦는 동안 다시 그려도 시트가 비지 않는다.
  let release;
  ctx.state.pending = new Promise((r) => { release = () => r({ ok: true, json: () => Promise.resolve([]) }); });
  ctx.picker.updateSessionPicker();
  assert.deepStrictEqual(names(m), ['A', 'tb', 'tc', 'D'], '이전 목록 유지');
  release(); await flush();
  assert.deepStrictEqual(names(m), ['A', 'tb', 'tc', 'D']);
  assert.strictEqual(m.querySelectorAll('.vt-session-row').length, 4, '겹쳐 그려도 행이 중복되지 않는다');
});

test('편집 모드 — 토글하면 뷰 블록만, 다시 토글하면 평소 목록으로 돌아온다', async () => {
  const ctx = await setup();
  const m = await openSheet(ctx);
  assert.ok(m.querySelector('#session-open-mac-btn'), '평소엔 맥에서 열기');
  m.querySelector('.vt-session-order-toggle').click(); await flush();
  assert.strictEqual(m.querySelectorAll('.vt-order-view').length, 3);
  assert.strictEqual(m.querySelector('.vt-session-select'), null, '편집 모드엔 행 클릭 이동이 없다');
  assert.strictEqual(m.querySelector('#session-manager-title').textContent, '뷰 편집');
  assert.strictEqual(m.querySelector('#session-open-mac-btn'), null);
  assert.strictEqual(ctx.doc.activeElement.classList.contains('vt-session-order-toggle'), true, '토글 포커스 유지');
  m.querySelector('.vt-session-order-toggle').click(); await flush();
  assert.deepStrictEqual(names(m), ['A', 'tb', 'tc', 'D']);
  assert.strictEqual(m.querySelector('#session-manager-title').textContent, '세션 관리');
});

test('시트를 닫았다 열면 편집 모드가 풀려 있다', async () => {
  const ctx = await setup();
  let m = await openSheet(ctx);
  m.querySelector('.vt-session-order-toggle').click(); await flush();
  m.querySelector('.vt-session-close').click();
  m = await openSheet(ctx);
  assert.strictEqual(m.querySelectorAll('.vt-order-view').length, 0);
  assert.deepStrictEqual(names(m), ['A', 'tb', 'tc', 'D']);
});

test('세션 이름은 HTML로 해석하지 않는다', async () => {
  const ctx = await setup();
  ctx.sess.addSession('x', '<img src=x onerror=alert(1)>');
  const m = await openSheet(ctx);
  assert.strictEqual(m.querySelector('.vt-session-list img'), null);
  assert.ok(names(m).includes('<img src=x onerror=alert(1)>'));
});
