// 액션 레지스트리 + data-action 위임 — F3(c) 신설.
//
// index.html의 인라인 onclick 29개와 quickopen.js:17-23,126-127의
// window[c.fn] 문자열 디스패치를 하나의 등록 테이블로 합친다. registerAction()
// 으로 명시 등록해야만 실행되므로, 옛 quickopen.js가 갖고 있던 위험
// ("함수가 const로 선언돼 있으면 window[c.fn]이 조용히 실패") — 이 파일 자체가
// 그 자리를 대체한다.
//
// 아직 classic script인 각 파일(search.js/terminal.js/picker.js/grid.js/
// viewer.js/queue.js/snippets.js/ports.js/pushui.js/theme.js/voice.js)이 자기
// 함수를 정의한 뒤 맨 아래서 registerAction('name', fn)으로 등록한다 — main.js가
// 이 모듈을 classic script보다 먼저 평가하므로 registerAction은 항상 준비돼 있다.

const registry = new Map();

export function registerAction(name, fn) {
  registry.set(name, fn);
}

export function getAction(name) {
  return registry.get(name);
}

// bubble 단계에 둔다 — capture로 걸면 기존 stopPropagation()을 부르는 핸들러
// (ports.js 스와이프, viewer.js 행 삽입, 탭 닫기)보다 먼저 실행돼 그 핸들러들의
// "이 클릭은 여기서 끝"이라는 의도를 깨뜨린다.
function initActionDelegation(root) {
  root.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const fn = registry.get(el.dataset.action);
    if (typeof fn !== 'function') return;
    fn(el, e);
  });
}
initActionDelegation(document);

window.registerAction = registerAction;
window.getAction = getAction;
