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
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { genCode, genDiff, genLongLine, genTree } from './gen.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..', '..');
const NODE_MODULES = resolve(FRONTEND, '..', 'node_modules');

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

const SCENARIOS = ['open-512k', 'open-4m', 'open-12m', 'long-line-1m', 'concurrent-3', 'diff-5000', 'tree',
  'live-10s', 'live-same-10s', 'live-4m-10s', 'live-grow-4m-10s'];

// 시나리오별 측정 시간 제한(ms) — live 는 10초를 돈다.
const TIMEOUT = { 'live-10s': 60000, 'live-same-10s': 60000, 'live-4m-10s': 60000, 'live-grow-4m-10s': 60000, 'open-12m': 60000 };

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
  ['/bench/code-12m.js', genCode(12 * 1024 * 1024, 16)],
  ['/bench/long-1m.min.js', genLongLine(1024 * 1024, 13)],
  ['/bench/live.js', genCode(512 * 1024 - 64, 14)],
  ['/bench/live-same.js', genCode(512 * 1024 - 64, 17)],
  ['/bench/live-4m.js', genCode(4 * 1024 * 1024 - 64, 18)],
  ['/bench/live-grow.log', genCode(4 * 1024 * 1024 - 64, 19)],
]);
const BYTES = new Map([...FILES].map(([k, v]) => [k, Buffer.from(v, 'utf8')]));
const DIFF = genDiff(5000, 15);
const TREE = genTree(TREE_ROOT);
let liveTick = 0;

// `/api/fs/file` 흉내. 범위 인자가 없으면 옛 모양(앞 512KB, truncated), 있으면 2판(API.md): 1MB 구간을 창 안의
// 마지막 `\n`에서 자르고(없으면 문자 경계에서 자르고 line_truncated), tail, rev·ETag·304, expected_rev → 409.
// 판(rev): 보통 파일은 고정. live.js 는 요청마다 마지막 줄이 바뀐다(옛 측정과 같다). live-4m.js 는 200ms 마다
// 바뀐다(구간을 이어 받는 사이에 바뀌면 409). live-same.js 는 바뀌지 않는다(알림만 오고 내용은 같음 → 304).
// live-grow.log 는 자라는 빌드 로그다 — 200ms 마다 끝에 한 줄이 붙고(앞은 그대로), rev 는 서버와 같은
// `dev:ino:size:mtime_ns` 모양이다.
const RANGE_MAX = 1024 * 1024;
let liveBigBase = 0;

function fileVersion(path) {
  const base = BYTES.get(path);
  if (!base) return null;
  if (path === '/bench/live.js') {
    const n = liveTick++;
    return { rev: `live:${n}`, bytes: Buffer.concat([base, Buffer.from(`// tick ${n}\n`)]) };
  }
  if (path === '/bench/live-4m.js') {
    const n = Math.floor((Date.now() - liveBigBase) / 200);
    return { rev: `big:${n}`, bytes: Buffer.concat([base, Buffer.from(`// tick ${n}\n`)]) };
  }
  if (path === '/bench/live-grow.log') {
    const n = Math.floor((Date.now() - liveBigBase) / 200);
    let tail = '';
    for (let i = 0; i < n; i += 1) tail += `[build] step ${i} ok\n`;
    const bytes = Buffer.concat([base, Buffer.from(tail)]);
    return { rev: `1:42:${bytes.length}:${n}`, bytes };
  }
  return { rev: `static:${path}`, bytes: base };
}

// UTF-8 문자 경계로 물린다(이어지는 바이트 0b10xxxxxx 앞에서 자르지 않는다).
function charBoundary(buf, end) {
  let e = end;
  while (e > 0 && e < buf.length && (buf[e] & 0xc0) === 0x80) e -= 1;
  return e;
}

