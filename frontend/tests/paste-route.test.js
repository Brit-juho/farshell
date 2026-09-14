// N24(2.1.5 2/n) — term/clipboard.js의 sendPaste. 클라이언트는 더 이상
// 마커를 붙이지 않는다(term.paste() 호출 삭제) — WS `{"type":"paste"}`로
// 원문만 보내고, 마커·개행·제어문자 판단은 서버가 한다(server/paste_prepare.py).
//
// 여기서 확인하는 것: WS가 열려 있으면 그쪽으로, 아니면(끊김/재연결 중)
// HTTP `/api/sessions/{id}/paste`로 대체 전송하는지 — 붙여넣기는 사용자가
// 직접 한 행동이라 sendToPty처럼 조용히 유실되면 안 된다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const STORE_JS = path.join(__dirname, '../js/core/store.js');
const CLIPBOARD_JS = path.join(__dirname, '../js/term/clipboard.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = 'http://localhost:7777';
  env.window.showToast = () => {};
  const cache = new Map();
  const S = await importFresh(STORE_JS, env.context, cache);
  const C = await importFresh(CLIPBOARD_JS, env.context, cache);
  return { window: env.window, S, C };
}

class FakeOpenSocket {
  constructor() { this.readyState = 1; this.sent = []; } // 1 = OPEN
  send(data) { this.sent.push(data); }
}

test('sendPaste — WS가 열려 있으면 paste 제어 메시지 하나로 보낸다(마커는 안 붙인다)', async () => {
  const { S, C } = await load();
  const sock = new FakeOpenSocket();
  S.registerSession('a', { ws: sock });
  await C.sendPaste('a', 'echo hi\nline2');
  assert.strictEqual(sock.sent.length, 1);
  const msg = JSON.parse(sock.sent[0]);
  assert.strictEqual(msg.type, 'paste');
  assert.strictEqual(msg.text, 'echo hi\nline2', '클라이언트는 원문을 그대로 보낸다 — 마커·개행 변환은 서버 몫');
});

test('sendPaste — 빈 텍스트는 아무 것도 안 보낸다', async () => {
  const { S, C } = await load();
  const sock = new FakeOpenSocket();
  S.registerSession('a', { ws: sock });
  await C.sendPaste('a', '');
  assert.strictEqual(sock.sent.length, 0);
});

test('sendPaste — WS가 안 열려 있으면 HTTP 대체 경로로 보낸다(조용히 버리지 않는다)', async () => {
  const { window, S, C } = await load();
  const closedSock = { readyState: 3, send: () => { throw new Error('OPEN 아닌데 send됨'); } }; // 3 = CLOSED
  S.registerSession('a', { ws: closedSock });
  const calls = [];
  window.fetch = (url, opts) => {
    calls.push({ url, opts });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  await C.sendPaste('a', 'hello');
  assert.strictEqual(calls.length, 1);
  assert.match(calls[0].url, /\/api\/sessions\/a\/paste$/);
  assert.strictEqual(calls[0].opts.method, 'POST');
  assert.strictEqual(JSON.parse(calls[0].opts.body).text, 'hello');
});

test('sendPaste — 세션 자체가 없어도 죽지 않는다(HTTP 폴백 경로로 조용히 시도)', async () => {
  const { window, C } = await load();
  let called = false;
  window.fetch = () => { called = true; return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
  await assert.doesNotReject(() => C.sendPaste('no-such-session', 'x'));
  assert.strictEqual(called, true);
});

test('sendToPty(키 입력 전용)는 그대로 wsHandle을 쓴다 — sendPaste와 분리된 경로', async () => {
  const { S, C } = await load();
  const sent = [];
  const handle = { readyState: 1, send: (b) => sent.push(b) };
  S.registerSession('a', { wsHandle: handle });
  C.sendToPty('a', '\x1b[A'); // 위 화살표 키 시퀀스 — 진짜 키 입력
  assert.strictEqual(sent.length, 1);
  // realm이 갈리므로 instanceof 대신 생성자 이름으로 본다(session-order.test.js와
  // 같은 이유) — 문자열/JSON이 아니라 인코딩된 바이트라는 것만 확인하면 된다.
  assert.strictEqual(typeof sent[0], 'object');
  assert.strictEqual(sent[0].constructor.name, 'Uint8Array', '바이트로 보낸다(JSON 아님)');
});
