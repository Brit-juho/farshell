// L5 — layout/dnd.js 순수 로직 검증: 5구역 드롭존 판정(computeDropZone)과
// 드롭 반영(applyPaneDrop, layout/store.js를 거쳐 중복 attach 금지까지).
// 포인터/DOM 이벤트 배선(wirePaneDropTarget/wireTouchDragSource)은 실브라우저
// 검증 대상 — 여기서는 DOM 이벤트 없이 확인 가능한 판정·상태 반영만 다룬다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const DND_JS = path.join(__dirname, '../js/layout/dnd.js');
const STORE_JS = path.join(__dirname, '../js/layout/store.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// dnd.js가 store.js의 스토어 싱글톤을 그대로 참조해야 applyPaneDrop이 실제
// 트리에 반영된다 — 같은 cache로 두 엔트리를 불러 같은 모듈 그래프를 공유한다
// (layout-store.test.js의 loadStore()와 같은 이유).
async function loadDnd() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  const S = await importFresh(STORE_JS, env.context, cache);
  const D = await importFresh(DND_JS, env.context, cache);
  return { S, D };
}

const RECT = { left: 0, top: 0, width: 100, height: 100 };

test('computeDropZone — 중앙은 center', async () => {
  const { D } = await loadDnd();
  assert.strictEqual(D.computeDropZone(RECT, 50, 50), 'center');
});

test('computeDropZone — 네 가장자리(25% 임계값 안쪽)', async () => {
  const { D } = await loadDnd();
  assert.strictEqual(D.computeDropZone(RECT, 5, 50), 'left');
  assert.strictEqual(D.computeDropZone(RECT, 95, 50), 'right');
  assert.strictEqual(D.computeDropZone(RECT, 50, 5), 'top');
  assert.strictEqual(D.computeDropZone(RECT, 50, 95), 'bottom');
});

test('computeDropZone — 코너 근처는 더 가까운 한쪽 방향으로만 판정된다', async () => {
  const { D } = await loadDnd();
  // (5,20): left까지 거리 0.05, top까지 거리 0.20 — left가 더 가깝다.
  assert.strictEqual(D.computeDropZone(RECT, 5, 20), 'left');
  // (20,5): top까지 거리 0.05, left까지 거리 0.20 — top이 더 가깝다.
  assert.strictEqual(D.computeDropZone(RECT, 20, 5), 'top');
});

test('computeDropZone — 폭/높이가 0이면 center로 취급(0으로 나누기 방지)', async () => {
  const { D } = await loadDnd();
  assert.strictEqual(D.computeDropZone({ left: 0, top: 0, width: 0, height: 0 }, 5, 5), 'center');
});

test('applyPaneDrop — center는 그 pane에 세션을 배정한다', async () => {
  const { S, D } = await loadDnd();
  const paneId = S.getActivePaneId();
  D.applyPaneDrop(paneId, 'center', 'sess-x');
  assert.strictEqual(S.getTree().session, 'sess-x');
});

test('applyPaneDrop — center 배정은 다른 leaf에 있던 세션을 그쪽에서 비운다(중복 attach 금지)', async () => {
  const { S, D } = await loadDnd();
  const rootId = S.getActivePaneId();
  S.setPaneSession('sess-x', rootId);
  const otherId = S.splitActivePane('row'); // 새 빈 leaf, 활성이 됨

  D.applyPaneDrop(otherId, 'center', 'sess-x');
  const tree = S.getTree();
  assert.strictEqual(tree.a.session, null, '기존 pane(rootId)은 비워져야 한다');
  assert.strictEqual(tree.b.session, 'sess-x', '드롭한 pane(otherId)에 배정돼야 한다');
});

