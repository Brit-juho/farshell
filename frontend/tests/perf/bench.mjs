// 파일 뷰어 렌더 성능 측정 — 페이지 쪽. run.mjs 가 `window.perfRun(이름)`을 부른다.
//
// 지금 렌더러(`viewer/file.js` renderFile·refreshFile, `viewer/diff.js` _renderDiffDOM)를 **번들하지 않은 원본 모듈
// 그대로** 불러 잰다. 단계 2부터 파일 본문은 CodeMirror 6이다(`viewer/editor.js`). 옛 줄 단위 렌더러의 수치는 기준선
// 문서에 남아 있다. 파일 트리는 단계 5부터 Solid + TS(`frontend/js/files/`)라 run.mjs가 묶은 `/perf-build/tree-bench.js`
// 를 부른다(옛 트리 `viewer/tree.js` showTree의 수치는 session-view-measurements.md 7.5에 남아 있다).
//
// 재는 구간: 요청 시작(가짜 서버, 실제 HTTP + JSON 파싱) → DOM 생성 → 다음 페인트.
// 빠지는 것: 지연 청크 다운로드(편집기 모듈과 JS 언어 팩은 측정 전에 미리 불러 둔다). 단계 9부터 diff 하이라이트도
// 같은 언어 팩을 쓴다(hljs 제거).
import '/static/js/core/env.js';   // window.API_BASE 등
import * as viewerFile from '/static/js/panels/viewer/file.js';
import { _renderDiffDOM } from '/static/js/panels/viewer/diff.js';
import { coalesce, watchPath } from '/static/js/shell/fs-watch.js';

const { renderFile, refreshFile, loadEditor } = viewerFile;
// 자동 갱신의 최소 간격(file.js refreshGap) — 그것이 없던 판과 비교해 잴 수 있게 없으면 0.
const refreshGap = viewerFile.refreshGap || (() => 0);
const TREE_ROOT = '/bench/tree';
const row = document.getElementById('bench-row');

// --- 요청·DOM 활동 추적 -----------------------------------------------------------------
let inflight = 0;
let fetchCount = 0;
let lastNet = 0;   // 마지막 요청(본문 읽기 포함)이 끝난 시각 — 다시 받았지만 바뀐 행이 없어 DOM이 그대로일 때의 끝
const _fetch = window.fetch.bind(window);
window.fetch = async (...a) => {
  inflight += 1;
  fetchCount += 1;
  try { return await _fetch(...a); } finally { inflight -= 1; lastNet = performance.now(); }
};
// 본문 읽기(JSON 파싱)도 진행 중으로 센다 — 헤더만 받고 끝난 것으로 보면, 감속 조건에서
// 파싱하는 사이에 "조용하다"고 잘못 판정한다.
const _json = Response.prototype.json;
Response.prototype.json = async function json() {
  inflight += 1;
  try { return await _json.call(this); } finally { inflight -= 1; lastNet = performance.now(); }
};
let lastMutation = performance.now();
new MutationObserver(() => { lastMutation = performance.now(); })
  .observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 다음 페인트가 끝난 시각(rAF 뒤 첫 태스크). */
function nextPaint() {
  return new Promise((r) => requestAnimationFrame(() => {
    const ch = new MessageChannel();
    ch.port1.onmessage = () => r(performance.now());
    ch.port2.postMessage(0);
  }));
}

/** 요청이 다 끝나고 DOM 이 `quiet` ms 동안 안 바뀔 때까지 기다린다. 마지막 DOM 변경 시각을 돌려준다.
 * `withNet`이면 마지막 요청이 끝난 시각과 둘 중 늦은 쪽(다시 받았는데 바뀐 행이 없으면 DOM이 그대로다). */
async function waitIdle(quiet = 150, limit = 20000, withNet = false) {
  const start = performance.now();
  for (;;) {
    await sleep(20);
    const now = performance.now();
    if (inflight === 0 && now - lastMutation >= quiet) return withNet ? Math.max(lastMutation, lastNet) : lastMutation;
    if (now - start > limit) throw new Error('waitIdle 시간 초과');
  }
}

