// 마크다운 미리보기 — 파일 개편 계획 2.2(marked + DOMPurify)와 7.2(정화 정책). `preview` 지연 청크.
//
// 라이브러리는 받지 않고 넘겨받는다(createMarkdown({ Marked, purify })) — 이 파일은 규칙만 갖는다. 실제 라이브러리를
// 붙이는 곳은 markdown-lib.js이고, 시험은 같은 라이브러리를 jsdom 창에 붙여 이 규칙을 그대로 돌린다.
//
// 정화 정책(계획 7.2)
// - 원문 HTML은 파서 단계에서 끈다: marked의 html 토큰을 글자로 이스케이프해 그린다(`<script>`가 글자로 보인다).
//   그다음 DOMPurify가 허용 목록으로 한 번 더 거른다(두 겹 — 하나가 뚫려도 다른 하나가 막는다).
// - 태그: p h1-h6 ul ol li code pre blockquote table thead tbody tr th td a img em strong del hr br, 비활성 체크박스.
// - 속성: href src alt title align(+ 체크박스의 type·checked·disabled). id·name·class·style·on*·srcset은 없다
//   (DOM clobbering·스타일 주입·이벤트 처리기 차단).
// - 링크: http·https·mailto만 남기고 새 탭 + rel="noopener noreferrer". 문서 안 앵커(`#…`)는 이 칸에서 움직인다
//   (부르는 쪽이 클릭을 받아 같은 이름의 제목으로 스크롤한다 — id를 허용하지 않으므로). 그 밖(javascript:·data:·
//   상대 경로·`//호스트`)은 href를 지운다.
// - 이미지: **정화가 끝난 뒤** 상대 경로만 문서 폴더 기준으로 풀어 `rawUrl(절대경로)`(= vtUrl('/api/fs/raw?…'))로
//   바꾼다. 서버가 fsguard로 판정하고, 중계 경로도 rawUrl이 유지한다. 외부 주소·data:·`/`로 시작하는 주소는
//   자리표시로 바꾼다(추적 방지, 같은 출처 API를 쿠키와 함께 부르는 GET 차단).
import { t } from '../../core/i18n.js';

export const MD_TAGS = [
  'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'code', 'pre', 'blockquote',
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a', 'img', 'em', 'strong', 'del', 'hr', 'br', 'input',
];
export const MD_ATTR = ['href', 'src', 'alt', 'title', 'align', 'type', 'checked', 'disabled'];

// DOMPurify 단계의 URI 허용: http·https·mailto와 스킴 없는 값(상대 경로·#앵커). 스킴 없는 값은 아래 뒤처리가
// 링크면 지우고 이미지면 문서 폴더 기준 /api/fs/raw로 바꾼다.
const URI_RE = /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.:-]|$))/i;
const LINK_OK = /^(?:https?|mailto):/i;
const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const ALIGN_OK = new Set(['left', 'center', 'right']);

/** 미리보기로 그릴 수 있는 크기(바이트) — 넘으면 원문(CodeMirror)으로 연다. */
export const MD_MAX_BYTES = 1024 * 1024;

function escHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

/** GitHub 방식에 가까운 제목 이름: 소문자, 앞뒤 공백 제거, 글자·숫자·공백·하이픈만 남기고 공백은 하이픈. */
export function slugify(text) {
  return String(text || '').trim().toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-');
}

/**
 * 문서 경로(절대) 기준으로 상대 경로를 푼다. 쿼리·조각(`?`·`#`)은 뗀다. `.`·`..`을 정리한다(루트 위로는 못 간다).
 * 상대 경로가 아니면(스킴, `/`·`\`로 시작, 빈 값) null.
 */