// 2026-09-21 버그 수정 — 위 테스트는 **같은 탭 안**의 중복만 본다. 레일에서
// 세션을 끌어 **다른 탭**의 pane에 떨어뜨려 그룹을 만드는 경우(그룹을 만드는
// 유일한 방법, ADR-29)는 이 케이스를 못 잡았다 — 원래 있던 솔로 탭이 안
// 지워져서 같은 세션이 두 탭에 동시에 남았다(사용자 보고: "그룹을 만들어도
// 그 세션의 옛 탭이 상단에 그대로 떠 있다", "레일에서 그룹 세션을 클릭하면
// 그룹이 아니라 엉뚱한(옛) 화면으로 간다").
// 2026-10-03(규칙 결정) — 드래그로 단독 뷰가 비면 그 뷰를 자동으로 닫는다(옛 동작).
// 칸 ×로 비운 단독 뷰가 빈 칸으로 남는 규칙과는 다르다(session-actions.js).
test('applyPaneDrop — 다른 뷰로 끌어 단독 뷰가 비면 그 뷰는 자동으로 닫힌다(교차 탭 중복 금지)', async () => {
  const { S, D } = await loadDnd();
  const soloTabId = S.getActiveTabId();
  S.setPaneSession('sess-a', S.getActivePaneId()); // soloTabId는 sess-a 혼자만의 탭

  const groupTabId = S.openLayoutTab({ label: 'group-tab' });
  const groupPaneId = S.getActivePaneId();

  D.applyPaneDrop(groupPaneId, 'right', 'sess-a'); // sess-a를 groupTab으로 드래그

  assert.ok(!S.getTabs().some((t) => t.id === soloTabId), '비게 된 원래 뷰는 닫힌다');
  assert.strictEqual(S.getActiveTabId(), groupTabId, '활성 뷰는 그대로 도착한 뷰');
  assert.strictEqual(S.findTabBySessionId('sess-a'), groupTabId, '레일 클릭 시 찾아가야 할 탭은 그룹 탭 하나뿐이어야 한다');
});

test('applyPaneDrop — center로 끌어와도 원래 단독 뷰가 비면 닫는다', async () => {
  const { S, D } = await loadDnd();
  const soloTabId = S.getActiveTabId();
  S.setPaneSession('sess-a', S.getActivePaneId());
  S.openLayoutTab({ label: 'other' });
  D.applyPaneDrop(S.getActivePaneId(), 'center', 'sess-a');
  assert.ok(!S.getTabs().some((t) => t.id === soloTabId));
});

test('applyPaneDrop — 같은 뷰 안에서 옮기면 뷰는 하나뿐이라 닫지 않는다', async () => {
  const { S, D } = await loadDnd();
  const tabId = S.getActiveTabId();
  S.setPaneSession('sess-a', S.getActivePaneId());
  const otherPane = S.splitActivePane('row');
  D.applyPaneDrop(otherPane, 'center', 'sess-a');
  assert.strictEqual(S.getTabs().length, 1);
  assert.strictEqual(S.getTabs()[0].id, tabId);
  assert.strictEqual(S.getTree().b.session, 'sess-a');
});

test('applyPaneDrop — 원래 뷰에 뷰어 칸이 남아 있으면 비지 않은 것으로 보고 닫지 않는다', async () => {
  const { S, D } = await loadDnd();
  const srcTabId = S.getActiveTabId();
  S.setPaneSession('sess-a', S.getActivePaneId());
  const viewerPane = S.splitActivePane('row');
  S.openViewerPane('a.txt', viewerPane);
  S.openLayoutTab({ label: 'other' });
  D.applyPaneDrop(S.getActivePaneId(), 'right', 'sess-a');
  assert.ok(S.getTabs().some((t) => t.id === srcTabId), '뷰어가 든 뷰는 남는다');
});

test('applyPaneDrop — 다른 탭의 pane에 떨어뜨려도 그 탭에 다른 멤버가 남아있으면 탭은 안 닫힌다', async () => {
  const { S, D } = await loadDnd();
  const sourceTabId = S.getActiveTabId();
  S.setPaneSession('sess-a', S.getActivePaneId());
  S.splitActivePane('row', 'sess-b'); // sourceTab = {sess-a, sess-b} 그룹

  const groupTabId = S.openLayoutTab({ label: 'group-tab' });
  const groupPaneId = S.getActivePaneId();
  D.applyPaneDrop(groupPaneId, 'right', 'sess-a'); // 그룹 중 sess-a만 다른 탭으로

  assert.deepStrictEqual(
    S.getTabs().map((t) => t.id).includes(sourceTabId), true,
    'sess-b가 남아있으므로 원래 탭은 닫히면 안 된다',
  );
  S.switchLayoutTab(sourceTabId);
  const remaining = S.getTree();
  const leaves = [remaining.a, remaining.b].filter(Boolean);
  assert.deepStrictEqual(leaves.map((l) => l.session).sort(), [null, 'sess-b'], 'sess-a 자리만 비고 sess-b는 그대로');
  assert.strictEqual(S.findTabBySessionId('sess-a'), groupTabId);
});

