// N17·N18 — Solid + TypeScript 파이프라인이 실제로 동작하는지 확인한다.
// 이 테스트가 검증하는 건 "우리 코드"가 아니라 "도구 사슬"(tsconfig →
// vite-plugin-solid → esbuild jsx 변환 → solid-js 런타임 → lib 모드 빌드)
// 이다 — §3부터 shell/*.tsx가 이 위에 얹인다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

test('vite build --mode test로 뽑은 smoke.js가 jsdom에서 solid 컴포넌트를 마운트한다', async () => {
  const env = await loadSolidBuild();
  _doms.push(env);

  assert.strictEqual(typeof env.mountSmoke, 'function', 'smoke.js가 mountSmoke를 export해야 한다');

  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  env.mountSmoke(root);

  const btn = root.querySelector('[data-testid="smoke-btn"]');
  assert.ok(btn, 'solid 컴포넌트가 실제 DOM으로 렌더돼야 한다');
  assert.strictEqual(btn.textContent, '0');

  btn.dispatchEvent(new env.window.Event('click', { bubbles: true }));
  assert.strictEqual(btn.textContent, '1', 'createSignal 갱신이 실제로 리렌더로 이어져야 한다');
});
