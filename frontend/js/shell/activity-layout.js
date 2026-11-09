// 활동 막대 배치 규칙 — 위 무리(활동)의 순서·숨김과 아래 무리(MCP·음성·설정)의 순서(시안 O05 오른쪽 칸).
//
// 순수 함수만 있다(DOM·Solid·설정 스토어 없음). 일반 JS인 이유는 hud-layout.js와 같다: 작업대
// (Workbench.tsx, 지연 청크)와 설정 화면(panels/settings/sections/activity-order.js)이 같은 규칙을 쓰고,
// 설정 화면의 단위 테스트 하네스는 .ts를 직접 읽지 못한다.
//
// 저장값은 기기 설정 `ui.activity.layout` 하나에 `{"v":1,"order":[…],"hidden":[…],"bottom":[…]}` JSON.
// 항목마다 키를 두면 순서를 표현할 수 없고 일부만 저장된 중간 상태가 생긴다(ui.hud.layout과 같은 이유).
//
// 규칙:
//   - 「세션」은 맨 위 고정이고 숨길 수 없다. 이 제품이 있는 이유(개입이 필요한 세션)로 가는 문이라,
//     설정을 한 번 잘못 만져 그게 사라지면 되찾을 길을 모른다(상태 막대의 에이전트 칩과 같은 이유).
//   - 숨긴 활동은 **막대에서만** 빠진다. 팔레트 명령(scm.show 등)과 키맵으로는 그대로 열리고, 그렇게 열면
//     사이드바에 그대로 뜬다 — 활동 버튼이 없을 뿐이다.
//   - 아래 무리는 순서만 바꾼다(숨기지 않는다). 설정 창을 여는 버튼을 숨기면 그걸 되돌릴 화면이 사라진다.
//   - 저장값에 없는 활동(새 버전에서 생긴 것)은 기본 자리 순서대로 끝에 붙는다 — 배치를 한 번 만진 사람이
//     새 활동을 영영 못 보는 일을 막는다. 모르는 id(옛 버전이 쓴 값)는 버린다.

/** 위 무리 활동 id. ⚠ shell/activity-data.ts의 ACTIVITIES와 core/settings.js의 `ui.side.activity` 값 목록과
 * 같아야 한다(frontend/tests/activity-layout.test.js가 대조). */
export const ACTIVITY_IDS = ['sessions', 'scm', 'dir', 'queue', 'files', 'snippets', 'ports', 'usage', 'system'];
/** 맨 위 고정 — 옮기거나 숨기지 않는다. */
export const FIXED_ACTIVITY = 'sessions';
/** 아래 무리(사이드바를 바꾸지 않는 동작). 기본 순서는 지금 막대 그대로다. */
export const BOTTOM_IDS = ['mcp', 'mic', 'settings'];
export const ACTIVITY_LAYOUT_KEY = 'ui.activity.layout';
export const ACTIVITY_LAYOUT_VERSION = 1;

const TOP_SET = new Set(ACTIVITY_IDS);
const BOTTOM_SET = new Set(BOTTOM_IDS);

/** @typedef {{ order: string[], hidden: string[], bottom: string[] }} ActivityLayout */

/** @returns {ActivityLayout} */
export function defaultActivityLayout() {
  return { order: [...ACTIVITY_IDS], hidden: [], bottom: [...BOTTOM_IDS] };
}

function pick(arr, allowed) {
  const out = [];
  if (!Array.isArray(arr)) return out;
  for (const id of arr) {
    if (typeof id !== 'string' || !allowed.has(id) || out.includes(id)) continue;
    out.push(id);
  }
  return out;
}

/**
 * 저장값(JSON 문자열 또는 객체) → 배치. **어떤 입력에도 쓸 수 있는 배치를 돌려준다** — 저장값이 깨졌다고
 * 활동 막대가 비면 사용자는 이유를 알 길이 없다.
 * @param {unknown} raw
 * @returns {ActivityLayout}
 */
export function parseActivityLayout(raw) {
  let v = raw;
  if (typeof raw === 'string') {
    if (!raw.trim()) return defaultActivityLayout();
    try { v = JSON.parse(raw); } catch (_) { return defaultActivityLayout(); }
  }
  if (!v || typeof v !== 'object' || Array.isArray(v)) return defaultActivityLayout();
  const o = /** @type {Record<string, unknown>} */ (v);
  const order = pick(o.order, TOP_SET).filter((id) => id !== FIXED_ACTIVITY);
  for (const id of ACTIVITY_IDS) if (id !== FIXED_ACTIVITY && !order.includes(id)) order.push(id);
  const hidden = pick(o.hidden, TOP_SET).filter((id) => id !== FIXED_ACTIVITY);
  const bottom = pick(o.bottom, BOTTOM_SET);
  for (const id of BOTTOM_IDS) if (!bottom.includes(id)) bottom.push(id);
  return { order: [FIXED_ACTIVITY, ...order], hidden, bottom };
}

