// 로그인 게이트의 "기기 기억" 선택 — 처음 보는 기기에서만 보이고, 고른 값이
// POST /api/auth에 실리며, OTP 2단계 재제출에도 그대로 남는다.
// 등록된 기기에서는 서버가 이 값을 무시하므로 화면도 숨긴다(보여 주면 거짓말).
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

const flush = () => new Promise((r) => setTimeout(r, 0));
const json = (status, body) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

async function boot({ status, authReplies = [] }) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const posts = [];
  env.window.fetch = (url, opts) => {
    if (url.startsWith('/api/capabilities')) return json(401, {});
    if (url.startsWith('/api/auth/status')) return status === 'fail' ? Promise.reject(new Error('net')) : json(200, status);
    if (url === '/api/auth') {
      posts.push(JSON.parse(opts.body));
      const next = authReplies.shift() || [401, { error: 'invalid' }];
      return json(next[0], next[1]);
    }
    return json(404, {});
  };
  vm.runInContext(GATE_JS, env.context);
  for (let i = 0; i < 4; i++) await flush();
  return { ...env, posts };
}

function submit(document, password = 'pw') {
  const pass = document.getElementById('login-pass');
  pass.value = password;
  document.getElementById('login-form').dispatchEvent(new document.defaultView.Event('submit', { cancelable: true }));
}

test('처음 보는 기기면 기간 선택이 보이고, 고른 값이 실린다', async () => {
  const { document, posts } = await boot({ status: { protected: true, device_known: false } });
  const wrap = document.getElementById('login-remember');
  assert.strictEqual(wrap.hidden, false);
  assert.strictEqual(wrap.querySelector('input:checked').value, '90d', '기본은 90일');
  wrap.querySelector('input[value="1h"]').checked = true;
  submit(document);
  await flush();
  assert.strictEqual(posts[0].remember, '1h');
});

test('등록된 기기면 숨기고 값을 보내지 않는다', async () => {
  const { document, posts } = await boot({ status: { protected: true, device_known: true } });
  assert.strictEqual(document.getElementById('login-remember').hidden, true);
  submit(document);
  await flush();
  assert.ok(!('remember' in posts[0]));
});

test('상태를 못 읽으면 숨긴 채로 두고 서버 기본에 맡긴다', async () => {
  const { document, posts } = await boot({ status: 'fail' });
  assert.strictEqual(document.getElementById('login-remember').hidden, true);
  submit(document);
  await flush();
  assert.ok(!('remember' in posts[0]));
});

test('OTP 2단계 재제출에도 같은 선택이 실린다', async () => {
  const { document, posts } = await boot({
    status: { protected: true, device_known: false },
    authReplies: [[401, { error: 'otp_required' }], [401, { error: 'otp_invalid', remaining: 4 }]],
  });
  document.querySelector('#login-remember input[value="24h"]').checked = true;
  submit(document);
  await flush(); await flush();
  assert.strictEqual(document.getElementById('login-otp-wrap').hidden, false);
  document.getElementById('login-otp').value = '123456';
  submit(document);
  await flush();
  assert.deepEqual(posts.map((p) => p.remember), ['24h', '24h']);
  assert.strictEqual(posts[1].otp, '123456');
});

test('invalid_remember는 비밀번호 오류와 다른 문구로 알린다', async () => {
  const { document } = await boot({
    status: { protected: true, device_known: false },
    authReplies: [[400, { error: 'invalid_remember' }]],
  });
  submit(document);
  await flush(); await flush();
  assert.match(document.getElementById('login-err-msg').textContent, /기기 기억 기간/);
});

// 입력 상태 표시 — Caps Lock·한글 입력 중일 때만 뜬다(사용자 요청 2026-10).
function key(document, type, init) {
  const KE = document.defaultView.KeyboardEvent;
  document.getElementById('login-pass').dispatchEvent(new KE(type, { bubbles: true, ...init }));
}

test('입력 상태 — 아무것도 안 눌렀으면 숨김, Caps Lock이면 표시, 끄면 사라짐', async () => {
  const { document } = await boot({ status: { protected: true, device_known: true } });
  const st = document.getElementById('login-pass-state');
  assert.strictEqual(st.hidden, true, '키를 누르기 전에는 알 수 없으니 말하지 않는다');
  key(document, 'keydown', { key: 'A', modifierCapsLock: true });
  assert.strictEqual(st.hidden, false);
  assert.match(st.textContent, /Caps Lock/);
  key(document, 'keydown', { key: 'a', modifierCapsLock: false });
  assert.strictEqual(st.hidden, true);
});

test('입력 상태 — 한글이 실제로 들어오면 표시, 영문 키가 오면 사라짐', async () => {
  const { document } = await boot({ status: { protected: true, device_known: true } });
  const st = document.getElementById('login-pass-state');
  const pass = document.getElementById('login-pass');
  // 모바일 키보드처럼 영문인데 조합 중으로 보고하는 경우 — 한글로 오판하지 않는다
  key(document, 'keydown', { key: 'Unidentified', keyCode: 229, isComposing: true });
  assert.strictEqual(st.hidden, true);
  pass.value = '가';
  pass.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  assert.strictEqual(st.hidden, false);
  assert.match(st.textContent, /한글 입력 중/);
  key(document, 'keydown', { key: 'r' });
  assert.strictEqual(st.hidden, true);
});

test('입력 상태 — Caps Lock과 한글이 같이면 둘 다 말한다, 칸을 떠나면 지운다', async () => {
  const { document } = await boot({ status: { protected: true, device_known: true } });
  const st = document.getElementById('login-pass-state');
  const pass = document.getElementById('login-pass');
  key(document, 'keydown', { key: 'Process', modifierCapsLock: true });
  pass.value = 'ㄱ';
  pass.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  assert.match(st.textContent, /Caps Lock.*한글 입력 중/);
  pass.dispatchEvent(new document.defaultView.FocusEvent('blur'));
  assert.strictEqual(st.hidden, true);
});
