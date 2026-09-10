// ADR-26/N35 — panels/viewer/*를 지연 청크로 뺀 뒤 실제로 재현됐던 버그의
// 회귀 테스트: Vite lib 모드는 entry를 항상 완전히 인라인하므로, 지연 로드된
// 모듈이 core/store.js 등을 **직접 import**하면 공유가 아니라 복제가
// 일어난다(같은 이름의 별개 싱글톤 두 벌). panels/viewer/tree.js는 그래서
// activeSession/activeSessionId/sendToPty를 core/store.js·term/clipboard.js
// 직접 import 대신 window 브리지(두 파일이 이미 노출해 두는 것)로만 참조한다
// — 이 테스트는 "동적으로 로드된 뷰어 코드가 진짜 활성 세션을 본다"를 실제로
// 검증한다. 정적 import로 되돌리면(=복제가 재발하면) 이 테스트가 그대로 잡는다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');
const CLIPBOARD_JS = path.join(__dirname, '../js/term/clipboard.js');
const VIEWER_LAZY_JS = path.join(__dirname, '../js/panels/viewer-lazy.js');

class FakeWebSocket {
  constructor() { this.readyState = FakeWebSocket.OPEN; this.sent = []; }
  send(data) { this.sent.push(data); }
}
FakeWebSocket.OPEN = 1;

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const { window } = env;
  window.fetch = async () => ({ ok: true, json: async () => ({ roots: [] }) });
  // tree.js가 showToast를 bare identifier(ui/toast.js의 window 브리지 관행,
  // 이 테스트는 그 파일을 안 불러오므로 최소 스텁을 직접 심어둔다)로 부른다.
  window.showToast = () => {};

  const cache = new Map();
  const store = await importFresh(CORE_STORE_JS, env.context, cache);
  // sendToPty의 window 브리지(term/clipboard.js 하단)를 심으려면 이 파일도
  // 그래프에 있어야 한다 — 실서비스에서는 main.js가 항상 먼저 불러온다.
  await importFresh(CLIPBOARD_JS, env.context, cache);
  // viewer-lazy.js는 registerAction만 부르고 core/dom.js는 그대로 import한다
  // (registerAction은 상태가 없는 순수 유틸이 아니라 레지스트리를 갖지만,
  // 이 테스트가 검증하는 건 viewer.show 자체가 아니라 세션 조회이므로
  // core/dom.js 중복 여부는 여기서 다루지 않는다 — quickopen.test.js가
  // 실제 dock/rail 배선으로 그 경로를 이미 덮는다).
  const { loadViewer } = await importFresh(VIEWER_LAZY_JS, env.context, cache);
  return { window, store, loadViewer };
}

test('window 브리지 — 동적 로드된 뷰어가 core/store.js의 진짜 활성 세션을 본다', async () => {
  const { window, store, loadViewer } = await load();

  const ws = new FakeWebSocket();
  store.registerSession('sess-1', { wsHandle: ws, tabEl: null, tmuxName: 'dev' });
  store.setActive('sess-1');

  // 회귀 확인: window 브리지가 실제로 살아 있고, 지연 로드 전에도 이미
  // store.js가 심어둔 값을 가리킨다.
  assert.strictEqual(window.activeSessionId(), 'sess-1');

  const { _insertPathToTerminal } = await loadViewer();
  _insertPathToTerminal('/tmp/example.txt');

  // 진짜 회귀 지점: 예전(직접 import)에는 tree.js가 자기만의 빈 sessions={}를
  // 봐서 sendToPty가 아무 세션도 못 찾아 조용히 no-op이었다. 이제는 window로
  // 참조하므로 위에서 registerSession한 바로 그 wsHandle에 실제로 써야 한다.
  assert.strictEqual(ws.sent.length, 1, '진짜 활성 세션의 wsHandle에 실제로 썼어야 한다');
  const written = new TextDecoder().decode(ws.sent[0]);
  assert.strictEqual(written, '/tmp/example.txt ');
});

test('window 브리지 — 세션이 없으면(activeSession() falsy) 조용히 안내만 한다', async () => {
  const { window, loadViewer } = await load();
  let toastMsg = null;
  window.showToast = (msg) => { toastMsg = msg; };

  assert.strictEqual(window.activeSession(), undefined);

  const { _insertPathToTerminal } = await loadViewer();
  _insertPathToTerminal('/tmp/example.txt');

  assert.match(toastMsg || '', /열려 있는 터미널 세션이 없습니다/);
});
