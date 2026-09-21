// N4 — pane 리프(`kind:'panel'`)가 그릴 수 있는 패널 목록. **단일 진실이다.**
//
// 왜 별도 파일인가: 이 목록을 읽어야 하는 곳이 셋이고 서로 성격이 다르다.
//   - `layout/persist.js` — 스냅샷의 `panel` 이름이 유효한지 검사(상수만 필요)
//   - `layout/panes.js`   — 그 칸을 실제로 그린다(지연 로더까지 필요)
//   - 진입점(빈 pane 시트·팔레트) — 사람이 읽는 이름이 필요
// 셋이 각자 목록을 들고 있으면 반드시 어긋난다. dock 탭 정의(`shell/Dock.tsx`의
// TABS)와도 짝이 맞아야 하는데 그쪽은 지연 청크(Solid)라 여기서 import 할 수
// 없다 — 대신 `panelId`를 같은 값으로 적어 두고 `frontend/tests`가 대조한다.
//
// ⚠ 지연 로딩: 화면 모듈은 전부 `panels` 청크다(ADR-26). 여기서 **정적으로**
// import 하면 그 청크가 core로 승격돼 초기 페이로드가 커진다 — `load()`는
// 반드시 동적 import여야 한다.

/** 패널 이름 → 메타. 이름은 스냅샷에 그대로 저장되므로 바꾸면 옛 배치가 깨진다. */
export const PANELS = {
  scm: {
    label: '소스컨트롤',
    icon: 'git-branch',
    panelId: 'vt-dock-scm',
    load: () => import('./viewer/scm.js').then((m) => m.showScm),
  },
  queue: {
    label: '큐',
    icon: 'list',
    panelId: 'vt-queue',
    load: () => import('../queue.js').then((m) => m.showQueue),
  },
  files: {
    label: '파일',
    icon: 'folder',
    panelId: 'vt-files',
    load: () => import('./files/files.js').then((m) => m.showFiles),
  },
  ports: {
    label: '포트',
    icon: 'plug',
    panelId: 'vt-ports',
    load: () => import('./ports/ports.js').then((m) => m.showPorts),
  },
  usage: {
    label: '사용량',
    icon: 'gauge',
    panelId: 'vt-usage',
    load: () => import('./usage.js').then((m) => m.showUsage),
  },
};

// N4 3단계 — dock 탭을 pane으로 끌어낼 때 dataTransfer에 싣는 mime.
// 값은 **패널 이름**(위 PANELS의 키)이라 이 파일이 제자리다. 세션 드래그의
// `SESSION_MIME`(layout/dnd.js)과 짝을 이루고, 드롭 타겟은 어느 쪽이 실렸는지
// 보고 갈라진다. dnd.js에 두지 않은 이유: `shell/Dock.tsx`가 이 상수를 읽어야
// 하는데 dnd.js는 상태 모듈(store.js)을 정적 import 해서 지연 청크가 건드리면
// 안 된다(ADR-26) — 이 파일은 정적 import가 없어 안전하다.
export const PANEL_MIME = 'text/vt-panel-name';

/** 스냅샷 검증용 — 상수만 쓰는 쪽(persist.js)이 이것만 import 한다. */
export const PANEL_KINDS = new Set(Object.keys(PANELS));

/** 사람이 읽는 이름. 빈 pane 시트·팔레트가 쓴다. */
export function panelLabel(name) {
  return PANELS[name]?.label || name;
}

/** 이 패널을 pane 칸 안에 그린다.
 *
 * 실패해도 **던지지 않는다** — 렌더 경로 한가운데라, 여기서 터지면 pane 하나가
 * 아니라 레이아웃 전체가 안 그려진다. 못 그리면 그 자리에 이유를 남긴다. */
export async function mountPanelInPane(name, bodyEl, paneId) {
  const meta = PANELS[name];
  if (!meta) return null;
  try {
    const show = await meta.load();
    return show({ paneHost: bodyEl, paneId }) || null;
  } catch (e) {
    console.error('[FarShell panel-pane]', name, e);
    bodyEl.replaceChildren();
    const err = document.createElement('div');
    err.className = 'vt-vw-empty warn';
    err.textContent = `${panelLabel(name)} 화면을 불러오지 못했습니다.`;
    bodyEl.appendChild(err);
    return null;
  }
}
