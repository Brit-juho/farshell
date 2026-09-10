// 최근 연 파일 — 구 viewer/tree.js에서 이관(N35 §6에서 모달 코드 뷰어를
// 없애면서 트리 렌더러는 사라졌지만, 이 목록은 팔레트의 「최근 파일」 섹션이
// 그대로 쓴다). 경로만 저장하므로 파일 내용은 남지 않는다.
const VT_RECENT_KEY = 'vt_viewer_recent';
const VT_RECENT_MAX = 8;

export function _loadRecent() {
  try {
    const v = JSON.parse(localStorage.getItem(VT_RECENT_KEY) || '[]');
    return Array.isArray(v) ? v.filter((p) => typeof p === 'string') : [];
  } catch (_) { return []; }
}

export function _pushRecent(path) {
  if (!path) return;
  try {
    const list = _loadRecent().filter((p) => p !== path);
    list.unshift(path);
    localStorage.setItem(VT_RECENT_KEY, JSON.stringify(list.slice(0, VT_RECENT_MAX)));
  } catch (_) { /* 사생활 보호 모드 등 — 최근 목록 없이 그냥 동작한다 */ }
}
