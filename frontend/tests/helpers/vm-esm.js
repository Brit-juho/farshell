'use strict';
// vm.SourceTextModule 기반 real-ESM 임포터 — jsdom 창의 내부 vm context 안에서
// 실제 소스 파일을 그대로 링크·평가한다. F2~F5가 쓰던 stripEsm(정규식으로
// import/export 구문을 지운 뒤 classic <script>로 주입) 기법을 대체한다: 이제
// 테스트가 소스를 텍스트로 조작할 필요 없이, 순환 import를 포함한 실제 모듈
// 그래프를 브라우저와 같은 방식으로 그대로 실행한다.
//
// 두 단계로 나뉜다:
//   1. createModule() — 그래프 전체를 먼저 "생성"만 한다(링크 전, import
//      specifier를 정규식으로 훑어 재귀적으로 vm.SourceTextModule을 만들어
//      cache에 채워 넣는다). 순환 참조(session.js↔picker.js 등)가 있어도 이
//      단계는 각 파일을 정확히 한 번씩만 생성하고 끝난다.
//   2. importFresh() — 그래프가 cache에 전부 채워진 뒤에야 link()를 부른다.
//      link()의 리졸버는 이제 cache에서 동기적으로 찾기만 하면 된다 — 만든
//      순서를 링크 시점에 재귀적으로 뒤섞으면(즉 1·2 단계를 합치면) 순환
//      참조에서 V8이 "request for 'X' is not in cache"로 죽는다(실측 확인:
//      link() 콜백 안에서 아직 생성 중인 모듈을 또 만들려 하면 실패).
//
// cache는 호출부(각 테스트의 buildXWindow)가 매번 새 Map으로 넘겨야 한다 —
// 그래야 core/store.js 같은 모듈 스코프 싱글톤(sessions/activeId)이 테스트마다
// 새로 시작한다. 같은 cache로 importFresh()를 여러 엔트리에 대해 반복 호출하는
// 것(예: 메인 그래프를 한 번 부른 뒤 core/dom.js를 엔트리로 한 번 더 부르는 것)은
// 안전하다 — 이미 링크·평가된 모듈은 재평가 없이 같은 네임스페이스를 그대로
// 반환한다.
//
// 반드시 --experimental-vm-modules 플래그로 실행해야 한다(package.json test
// 스크립트에 고정, node:vm의 SourceTextModule이 이 플래그 없이는 존재하지 않음).
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

// 링크를 실제로 실행하지 않고 import 대상 파일 경로만 뽑는다(exec 아님, 순수 텍스트
// 스캔) — 링크 단계에서 쓸 "이 모듈이 무엇을 참조하는가" 정보를 미리 알기 위해서다.
const IMPORT_RE = /^import\s+(?:[\s\S]*?\bfrom\s+)?['"]([^'"]+)['"];?\s*$/gm;
function parseImportSpecifiers(src) {
  const specs = [];
  let m;
  IMPORT_RE.lastIndex = 0;
  while ((m = IMPORT_RE.exec(src))) specs.push(m[1]);
  return specs;
}

// N35 — 런타임 동적 import() 지원. panels/viewer-lazy.js처럼 실제 소스가
// `import('./x.js')`를 쓰는 지연 로딩 코드를 테스트하려면 필요하다. 정적
// import(위 IMPORT_RE)만 미리 그래프로 깔아두는 1단계 방식과 달리, 동적
// import는 "그 줄이 실제로 실행되는 순간"에야 무엇을 부를지 알 수 있으므로
// 그때 가서 즉석으로 createModule → link → evaluate 한다 — 이미 캐시에 있는
// 모듈(정적 그래프에 포함돼 있던 것)이면 재생성 없이 그대로 재사용한다.
function createModule(filePath, context, cache) {
  const resolved = path.resolve(filePath);
  if (cache.has(resolved)) return cache.get(resolved);
  const src = fs.readFileSync(resolved, 'utf8');
  const mod = new vm.SourceTextModule(src, {
    identifier: resolved,
    context,
    importModuleDynamically: async (specifier, referencingModule) => {
      const depPath = path.resolve(path.dirname(referencingModule.identifier), specifier);
      const dep = createModule(depPath, context, cache);
      if (dep.status === 'unlinked') await dep.link(cacheLinker(context, cache));
      if (dep.status !== 'evaluated') await dep.evaluate();
      return dep.namespace;
    },
  });
  cache.set(resolved, mod);
  for (const spec of parseImportSpecifiers(src)) {
    createModule(path.resolve(path.dirname(resolved), spec), context, cache);
  }
  return mod;
}

function cacheLinker(context, cache) {
  return (specifier, referencingModule) => {
    const depPath = path.resolve(path.dirname(referencingModule.identifier), specifier);
    const dep = cache.get(path.resolve(depPath));
    if (!dep) throw new Error(`vm-esm: linked module not pre-created for "${specifier}" (from ${referencingModule.identifier})`);
    return dep;
  };
}

async function importFresh(entryFile, context, cache) {
  const mod = createModule(entryFile, context, cache);
  if (mod.status === 'unlinked') await mod.link(cacheLinker(context, cache));
  if (mod.status !== 'evaluated') await mod.evaluate();
  return mod.namespace;
}

module.exports = { importFresh };
