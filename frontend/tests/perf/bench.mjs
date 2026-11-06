// 파일 뷰어 렌더 성능 측정 — 페이지 쪽. run.mjs 가 `window.perfRun(이름)`을 부른다.
//
// 지금 렌더러(`viewer/file.js` renderFile·refreshFile, `viewer/diff.js` _renderDiffDOM, `viewer/tree.js`
// showTree)를 **번들하지 않은 원본 모듈 그대로** 불러 잰다. 단계 2부터 파일 본문은 CodeMirror 6이다
// (`viewer/editor.js`). 옛 줄 단위 렌더러의 수치는 기준선 문서에 남아 있다.
//
// 재는 구간: 요청 시작(가짜 서버, 실제 HTTP + JSON 파싱) → DOM 생성 → 다음 페인트.
// 빠지는 것: 지연 청크 다운로드(편집기 모듈과 JS 언어 팩, hljs 스크립트는 측정 전에 미리 불러 둔다).
import '/static/js/core/env.js';   // window.API_BASE 등
import { renderFile, refreshFile, ensureHljs, loadEditor } from '/static/js/panels/viewer/file.js';
import { _renderDiffDOM } from '/static/js/panels/viewer/diff.js';
import { showTree } from '/static/js/panels/viewer/tree.js';
import { coalesce } from '/static/js/shell/fs-watch.js';

const TREE_ROOT = '/bench/tree';
const row = document.getElementById('bench-row');

// --- 요청·DOM 활동 추적 -----------------------------------------------------------------
let inflight = 0;
let fetchCount = 0;
const _fetch = window.fetch.bind(window);
window.fetch = async (...a) => {
  inflight += 1;
  fetchCount += 1;
  try { return await _fetch(...a); } finally { inflight -= 1; }
};
// 본문 읽기(JSON 파싱)도 진행 중으로 센다 — 헤더만 받고 끝난 것으로 보면, 감속 조건에서
// 파싱하는 사이에 "조용하다"고 잘못 판정한다.
const _json = Response.prototype.json;
Response.prototype.json = async function json() {
  inflight += 1;
  try { return await _json.call(this); } finally { inflight -= 1; }
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

/** 요청이 다 끝나고 DOM 이 `quiet` ms 동안 안 바뀔 때까지 기다린다. 마지막 DOM 변경 시각을 돌려준다. */
async function waitIdle(quiet = 150, limit = 20000) {
  const start = performance.now();
  for (;;) {
    await sleep(20);
    const now = performance.now();
    if (inflight === 0 && now - lastMutation >= quiet) return lastMutation;
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

async function openFile(path, n = 1) {
  const boxes = Array.from({ length: n }, box);
  const t0 = performance.now();
  await Promise.all(boxes.map((b) => renderFile(b.host, path)));
  const t1 = await nextPaint();
  // 편집기 청크를 못 받으면 file.js는 일반 텍스트로 대신 그린다 — 그 수치를 편집기 수치로 착각하지 않게 멈춘다.
  if (boxes.some((b) => !b.host.querySelector('.cm-editor'))) throw new Error('편집기(CodeMirror)로 그려지지 않았다');
  return { paintMs: Math.round(t1 - t0), ...(await common(t0, t1)) };
}

async function diff5000() {
  const { host } = box();
  const t0 = performance.now();
  const { diff } = await (await fetch('/__data/diff')).json();
  _renderDiffDOM(host, diff);
  const t1 = await nextPaint();
  return { paintMs: Math.round(t1 - t0), ...(await common(t0, t1)) };
}

// 트리: 5,000개 폴더(50 × 100). 열기 → 폴더 100개 펼치기 → 최상단 변경 알림 한 번에 다시 그리기.
async function tree() {
  const host = document.createElement('div');
  host.className = 'bench-box';
  row.appendChild(host);
  const t0 = performance.now();
  await showTree({ paneHost: host, paneId: 'bench' });
  const t1 = await nextPaint();
  const open = await common(t0, t1);

  // 펼치기 시간 = 클릭부터 그 클릭의 마지막 DOM 변경까지, 100번의 합(조용해지길 기다린 시간은 뺀다).
  let expandMs = 0;
  const click = async (path) => {
    const r = host.querySelector(`.vt-vw-trow[data-path="${path}"]`);
    if (!r) throw new Error(`행 없음: ${path}`);
    const c0 = performance.now();
    r.click();
    expandMs += (await waitIdle(30)) - c0;
  };
  for (let i = 0; i < 50; i += 1) await click(`${TREE_ROOT}/pkg-${String(i).padStart(2, '0')}`);
  for (let j = 0; j < 50; j += 1) await click(`${TREE_ROOT}/pkg-00/mod-${String(j).padStart(3, '0')}`);

  const expanded = host.querySelectorAll('.vt-vw-trow.open').length;
  if (expanded !== 100) throw new Error(`펼친 폴더가 100개가 아니다: ${expanded}`);
  const before = fetchCount;
  const r0 = performance.now();
  window.__fakeWsEmit([{ kind: 'dir', path: TREE_ROOT }]);
  const r1 = await waitIdle(150);
  const reload = longTasks(r0, r1 + 150);
  return {
    paintMs: Math.round(t1 - t0), ...open,
    expandMs: Math.round(expandMs),
    reloadMs: Math.round(r1 - r0),
    reloadLongTaskSum: reload.longTaskSum,
    reloadRequests: fetchCount - before,
    domNodes: document.getElementsByTagName('*').length,
  };
}

// 연속 변경: 열린 파일에 200ms 마다 변경 알림, 10초. 실제 자동 갱신과 같이 coalesce 로 묶어
// refreshFile 을 부른다(실행 중 온 알림은 끝난 뒤 한 번으로 합쳐진다).
async function live() {
  const path = '/bench/live.js';
  const { scroll, host } = box();
  await renderFile(host, path);
  await nextPaint();
  let refreshes = 0;
  const refresh = coalesce(async () => { await refreshFile(scroll, path, () => true); refreshes += 1; });
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
  const sorted = frames.slice().sort((a, b) => a - b);
  const p95 = sorted[Math.min(sorted.length - 1, Math.ceil(0.95 * sorted.length) - 1)] || 0;
  return {
    frameP95: Math.round(p95), frameMax: Math.round(sorted[sorted.length - 1] || 0),
    refreshes, ...(await common(t0, t1)),
  };
}

const SCENARIOS = {
  'open-512k': () => openFile('/bench/code-512k.js'),
  'open-4m': () => openFile('/bench/code-4m.js'),         // 서버가 앞 512KB 만 준다(지금 동작 그대로)
  'long-line-1m': () => openFile('/bench/long-1m.min.js'), // 같은 이유로 512KB 한 줄
  'concurrent-3': () => openFile('/bench/code-512k.js', 3),
  'diff-5000': diff5000,
  tree,
  'live-10s': live,
};

window.perfReady = (async () => {
  await ensureHljs();   // diff 시나리오가 쓴다
  const editor = await loadEditor();
  await editor.loadLanguage('javascript');   // 측정 파일이 모두 .js 다
  await nextPaint();
})();

window.perfRun = async (name) => {
  const fn = SCENARIOS[name];
  if (!fn) throw new Error(`알 수 없는 시나리오: ${name}`);
  return fn();
};
