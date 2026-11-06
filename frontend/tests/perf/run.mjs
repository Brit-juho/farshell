#!/usr/bin/env node
// 파일 뷰어 렌더 성능 측정 — 실행기. CI 에 넣지 않는다(실행 시간이 길고 기계마다 수치가 다르다).
//
//   node frontend/tests/perf/run.mjs                     # 전체: 데스크톱 + 모바일(CPU 4배 감속), 시나리오마다 10회
//   node frontend/tests/perf/run.mjs --runs 3 --only open-512k,diff-5000 --profile mobile
//   node frontend/tests/perf/run.mjs --out /tmp/perf.json
//
// 하는 일
// - 127.0.0.1 임의 포트에 작은 HTTP 서버를 띄운다. `/static/*`은 frontend/, `/perf/*`는 이 폴더,
//   `/api/fs/*`는 서버 응답 모양을 흉내 낸 가짜다(입력은 gen.mjs 가 만든다 — 실제 코드·경로를 쓰지 않는다).
//   FarShell 서버, tmux, ~/.vt 는 건드리지 않는다.
// - headless Chrome 을 임시 프로필로 띄우고 DevTools 프로토콜(CDP)로 조작한다. 의존성 없음(Node 22+의 WebSocket).
//   Chrome 경로는 `--chrome` 또는 환경변수 `CHROME`(기본: macOS 표준 위치).
// - 실행마다 새 탭을 연다(캐시·힙이 이전 실행에 섞이지 않게). 결과는 중앙값과 p95(가장 가까운 순위).
//
// 화면 CSS 는 `frontend/dist/app.css`(npm run build 결과)를 쓴다. 없으면 경고하고 CSS 없이 잰다.
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { genCode, genDiff, genLongLine, genTree } from './gen.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..', '..');

// --- 인자 ---------------------------------------------------------------------------
const args = process.argv.slice(2);
function opt(name, def) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
}
const RUNS = Math.max(1, parseInt(opt('runs', '10'), 10) || 10);
const PROFILES = opt('profile', 'desktop,mobile').split(',').filter(Boolean);
const ONLY = opt('only', '').split(',').filter(Boolean);
const OUT = opt('out', '');
const CHROME = opt('chrome', process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');

const SCENARIOS = ['open-512k', 'open-4m', 'long-line-1m', 'concurrent-3', 'diff-5000', 'tree', 'live-10s'];

// 시나리오별 측정 시간 제한(ms) — live 는 10초를 돈다.
const TIMEOUT = { 'live-10s': 60000 };

const PROFILE_DEF = {
  desktop: { width: 1280, height: 800, scale: 1, mobile: false, cpu: 1 },
  mobile: { width: 390, height: 844, scale: 3, mobile: true, cpu: 4 },
};

// --- 가짜 서버 데이터 -----------------------------------------------------------------
// 서버 상한을 그대로 흉내 낸다(server/fsguard.py MAX_BYTES·MAX_ENTRIES, routes/files.py 미리 받기 상한).
const MAX_BYTES = 512 * 1024;
const MAX_ENTRIES = 1000;
const MAX_PREFETCH_DIRS = 150;
const MAX_PREFETCH_CHILD_ENTRIES = 200;
const TREE_ROOT = '/bench/tree';

const FILES = new Map([
  ['/bench/code-512k.js', genCode(512 * 1024, 11)],
  ['/bench/code-4m.js', genCode(4 * 1024 * 1024, 12)],
  ['/bench/long-1m.min.js', genLongLine(1024 * 1024, 13)],
  ['/bench/live.js', genCode(512 * 1024 - 64, 14)],
]);
const DIFF = genDiff(5000, 15);
const TREE = genTree(TREE_ROOT);
let liveTick = 0;

function fileResponse(path) {
  let text = FILES.get(path);
  if (text == null) return null;
  // 연속 변경 시나리오: 요청마다 마지막 줄이 바뀐다(서버가 매번 새 내용을 준다).
  if (path === '/bench/live.js') text += `// tick ${liveTick++}\n`;
  const buf = Buffer.from(text, 'utf8');
  const cut = buf.subarray(0, MAX_BYTES);
  return {
    path, size: buf.length, binary: false, image: false,
    truncated: buf.length > cut.length, content: cut.toString('utf8'),
  };
}

function listDir(path) {
  const all = TREE.get(path);
  if (!all) return null;
  const entries = all.slice(0, MAX_ENTRIES).map((e) => ({ ...e }));
  return { path, entries, truncated: all.length > MAX_ENTRIES };
}

// routes/files.py `_list_dir_deep` 와 같은 순서·예산으로 미리 받기를 채운다.
function listDeep(path, depth) {
  const root = listDir(path);
  if (!root || !depth) return root;
  const budget = [MAX_PREFETCH_DIRS];
  const fill = (listing, base, left) => {
    if (left <= 0) return;
    for (const e of listing.entries) {
      if (!e.dir || budget[0] <= 0) continue;
      budget[0] -= 1;
      const child = `${base}/${e.name}`;
      const sub = listDir(child);
      if (!sub || sub.entries.length > MAX_PREFETCH_CHILD_ENTRIES) continue;
      e.children = sub;
      fill(sub, child, left - 1);
    }
  };
  fill(root, path, depth);
  return root;
}

const counts = new Map();
function count(key) { counts.set(key, (counts.get(key) || 0) + 1); }

const MIME = { '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json' };

function sendJson(res, code, body) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(code, { 'content-type': 'application/json', 'content-length': data.length, 'cache-control': 'no-store' });
  res.end(data);
}

