// 마크다운 미리보기에 실제 라이브러리를 붙인다 — marked(MIT) + DOMPurify(MPL-2.0 OR Apache-2.0).
// `preview` 지연 청크(vite.config.js chunkOf): 마크다운 파일을 「렌더」로 열 때만 받는다. 규칙은 markdown.js.
import { Marked } from 'marked';
import createDOMPurify from 'dompurify';
import { createMarkdown } from './markdown.js';

let _render = null;

/** `(text, { docPath, rawUrl }) → DocumentFragment` — 정화·정책 적용이 끝난 조각. */
export function renderMarkdown(text, opts) {
  // 이 창 전용 인스턴스 — 다른 곳의 DOMPurify 설정·훅과 섞이지 않는다.
  if (!_render) _render = createMarkdown({ Marked, purify: createDOMPurify(window) });
  return _render(text, opts);
}
