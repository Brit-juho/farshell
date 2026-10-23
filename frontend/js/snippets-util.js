// 스니펫의 순수 규칙 — DOM·네트워크 없이 단위 테스트할 수 있게 snippets.js에서 뺐다.
//
// 모델: 본문의 줄바꿈은 키보드의 Enter와 같다. **본문 끝의 줄바꿈 하나가 곧
// 「마지막 줄까지 Enter」**다(서버 snippet_store.normalize_text와 같은 규칙).
// 편집 화면은 이 끝 줄바꿈을 눈에 안 보이는 글자로 두지 않고 「끝에 Enter 포함」
// 스위치로 드러낸다 — 그래서 본문과 스위치를 오가는 변환이 필요하다.

/** 저장된 본문 → 편집 화면의 (본문, 스위치). */
export function splitText(text) {
  const raw = String(text ?? '');
  return { body: raw.replace(/\n+$/, ''), enter: raw.endsWith('\n') };
}

/** 편집 화면의 (본문, 스위치) → 저장할 본문. 본문 끝의 빈 줄은 스위치로 대신한다. */
export function composeText(body, enter) {
  const b = String(body ?? '').replace(/\r\n?/g, '\n').replace(/^\n+/, '').replace(/\n+$/, '');
  if (!b.trim()) return '';
  return enter ? `${b}\n` : b;
}

/** 목록에 보일 이름 — 이름이 없으면 본문 첫 줄. */
export function displayName(item) {
  if (item.label) return item.label;
  const first = splitText(item.text).body.split('\n').find((l) => l.trim()) || '';
  return first.trim();
}

/** 검색: 이름·본문에 모든 단어가 들어 있으면 일치(대소문자 무시). */
export function matchesQuery(item, query) {
  const words = String(query ?? '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = `${item.label || ''}\n${item.text || ''}`.toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** 새 세션에서 실행할 때 서버에 보낼 이름 후보(비면 서버가 cwd 폴더명으로 정한다). */
export function sessionNameFor(item, typed) {
  const t = String(typed ?? '').trim();
  if (t) return t;
  return item.label ? item.label.trim() : '';
}

/** 입력 방식에 따라 터미널로 보낼 조각.
 *  - 'paste': 본문 전체를 붙여넣기로, 끝 줄바꿈이 있으면 Enter 한 번을 따로.
 *  - 'type' : 줄마다 Enter(새 세션 — 각 줄이 차례로 실행된다). 끝 줄바꿈 없으면 마지막 줄은 입력만. */
export function planInput(text, how) {
  const { body, enter } = splitText(text);
  if (how === 'paste') return { paste: body, enter };
  return { type: enter ? `${body}\n` : body };
}

/** 서버가 앱의 세션 이름 규칙([A-Za-z0-9_-])에 맞추려고 이름을 바꿨는가.
 *  원하는 이름 그대로이거나 `이름-1`처럼 번호만 붙었으면 바뀐 게 아니다. */
export function sessionNameAdjusted(requested, actual) {
  const r = String(requested ?? '').trim();
  if (!r) return false;
  const a = String(actual ?? '');
  return !(a === r || a.startsWith(`${r}-`));
}