function serveStatic(res, base, rel) {
  const full = normalize(join(base, rel));
  if (!full.startsWith(base + sep)) { res.writeHead(403); res.end(); return; }
  let st;
  try { st = statSync(full); } catch { res.writeHead(404); res.end(); return; }
  if (!st.isFile()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': MIME[extname(full)] || 'application/octet-stream', 'cache-control': 'no-store' });
  res.end(readFileSync(full));
}

const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const p = url.pathname;
  if (p === '/api/fs/file') {
    count('file');
    const out = fileResponse(url.searchParams.get('path') || '');
    return out ? sendJson(res, 200, out) : sendJson(res, 404, { error: 'not found' });
  }
  if (p === '/api/fs/tree') {
    count('tree');
    const out = listDeep(url.searchParams.get('path') || '', parseInt(url.searchParams.get('depth') || '0', 10));
    return out ? sendJson(res, 200, out) : sendJson(res, 404, { error: 'not a directory' });
  }
  if (p === '/api/fs/roots') return sendJson(res, 200, { roots: [TREE_ROOT] });
  if (p === '/__data/diff') { count('diff'); return sendJson(res, 200, { diff: DIFF }); }
  if (p === '/__counts') return sendJson(res, 200, Object.fromEntries(counts));
  if (p === '/__reset') { counts.clear(); return sendJson(res, 200, {}); }
  if (p.startsWith('/static/')) return serveStatic(res, FRONTEND, p.slice('/static/'.length));
  if (p.startsWith('/perf/')) return serveStatic(res, HERE, p.slice('/perf/'.length));
  return sendJson(res, 404, { error: 'not found' });
});

// --- CDP ----------------------------------------------------------------------------
class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { ok, fail } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) fail(new Error(`${msg.error.message} ${msg.error.data || ''}`));
        else ok(msg.result);
      } else if (msg.method) {
        for (const l of this.listeners) l(msg);
      }
    };
  }
  open() {
    return new Promise((ok, fail) => { this.ws.onopen = ok; this.ws.onerror = () => fail(new Error('CDP 연결 실패')); });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    return new Promise((ok, fail) => this.pending.set(id, { ok, fail }));
  }
  once(method, sessionId) {
    return new Promise((ok) => {
      const l = (msg) => {
        if (msg.method === method && (!sessionId || msg.sessionId === sessionId)) {
          this.listeners = this.listeners.filter((x) => x !== l);
          ok(msg.params);
        }
      };
      this.listeners.push(l);
    });
  }
  close() { try { this.ws.close(); } catch { /* 이미 닫힘 */ } }
}

async function launchChrome() {
  if (!existsSync(CHROME)) throw new Error(`Chrome 을 찾을 수 없습니다: ${CHROME} (--chrome 또는 CHROME 로 지정)`);
  const profile = mkdtempSync(join(tmpdir(), 'fsh-perf-chrome-'));
  const proc = spawn(CHROME, [
    '--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--enable-precise-memory-info', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', 'about:blank',
  ], { stdio: 'ignore' });
  const portFile = join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 100 && !existsSync(portFile); i += 1) await new Promise((r) => setTimeout(r, 100));
  if (!existsSync(portFile)) { proc.kill(); throw new Error('Chrome 디버깅 포트를 얻지 못했습니다'); }
  const [port, path] = readFileSync(portFile, 'utf8').trim().split('\n');
  const cdp = new Cdp(`ws://127.0.0.1:${port}${path}`);
  await cdp.open();
  const stop = () => {
    cdp.close();
    proc.kill();
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome 이 아직 쥐고 있으면 남는다 */ }
  };
  return { cdp, stop };
}

