// 2.1.6 — 토스트 중복 합치기(js/ui/toast.js).
//
// 실브라우저 검증에서 "설정을 서버에 저장하지 못했습니다 (이 기기에서만
// 적용됨)"가 화면 아래에 셋까지 쌓인 걸 재현했다. 같은 말을 세 번 하는 건
// 정보가 아니라 잡음이고, 쌓인 만큼 터미널을 가린다.
//
// 검사하는 계약:
//   1. key 없는 같은 문구는 새로 쌓지 않고 한 줄로 합쳐 횟수를 붙인다
//   2. 타입이 다르면 다른 토스트다(경고와 성공이 합쳐지면 안 된다)
//   3. key가 있는 호출의 "제자리 교체"는 예전 그대로다(회귀 방지)
//   4. 사라진 뒤 같은 문구가 다시 오면 횟수는 1부터 — 합치기 대상은
//      **지금 화면에 떠 있는 것**뿐이다
const { test, after } = require('node:test');
const assert = require('node:assert');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const path = require('node:path');

const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');

const envs = [];
after(() => { for (const e of envs) e.window.close(); });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  envs.push(env);
  const mod = await importFresh(TOAST_JS, env.context, new Map());
  return { env, mod, document: env.document };
}

const toasts = (document) =>
  [...document.querySelectorAll('#vt-toasts .vt-toast')];

test('같은 문구를 연달아 띄우면 한 줄로 합치고 횟수를 붙인다', async () => {
  const { mod, document } = await load();
  mod.showToast('설정을 서버에 저장하지 못했습니다', 'error');
  mod.showToast('설정을 서버에 저장하지 못했습니다', 'error');
  mod.showToast('설정을 서버에 저장하지 못했습니다', 'error');

  const list = toasts(document);
  assert.strictEqual(list.length, 1, '세 번 띄워도 한 줄');
  assert.match(list[0].textContent, /설정을 서버에 저장하지 못했습니다/);
  assert.strictEqual(list[0].querySelector('.vt-toast-count').textContent, '×3');
});

test('타입이 다르면 합치지 않는다', async () => {
  const { mod, document } = await load();
  mod.showToast('같은 문구', 'error');
  mod.showToast('같은 문구', 'success');
  assert.strictEqual(toasts(document).length, 2);
});

test('문구가 다르면 예전처럼 쌓인다', async () => {
  const { mod, document } = await load();
  mod.showToast('첫 번째', 'info');
  mod.showToast('두 번째', 'info');
  assert.strictEqual(toasts(document).length, 2);
});

test('key가 있으면 예전처럼 제자리 교체 — 횟수 배지를 붙이지 않는다', async () => {
  const { mod, document } = await load();
  mod.showToast('도구 실행 중', 'info', { key: 'agent-tool' });
  mod.showToast('도구 끝', 'info', { key: 'agent-tool' });

  const list = toasts(document);
  assert.strictEqual(list.length, 1);
  assert.strictEqual(list[0].textContent, '도구 끝', '최신 값으로 갱신되는 게 key의 의도');
  assert.strictEqual(list[0].querySelector('.vt-toast-count'), null);
});

test('사라진 뒤 다시 오면 횟수는 1부터 센다', async () => {
  const { mod, document } = await load();
  const el = mod.showToast('한 번', 'info');
  mod.showToast('한 번', 'info');
  assert.strictEqual(el.querySelector('.vt-toast-count').textContent, '×2');

  mod.dismissToast(el);
  assert.strictEqual(toasts(document).length, 0);

  const again = mod.showToast('한 번', 'info');
  assert.strictEqual(toasts(document).length, 1);
  assert.strictEqual(again.querySelector('.vt-toast-count'), null,
    '새 토스트는 배지 없이 시작한다');
});
