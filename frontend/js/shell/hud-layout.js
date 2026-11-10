// 상태 막대(옛 메뉴바·하단바, HUD) 배치 규칙 — 어떤 칩을 어느 쪽에 어떤 순서로 둘지(2026-10-23; 2026-11 맨 위
// 메뉴바로 갔다가, 리디자인 2단계에서 아래 상태 막대 24로 돌아왔다).
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
//
// **에이전트 칩은 배치 밖이다**(세대 3). 상태 막대 맨 왼쪽에 고정이라 옮기거나 끌 수 없다 — 이 제품이 있는
// 이유(개입이 필요한 세션)를 알리는 유일한 칩이고, 설정을 한 번 잘못 만져 그게 사라지면 되찾을 길을 모른다.
/** @typedef {'server'|'screens'|'e2e'|'safe-mode'|'cpu'|'gpu'|'ram'|'net'|'usage'|'version'|'agents'|'hooks'|'push'|'clock'|'account'} HudKind */
/** @typedef {{ left: HudKind[], right: HudKind[] }} HudLayout */
/** @typedef {'left'|'right'|'off'} HudZone */

/** 배치로 옮길 수 있는 종류. @type {readonly HudKind[]} */
export const HUD_KINDS = [
  'server', 'screens', 'e2e', 'safe-mode', 'cpu', 'gpu', 'ram', 'net', 'usage', 'version',
  'push', 'clock', 'account',
];

/** 늘 상태 막대 맨 왼쪽에 고정된 종류(배치에 적지 않는다). @type {readonly HudKind[]} */
export const PINNED_HUD_KINDS = ['agents'];

/**
 * 이상이 있을 때만 생기는 경고 칩 — 배치(설정 화면)와 상관없이 고정 칩 바로 다음에 나온다. 설정을 잘못 만져 경고가 사라지면
 * 되찾을 길이 없어서 끌 수 없게 배치 밖에 둔다(고정 칩과 달리 설정 목록에는 나오지 않는다). `hooks` = 훅 점검 필요.
 * @type {readonly HudKind[]}
 */
export const ALERT_HUD_KINDS = ['hooks'];

/**
 * 기본 배치. 「서버 :7777」은 뺐다(사용자 요청, 2026-10-23) — 지금 이 화면을 보고 있다는
 * 것 자체가 서버가 떠 있다는 증거라 맨 왼쪽 자리를 차지할 값어치가 없다. 필요하면 설정
 * → 상태 막대에서 다시 켠다.
 * @type {HudLayout}
 */
export const DEFAULT_HUD_LAYOUT = {
  // 고정된 에이전트 칩 다음 — 연결·보안·화면 순(시안 D01: 서버 · E2E · 세이프모드 · 연결된 화면).
  left: ['e2e', 'safe-mode', 'screens'],
  right: ['cpu', 'gpu', 'ram', 'net', 'account', 'usage', 'push', 'version', 'clock'],
};

// 저장 형식의 세대. 2026-11 메뉴바 이전(세대 표시 없음)에 저장된 배치는 새 종류(agents·push·clock)가
// 「꺼져 있는 것」이 아니라 「그때는 없던 것」이다 — 그대로 두면 이전에 한 번이라도 배치를 만진 사람은
// 새 칩을 영영 못 본다(배치에 없는 종류는 안 보인다는 규칙). 그래서 세대 표시가 없는 배치를 읽을 때
// 새 종류를 기본 자리에 **한 번** 얹고, 저장할 때 세대를 적는다. 전부 끈 배치는 사용자의 선택이라 건드리지 않는다.
//
// 세대 3(리디자인 2단계): 에이전트 칩이 배치 밖으로 나가 고정됐다. 옛 배치에 적힌 `agents`는 읽을 때 버리고
// (모르는 종류처럼), 저장하면 세대 3이 적힌다. 세대 2 → 3에서 사용자가 옮길 수 있는 종류는 그대로라 따로 얹을 것은 없다.
//
// 세대 4(계정 칩): `account`(지금 기본 계정과 사용률 — 누르면 기본 계정을 바꾼다)가 새 종류로 늘었다. 세대 3 이하 배치를 읽을 때
// 「사용량」 앞(없으면 오른쪽 끝)에 **한 번** 얹는다. 계정이 하나도 없거나 다른 맥을 보는 중이면 값이 없어 칩이 안 그려진다.
export const HUD_LAYOUT_VERSION = 4;
/** @type {ReadonlyArray<{kind: HudKind, zone: 'left'|'right', front?: boolean}>} */
const ADDED_IN_V2 = [
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
 * 비었거나 깨졌거나 낡았다고 상태 막대가 사라지면 사용자는 왜인지 알 길이 없다.
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
  // 세대 표시가 없는(2 이전) 배치만 새 종류를 얹는다 — 세대 2는 이미 push·clock을 아는 배치다.
  const legacy = !(Number(v.v) >= 2);
  const filled = out.left.length || out.right.length;
  if (legacy && filled) {
    for (const { kind, zone, front } of ADDED_IN_V2) {
      if (seen.has(kind)) continue;
      if (front) out[zone].unshift(kind); else out[zone].push(kind);
    }
  }
  // 세대 4 미만: 계정 칩을 「사용량」 앞에(사용량을 끈 배치면 오른쪽 끝에). 전부 끈 배치는 사용자의 선택이라 건드리지 않는다.
  if (!(Number(v.v) >= 4) && filled && !seen.has('account')) {
    const at = out.right.indexOf('usage');
    if (at >= 0) out.right.splice(at, 0, 'account'); else out.right.push('account');
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
  // 고정 종류는 배치 밖이다 — 어떤 경로(드래그·선택·버튼)로 와도 배치를 바꾸지 않는다.
  if (!KIND_SET.has(kind)) return layout;
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
