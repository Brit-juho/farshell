// 로그인 게이트의 OTP 6칸(시안 M10) — 한 칸에 한 자리, 다음 칸으로 넘어가기, 빈 칸에서 Backspace는 앞 칸,
// 여섯 자리 붙여넣기·자동 채우기는 한꺼번에. 제출 값은 숨은 #login-otp 하나이고, 오류 흐름(otp_invalid 등)은 그대로다.
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

async function boot(authReplies = []) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const posts = [];
  env.window.fetch = (url, opts) => {
    if (url.startsWith('/api/capabilities')) return json(401, {});
    if (url.startsWith('/api/auth/status')) return json(200, { protected: true, device_known: true });
    if (url === '/api/auth') {
      posts.push(JSON.parse(opts.body));
      const next = authReplies.shift() || [401, { error: 'invalid' }];
      return json(next[0], next[1]);
    }
    return json(404, {});
  };
  vm.runInContext(GATE_JS, env.context);
  for (let i = 0; i < 4; i++) await flush();
  const { document } = env;
  const boxes = [...document.querySelectorAll('#login-otp-boxes .login-otp-box')];
  const otp = document.getElementById('login-otp');
  const submit = () => {
    document.getElementById('login-pass').value = 'pw';
    document.getElementById('login-form').dispatchEvent(new env.window.Event('submit', { cancelable: true }));
  };
  // 한 칸에 글자를 넣는 것(브라우저가 하는 일) + input 이벤트
  const type = (box, value, inputType = 'insertText') => {
    box.value = value;
    const ev = new env.window.Event('input', { bubbles: true });
    Object.defineProperty(ev, 'inputType', { value: inputType });
    Object.defineProperty(ev, 'data', { value: inputType === 'insertText' ? value.slice(-1) : null });
    box.dispatchEvent(ev);
  };
  const key = (box, k) => {
    const ev = new env.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    box.dispatchEvent(ev);
    return ev;
  };
  const paste = (box, text) => {
    const ev = new env.window.Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'clipboardData', { value: { getData: () => text } });
    box.dispatchEvent(ev);
    return ev;
  };
  const active = () => boxes.indexOf(document.activeElement);
  return { ...env, posts, boxes, otp, submit, type, key, paste, active };
}

async function bootOtp(extra = []) {
  const env = await boot([[401, { error: 'otp_required' }], ...extra]);
  env.submit();
  for (let i = 0; i < 3; i++) await flush();
  await new Promise((r) => setTimeout(r, 60));   // gate.js가 50ms 뒤 첫 칸에 초점을 준다
  return env;
}

test('마크업 — 6칸 · 숫자 키패드 · 첫 칸만 one-time-code · 묶음 이름 · 숨은 제출 값 하나', async () => {
  const { document, boxes, otp } = await boot();
  assert.strictEqual(boxes.length, 6);
  for (const b of boxes) {
    assert.strictEqual(b.getAttribute('inputmode'), 'numeric');
    assert.strictEqual(b.getAttribute('pattern'), '[0-9]*');
  }
  assert.deepStrictEqual(boxes.map((b) => b.getAttribute('autocomplete')), ['one-time-code', 'off', 'off', 'off', 'off', 'off']);
  const group = document.getElementById('login-otp-boxes');
  assert.strictEqual(group.getAttribute('role'), 'group');
  assert.strictEqual(document.getElementById(group.getAttribute('aria-labelledby')).textContent, '인증 코드');
  assert.deepStrictEqual(boxes.map((b) => b.getAttribute('aria-label')), [1, 2, 3, 4, 5, 6].map((n) => `인증 코드 ${n}번째 자리`));
  assert.strictEqual(otp.type, 'hidden');
  assert.strictEqual(otp.name, 'vt-otp');
});

test('otp_required — 6칸이 열리고 첫 칸에 초점', async () => {
  const { document, active } = await bootOtp();
  assert.strictEqual(document.getElementById('login-otp-wrap').hidden, false);
  assert.strictEqual(active(), 0);
});

test('숫자를 치면 다음 칸으로, 숫자가 아니면 칸을 비운 채 머문다', async () => {
  const { boxes, otp, type, active } = await bootOtp();
  type(boxes[0], '4');
  assert.strictEqual(active(), 1);
  type(boxes[1], 'a');
  assert.strictEqual(boxes[1].value, '');
  assert.strictEqual(active(), 1);
  type(boxes[1], '8'); type(boxes[2], '2');
  assert.strictEqual(otp.value, '482');
  assert.strictEqual(active(), 3);
  // 이미 숫자가 있는 칸에 하나 더 친 경우 — 새 자리로 바꾼다(뒤로 밀지 않는다)
  type(boxes[0], '47');
  assert.strictEqual(boxes[0].value, '7');
  assert.strictEqual(otp.value, '782');
  // 마지막 칸은 머문다
  for (let i = 3; i < 6; i++) type(boxes[i], String(i));
  assert.strictEqual(otp.value, '782345');
  assert.strictEqual(active(), 5);
});

