// R6 — term/upload.js. 업로드 구현이 **하나**라는 것이 이 파일의 관심사다:
// `#file-input`·터미널 칸 드롭·파일 패널 드롭 셋이 같은 함수를 타므로, 여기서
// 고정하는 규칙(순차 전송 · 경로 삽입 여부 · 폴더 제외 · 실패 격리)이 세
// 진입점 모두에 그대로 적용된다.
//
// 네트워크는 XMLHttpRequest를 갈아 끼워 가짜로 만든다(fetch가 아닌 이유는
// upload.js 머리말 참고 — 업로드 진행률은 xhr.upload로만 나온다). 실제 업로드는
// e2e의 몫이고, 여기서 보려는 건 "몇 번, 어떤 순서로, 무엇을 붙여넣는가"다.
//
// ⚠ 배열 비교에 deepStrictEqual을 쓰지 않는다: 반환 배열은 vm context 안에서
// 만들어져 이 파일의 Array와 **realm이 다르고**, deepStrictEqual은 프로토타입까지
// 보기 때문에 내용이 같아도 떨어진다. 이름·경로를 이어 붙인 문자열로 비교한다
// — 순서까지 같이 보는 건 그대로다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const UPLOAD_JS = path.join(__dirname, '../js/term/upload.js');
const STORE_JS = path.join(__dirname, '../js/core/store.js');
const CLIPBOARD_JS = path.join(__dirname, '../js/term/clipboard.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

/** 기본 응답기 — 성공 하나(`ok:true`)를 흉내 낸다. 테스트가 특정 호출만 다르게
 *  답하고 싶으면(예: 두 번째 요청만 413) 자신만의 responder를 넘긴다. */
function defaultResponder({ index, name }) {
  return { status: 200, json: { ok: true, id: `id-${index}`, path: `/files/${name || index}`, size: 1 } };
}

/** `env.window.XMLHttpRequest`를 갈아 끼운다 — upload.js가 실제로 부르는 면
 *  (open/setRequestHeader/upload.addEventListener/addEventListener/send)만
 *  흉내 낸다. `send()`는 마이크로태스크에서 responder를 불러 `load` 핸들러를
 *  깨운다(진짜 XHR처럼 비동기). */
function installFakeXHR(env, calls, responder = defaultResponder) {
  class FakeXHR {
    constructor() {
      this._handlers = {};
      this._uploadHandlers = {};
      this.upload = { addEventListener: (ev, cb) => { this._uploadHandlers[ev] = cb; } };
    }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(k, v) { this.headers = this.headers || {}; this.headers[k] = v; }
    addEventListener(ev, cb) { this._handlers[ev] = cb; }
    // 취소 테스트용 — 진짜 xhr처럼 abort 핸들러를 깨우고, 이미 끝난 요청은
    // 무시한다(브라우저 계약과 같다).
    abort() {
      if (this._done) return;
      this._done = true;
      if (this._handlers.abort) this._handlers.abort();
    }
    send(body) {
      calls.push({ url: this.url, body });
      const name = body && typeof body.get === 'function' ? (body.get('file') || {}).name : null;
      if (this._uploadHandlers.progress) {
        this._uploadHandlers.progress({ lengthComputable: true, loaded: 1, total: 1 });
      }
      Promise.resolve().then(() => {
        if (this._done) return;   // abort()가 먼저 왔다
        this._done = true;
        const r = responder({ url: this.url, body, name, index: calls.length });
        this.status = r.status;
        this.responseText = JSON.stringify(r.json);
        if (this._handlers.load) this._handlers.load();
      });
    }
  }
  env.window.XMLHttpRequest = FakeXHR;
}

/** upload.js와 그 협력자(store·clipboard)를 **같은 모듈 그래프**로 불러온다 —
 *  따로 부르면 upload.js가 보는 싱글톤과 테스트가 조작하는 싱글톤이 갈라진다
 *  (layout-dnd.test.js의 loadDnd()와 같은 이유). */
async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  const calls = [];
  installFakeXHR(env, calls);
  const S = await importFresh(STORE_JS, env.context, cache);
  const C = await importFresh(CLIPBOARD_JS, env.context, cache);
  const U = await importFresh(UPLOAD_JS, env.context, cache);
  return { env, U, S, C, calls };
}

/** jsdom의 File. DataTransfer는 jsdom에 없어서 필요한 면만 흉내 낸다 —
 *  upload.js가 실제로 읽는 것은 `types` · `items` · `files` 셋뿐이다. */
