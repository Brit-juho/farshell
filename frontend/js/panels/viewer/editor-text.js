// 코드 뷰어(CodeMirror 6) 보조 — CM을 import하지 않는 순수 함수만 둔다(jsdom 시험이 그대로 부를 수 있게).
// editor.js와 같은 `editor` 지연 청크에 들어간다(vite.config.mjs chunkOf).

/**
 * 옛 내용에서 새 내용으로 가는 가장 작은 한 덩어리 교체를 구한다. 같으면 null.
 * 앞뒤 공통 부분을 잘라 낸 가운데만 바꾼다 — 파일 끝에 줄이 붙는 흔한 변경이면 끝부분만 바뀌어
 * 스크롤·선택이 그대로 남고, 보이는 줄을 다시 그리지 않는다.
 * 서로게이트 쌍을 가르지 않도록 경계를 한 칸 물린다(반쪽 문자가 생기면 화면에 깨진 글자가 남는다).
 */
export function textChange(prev, next) {
  if (prev === next) return null;
  const max = Math.min(prev.length, next.length);
  let start = 0;
  while (start < max && prev.charCodeAt(start) === next.charCodeAt(start)) start += 1;
  if (start > 0 && _isHigh(prev.charCodeAt(start - 1))) start -= 1;
  let endPrev = prev.length;
  let endNext = next.length;
  while (endPrev > start && endNext > start && prev.charCodeAt(endPrev - 1) === next.charCodeAt(endNext - 1)) {
    endPrev -= 1;
    endNext -= 1;
  }
  if (endPrev < prev.length && _isLow(prev.charCodeAt(endPrev))) {
    endPrev += 1;
    endNext += 1;
  }
  return { from: start, to: endPrev, insert: next.slice(start, endNext) };
}

function _isHigh(c) { return c >= 0xd800 && c <= 0xdbff; }
function _isLow(c) { return c >= 0xdc00 && c <= 0xdfff; }

/**
 * 하이라이트 언어 키. `VTDiffLex.langForPath`(옛 highlight.js 이름)를 그대로 따르고, 확장자로만 갈리는 변형
 * (tsx·jsx, html과 xml)만 덧붙인다. 모르는 확장자는 null(일반 텍스트).
 */
export function editorLangKey(path, hljsName) {
  if (!hljsName) return null;
  const base = String(path || '').split('/').pop().toLowerCase();
  const ext = base.includes('.') ? base.slice(base.lastIndexOf('.') + 1) : '';
  if (hljsName === 'typescript') return ext === 'tsx' ? 'tsx' : 'typescript';
  if (hljsName === 'javascript') return 'javascript';   // jsx 문법은 js 파서가 함께 받는다
  return hljsName;
}