export function resolveRelative(docPath, rel) {
  let r = String(rel || '').trim();
  if (!r || HAS_SCHEME.test(r) || r.startsWith('/') || r.startsWith('\\')) return null;
  r = r.split(/[?#]/, 1)[0];
  if (!r) return null;
  try { r = decodeURIComponent(r); } catch (_) { /* 잘못된 %는 그대로 */ }
  if (r.includes('\0')) return null;
  const base = String(docPath || '').split('/').slice(0, -1);
  const out = base.filter((s, i) => i === 0 || s);
  for (const seg of r.split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') { if (out.length > 1) out.pop(); continue; }
    out.push(seg);
  }
  const joined = out.join('/');
  return joined.startsWith('/') ? joined : `/${joined}`;
}

function blockedImage(doc, src, alt) {
  const box = doc.createElement('span');
  box.className = 'vt-md-blocked';
  const label = doc.createElement('span');
  label.className = 'vt-md-blocked-label';
  label.textContent = t('외부 이미지를 막았습니다');
  box.appendChild(label);
  const url = String(src || '').trim();
  if (url) {
    const u = doc.createElement('span');
    u.className = 'vt-md-blocked-url';
    // data: 주소는 길고 의미가 없다 — 앞부분만.
    u.textContent = url.length > 80 ? `${url.slice(0, 77)}…` : url;
    box.appendChild(u);
    if (!/^data:/i.test(url)) {
      const b = doc.createElement('button');
      b.type = 'button';
      b.className = 'vt-vw-bar-btn vt-md-copy';
      b.dataset.copy = url;
      b.textContent = t('주소 복사');
      box.appendChild(b);
    }
  }
  if (alt) box.title = alt;
  return box;
}

/** 정화가 끝난 조각을 정책대로 다듬는다(링크·이미지·체크박스·align). */
function finish(frag, opts) {
  const doc = frag.ownerDocument;
  for (const a of [...frag.querySelectorAll('a')]) {
    const href = (a.getAttribute('href') || '').trim();
    a.removeAttribute('target');
    a.removeAttribute('rel');
    if (href.startsWith('#') && href.length > 1) continue;
    if (LINK_OK.test(href)) {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
    } else {
      a.removeAttribute('href');
    }
  }
  for (const img of [...frag.querySelectorAll('img')]) {
    img.removeAttribute('srcset');
    const src = img.getAttribute('src') || '';
    const abs = resolveRelative(opts.docPath, src);
    if (abs && typeof opts.rawUrl === 'function') {
      img.setAttribute('src', opts.rawUrl(abs));
      img.setAttribute('loading', 'lazy');
      img.setAttribute('referrerpolicy', 'no-referrer');
    } else {
      img.replaceWith(blockedImage(doc, src, img.getAttribute('alt') || ''));
    }
  }
  for (const input of [...frag.querySelectorAll('input')]) {
    if ((input.getAttribute('type') || '').toLowerCase() !== 'checkbox') { input.remove(); continue; }
    const checked = input.hasAttribute('checked');
    for (const name of input.getAttributeNames()) input.removeAttribute(name);
    input.setAttribute('type', 'checkbox');
    input.setAttribute('disabled', '');
    if (checked) input.setAttribute('checked', '');
  }
  for (const el of [...frag.querySelectorAll('[align]')]) {
    if (!ALIGN_OK.has((el.getAttribute('align') || '').toLowerCase())) el.removeAttribute('align');
  }
  return frag;
}

/**
 * 렌더러를 만든다. `Marked`는 marked의 클래스, `purify`는 창에 붙인 DOMPurify 인스턴스.
 * 돌려주는 함수: `(text, { docPath, rawUrl }) → DocumentFragment`(정화·정책 적용이 끝난 것).
 */
export function createMarkdown({ Marked, purify }) {
  const md = new Marked({
    gfm: true,
    breaks: false,
    async: false,
    renderer: {
      // 원문 HTML(블록·인라인 모두)은 글자로 — 파서 단계에서 끈다.
      html(token) { return escHtml(token && typeof token === 'object' ? token.text : token); },
    },
  });
  const config = {
    ALLOWED_TAGS: MD_TAGS,
    ALLOWED_ATTR: MD_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOWED_URI_REGEXP: URI_RE,
    ALLOW_UNKNOWN_PROTOCOLS: false,
    SANITIZE_DOM: true,
    KEEP_CONTENT: true,
    RETURN_DOM_FRAGMENT: true,
  };
  return function render(text, opts = {}) {
    const html = md.parse(String(text == null ? '' : text));
    const frag = purify.sanitize(html, config);
    return finish(frag, opts);
  };
}