function fileResponse(path, q, ifNoneMatch) {
  const v = fileVersion(path);
  if (!v) return null;
  const { rev, bytes } = v;
  const size = bytes.length;
  const enc = q.get('encoding') || 'auto';
  const head = { path, size, rev, binary: false, image: false, encoding: 'utf-8', encoding_confident: true, bom: false };
  const hasRange = q.has('offset') || q.has('length');
  const tail = q.has('tail') ? Number(q.get('tail')) : null;
  if (!hasRange && tail == null) {
    const cut = bytes.subarray(0, MAX_BYTES);
    return { status: 200, body: { ...head, truncated: size > cut.length, content: cut.toString('utf8') } };
  }
  const offset = Number(q.get('offset') || 0);
  const length = Number(q.get('length') || RANGE_MAX);
  const etag = `W/"${rev}|${enc}|${tail != null ? `t${tail}` : `${offset}+${length}`}"`;
  const expected = q.get('expected_rev');
  if (expected && expected !== rev) return { status: 409, body: { error: 'changed', rev, reason: 'changed' } };
  if (ifNoneMatch && ifNoneMatch === etag) return { status: 304, etag };
  if (tail != null) {
    let start = Math.max(0, size - tail);
    let lineTruncated = false;
    if (start > 0) {
      const i = bytes.indexOf(0x0a, start - 1);
      if (i >= 0 && i + 1 < size) start = i + 1;
      else { lineTruncated = true; start = charBoundary(bytes, start); }
    }
    return { status: 200, etag, body: { ...head, content: bytes.subarray(start).toString('utf8'), offset: start, length: size - start,
      next_offset: null, eof: true, line_truncated: lineTruncated, truncated: false, tail: true } };
  }
  let end = Math.min(size, offset + length);
  let lineTruncated = false;
  if (end < size) {
    const i = bytes.lastIndexOf(0x0a, end - 1);
    if (i >= offset) end = i + 1;
    else { lineTruncated = true; end = charBoundary(bytes, end); }
  }
  const eof = end >= size;
  return { status: 200, etag, body: { ...head, content: bytes.subarray(offset, end).toString('utf8'), offset, length: end - offset,
    next_offset: eof ? null : end, eof, line_truncated: lineTruncated, truncated: !eof } };
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

// --- 편집기 모듈(단계 2) --------------------------------------------------------------
// 측정 페이지는 번들하지 않은 원본 모듈을 그대로 부른다. editor.js가 쓰는 `@codemirror/*` 같은 맨 이름 import를
// 브라우저가 풀 수 있게 import map을 만들어 bench.html에 끼운다(`/node_modules/*`는 이 서버가 그대로 준다).
// 대상은 CodeMirror와 그 의존 패키지뿐이다.
function importMap() {
  const imports = {};
  const add = (name, dir) => {
    let pkg;
    try { pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8')); } catch { return; }
    let e = pkg.exports;
    if (e && typeof e === 'object' && e['.']) e = e['.'];
    const entry = typeof e === 'string' ? e : (e && (e.import || e.default)) || pkg.module;
    if (entry && !String(entry).includes('*')) imports[name] = `/node_modules/${name}/${String(entry).replace(/^\.\//, '')}`;
    // @codemirror/legacy-modes처럼 하위 경로만 내보내는 패키지(`./mode/*`).
    const sub = pkg.exports && pkg.exports['./mode/*'];
    if (sub) {
      for (const f of readdirSync(join(dir, 'mode'))) {
        if (f.endsWith('.js')) imports[`${name}/mode/${f.slice(0, -3)}`] = `/node_modules/${name}/mode/${f}`;
      }
    }
  };
  for (const scope of ['@codemirror', '@lezer', '@marijn']) {
    const d = join(NODE_MODULES, scope);
    if (!existsSync(d)) continue;
    for (const n of readdirSync(d)) add(`${scope}/${n}`, join(d, n));
  }
  for (const n of ['crelt', 'style-mod', 'w3c-keyname']) add(n, join(NODE_MODULES, n));
  return JSON.stringify({ imports });
}

function serveBench(res) {
  const html = readFileSync(join(HERE, 'bench.html'), 'utf8')
    .replace('<!--IMPORTMAP-->', `<script type="importmap">${importMap()}</script>`);
  res.writeHead(200, { 'content-type': 'text/html', 'cache-control': 'no-store' });
  res.end(html);
}

const counts = new Map();
function count(key) { counts.set(key, (counts.get(key) || 0) + 1); }

const MIME = { '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json' };

function sendJson(res, code, body, extra = {}) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(code, { 'content-type': 'application/json', 'content-length': data.length, 'cache-control': 'no-store', ...extra });
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
    const out = fileResponse(url.searchParams.get('path') || '', url.searchParams, req.headers['if-none-match']);
    if (!out) return sendJson(res, 404, { error: 'not found' });
    if (out.status === 304) { count('file304'); res.writeHead(304, { etag: out.etag, 'cache-control': 'private, no-cache' }); res.end(); return; }
    if (out.status === 409) count('file409');
    if (out.body && typeof out.body.content === 'string') counts.set('fileBytes', (counts.get('fileBytes') || 0) + Buffer.byteLength(out.body.content));
    return sendJson(res, out.status, out.body, out.etag ? { etag: out.etag } : {});
  }
  if (p === '/__live-big-reset') { liveBigBase = Date.now(); return sendJson(res, 200, {}); }
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
  if (p === '/perf/bench.html') return serveBench(res);
  if (p.startsWith('/perf/')) return serveStatic(res, HERE, p.slice('/perf/'.length));
  if (p.startsWith('/node_modules/')) return serveStatic(res, NODE_MODULES, p.slice('/node_modules/'.length));
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
    // 304·409 는 요청 수에 이미 들어 있다(따로 보이기만 한다).
    const { file304 = 0, file409 = 0, fileBytes = 0, ...rest } = reqs;
    return {
      ...result.value, requests: Object.values(rest).reduce((a, b) => a + b, 0), notModified: file304, changed409: file409,
      fetchedKB: Math.round(fileBytes / 1024),
    };
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
  ['refreshes', '다시 읽기(회)'],
  ['redraws', '내용 바뀜(회)'],
  ['expandMs', '폴더 100개 펼치기(ms)'],
  ['reloadMs', '변경 1회 다시 그리기(ms)'],
  ['reloadLongTaskSum', '다시 그리기 long task(ms)'],
  ['reloadRequests', '다시 그리기 요청(회)'],
  ['loadedKB', '받은 본문(KB)'],
  ['fetchedKB', '내려받은 본문 합(KB)'],
  ['liveKB', '연속 변경 중 내려받은 본문(KB)'],
  ['notModified', '304(회)'],
  ['changed409', '409(회)'],
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