test('빈 칸에서 Backspace는 앞 칸으로 가서 그 자리를 지운다, 채워진 칸은 브라우저 기본 삭제', async () => {
  const { boxes, otp, type, key, active } = await bootOtp();
  type(boxes[0], '1'); type(boxes[1], '2'); type(boxes[2], '3');
  assert.strictEqual(active(), 3);
  const ev = key(boxes[3], 'Backspace');
  assert.strictEqual(ev.defaultPrevented, true);
  assert.strictEqual(active(), 2);
  assert.strictEqual(otp.value, '12');
  const ev2 = key(boxes[1], 'Backspace');   // 채워진 칸 — 막지 않는다
  assert.strictEqual(ev2.defaultPrevented, false);
  assert.strictEqual(key(boxes[0], 'Backspace').defaultPrevented, false, '첫 칸에서는 갈 곳이 없다');
  key(boxes[2], 'ArrowLeft');
  assert.strictEqual(active(), 1);
  key(boxes[1], 'ArrowRight');
  assert.strictEqual(active(), 2);
});

test('여섯 자리 붙여넣기 — 어느 칸에서든 처음부터 채운다, 숫자 아닌 글자는 버린다', async () => {
  const { boxes, otp, paste, active } = await bootOtp();
  const ev = paste(boxes[3], ' 246-813 ');
  assert.strictEqual(ev.defaultPrevented, true);
  assert.deepStrictEqual(boxes.map((b) => b.value), ['2', '4', '6', '8', '1', '3']);
  assert.strictEqual(otp.value, '246813');
  assert.strictEqual(active(), 5);
  // 짧은 붙여넣기는 그 칸부터
  boxes.forEach((b) => { b.value = ''; });
  paste(boxes[2], '99');
  assert.deepStrictEqual(boxes.map((b) => b.value), ['', '', '9', '9', '', '']);
  assert.strictEqual(active(), 4);
  // 숫자가 없으면 아무것도 바꾸지 않는다
  assert.strictEqual(paste(boxes[0], 'abc').defaultPrevented, true);
  assert.strictEqual(boxes[0].value, '');
});

test('자동 채우기 — 첫 칸에 여섯 자리가 한꺼번에 들어오면 나눠 담는다', async () => {
  const { boxes, otp, type } = await bootOtp();
  type(boxes[0], '135790', 'insertReplacementText');
  assert.deepStrictEqual(boxes.map((b) => b.value), ['1', '3', '5', '7', '9', '0']);
  assert.strictEqual(otp.value, '135790');
});

test('제출 — 칸을 이은 값 하나가 실리고, 6자리가 아니면 보내지 않는다', async () => {
  const { document, boxes, posts, submit, paste, type } = await bootOtp([[401, { error: 'otp_invalid', remaining: 4 }]]);
  type(boxes[0], '1');
  submit();
  await flush();
  assert.strictEqual(posts.length, 1, '1자리로는 보내지 않는다');
  assert.match(document.getElementById('login-err-msg').textContent, /6자리/);
  paste(boxes[0], '123456');
  submit();
  await flush(); await flush();
  assert.strictEqual(posts[1].otp, '123456');
});

test('otp_invalid — 남은 시도를 알리고 칸을 비운 뒤 첫 칸으로', async () => {
  const { document, boxes, otp, posts, submit, paste, active } = await bootOtp([[401, { error: 'otp_invalid', remaining: 4 }], [429, { error: 'otp_locked', retry_after: 600 }]]);
  paste(boxes[0], '111111');
  submit();
  await flush(); await flush();
  assert.match(document.getElementById('login-err-msg').textContent, /인증 코드가 올바르지 않습니다/);
  assert.match(document.getElementById('login-err-detail').textContent, /4/);
  assert.deepStrictEqual(boxes.map((b) => b.value), ['', '', '', '', '', '']);
  assert.strictEqual(otp.value, '');
  assert.strictEqual(active(), 0);
  paste(boxes[0], '222222');
  submit();
  await flush(); await flush();
  assert.match(document.getElementById('login-err-msg').textContent, /시도 횟수를 초과했습니다/);
  assert.deepStrictEqual(posts.map((p) => p.otp), [undefined, '111111', '222222']);
});
