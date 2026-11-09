// L7 — voice/media-session.js의 toggleVoiceOnly() 마이크 재배치 회귀 테스트.
// 이 파일은 voice.js 전용 lib entry(별도 빌드, core/dom.js 미참조)라
// window.registerAction만 core/dom.js를 따로 임포트해 준비해준다 — 실제
// 부팅에서도 app.js(core/dom.js)가 먼저 로드된 뒤 voice.js가 나중에 동적
// 주입되는 순서와 같다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const DOM_JS = path.join(__dirname, '../js/core/dom.js');
const MEDIA_SESSION_JS = path.join(__dirname, '../js/voice/media-session.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function buildWindow() {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;

  const cache = new Map();
  const domNs = await importFresh(DOM_JS, env.context, cache); // window.registerAction 브리지
  const mediaNs = await importFresh(MEDIA_SESSION_JS, env.context, cache);

  return { window, dom: domNs, ...mediaNs };
}

test('음성 전용 모드 ON → 마이크가 #topbar로 옮겨간다(가운데 정렬 CSS가 그 자식만 본다)', async () => {
  const { window, toggleVoiceOnly } = await buildWindow();
  const mic = window.document.getElementById('mic-btn-wrap');
  const originalParentId = mic.parentElement.id; // vt-mic-park(작업대가 오기 전 대기 자리)
  assert.strictEqual(originalParentId, 'vt-mic-park');

  toggleVoiceOnly();

  assert.strictEqual(window.document.body.classList.contains('voice-only-mode'), true);
  assert.strictEqual(mic.parentElement.id, 'topbar');
});

test('음성 전용 모드 OFF → 마이크가 원래 자리로 정확히 되돌아간다', async () => {
  const { window, toggleVoiceOnly } = await buildWindow();
  const mic = window.document.getElementById('mic-btn-wrap');

  toggleVoiceOnly(); // ON
  toggleVoiceOnly(); // OFF

  assert.strictEqual(window.document.body.classList.contains('voice-only-mode'), false);
  assert.strictEqual(mic.parentElement.id, 'vt-mic-park');
});
