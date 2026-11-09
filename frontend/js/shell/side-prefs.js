// 작업대 사이드바의 폭·접힘·활동 — 저장값을 읽고 화면 폭에 맞추는 규칙만 둔다(순수 함수).
//
// 사이드바는 **활동 전체가 폭 하나를 공유한다**(리디자인 결정 1). 활동마다 폭을 따로 두면
// 활동을 바꿀 때마다 칸이 넓어졌다 좁아져 보이는 모든 터미널이 다시 맞춰지고(SIGWINCH)
// 에이전트 화면이 흔들린다. 하나로 두면 활동을 바꿔도 칸 크기가 그대로다.
//
// 저장 키는 기기 스코프(`core/settings.js`). 옛 키는 **읽기 폴백으로만** 쓴다 — 옛 값을 새 키로
// 옮겨 쓰지 않는다(다른 버전의 화면이 같은 기기 설정을 읽어도 그쪽 값이 그대로 남게).
//
// Workbench.tsx(지연 청크)와 테스트가 함께 쓴다. 상태를 가진 모듈을 import 하지 않는다.
import { REGULAR_MAX } from '../layout/breakpoints.js';

export const SIDE_MIN = 240;
export const SIDE_MAX = 480;
export const SIDE_DEFAULT = 360;
/** regular(720~1279)에서 펼친 사이드바의 상한. 저장값을 바꾸지 않고 그리는 폭만 누른다. */
export const SIDE_REGULAR_CAP = 260;
/** 사이드바가 창에서 가져갈 수 있는 최대 비율(40vw). */
export const SIDE_VW_RATIO = 0.4;
/** 활동 막대와 접힌 세션 띠의 폭. */
export const ACTIVITY_W = 48;
export const STRIP_W = 48;

export const SIDE_WIDTH_KEY = 'ui.side.width';
export const SIDE_ACTIVITY_KEY = 'ui.side.activity';
export const SIDE_COLLAPSED_KEY = 'ui.side.collapsed';
/** 폭 폴백 순서 — 마지막으로 쓰던 도구 패널(dock) 폭이 세션 레일 폭보다 사이드바에 가깝다. */
export const SIDE_WIDTH_FALLBACK_KEYS = ['ui.dock.width', 'ui.rail.width'];
export const SIDE_COLLAPSED_FALLBACK_KEYS = ['ui.rail.collapsed'];
export const SESSIONS_ACTIVITY = 'sessions';

/** 저장 폭을 240~480 정수로. 숫자가 아니면 기본 360. */
export function clampSideWidth(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return SIDE_DEFAULT;
  return Math.min(SIDE_MAX, Math.max(SIDE_MIN, Math.round(v)));
}

// get(key) → 값, has(key) → 사용자가 정한 값이 있는가(기본값과 구분). core/settings.js의 get/has.
function firstStored(keys, get, has) {
  for (const k of keys) if (has(k)) return { key: k, value: get(k) };
  return null;
}

/** 펼쳤을 때 쓰고 싶은 폭(사용자 의도). 새 키 → `ui.dock.width` → `ui.rail.width` → 360. */
export function readSideWidth(get, has) {
  const hit = firstStored([SIDE_WIDTH_KEY, ...SIDE_WIDTH_FALLBACK_KEYS], get, has);
  return hit ? clampSideWidth(hit.value) : SIDE_DEFAULT;
}

/**
 * 접힘 여부. 새 키 → `ui.rail.collapsed` → 티어 기본값(wide 미만이면 접힘으로 시작).
 * 저장값이 있으면 언제나 그게 이긴다 — 사용자가 직접 정한 것이므로.
 */
export function readSideCollapsed(get, has, viewportWidth) {
  const hit = firstStored([SIDE_COLLAPSED_KEY, ...SIDE_COLLAPSED_FALLBACK_KEYS], get, has);
  if (hit) return Boolean(hit.value);
  return Number(viewportWidth) < REGULAR_MAX;
}

/** 저장된 활동 id. 없거나 빈 값이면 세션. 지금 쓸 수 있는지는 effectiveActivity가 따로 본다. */
export function readSideActivity(get, has) {
  const v = has(SIDE_ACTIVITY_KEY) ? String(get(SIDE_ACTIVITY_KEY) || '') : '';
  return v || SESSIONS_ACTIVITY;
}

/**
 * 지금 화면에 쓸 활동. 저장된 활동이 목록에 없으면(포트·사용량이 이 서버에서 꺼졌거나 아직
 * capabilities가 안 왔으면) 세션으로 물러난다. 저장값은 건드리지 않는다 — capabilities가
 * 도착하면 원래 활동으로 돌아온다(폭은 공유라 그 사이 칸 크기는 변하지 않는다).
 */
export function effectiveActivity(stored, availableIds) {
  return availableIds.includes(stored) ? stored : SESSIONS_ACTIVITY;
}

/**
 * 사이드바의 표시 상태.
 *   'open'   — 저장 폭(창 폭으로 한 번 더 누른 값)
 *   'strip'  — 세션 활동을 접었을 때의 48px 띠(지금 레일의 접힘 모양)
 *   'closed' — 도구 활동을 접었을 때 0(활동 막대만 남는다)
 */
export function sideMode(collapsed, activity) {
  if (!collapsed) return 'open';
  return activity === SESSIONS_ACTIVITY ? 'strip' : 'closed';
}

/**
 * 실제로 그려지는 사이드바 폭(px). CSS(90-workbench.css)의 `--vt-side-w` 식과 같은 규칙이다 —
 * 화면은 CSS가 그리고, 이 함수는 테스트와 리사이저 시작값 계산에 쓴다.
 *   open: min(저장 폭, 40vw), regular(720~1279)는 거기에 260을 한 번 더 건다.
 *   compact(<720)는 작업대 자체가 없어 0.
 */
export function renderedSideWidth(mode, stored, viewportWidth, compactMax = 720) {
  const vw = Number(viewportWidth) || 0;
  if (vw < compactMax) return 0;
  if (mode === 'strip') return STRIP_W;
  if (mode === 'closed') return 0;
  let w = Math.min(clampSideWidth(stored), Math.floor(vw * SIDE_VW_RATIO));
  if (vw < REGULAR_MAX) w = Math.min(w, SIDE_REGULAR_CAP);
  return w;
}

/**
 * 활동 버튼을 눌렀을 때의 다음 상태. 펼친 상태에서 지금 활동을 다시 누르면 접고,
 * 그 밖에는 그 활동으로 펼친다(접혀 있었으면 펼치면서 바꾼다).
 */
export function nextOnActivityClick(state, id) {
  if (!state.collapsed && state.activity === id) return { activity: id, collapsed: true };
  return { activity: id, collapsed: false };
}

/** 리사이저가 끄는 동안의 폭 — 시작 폭 + 이동량을 240~480으로. */
export function dragSideWidth(startWidth, dx) {
  return clampSideWidth(Number(startWidth) + Number(dx));
}
