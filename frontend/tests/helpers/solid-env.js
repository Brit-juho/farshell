'use strict';
// N18 — .ts/.tsx 검증용 공용 하네스. vm-esm.js는 .ts/.tsx를 직접 못 읽지만
// (TS/JSX 변환이 필요), `vite build --mode test`로 미리 컴파일해 나온
// frontend/dist-test/test-entry.js는 평범한 ESM 번들(외부 import 없음, solid-js
// 런타임까지 전부 인라인)이라 vm-esm.js의 importFresh로 다른 테스트와 똑같이
// 로드할 수 있다. 새 TS 모듈을 테스트하려면 fixtures/test-entry.ts에 re-export만
// 추가하면 된다. 빌드는 프로세스당 한 번(buildOnce)만 돈다.
//
// 산출 디렉터리는 **프로세스마다 따로** 쓴다(`dist-test/p<pid>/`). `node --test`는
// 파일마다 별도 프로세스를 띄워 각자 `loadSolidBuild()`를 부르는데, 예전엔 모두 같은
// `dist-test/`에 빌드했다. vite의 `emptyOutDir`가 그 디렉터리를 통째로 비우므로, 먼저
// 끝난 프로세스가 `test-entry.js`를 읽는 사이에 늦게 시작한 프로세스의 빌드가 지워
// ENOENT/손상된 번들이 났다(전체 실행에서만 palette 계열이 깨지고 단독 실행은 통과하던
// 간헐 실패의 원인). 파일시스템 락과 읽기 재시도로 가렸던 것을, 디렉터리를 나눠 경쟁
// 자체를 없앴다 — 락도 재시도도 필요 없다. 빌드는 프로세스당 한 번이고 끝나면 지운다.
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { createDomEnv } = require('./dom-env');
const { importFresh } = require('./vm-esm');

const REPO_ROOT = path.resolve(__dirname, '../../..');
const OUT_DIR = path.join(REPO_ROOT, 'frontend/dist-test', `p${process.pid}`);
const DIST_JS = path.join(OUT_DIR, 'test-entry.js');

let _built = false;
function buildOnce() {
  if (_built) return;
  execFileSync(
    'npx', ['vite', 'build', '--mode', 'test', '--outDir', path.relative(REPO_ROOT, OUT_DIR)],
    { cwd: REPO_ROOT, stdio: 'pipe' },
  );
  process.once('exit', () => {
    try { fs.rmSync(OUT_DIR, { recursive: true, force: true }); } catch (_) { /* best effort */ }
  });
  _built = true;
}

// smoke.js를 실제 jsdom window의 vm context에서 ESM으로 로드한다. 반환값은
// { dom, window, document, context, ...smoke.js의 named export }.
async function loadSolidBuild() {
  buildOnce();
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  const ns = await importFresh(DIST_JS, env.context, new Map());
  return { ...env, ...ns };
}

module.exports = { loadSolidBuild, buildOnce, DIST_JS };