async function runOnce(cdp, base, profile, scenario) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  try {
    const def = PROFILE_DEF[profile];
    await cdp.send('Page.enable', {}, sessionId);
    await cdp.send('Runtime.enable', {}, sessionId);
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: def.width, height: def.height, deviceScaleFactor: def.scale, mobile: def.mobile,
    }, sessionId);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: def.cpu }, sessionId);
    const loaded = cdp.once('Page.loadEventFired', sessionId);
    await cdp.send('Page.navigate', { url: `${base}/perf/bench.html` }, sessionId);
    await loaded;
    await fetch(`${base}/__reset`);
    const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
      expression: `window.perfReady.then(() => window.perfRun(${JSON.stringify(scenario)}))`,
      awaitPromise: true, returnByValue: true, timeout: TIMEOUT[scenario] || 30000,
    }, sessionId);
    if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
    const reqs = await (await fetch(`${base}/__counts`)).json();
    return { ...result.value, requests: Object.values(reqs).reduce((a, b) => a + b, 0) };
  } finally {
    await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
  }
}

function pct(sorted, p) {
  if (!sorted.length) return null;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[i];
}

function summarize(runs) {
  const keys = Object.keys(runs[0]).filter((k) => typeof runs[0][k] === 'number');
  const out = {};
  for (const k of keys) {
    const v = runs.map((r) => r[k]).filter((x) => typeof x === 'number').sort((a, b) => a - b);
    out[k] = { median: pct(v, 0.5), p95: pct(v, 0.95) };
  }
  return out;
}

const COLS = [
  ['paintMs', '첫 페인트(ms)'],
  ['longTaskSum', 'long task 합(ms)'],
  ['longTaskMax', 'long task 최대(ms)'],
  ['frameP95', '프레임 간격 p95(ms)'],
  ['frameMax', '프레임 간격 최대(ms)'],
  ['refreshes', '다시 그리기(회)'],
  ['expandMs', '폴더 100개 펼치기(ms)'],
  ['reloadMs', '변경 1회 다시 그리기(ms)'],
  ['reloadLongTaskSum', '다시 그리기 long task(ms)'],
  ['reloadRequests', '다시 그리기 요청(회)'],
  ['domNodes', 'DOM 노드'],
  ['heapMB', 'JS 힙(MB)'],
  ['requests', '요청(회)'],
];

function fmt(x) {
  if (x == null) return '—';
  return Number.isInteger(x) ? String(x) : x.toFixed(1);
}

function table(results) {
  const lines = [];
  for (const profile of Object.keys(results)) {
    const def = PROFILE_DEF[profile];
    lines.push(`\n### ${profile} (${def.width}×${def.height}, CPU ${def.cpu}배 감속, ${RUNS}회, 값은 중앙값 / p95)\n`);
    lines.push(`| 시나리오 | ${COLS.map((c) => c[1]).join(' | ')} |`);
    lines.push(`|---|${COLS.map(() => '---').join('|')}|`);
    for (const [scenario, s] of Object.entries(results[profile])) {
      const cells = COLS.map(([k]) => (s[k] ? `${fmt(s[k].median)} / ${fmt(s[k].p95)}` : '—'));
      lines.push(`| ${scenario} | ${cells.join(' | ')} |`);
    }
  }
  return lines.join('\n');
}

async function main() {
  if (!existsSync(join(FRONTEND, 'dist', 'app.css'))) {
    console.warn('경고: frontend/dist/app.css 가 없어 CSS 없이 잽니다(레이아웃 비용이 실제와 다르다). 먼저 npm run build.');
  }
  await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
  const base = `http://127.0.0.1:${server.address().port}`;
  const { cdp, stop } = await launchChrome();
  const results = {};
  const raw = {};
  try {
    const version = await cdp.send('Browser.getVersion');
    console.log(`Chrome ${version.product}, Node ${process.version}, runs=${RUNS}`);
    for (const profile of PROFILES) {
      if (!PROFILE_DEF[profile]) throw new Error(`알 수 없는 프로필: ${profile}`);
      results[profile] = {};
      raw[profile] = {};
      for (const scenario of SCENARIOS) {
        if (ONLY.length && !ONLY.includes(scenario)) continue;
        const runs = [];
        for (let i = 0; i < RUNS; i += 1) runs.push(await runOnce(cdp, base, profile, scenario));
        raw[profile][scenario] = runs;
        results[profile][scenario] = summarize(runs);
        process.stderr.write(`  ${profile} ${scenario} 완료\n`);
      }
    }
    console.log(table(results));
    if (OUT) {
      writeFileSync(OUT, JSON.stringify({
        date: new Date().toISOString(), chrome: version.product, node: process.version, runs: RUNS, results, raw,
      }, null, 2));
      console.log(`\n원자료: ${OUT}`);
    }
  } finally {
    stop();
    server.close();
  }
}

main().catch((e) => { console.error(e.stack || String(e)); process.exit(1); });