/** 기본 배치와 같으면 ''(저장값을 지워 기본을 따르게 한다), 다르면 JSON. @param {ActivityLayout} layout */
export function serializeActivityLayout(layout) {
  const norm = parseActivityLayout(layout);
  if (isDefaultActivityLayout(norm)) return '';
  return JSON.stringify({ v: ACTIVITY_LAYOUT_VERSION, order: norm.order, hidden: norm.hidden, bottom: norm.bottom });
}

/** @param {ActivityLayout} layout */
export function isDefaultActivityLayout(layout) {
  const d = defaultActivityLayout();
  return layout.hidden.length === 0
    && layout.order.join() === d.order.join()
    && layout.bottom.join() === d.bottom.join();
}

/**
 * 같은 무리 안에서 한 칸 위(-1)/아래(+1). 고정 활동·끝·모르는 id면 입력을 그대로 돌려준다(새 객체 아님).
 * 「세션」 바로 아래 항목은 더 올라가지 못한다.
 * @param {ActivityLayout} layout @param {string} id @param {-1|1} dir @returns {ActivityLayout}
 */
export function stepActivity(layout, id, dir) {
  const group = BOTTOM_SET.has(id) ? 'bottom' : 'order';
  const list = layout[group];
  const from = list.indexOf(id);
  const to = from + dir;
  const min = group === 'order' ? 1 : 0;
  if (from < 0 || id === FIXED_ACTIVITY || to < min || to >= list.length) return layout;
  return moveActivity(layout, id, to);
}

/**
 * 같은 무리 안의 `index` 자리로 옮긴 **새 배치**(입력은 바꾸지 않는다). 위 무리에서는 0(세션 자리)을 넘볼 수 없어
 * 1로 누른다. 범위를 넘으면 끝으로. 드래그와 ↑↓ 버튼이 모두 이 함수 하나로 모인다(hud-layout.js와 같은 이유).
 * @param {ActivityLayout} layout @param {string} id @param {number} index @returns {ActivityLayout}
 */
export function moveActivity(layout, id, index) {
  const group = BOTTOM_SET.has(id) ? 'bottom' : (TOP_SET.has(id) ? 'order' : null);
  if (!group || id === FIXED_ACTIVITY) return layout;
  const list = layout[group].filter((x) => x !== id);
  if (list.length === layout[group].length) return layout;
  const min = group === 'order' ? 1 : 0;
  const at = Math.max(min, Math.min(list.length, Math.trunc(Number(index)) || 0));
  list.splice(at, 0, id);
  return { ...layout, [group]: list };
}

/** 위 무리 활동의 숨김을 켜고 끈 **새 배치**. 고정 활동·아래 무리·모르는 id는 그대로.
 * @param {ActivityLayout} layout @param {string} id @param {boolean} hide @returns {ActivityLayout} */
export function setActivityHidden(layout, id, hide) {
  if (!TOP_SET.has(id) || id === FIXED_ACTIVITY) return layout;
  const hidden = layout.hidden.filter((x) => x !== id);
  if (hide) hidden.push(id);
  return { ...layout, hidden };
}

/**
 * 막대에 그릴 활동 — 지금 쓸 수 있는 활동(capability 게이팅 뒤)을 배치 순서로 놓고 숨긴 것을 뺀다.
 * 배치에 없는 활동은 원래 순서대로 끝에 붙는다(파싱이 이미 채우지만 객체를 직접 넘겨도 안전하게).
 * @template {{id: string}} A
 * @param {A[]} available @param {ActivityLayout} layout @returns {A[]}
 */
export function orderedActivities(available, layout) {
  const rank = (id) => {
    const i = layout.order.indexOf(id);
    return i < 0 ? layout.order.length + ACTIVITY_IDS.indexOf(id) : i;
  };
  const hidden = new Set(layout.hidden);
  return available
    .filter((a) => a.id === FIXED_ACTIVITY || !hidden.has(a.id))
    .sort((a, b) => rank(a.id) - rank(b.id));
}

/** 아래 무리 항목의 CSS `order` 값(0부터). 모르는 id면 기본 자리. @param {ActivityLayout} layout @param {string} id */
export function bottomOrderOf(layout, id) {
  const i = layout.bottom.indexOf(id);
  return i < 0 ? BOTTOM_IDS.indexOf(id) : i;
}