function longTasks(t0, t1) {
  const hit = window.__longTasks.filter((e) => e.start >= t0 - 1 && e.start <= t1);
  return {
    longTaskSum: Math.round(hit.reduce((a, e) => a + e.dur, 0)),
    longTaskMax: Math.round(hit.reduce((a, e) => Math.max(a, e.dur), 0)),
  };
}

async function common(t0, t1) {
  await sleep(100);   // long task 관찰자 콜백이 도착할 시간
  return {
    ...longTasks(t0, t1),
    domNodes: document.getElementsByTagName('*').length,
    heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e5) / 10 : null,
  };
}

function box() {
  const scroll = document.createElement('div');
  scroll.className = 'bench-box';
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  scroll.appendChild(host);
  row.appendChild(scroll);
  return { scroll, host };
}

/** 편집기에 들어간 글자 수(KB) — 단계 3부터 큰 파일은 앞 4MB까지 이어 받는다. */
function loadedKB(host) {
  const h = host._vtEditor;
  return h ? Math.round(h.view.state.doc.length / 1024) : null;
}

async function openFile(path, n = 1) {
  const boxes = Array.from({ length: n }, box);
  const t0 = performance.now();
  await Promise.all(boxes.map((b) => renderFile(b.host, path)));
  const t1 = await nextPaint();
  // 편집기 청크를 못 받으면 file.js는 일반 텍스트로 대신 그린다 — 그 수치를 편집기 수치로 착각하지 않게 멈춘다.
  if (boxes.some((b) => !b.host.querySelector('.cm-editor'))) throw new Error('편집기(CodeMirror)로 그려지지 않았다');
  return { paintMs: Math.round(t1 - t0), loadedKB: loadedKB(boxes[0].host), ...(await common(t0, t1)) };
}

async function diff5000() {
  const { host } = box();
  const t0 = performance.now();
  const { diff } = await (await fetch('/__data/diff')).json();
  _renderDiffDOM(host, diff);
  // 단계 9부터 문맥 줄 하이라이트가 언어 팩(Promise)을 거쳐 온다 — 칠해진 뒤의 페인트까지 잰다(옛 hljs는 동기였다).
  if (host._vtDiff) await host._vtDiff.highlighted;
  const t1 = await nextPaint();
  const rows = host.querySelectorAll('.vt-vw-dl').length;
  if (!host.querySelector('.vt-vw-tx [class^="tok-"], .vt-vw-tx [class^="hljs-"]')) throw new Error('diff 문맥 줄이 하이라이트되지 않았다');
  return { paintMs: Math.round(t1 - t0), domRows: rows, ...(await common(t0, t1)) };
}

// --- 파일 트리(단계 5, files/mount.tsx) ---------------------------------------------------
let treeMod = null;
async function mountTree() {
  if (!treeMod) treeMod = await import('/perf-build/tree-bench.js');
  const host = document.createElement('div');
  host.className = 'bench-box';
  host.style.display = 'flex';
  host.style.flexDirection = 'column';
  row.appendChild(host);
  const mounted = treeMod.mountFileTree(host, {
    host: 'local', remote: false,
    fetchJSON: async (p) => { const r = await fetch(p); if (!r.ok) throw new Error(String(r.status)); return r.json(); },
    watch: watchPath,
    fetchHostLabel: async () => 'bench',
    openFile: () => {}, insertPath: () => true, copyPath: async () => true, sharePath: () => {},
    terminalCwd: async () => null, sessionName: () => '', recent: () => [], clearRecent: () => {},
    toast: () => {}, getDense: () => false, subscribeSettings: () => () => {},
  });
  return { host, mounted };
}
const treeRows = (host) => host.querySelectorAll('.vt-ft-row').length;

