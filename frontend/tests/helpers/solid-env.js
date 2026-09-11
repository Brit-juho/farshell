'use strict';
// N18 — .ts/.tsx 검증용 공용 하네스. vm-esm.js는 .ts/.tsx를 직접 못 읽지만
// (TS/JSX 변환이 필요), `vite build --mode test`로 미리 컴파일해 나온
// frontend/dist-test/test-entry.js는 평범한 ESM 번들(외부 import 없음, solid-js
// 런타임까지 전부 인라인)이라 vm-esm.js의 importFresh로 다른 테스트와 똑같이
// 로드할 수 있다. 새 TS 모듈을 테스트하려면 fixtures/test-entry.ts에 re-export만
// 추가하면 된다. 빌드는 프로세스당 한 번(buildOnce)만 돈다.
//
// N46 실측 버그: `node --test`는 파일마다 별도 프로세스를 띄우므로 `_built`는
// 프로세스 하나 안에서만 유효하다 — hud.test.js·rail-data.test.js·
// palette.test.js·rail-palette-parity.test.js 등 여러 파일이 동시에
// loadSolidBuild()를 부르면 각자 독립적으로 `vite build`를 실행해 **같은
// frontend/dist-test/test-entry.js를 동시에 덮어쓴다**. 한쪽이 파일을 쓰는
// 중간에 다른 쪽이 읽으면 손상된(불완전한) 모듈이 로드돼 export가 없는 것처럼
// 보이는 간헐적 실패가 난다(실측 — rail-data.test.js가 애먼 타이밍에 깨졌다).
// 파일시스템 락(`wx` 배타 생성)으로 빌드를 프로세스 간에 직렬화한다.
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { createDomEnv } = require('./dom-env');
const { importFresh } = require('./vm-esm');

const REPO_ROOT = path.resolve(__dirname, '../../..');
const DIST_JS = path.join(REPO_ROOT, 'frontend/dist-test/test-entry.js');
// 실측 버그(2.1.0 실기기 검증 중 재현): 락 파일을 `dist-test/` 안에 두면, vite의
// `emptyOutDir`(test 모드는 항상 true)가 그 빌드 자신이 만든 락 파일까지
// **자기가 지운다** — A가 락을 쥔 채 빌드 중인데 emptyOutDir가 디렉터리를 통째로
// 비우는 순간, 락 파일도 같이 사라진다. 그 틈에 B가 "락 없음"으로 보고 동시에
// 두 번째 `vite build`를 시작해 같은 디렉터리를 서로 지우며 써서 ENOENT/손상된
// 번들이 나왔다. 락은 빌드가 지우는 디렉터리 **밖**에 둬야 한다.
const LOCK_PATH = path.join(REPO_ROOT, 'frontend/.dist-test.lock');
const LOCK_STALE_MS = 30000; // 빌드가 이보다 오래 걸리면 죽은 락으로 보고 뺏는다.

let _built = false;
function _sleepSync(ms) {
  // buildOnce()는 동기 함수라(importFresh 호출 그래프 전체가 그 위에 얹인다)
  // Promise 기반으로 못 잔다 — 저장소가 macOS/Linux만 지원하므로 `sleep`을
  // 그대로 쓴다(AGENTS.md/CLAUDE.md의 지원 OS 표).
  execFileSync('sleep', [String(ms / 1000)]);
}

function buildOnce() {
  if (_built) return;
  fs.mkdirSync(path.dirname(LOCK_PATH), { recursive: true });
  for (;;) {
    try {
      fs.closeSync(fs.openSync(LOCK_PATH, 'wx'));
      break; // 락 획득 — 이 프로세스가 빌드한다.
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      let age = Infinity;
      try { age = Date.now() - fs.statSync(LOCK_PATH).mtimeMs; } catch (_) { /* 그 사이 해제됨 */ }
      if (age > LOCK_STALE_MS) {
        try { fs.unlinkSync(LOCK_PATH); } catch (_) { /* 다른 프로세스가 먼저 지웠다 */ }
        continue;
      }
      _sleepSync(100);
    }
  }
  try {
    execFileSync('npx', ['vite', 'build', '--mode', 'test'], { cwd: REPO_ROOT, stdio: 'pipe' });
  } finally {
    try { fs.unlinkSync(LOCK_PATH); } catch (_) { /* no-op */ }
  }
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
