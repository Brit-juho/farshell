// 코드 뷰어 공용 상태·상수·아이콘 — F4에서 viewer.js(1230줄)에서 분리. 다른 5개
// viewer/*.js 파일 전부가 이 모듈을 참조하는 리프(leaf) — 순환 import를 피하려고
// 진짜 공유되는 것(상태 객체·localStorage 저장/복원·SVG 아이콘·`_setMsg` 헬퍼)만
// 여기 둔다. 스킬/기능별 로직은 shell/tree/file/diff/git.js로 나뉜다.
export const VT_MODE_KEY = 'vt_viewer_mode';
export const VT_DOCKW_KEY = 'vt_viewer_dockw';
export const VT_DOCK_W_DEFAULT = 420;
export const VT_DOCK_W_MIN = 280;
export const VT_TREEW_KEY = 'vt_viewer_treew';
export const VT_TREE_W_DEFAULT = 230;
export const VT_TREE_W_MIN = 160;

export const _ICON_CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 6 6 6-6 6"/></svg>';
export const _ICON_SHEET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="15" x2="21" y2="15"/></svg>';
export const _ICON_DOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="14" y1="3" x2="14" y2="21"/></svg>';
export const _ICON_FULL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>';
export const _ICON_SIDEBAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>';
export const _ICON_PIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s7-6.1 7-11.5A7 7 0 0 0 5 9.5C5 14.9 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.3"/></svg>';
// L2: Termius SFTP→프롬프트 패턴 — 경로를 터미널로 삽입.
export const _ICON_INSERT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';

// 객체 하나를 여러 파일이 공유 — 프로퍼티를 직접 mutate하므로(재할당이 아니라)
// import한 쪽도 항상 최신 상태를 본다(별도 getter 불필요).
export let _viewerState = {
  root: null,             // 트리 최상단 디렉토리. 시작값은 서버 시작 루트(기본 ~/GitHub)지만
                            // ".." 로 위로 이동하면 바뀐다(서버 경계 — 기본 홈 — 까지 허용).
  cwd: null,               // diff 대상 기본값(루트)
  mode: 'tree',            // 콘텐츠: 'tree' | 'file' | 'diff' — sheet의 활성 화면 판정에 쓴다
  displayMode: 'sheet',    // 레이아웃: 'sheet' | 'dock' | 'full'
  selectedPath: null,
  expanded: new Set(),     // 펼쳐진 디렉토리 절대경로 — 접었다 다시 펼 때 재요청 판단에 씀
};

export function _isMobile() { return window.matchMedia('(max-width:560px)').matches; }

export function _loadMode() {
  if (_isMobile()) return 'sheet';
  try {
    const m = localStorage.getItem(VT_MODE_KEY);
    return (m === 'dock' || m === 'full') ? m : 'sheet';
  } catch (_) { return 'sheet'; }
}
export function _saveMode(m) { try { localStorage.setItem(VT_MODE_KEY, m); } catch (_) {} }

export function _loadDockW() {
  try {
    const w = parseInt(localStorage.getItem(VT_DOCKW_KEY), 10);
    return Number.isFinite(w) && w > 0 ? w : VT_DOCK_W_DEFAULT;
  } catch (_) { return VT_DOCK_W_DEFAULT; }
}
export function _saveDockW(w) { try { localStorage.setItem(VT_DOCKW_KEY, String(w)); } catch (_) {} }
export function _clampDockW(w) {
  const maxW = Math.floor(window.innerWidth * 0.7);
  return Math.max(VT_DOCK_W_MIN, Math.min(w, maxW));
}

// 폴더 트리 패널(좌측) 폭 — dock/full 2단 분할 전용. 도킹 폭과 같은 저장/클램프 패턴.
export function _loadTreeW() {
  try {
    const w = parseInt(localStorage.getItem(VT_TREEW_KEY), 10);
    return Number.isFinite(w) && w > 0 ? w : VT_TREE_W_DEFAULT;
  } catch (_) { return VT_TREE_W_DEFAULT; }
}
export function _saveTreeW(w) { try { localStorage.setItem(VT_TREEW_KEY, String(w)); } catch (_) {} }
export function _clampTreeW(w) {
  const body = document.getElementById('vt-vw-body');
  const total = body ? body.clientWidth : 800;
  const maxW = Math.floor(total * 0.7);
  return Math.max(VT_TREE_W_MIN, Math.min(w, maxW));
}

// 정적 안내 메시지용 — textContent 만 쓰므로 이스케이프가 필요 없다.
// 줄바꿈은 <br> 엘리먼트로 표현한다(문자열 조립 없이).
export function _setMsg(container, className, lines) {
  container.innerHTML = '';
  const div = document.createElement('div');
  div.className = className;
  lines.forEach((line, i) => {
    if (i > 0) div.appendChild(document.createElement('br'));
    div.appendChild(document.createTextNode(line));
  });
  container.appendChild(div);
}