// 트리: 5,000개 폴더(50 × 100). 열기 → 폴더 100개 펼치기 → 최상단 변경 알림 한 번에 다시 그리기.
// 가상 스크롤이라 화면 밖 행은 DOM에 없다 — 펼치기는 행 클릭 대신 같은 처리(store.toggle)를 부른다.
async function tree() {
  const { host, mounted } = await mountTree();
  const { store } = mounted;
  const t0 = performance.now();
  await store.open(TREE_ROOT);
  // 알림(마이크로태스크)이 화면을 동기로 고친 뒤에 이 줄이 돈다 — 다음 페인트까지가 첫 화면이다.
  const t1 = await nextPaint();
  const open = await common(t0, t1);

  let expandMs = 0;
  const toggle = async (path) => {
    const c0 = performance.now();
    await store.toggle(path);
    expandMs += (await waitIdle(30, 20000, true)) - c0;
  };
  for (let i = 0; i < 50; i += 1) await toggle(`${TREE_ROOT}/pkg-${String(i).padStart(2, '0')}`);
  for (let j = 0; j < 50; j += 1) await toggle(`${TREE_ROOT}/pkg-00/mod-${String(j).padStart(3, '0')}`);
  if (store.expanded.size !== 100) throw new Error(`펼친 폴더가 100개가 아니다: ${store.expanded.size}`);
  const before = fetchCount;
  const r0 = performance.now();
  window.__fakeWsEmit([{ kind: 'tree', path: TREE_ROOT }], { dirs: [''] });
  const r1 = await waitIdle(150, 20000, true);
  const reload = longTasks(r0, r1 + 150);
  return {
    paintMs: Math.round(t1 - t0), ...open,
    expandMs: Math.round(expandMs),
    reloadMs: Math.round(r1 - r0),
    reloadLongTaskSum: reload.longTaskSum,
    reloadRequests: fetchCount - before,
    rowsShown: treeRows(host),
    domNodes: document.getElementsByTagName('*').length,
  };
}

// 파일 5,000개짜리 폴더 하나를 트리 최상단으로 연다. 첫 화면(첫 페이지 1000개)까지, 「더 불러오기」로 5,000개를
// 다 받기까지, 그 뒤 폴더 변경 알림 한 번(받아 둔 5페이지를 다시 받는다).
async function tree5k() {
  const { host, mounted } = await mountTree();
  const { store } = mounted;
  const BIG = '/bench/big';
  const t0 = performance.now();
  await store.open(BIG);
  // 알림(마이크로태스크)이 화면을 동기로 고친 뒤에 이 줄이 돈다 — 다음 페인트까지가 첫 화면이다.
  const t1 = await nextPaint();
  const open = await common(t0, t1);
  const l0 = performance.now();
  for (let n = 0; n < 10; n += 1) {
    const d = store.dirs.get(store.key(BIG));
    if (!d || !d.next) break;
    await store.loadMore(BIG);
  }
  const l1 = await waitIdle(30, 20000, true);
  const d = store.dirs.get(store.key(BIG));
  if (!d || d.entries.length !== 5000) throw new Error(`5,000개를 다 받지 못했다: ${d && d.entries.length}`);
  const before = fetchCount;
  const r0 = performance.now();
  window.__fakeWsEmit([{ kind: 'tree', path: BIG }], { dirs: [''] });
  const r1 = await waitIdle(150, 20000, true);
  const reload = longTasks(r0, r1 + 150);
  return {
    paintMs: Math.round(t1 - t0), ...open, rowsShown: treeRows(host),
    loadAllMs: Math.round(l1 - l0),
    reloadMs: Math.round(r1 - r0), reloadLongTaskSum: reload.longTaskSum, reloadRequests: fetchCount - before,
    domNodes: document.getElementsByTagName('*').length,
  };
}

