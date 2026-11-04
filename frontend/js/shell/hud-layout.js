// 메뉴바(옛 하단바, HUD) 배치 규칙 — 어떤 칩을 어느 쪽에 어떤 순서로 둘지(2026-10-23, 2026-11 맨 위로 이동).
//
// 순수 함수만 있다(DOM·fetch·Solid 없음). 일반 JS인 이유: HUD(hud-data.ts)와 설정 화면
// (panels/settings/sections/hud.js)이 같은 규칙을 써야 하고, 설정 화면의 단위 테스트 하네스는
// .ts를 직접 읽지 못한다.
//
// 「종류(kind)」는 설정 화면이 다루는 단위다. 사용량 프로필은 서버가 주는 만큼 여러 칩이
// 되지만 사용자가 고르는 건 「사용량」 하나다 — 칩 id(`usage:<이름>`)로 배치를 저장하면
// 프로필이 늘고 줄 때마다 저장값이 어긋난다.
//
// **배치에 없는 종류는 안 보인다**(선택 사항이라 새 종류는 켜기 전까지 꺼져 있다). 값이
// 없으면 숨긴다는 기존 규칙은 그 위에 그대로 있다 — 배치는 「보여도 되는 것」을 정할 뿐
// 값이 없는 칩을 억지로 그리지 않는다.

/** @typedef {'server'|'screens'|'e2e'|'safe-mode'|'cpu'|'gpu'|'ram'|'net'|'usage'|'version'|'agents'|'push'|'clock'} HudKind */
/** @typedef {{ left: HudKind[], right: HudKind[] }} HudLayout */
/** @typedef {'left'|'right'|'off'} HudZone */

/** @type {readonly HudKind[]} */
export const HUD_KINDS = [
  'server', 'screens', 'e2e', 'safe-mode', 'cpu', 'gpu', 'ram', 'net', 'usage', 'version',
  'agents', 'push', 'clock',
];

/**
 * 기본 배치. 「서버 :7777」은 뺐다(사용자 요청, 2026-10-23) — 지금 이 화면을 보고 있다는
 * 것 자체가 서버가 떠 있다는 증거라 맨 왼쪽 자리를 차지할 값어치가 없다. 필요하면 설정
 * → 메뉴바에서 다시 켠다.
 * @type {HudLayout}
 */
export const DEFAULT_HUD_LAYOUT = {
  left: ['agents', 'screens', 'e2e', 'safe-mode'],
  right: ['cpu', 'gpu', 'ram', 'net', 'usage', 'push', 'version', 'clock'],
};

// 저장 형식의 세대. 2026-11 메뉴바 이전(세대 표시 없음)에 저장된 배치는 새 종류(agents·push·clock)가
// 「꺼져 있는 것」이 아니라 「그때는 없던 것」이다 — 그대로 두면 이전에 한 번이라도 배치를 만진 사람은
// 새 칩을 영영 못 본다(배치에 없는 종류는 안 보인다는 규칙). 그래서 세대 표시가 없는 배치를 읽을 때
// 새 종류를 기본 자리에 **한 번** 얹고, 저장할 때 세대를 적는다. 전부 끈 배치는 사용자의 선택이라 건드리지 않는다.
export const HUD_LAYOUT_VERSION = 2;
/** @type {ReadonlyArray<{kind: HudKind, zone: 'left'|'right', front?: boolean}>} */
const ADDED_IN_V2 = [
  { kind: 'agents', zone: 'left', front: true },
  { kind: 'push', zone: 'right' },
  { kind: 'clock', zone: 'right' },
];

const KIND_SET = new Set(HUD_KINDS);

/** @returns {HudLayout} */
function defaultLayout() {
  return { left: [...DEFAULT_HUD_LAYOUT.left], right: [...DEFAULT_HUD_LAYOUT.right] };
}

