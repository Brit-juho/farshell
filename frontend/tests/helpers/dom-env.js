'use strict';
// 테스트마다 새 jsdom 창 + 그 창 소유의 vm context를 만든다. runScripts:
// 'dangerously'는 <script> 실행 허용용이 아니라(우리는 <script> 태그를 안 쓴다)
// getInternalVMContext()가 요구하는 전제조건이다 — jsdom이 스크립트 실행을
// 허용하지 않은 창은 애초에 내부 vm context를 안 만든다.
//
// 반환된 dom.window.close()는 호출부(각 테스트 파일의 after())가 반드시 불러야
// 한다 — term/*.js가 만드는 setTimeout/setInterval(재연결·리사이즈 디바운스,
// 프리뷰 keepalive 등)을 정리하지 않으면 그 타이머들이 다 소진될 때까지
// 프로세스가 붙잡혀 node --test가 느려진다(F4/F5 하네스에서 이미 겪은 문제).
const { JSDOM } = require('jsdom');

function createDomEnv(html, opts = {}) {
  const dom = new JSDOM(html, {
    url: 'http://localhost/',
    pretendToBeVisual: true,
    runScripts: 'dangerously',
    ...opts,
  });
  return { dom, window: dom.window, document: dom.window.document, context: dom.getInternalVMContext() };
}

module.exports = { createDomEnv };