// 연속 변경: 열린 파일에 200ms 마다 변경 알림, 10초. 실제 자동 갱신과 같이 coalesce 로 묶어
// refreshFile 을 부른다(실행 중 온 알림은 끝난 뒤 한 번으로 합쳐진다).
// - live.js: 요청마다 마지막 줄이 바뀐다(512KB, 구간 하나).
// - live-same.js: 알림만 오고 파일은 그대로 — 조건부 요청이 304 를 받아 다시 그리지 않아야 한다.
// - live-4m.js: 4MB, 200ms 마다 바뀐다 — 1MB 구간을 이어 받는 사이에 바뀌면 409 로 처음부터 다시 받는다.
// - live-grow.log: 4MB 빌드 로그, 200ms 마다 끝에 한 줄. 커진 끝만 이어 받는지(내려받은 본문 합) 본다.
// 자동 갱신과 같이 큰 칸은 최소 간격(refreshGap)을 둔다.
async function live(path) {
  if (path === '/bench/live-4m.js' || path === '/bench/live-grow.log') await fetch('/__live-big-reset');
  const { scroll, host } = box();
  await renderFile(host, path);
  await nextPaint();
  let refreshes = 0;
  let redraws = 0;
  const h = host._vtEditor;
  if (h) {
    const set = h.setText;
    h.setText = (t) => { const changed = set(t); if (changed) redraws += 1; return changed; };
    if (h.replaceEnd) {
      const rep = h.replaceEnd;
      h.replaceEnd = (n, t) => { const changed = rep(n, t); if (changed) redraws += 1; return changed; };
    }
  }
  // 연속 변경 10초 동안 내려받은 본문(여는 데 든 것은 뺀다). 가짜 서버가 응답 content 바이트를 센다.
  const bytesNow = async () => ((await (await fetch('/__counts')).json()).fileBytes || 0);
  const b0 = await bytesNow();
  const refresh = coalesce(async () => { await refreshFile(scroll, path, () => true); refreshes += 1; },
    { minGap: () => refreshGap(scroll) });
  const frames = [];
  let running = true;
  let prev = performance.now();
  const loop = (ts) => {
    frames.push(ts - prev);
    prev = ts;
    if (running) requestAnimationFrame(loop);
  };
  const t0 = performance.now();
  requestAnimationFrame((ts) => { prev = ts; requestAnimationFrame(loop); });
  const timer = setInterval(refresh, 200);
  await sleep(10000);
  clearInterval(timer);
  running = false;
  const t1 = performance.now();
  await waitIdle(150, 30000).catch(() => {});
  const liveKB = Math.round((await bytesNow() - b0) / 1024);
  const sorted = frames.slice().sort((a, b) => a - b);
  const p95 = sorted[Math.min(sorted.length - 1, Math.ceil(0.95 * sorted.length) - 1)] || 0;
  return {
    frameP95: Math.round(p95), frameMax: Math.round(sorted[sorted.length - 1] || 0),
    refreshes, redraws, loadedKB: loadedKB(host), liveKB, ...(await common(t0, t1)),
  };
}

const SCENARIOS = {
  'open-512k': () => openFile('/bench/code-512k.js'),
  'open-4m': () => openFile('/bench/code-4m.js'),         // 단계 3부터 1MB 구간 4개로 4MB 전체
  'open-12m': () => openFile('/bench/code-12m.js'),       // 앞 4MB + 「더 보기」
  'long-line-1m': () => openFile('/bench/long-1m.min.js'), // 1MB 넘는 한 줄 — 첫 구간이 line_truncated, 다음 구간이 이어 붙는다
  'concurrent-3': () => openFile('/bench/code-512k.js', 3),
  'diff-5000': diff5000,
  tree,
  'tree-5k': tree5k,
  'live-10s': () => live('/bench/live.js'),
  'live-same-10s': () => live('/bench/live-same.js'),
  'live-4m-10s': () => live('/bench/live-4m.js'),
  'live-grow-4m-10s': () => live('/bench/live-grow.log'),
};

window.perfReady = (async () => {
  const editor = await loadEditor();
  await editor.loadLanguage('javascript');   // 측정 파일이 모두 .js 다
  await nextPaint();
})();

window.perfRun = async (name) => {
  const fn = SCENARIOS[name];
  if (!fn) throw new Error(`알 수 없는 시나리오: ${name}`);
  return fn();
};
