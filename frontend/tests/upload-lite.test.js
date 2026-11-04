// term/upload-lite.js — 업로드의 core 쪽 얇은 층. 본체(upload.js)가 아직 안 올라왔을 때의 계약을 고정한다.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const wins = [];
after(() => wins.forEach(w => w.close()));

async function setup() {
  const env = createDomEnv('<html><body></body></html>');
  wins.push(env.window);
  const mod = await importFresh(path.join(__dirname, '../js/term/upload-lite.js'), env.context, new Map());
  return { w: env.window, mod };
}

test('본체가 올라오기 전에도 window 브리지가 있고, 작업 없음/취소 0을 답한다', async () => {
  const { w } = await setup();
  assert.equal(typeof w.vtUploadFiles, 'function');
  assert.equal(w.vtUploadJobs().length, 0);
  assert.equal(w.vtCancelUpload('all'), 0);
});

test('빈 입력은 본체를 받지 않고 빈 배열을 돌려준다', async () => {
  const { mod } = await setup();
  assert.equal((await mod.uploadFiles(null)).length, 0);
  assert.equal((await mod.uploadFiles([])).length, 0);
});

test('hasFiles — OS 파일 드래그만 참이다', async () => {
  const { mod } = await setup();
  assert.equal(mod.hasFiles({ types: ['Files'] }), true);
  assert.equal(mod.hasFiles({ types: ['text/vt-tab-id'] }), false);
  assert.equal(mod.hasFiles(null), false);
});

test('드롭 대상 밖의 파일 드래그는 창 수준에서 기본 동작(페이지 이동)이 막힌다', async () => {
  const { w } = await setup();
  const ev = new w.Event('dragover', { cancelable: true });
  Object.defineProperty(ev, 'dataTransfer', { value: { types: ['Files'] } });
  w.dispatchEvent(ev);
  assert.equal(ev.defaultPrevented, true);
  const other = new w.Event('dragover', { cancelable: true });
  Object.defineProperty(other, 'dataTransfer', { value: { types: ['text/plain'] } });
  w.dispatchEvent(other);
  assert.equal(other.defaultPrevented, false);
});
