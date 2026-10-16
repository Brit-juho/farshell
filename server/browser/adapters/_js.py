"""어댑터가 페이지에서 돌리는 **고정 스크립트**(에이전트가 보낸 코드가 아니다). 두 실연동 어댑터가 같은 것을 쓴다."""

# 보이는 상호작용 요소를 번호 매기고(`data-fsh-ref`) 목록으로 돌려준다. 입력 값(특히 password)은 읽지 않는다 — 이름·역할·placeholder 만.
SNAPSHOT = r"""
(() => {
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=menuitem],[role=tab],[onclick],[contenteditable=""],[contenteditable="true"]';
  document.querySelectorAll('[data-fsh-ref]').forEach(e => e.removeAttribute('data-fsh-ref'));
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(SEL)) {
    if (n >= 200) break;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    n += 1;
    el.setAttribute('data-fsh-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let name = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || '';
    if (!name && type !== 'password' && tag !== 'input' && tag !== 'textarea') name = (el.innerText || el.textContent || '').trim();
    if (!name && tag === 'input' && (type === 'submit' || type === 'button')) name = el.getAttribute('value') || '';
    out.push({ n, tag, role: el.getAttribute('role') || tag, type, name: name.replace(/\s+/g, ' ').slice(0, 120) });
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 20000);
  return { url: location.href, title: document.title, text, elements: out };
})()
"""

# localStorage·sessionStorage — values 가 false 면 키 이름만 가져온다(값을 페이지 밖으로 내보내지 않는다).
STORAGE = r"""
((values) => {
  const dump = (s) => { const o = {}; try { for (let i = 0; i < s.length; i++) { const k = s.key(i); o[k] = values ? s.getItem(k) : null; } } catch (e) {} return o; };
  return { local: dump(window.localStorage), session: dump(window.sessionStorage) };
})
"""

# 요소 중심으로 스크롤하고 좌표를 돌려준다(CDP 의 마우스 입력용).
BOX = r"""
((sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
})
"""

FOCUS = r"""
((sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  el.scrollIntoView({ block: 'center' });
  el.focus();
  if (typeof el.select === 'function' && el.tagName !== 'SELECT') { try { el.select(); } catch (e) {} }
  return true;
})
"""


def native(n) -> str:
    return f'[data-fsh-ref="{int(n)}"]'