/**
 * 저장된 값(JSON 문자열) → 배치. **어떤 입력에도 쓸 수 있는 배치를 돌려준다** — 저장값이
 * 비었거나 깨졌거나 낡았다고 메뉴바가 사라지면 사용자는 왜인지 알 길이 없다.
 * 모르는 종류는 버리고(예전 버전이 쓴 값), 같은 종류가 두 번 나오면 먼저 나온 쪽만 남긴다.
 * 전부 끈 배치(`{"left":[],"right":[]}`)는 사용자의 선택이므로 그대로 존중한다.
 * @param {unknown} raw
 * @returns {HudLayout}
 */
export function parseHudLayout(raw) {
  let v = raw;
  if (typeof raw === 'string') {
    if (!raw.trim()) return defaultLayout();
    try { v = JSON.parse(raw); } catch (_) { return defaultLayout(); }
  }
  if (!v || typeof v !== 'object' || !Array.isArray(v.left) || !Array.isArray(v.right)) {
    return defaultLayout();
  }
  const seen = new Set();
  const pick = (arr) => {
    const out = [];
    for (const k of arr) {
      if (typeof k !== 'string' || !KIND_SET.has(k) || seen.has(k)) continue;
      seen.add(k);
      out.push(k);
    }
    return out;
  };
  const left = pick(v.left);
  const out = { left, right: pick(v.right) };
  const legacy = !(Number(v.v) >= HUD_LAYOUT_VERSION);
  if (legacy && (out.left.length || out.right.length)) {
    for (const { kind, zone, front } of ADDED_IN_V2) {
      if (seen.has(kind)) continue;
      if (front) out[zone].unshift(kind); else out[zone].push(kind);
    }
  }
  return out;
}

/** @param {HudLayout} layout */
export function serializeHudLayout(layout) {
  return JSON.stringify({ v: HUD_LAYOUT_VERSION, left: layout.left, right: layout.right });
}

/** @param {HudLayout} a @param {HudLayout} b */
export function sameHudLayout(a, b) {
  return serializeHudLayout(a) === serializeHudLayout(b);
}

/** 종류가 지금 어느 구역에 있나. @param {HudLayout} layout @param {HudKind} kind @returns {HudZone} */
export function hudZoneOf(layout, kind) {
  if (layout.left.includes(kind)) return 'left';
  if (layout.right.includes(kind)) return 'right';
  return 'off';
}

/**
 * 종류 하나를 다른 구역(또는 같은 구역의 다른 자리)으로 옮긴 **새 배치**. 입력은 바꾸지
 * 않는다. `index`는 옮긴 뒤 그 구역에서의 자리(0부터)이고 범위를 넘으면 끝으로 들어간다.
 * 설정 화면의 드래그·위아래 버튼·구역 선택이 전부 이 함수 하나로 모인다 — 세 경로가
 * 각자 배열을 만지면 서로 다른 규칙이 생긴다.
 * @param {HudLayout} layout @param {HudKind} kind @param {HudZone} zone @param {number} [index]
 * @returns {HudLayout}
 */
export function moveHudKind(layout, kind, zone, index) {
  const next = {
    left: layout.left.filter((k) => k !== kind),
    right: layout.right.filter((k) => k !== kind),
  };
  if (zone === 'off') return next;
  const list = next[zone];
  const at = index == null ? list.length : Math.max(0, Math.min(list.length, index));
  list.splice(at, 0, kind);
  return next;
}

/** 같은 구역 안에서 한 칸 위(-1)/아래(+1). 끝이거나 꺼진 칩이면 입력을 그대로 돌려준다.
 * @param {HudLayout} layout @param {HudKind} kind @param {-1|1} dir @returns {HudLayout} */
export function stepHudKind(layout, kind, dir) {
  const zone = hudZoneOf(layout, kind);
  if (zone === 'off') return layout;
  const list = layout[zone];
  const from = list.indexOf(kind);
  const to = from + dir;
  if (to < 0 || to >= list.length) return layout;
  return moveHudKind(layout, kind, zone, to);
}
