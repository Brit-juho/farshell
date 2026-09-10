'use strict';
// N18 — Solid/TSX 파이프라인 검증용. vm-esm.js는 .tsx를 직접 못 읽지만(TS/JSX
// 변환이 필요), `vite build --mode test`로 tsx를 미리 컴파일해 나온
// frontend/dist-test/smoke.js는 평범한 ESM 번들(외부 import 없음, solid-js
// 런타임까지 전부 인라인)이라 vm-esm.js의 importFresh로 다른 테스트와 똑같이
// 로드할 수 있다.
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { createDomEnv } = require('./dom-env');
const { importFresh } = require('./vm-esm');

const REPO_ROOT = path.resolve(__dirname, '../../..');
const DIST_JS = path.join(REPO_ROOT, 'frontend/dist-test/smoke.js');

let _built = false;
function buildOnce() {
  if (_built) return;
  execFileSync('npx', ['vite', 'build', '--mode', 'test'], { cwd: REPO_ROOT, stdio: 'pipe' });
  _built = true;
}

// smoke.js를 실제 jsdom window의 vm context에서 ESM으로 로드한다. 반환값은
// { dom, window, document, context, ...smoke.js의 named export }.
async function loadSolidBuild() {
  buildOnce();
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  const cache = new Map();
  const ns = await importFresh(DIST_JS, env.context, cache);
  return { ...env, ...ns };
}

module.exports = { loadSolidBuild, buildOnce, DIST_JS };
