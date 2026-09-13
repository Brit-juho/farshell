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