test('applyPaneDrop — 가장자리는 그 방향으로 분할한 새 leaf에 배정한다', async () => {
  const { S, D } = await loadDnd();
  const paneId = S.getActivePaneId();
  D.applyPaneDrop(paneId, 'right', 'sess-y');

  const tree = S.getTree();
  assert.strictEqual(tree.t, 'split');
  assert.strictEqual(tree.dir, 'row');
  assert.strictEqual(tree.a.id, paneId, '기존 pane은 그대로 a(왼쪽)에 남는다');
  assert.strictEqual(tree.b.session, 'sess-y', '새 leaf(오른쪽)에 드롭한 세션이 배정된다');
});

test('applyPaneDrop — left/top 드롭은 새 leaf가 먼저(a) 온다', async () => {
  const { S, D } = await loadDnd();
  const paneId = S.getActivePaneId();
  D.applyPaneDrop(paneId, 'left', 'sess-z');

  const tree = S.getTree();
  assert.strictEqual(tree.dir, 'row');
  assert.strictEqual(tree.a.session, 'sess-z', '새 leaf가 왼쪽(a)에 와야 한다');
  assert.strictEqual(tree.b.id, paneId, '기존 pane은 오른쪽(b)으로 밀려난다');
});

test('applyPaneDrop — sessionId가 없으면 아무 것도 하지 않는다', async () => {
  const { S, D } = await loadDnd();
  const before = S.getTree();
  D.applyPaneDrop(S.getActivePaneId(), 'center', null);
  assert.strictEqual(S.getTree(), before);
});

test('canSplit — pane 상한에 걸리면 가장자리 드롭도 분할하지 않는다', async () => {
  const { S, D } = await loadDnd();
  // splitActivePane은 게이트 없이 직접 트리를 조작하는 raw store 함수라 상한을
  // 무시하고 6개까지 만들 수 있다 — canSplit()이 그걸 보고 false를 내는지가
  // 이 테스트의 관심사(기본 jsdom innerWidth=1024는 새 체계에서 regular=4,
  // 6 > 4이니 이 경계에서도 여전히 false여야 함).
  let id = S.getActivePaneId();
  for (let i = 0; i < 5; i++) id = S.splitActivePane('row');
  assert.strictEqual(S.countLeaves(), 6);
  assert.strictEqual(D.canSplit(), false);

  const before = S.getTree();
  D.applyPaneDrop(id, 'right', 'sess-overflow');
  assert.strictEqual(S.getTree(), before, '상한 초과 시 트리가 바뀌지 않아야 한다');
});

// N16 — 10-shell-layout.md §3: compact/regular/wide/xwide 4구간. xwide(N4)는
// 상한이 없다(Infinity) — countLeaves()가 아무리 커져도 canSplit()이 계속
// true여야 하므로 숫자 비교 대신 isFinite로 확인한다.
test('tierCap — 폭 구간별 상한 2/4/6/무제한 (breakpoints.js와 같은 경계값)', async () => {
  const { D } = await loadDnd();
  const env = _doms[_doms.length - 1].window;
  const setW = (w) => Object.defineProperty(env, 'innerWidth', { value: w, configurable: true });
  setW(390);  assert.strictEqual(D.tierCap(), 2);
  setW(719);  assert.strictEqual(D.tierCap(), 2);
  setW(720);  assert.strictEqual(D.tierCap(), 4);
  setW(1279); assert.strictEqual(D.tierCap(), 4);
  setW(1280); assert.strictEqual(D.tierCap(), 6);
  setW(1599); assert.strictEqual(D.tierCap(), 6);
  setW(1600); assert.strictEqual(D.tierCap(), Infinity);
  setW(2560); assert.strictEqual(D.tierCap(), Infinity);
});

// ── N37 3단계 4/n — 세션 드래그 소스 ──────────────────────────────────────
// 탭 줄이 사라지면 레일 행이 유일한 드래그 소스가 된다. 배선 자체(dragstart가
// SESSION_MIME에 세션 id를 싣는지, id가 없으면 아예 시작하지 않는지)는 jsdom의
// 합성 이벤트로 확인 가능하다 — 실제 드래그 제스처만 실브라우저 몫이다.
function fakeDragEvent(win, type) {
  const e = new win.Event(type, { bubbles: true, cancelable: true });
  const data = {};
  e.dataTransfer = {
    setData: (mime, v) => { data[mime] = v; },
    getData: (mime) => data[mime],
    effectAllowed: null,
  };
  e._data = data;
  return e;
}

