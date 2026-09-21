// R6 — term/upload.js. 업로드 구현이 **하나**라는 것이 이 파일의 관심사다:
// `#file-input`·터미널 칸 드롭·파일 패널 드롭 셋이 같은 함수를 타므로, 여기서
// 고정하는 규칙(순차 전송 · 경로 삽입 여부 · 폴더 제외 · 실패 격리)이 세
// 진입점 모두에 그대로 적용된다.
//
// 네트워크는 fetch를 갈아 끼워 가짜로 만든다 — 실제 업로드는 e2e의 몫이고,
// 여기서 보려는 건 "몇 번, 어떤 순서로, 무엇을 붙여넣는가"다.
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

/** upload.js와 그 협력자(store·clipboard)를 **같은 모듈 그래프**로 불러온다 —
 *  따로 부르면 upload.js가 보는 싱글톤과 테스트가 조작하는 싱글톤이 갈라진다
 *  (layout-dnd.test.js의 loadDnd()와 같은 이유). */
async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  const calls = [];
  env.window.fetch = async (url, init) => {
    calls.push({ url: String(url), body: init && init.body });
    const name = init && init.body && typeof init.body.get === 'function'
      ? (init.body.get('file') || {}).name : null;
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true, id: `id-${calls.length}`, path: `/files/${name || calls.length}`, size: 1 }),
    };
  };
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
  const { env, U } = await load();
  const orig = env.window.fetch;
  let n = 0;
  env.window.fetch = async (url, init) => {
    n += 1;
    if (n === 2) return { ok: false, status: 413, json: async () => ({ error: 'file too large', max_mb: 100 }) };
    return orig(url, init);
  };
  const files = ['a.txt', 'big.bin', 'c.txt'].map((x) => mkFile(env, x));
  const done = await U.uploadFiles(files, { sessionId: 's', insertPath: false });
  assert.strictEqual(done.length, 2, '실패한 하나만 빠져야 한다');
  assert.strictEqual(done.map((d) => d.path).join(' '), '/files/a.txt /files/c.txt');
  assert.strictEqual(n, 3, '실패 뒤에도 세 번째 요청이 나가야 한다');
});

test('uploadFiles — 빈 입력은 요청을 만들지 않는다', async () => {
  const { U, calls } = await load();
  assert.strictEqual((await U.uploadFiles(null)).length, 0);
  assert.strictEqual((await U.uploadFiles([])).length, 0);
  assert.strictEqual(calls.length, 0);
});