function fakeDT(files, { withEntries = true, dirs = [] } = {}) {
  const items = files.map((f) => ({
    kind: 'file',
    getAsFile: () => f,
    ...(withEntries ? { webkitGetAsEntry: () => ({ isDirectory: dirs.includes(f.name) }) } : {}),
  }));
  return { types: ['Files'], items, files, getData: () => '' };
}

function mkFile(env, name) {
  return new env.window.File([name], name, { type: 'text/plain' });
}

test('hasFiles — 앱 내부 드래그(세션·dock 탭)와 OS 파일 드래그를 가른다', async () => {
  const { U } = await load();
  assert.strictEqual(U.hasFiles({ types: ['Files'] }), true);
  assert.strictEqual(U.hasFiles({ types: ['text/vt-tab-id'] }), false);
  assert.strictEqual(U.hasFiles({ types: ['text/vt-panel-name'] }), false);
  assert.strictEqual(U.hasFiles(null), false);
  assert.strictEqual(U.hasFiles({}), false);
});

test('filesFromDataTransfer — 폴더는 빼고 파일만 남긴다', async () => {
  const { env, U } = await load();
  const a = mkFile(env, 'a.txt');
  const folder = mkFile(env, 'src');
  const b = mkFile(env, 'b.txt');
  const got = U.filesFromDataTransfer(fakeDT([a, folder, b], { dirs: ['src'] }));
  assert.strictEqual(got.map((f) => f.name).join(','), 'a.txt,b.txt');
});

test('filesFromDataTransfer — webkitGetAsEntry가 없는 환경에서는 files를 그대로 쓴다', async () => {
  const { env, U } = await load();
  const a = mkFile(env, 'a.txt');
  const got = U.filesFromDataTransfer(fakeDT([a], { withEntries: false }));
  assert.strictEqual(got.map((f) => f.name).join(','), 'a.txt');
});

test('uploadFiles — 여러 개를 요청 하나씩 순차로 보낸다(서버가 파일 1개/요청)', async () => {
  const { env, U, calls } = await load();
  const files = ['a.txt', 'b.txt', 'c.txt'].map((n) => mkFile(env, n));
  const done = await U.uploadFiles(files, { sessionId: 'sess-1', insertPath: false });
  assert.strictEqual(done.length, 3);
  assert.strictEqual(calls.length, 3, '요청 수가 파일 수와 같아야 한다');
  for (const c of calls) assert.ok(c.url.includes('/api/upload?session_id=sess-1'), c.url);
});

test('uploadFiles — DataTransfer를 그대로 받아도 된다(지연 청크용 브리지 계약)', async () => {
  const { env, U, calls } = await load();
  const dt = fakeDT([mkFile(env, 'a.txt'), mkFile(env, 'dir')], { dirs: ['dir'] });
  await U.uploadFiles(dt, { sessionId: 's', insertPath: false });
  assert.strictEqual(calls.length, 1, '폴더는 걸러지고 파일 하나만 올라가야 한다');
});

test('uploadFiles — 성공한 것만, 올린 순서대로 돌려준다', async () => {
  const { env, U } = await load();
  // 경로 삽입 자체(sendPaste)는 살아 있는 WebSocket이 있어야 관찰되므로 실브라우저
  // 검증(e2e)의 몫이다. 여기서는 그 삽입에 **무엇이 실릴지**를 고정한다 —
  // 반환된 순서가 곧 붙여넣는 순서다(`done.map(d => d.path).join(' ')`).
  const files = ['a.txt', 'b.txt'].map((n) => mkFile(env, n));
  const done = await U.uploadFiles(files, { sessionId: 'sess-1', insertPath: true });
  assert.strictEqual(done.map((d) => d.path).join(' '), '/files/a.txt /files/b.txt');
});

test('uploadFiles — 하나가 실패해도 나머지는 계속 올라간다', async () => {
  const { env, U, calls } = await load();
  installFakeXHR(env, calls, (ctx) => (ctx.index === 2
    ? { status: 413, json: { error: 'file too large', max_mb: 100 } }
    : defaultResponder(ctx)));
  const files = ['a.txt', 'big.bin', 'c.txt'].map((x) => mkFile(env, x));
  const done = await U.uploadFiles(files, { sessionId: 's', insertPath: false });
  assert.strictEqual(done.length, 2, '실패한 하나만 빠져야 한다');
  assert.strictEqual(done.map((d) => d.path).join(' '), '/files/a.txt /files/c.txt');
  assert.strictEqual(calls.length, 3, '실패 뒤에도 세 번째 요청이 나가야 한다');
});

