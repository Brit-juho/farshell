// 로그인 게이트의 마감 — 서버가 연결은 받는데 답을 안 하던 사고에서 게이트가
// 「연결을 확인하는 중」에 영원히 멈췄다. 이 테스트가 지키는 것:
//   1. 확인이 마감을 넘기면 멈춘 링 대신 「서버가 응답하지 않습니다」 + 다시 시도.
//      게이트는 숨기지 않는다(숨기면 앱이 같은 서버에 요청을 더 쌓는다).
//   2. 네트워크 오류(거부)는 예전처럼 게이트를 숨긴다 — 오프라인 캐시 셸 위에서
//      도달 불가 배너가 말하게 하는 의도된 동작이다.
//   3. 다시 시도는 같은 확인을 다시 한다.
//   4. 비밀번호 제출이 마감을 넘기면 같은 문구로 알리고 버튼을 푼다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createDomEnv } = require('./helpers/dom-env');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const GATE_JS = fs.readFileSync(path.join(__dirname, '../js/gate.js'), 'utf8');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (status, body) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
// 끝나지 않는 요청. abort되면 브라우저처럼 AbortError로 거부한다.
function hang(opts) {
  return new Promise((_, reject) => {
    if (opts && opts.signal) opts.signal.addEventListener('abort', () => reject(new Error('AbortError')));
  });
}

// replies: url 접두사 → (opts, n) => Promise. 8초 마감은 20ms로 줄인다(그 외 타이머는 그대로).
function boot(replies, { search = '' } = {}) {
  const env = createDomEnv(INDEX_HTML, { url: `http://localhost/${search}` });
  _doms.push(env.dom);
  const w = env.window;
  const realSetTimeout = w.setTimeout.bind(w);
  w.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms >= 8000 ? 20 : ms, ...rest);
  const calls = [];
  w.fetch = (url, opts) => {
    calls.push({ url, opts });
    const n = calls.filter((c) => c.url === url).length;
    for (const [prefix, fn] of Object.entries(replies)) {
      if (url === prefix || url.startsWith(prefix + '?')) return fn(opts, n);
    }
    return json(404, {});
  };
  w.location.reload = () => {};
  vm.runInContext(GATE_JS, env.context);
  return { ...env, calls };
}

const $ = (document, id) => document.getElementById(id);

test('첫 확인이 마감을 넘기면 게이트를 숨기지 않고 다시 시도를 준다', async () => {
  const { document, window, calls } = boot({ '/api/capabilities': (opts) => hang(opts) });
  await wait(60);
  assert.strictEqual($(document, 'login-gate').hidden, false, '마감 초과로 게이트가 사라지면 안 된다');
  assert.strictEqual(window.__vtAuthed, false);
  assert.strictEqual($(document, 'login-spinner').hidden, true, '링이 계속 돌면 안 된다');
  assert.strictEqual($(document, 'login-form').hidden, true, '비밀번호 문제가 아니다');
  assert.strictEqual($(document, 'login-probe').hidden, false);
  assert.match($(document, 'login-probe-msg').textContent, /서버가 응답하지 않습니다/);
  assert.match($(document, 'login-probe-detail').textContent, /fsh restart/);
  assert.ok(calls[0].opts.signal && calls[0].opts.signal.aborted, '매달린 요청은 끊는다');
});

test('다시 시도는 같은 확인을 다시 하고, 이번에 답하면 그대로 진행한다', async () => {
  const { document, window, calls } = boot({
    '/api/capabilities': (opts, n) => (n === 1 ? hang(opts) : json(200, {})),
  });
  await wait(60);
  $(document, 'login-retry').click();
  assert.strictEqual($(document, 'login-probe').hidden, true);
  assert.strictEqual($(document, 'login-spinner').hidden, false, '다시 확인하는 동안은 링');
  await wait(10);
  assert.strictEqual(calls.filter((c) => c.url === '/api/capabilities').length, 2);
  assert.strictEqual($(document, 'login-gate').hidden, true);
  assert.strictEqual(window.__vtAuthed, true);
});

test('다시 시도 뒤 401이면 비밀번호 폼으로 간다', async () => {
  const { document } = boot({
    '/api/capabilities': (opts, n) => (n === 1 ? hang(opts) : json(401, {})),
    '/api/auth/status': () => json(200, { protected: true, device_known: true }),
  });
  await wait(60);
  $(document, 'login-retry').click();
  await wait(10);
  assert.strictEqual($(document, 'login-form').hidden, false);
  assert.strictEqual($(document, 'login-probe').hidden, true);
});

test('네트워크 오류(거부)는 예전처럼 게이트를 숨긴다 — 오프라인 셸 위에서 배너가 말한다', async () => {
  const { document, window } = boot({ '/api/capabilities': () => Promise.reject(new TypeError('Failed to fetch')) });
  await wait(10);
  assert.strictEqual($(document, 'login-gate').hidden, true);
  assert.strictEqual(window.__vtAuthed, true);
  assert.strictEqual($(document, 'login-probe').hidden, true);
});

test('기기 상태 확인이 매달려도 폼은 그대로 쓸 수 있다(기억 선택만 숨김)', async () => {
  const { document } = boot({
    '/api/capabilities': () => json(401, {}),
    '/api/auth/status': (opts) => hang(opts),
  });
  await wait(60);
  assert.strictEqual($(document, 'login-form').hidden, false);
  assert.strictEqual($(document, 'login-remember').hidden, true);
  assert.strictEqual($(document, 'login-err').hidden, true);
});

test('비밀번호 제출이 마감을 넘기면 알리고 버튼을 푼다', async () => {
  const { document } = boot({
    '/api/capabilities': () => json(401, {}),
    '/api/auth/status': () => json(200, { protected: true, device_known: true }),
    '/api/auth': (opts) => hang(opts),
  });
  await wait(10);
  $(document, 'login-pass').value = 'pw';
  $(document, 'login-form').dispatchEvent(new document.defaultView.Event('submit', { cancelable: true }));
  assert.strictEqual($(document, 'login-submit').disabled, true);
  await wait(60);
  assert.strictEqual($(document, 'login-err').hidden, false);
  assert.match($(document, 'login-err-msg').textContent, /서버가 응답하지 않습니다/);
  assert.match($(document, 'login-err-detail').textContent, /fsh restart/);
  assert.strictEqual($(document, 'login-submit').disabled, false, '다시 누를 수 있어야 한다');
});

test('QR 티켓 제출이 마감을 넘기면 다시 시도를 주고, 다시 시도는 티켓을 다시 낸다', async () => {
  const { document, calls } = boot({
    '/api/auth': (opts, n) => (n === 1 ? hang(opts) : json(200, {})),
  }, { search: '?ticket=abc' });
  await wait(60);
  assert.strictEqual($(document, 'login-probe').hidden, false);
  assert.strictEqual($(document, 'login-gate').hidden, false);
  $(document, 'login-retry').click();
  await wait(10);
  const posts = calls.filter((c) => c.url === '/api/auth');
  assert.strictEqual(posts.length, 2);
  assert.deepEqual(JSON.parse(posts[1].opts.body), { ticket: 'abc' });
});