test('wireSessionDragSource — dragstart가 세션 id를 SESSION_MIME으로 싣는다', async () => {
  const { D } = await loadDnd();
  const dom = _doms[_doms.length - 1];
  const el = dom.window.document.createElement('div');
  dom.window.document.body.appendChild(el);
  D.wireSessionDragSource(el, () => 'sess-1');
  assert.strictEqual(el.draggable, true);
  const e = fakeDragEvent(dom.window, 'dragstart');
  el.dispatchEvent(e);
  assert.strictEqual(e._data[D.SESSION_MIME], 'sess-1');
  assert.ok(el.classList.contains('dragging'));
  el.dispatchEvent(fakeDragEvent(dom.window, 'dragend'));
  assert.ok(!el.classList.contains('dragging'));
});

test('wireSessionDragSource — 세션 id가 없으면 드래그를 시작하지 않는다', async () => {
  const { D } = await loadDnd();
  const dom = _doms[_doms.length - 1];
  const el = dom.window.document.createElement('div');
  dom.window.document.body.appendChild(el);
  D.wireSessionDragSource(el, () => null);
  const e = fakeDragEvent(dom.window, 'dragstart');
  el.dispatchEvent(e);
  assert.strictEqual(e.defaultPrevented, true);
  assert.strictEqual(e._data[D.SESSION_MIME], undefined);
  assert.ok(!el.classList.contains('dragging'));
});

// ── N4 3단계: dock 탭을 pane으로 끌어내기 ────────────────────────────────
// applyPaneDrop과 같은 5구역을 쓰되 center의 의미가 다르다 — 패널은
// "그 칸을 직접 겨냥한" 드롭이라 쪼개지 않고 교체한다(store.setPanePanel).
test('applyPanelDrop — center는 그 칸을 패널 칸으로 교체한다(터미널이 들어 있어도)', async () => {
  const { S, D } = await loadDnd();
  const paneId = S.getActivePaneId();
  S.setPaneSession('sess-a', paneId);
  assert.strictEqual(S.getTree().session, 'sess-a');

  assert.strictEqual(D.applyPanelDrop(paneId, 'center', 'queue'), true);
  const leaf = S.getTree();
  assert.strictEqual(leaf.kind, 'panel');
  assert.strictEqual(leaf.panel, 'queue');
  assert.strictEqual(leaf.session, null, '한 칸이 터미널이면서 동시에 패널일 수는 없다');
});

test('applyPanelDrop — 가장자리는 그 방향으로 쪼개고 새 leaf를 패널 칸으로 만든다', async () => {
  const { S, D } = await loadDnd();
  const paneId = S.getActivePaneId();
  assert.strictEqual(D.applyPanelDrop(paneId, 'bottom', 'ports'), true);

  const tree = S.getTree();
  assert.strictEqual(tree.t, 'split');
  assert.strictEqual(tree.dir, 'col');
  assert.strictEqual(tree.a.id, paneId, 'bottom이면 기존 pane이 위(a)에 남는다');
  assert.strictEqual(tree.b.kind, 'panel');
  assert.strictEqual(tree.b.panel, 'ports');
});

test('applyPanelDrop — 모르는 패널 이름은 트리에 들이지 않는다', async () => {
  const { S, D } = await loadDnd();
  const before = S.getTree();
  assert.strictEqual(D.applyPanelDrop(S.getActivePaneId(), 'center', 'nope'), false);
  assert.strictEqual(D.applyPanelDrop(S.getActivePaneId(), 'center', ''), false);
  assert.strictEqual(S.getTree(), before);
});

test('applyPanelDrop — pane 상한에 걸리면 가장자리 드롭도 분할하지 않는다', async () => {
  const { S, D } = await loadDnd();
  let id = S.getActivePaneId();
  for (let i = 0; i < 5; i++) id = S.splitActivePane('row');
  assert.strictEqual(D.canSplit(), false);

  const before = S.getTree();
  assert.strictEqual(D.applyPanelDrop(id, 'right', 'usage'), false);
  assert.strictEqual(S.getTree(), before);
});