test('uploadFiles — 빈 입력은 요청을 만들지 않는다', async () => {
  const { U, calls } = await load();
  assert.strictEqual((await U.uploadFiles(null)).length, 0);
  assert.strictEqual((await U.uploadFiles([])).length, 0);
  assert.strictEqual(calls.length, 0);
});

// 2026-09-23 — vt:upload-progress는 이제 "다시 그려라" 신호일 뿐 페이로드가
// 없다(파일 상단 주석). 상태는 항상 U.uploadJobs()로 읽는다 — 이벤트가 올
// 때마다 그 시점의 스냅샷을 기록해 phase 흐름을 검증한다.
test('uploadFiles — 첨부 패널이 uploadJobs()로 진행 상태를 읽는다(queued→uploading→saving→done)', async () => {
  const { env, U } = await load();
  const phases = [];
  env.window.document.addEventListener('vt:upload-progress', () => {
    const jobs = U.uploadJobs();
    phases.push(jobs.length ? jobs[0].phase : 'gone');
  });
  await U.uploadFiles([mkFile(env, 'a.txt')], { sessionId: 's', insertPath: false });
  assert.deepStrictEqual(phases, ['queued', 'uploading', 'saving', 'gone']);
});

test('uploadFiles — 실패한 파일은 목록에서 빠진다(진행 행이 남지 않게)', async () => {
  const { env, U, calls } = await load();
  installFakeXHR(env, calls, () => ({ status: 413, json: { error: 'file too large', max_mb: 1 } }));
  await U.uploadFiles([mkFile(env, 'big.bin')], { sessionId: 's', insertPath: false });
  assert.strictEqual(U.uploadJobs().length, 0);
});

test('uploadFiles — 여러 개를 올리면 나머지는 queued로 대기열에 보인다', async () => {
  const { env, U } = await load();
  let sawQueuedDuringFirstUpload = false;
  env.window.document.addEventListener('vt:upload-progress', () => {
    const jobs = U.uploadJobs();
    if (jobs.some((j) => j.phase === 'uploading') && jobs.some((j) => j.phase === 'queued')) {
      sawQueuedDuringFirstUpload = true;
    }
  });
  await U.uploadFiles(['a.txt', 'b.txt'].map((n) => mkFile(env, n)), { sessionId: 's', insertPath: false });
  assert.strictEqual(sawQueuedDuringFirstUpload, true);
});

test('cancelUpload — 대기 중인 파일은 아예 요청을 보내지 않는다', async () => {
  const { env, U, calls } = await load();
  env.window.document.addEventListener('vt:upload-progress', () => {
    const uploading = U.uploadJobs().find((j) => j.phase === 'uploading');
    const queued = U.uploadJobs().find((j) => j.phase === 'queued');
    if (uploading && queued) U.cancelUpload(queued.clientId);
  });
  const files = ['a.txt', 'b.txt'].map((n) => mkFile(env, n));
  const done = await U.uploadFiles(files, { sessionId: 's', insertPath: false });
  assert.strictEqual(done.length, 1, '취소된 두 번째 파일은 요청조차 나가지 않아야 한다');
  assert.strictEqual(calls.length, 1);
});

test('cancelUpload — 업로드 중인 파일은 abort되고 경로가 삽입되지 않는다', async () => {
  const { env, U, calls } = await load();
  // 진행 이벤트가 오면(=uploading으로 전환됐으면) 바로 취소한다. FakeXHR.send()가
  // progress를 동기로 한 번 쏘므로, 이 시점엔 아직 load가 안 왔다(load는
  // Promise.resolve().then()이라 마이크로태스크 한 틀 뒤).
  env.window.document.addEventListener('vt:upload-progress', () => {
    const j = U.uploadJobs().find((x) => x.phase === 'uploading' || x.phase === 'saving');
    if (j) U.cancelUpload(j.clientId);
  });
  const done = await U.uploadFiles([mkFile(env, 'a.txt')], { sessionId: 's', insertPath: false });
  assert.strictEqual(done.length, 0);
  assert.strictEqual(U.uploadJobs().length, 0);
});

test('cancelUpload — 알 수 없는 clientId나 이미 끝난 작업은 조용히 0을 반환한다', async () => {
  const { U } = await load();
  assert.strictEqual(U.cancelUpload('no-such-id'), 0);
  assert.strictEqual(U.cancelUpload('all'), 0);
});
