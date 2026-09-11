// S2 — 설정 스토어. **설정의 단일 진실은 스코프별로 갈린다**(ADR-5 확장,
// N3/60-settings-palette.md §1): `global` 키는 `/api/workspace`의 `settings`,
// `device` 키는 `/api/device-settings`. localStorage는 두 경우 모두 서버
// 응답 도착 전 깜빡임을 막는 **프리렌더 캐시로만** 쓴다.
//
// 왜 스코프가 갈리나: 폰에서 핀치줌으로 키운 폰트 크기나 접어둔 rail 폭이
// `/api/workspace`(전역)에 같이 저장되면, 그 값이 맥 터미널에도 그대로
// 반영된다 — "폰에서 조절했더니 맥 화면이 커졌다"는 실제로 있었던 문제다
// (N36/N35 커밋에서 rail/dock 폭을 device-settings가 없어 임시로
// localStorage 전용으로 둔 이유이기도 하다 — 이 파일이 그 임시 상태를
// 정식 스토어로 흡수한다).
//
// 왜 필요했나(S2 원 동기, 그대로 유지): 같은 키를 두 파일이 각자 읽는 코드가
// 실제로 있었다(`vt_autocopy_on_select` — selection.js·moreMenu.js·
// voice/notify.js 세 곳).
//
// 규칙:
//   - 변경은 **즉시 반영**된다(재시작 없음). 구독자에게 알린 뒤 서버에 PUT.
//   - **쓰기 실패는 조용히 큐잉하지 않는다.** 토스트로 알리고 캐시만 갱신하며,
//     다음 로드 때 서버 값이 이긴다. 조용한 유실보다 눈에 보이는 실패가 낫다.
//   - `migrate`가 지정된 키는 **기존 localStorage 값에서 1회 이관**한다.
//     이관 후 원본 키는 지우지 않는다(롤백 여지 — 2.0이 끝나면 정리한다).
//   - **스코프는 스키마가 정한다 — 사용자가 바꾸지 않는다**(문서 §1의 화면
//     2b 규칙: 배지는 읽기 전용).
import { vtFetch } from './api.js';

// parse: 옛 localStorage 문자열 → 새 값. 없으면 그대로 쓴다.
const onOff = (v) => v !== 'off';

export const SCHEMA = {
  // scope 없음 = 'global'(문서 §1: "기본은 global").
  'terminal.fontSize':      { type: 'int',  def: 14, min: 8, max: 28, migrate: 'vt_font_size', scope: 'device' },
  'terminal.cursorStyle':   { type: 'enum', def: 'block', values: ['block', 'underline', 'bar'] },
  'terminal.cursorBlink':   { type: 'bool', def: true },
  'terminal.scrollback':    { type: 'int',  def: 2000, min: 500, max: 10000 },
  // S1 spike 성공 — off면 앱의 마우스 리포팅을 무시하고 항상 로컬 선택(iTerm2 기본 동작).
  // 문서 §1 표의 "mouse.forward"에 대응 — 기기 성능/입력 방식에 달린 값이라 device.
  'mouse.forwardToApp':     { type: 'bool', def: true, scope: 'device' },
  'mouse.autocopyOnSelect': { type: 'bool', def: true, migrate: 'vt_autocopy_on_select', parse: onOff },
  // "맥에서도 열기" — 새 세션을 만들 때 서버가 osascript로 iTerm 창도 함께 연다.
  // E2(2026-09-08)에서 스토어로 승격했다. 그전엔 ui/moreMenu.js가 localStorage를
  // 직접 읽고, 소비처(term/session.js·term/tmux-panel.js)는 체크박스 엘리먼트의
  // .checked를 매번 읽는 구조였다 — 값의 주인이 DOM이라 폰에서 켠 게 맥북에
  // 안 넘어갔다. autocopyOnSelect가 S2에서 같은 이유로 먼저 옮겨온 자리다.
  // 기본값은 off — 새 세션마다 맥 터미널 창이 뜨는 걸 원치 않는 사용자가 다수다.
  'session.openOnMac':      { type: 'bool', def: false, migrate: 'vt_auto_mac', parse: onOff },
  // 터치 기기에서 짧은 탭을 앱으로 합성 전달하는 기존 동작(term/xterm-setup.js).
  // 지금까지 끌 방법이 없었다.
  'mouse.touchTapToApp':    { type: 'bool', def: true },
  'a11y.screenReader':      { type: 'enum', def: 'auto', values: ['auto', 'on', 'off'], migrate: 'vt-a11y',
                              parse: (v) => (v === '1' ? 'on' : v === '0' ? 'off' : 'auto') },
  'theme.skin':             { type: 'str',  def: 'farshell', migrate: 'vt-skin' },
  // 문서 §1 표: "keybar.collapsed · keybar.* → device".
  'keybar.collapsed':       { type: 'bool', def: false, migrate: 'vt_keybar_collapsed', parse: (v) => v === '1', scope: 'device' },
  // S3: 키맵 재정의({id: {binding, passthrough}})를 JSON 문자열로 한 덩어리
  // 저장한다. 항목마다 스키마 키를 만들면 스키마가 키맵의 사본이 되어 반드시
  // 어긋난다 — 키맵의 진짜 스키마는 core/keymap.js의 ACTIONS 하나뿐이다.
  'keymap.overrides':       { type: 'str', def: '{}' },
  // L8: 우측 레일(사용량) 접힘 상태. 폭은 지금 섹션이 하나뿐이라 고정으로 두고,
  // 섹션이 늘어나면(변경 파일·최근 diff) 그때 폭도 설정으로 뺀다.
  // 문서 §1 표: "ui.rail.collapsed/width · ui.dock.* → device"와 같은 부류.
  'rightRail.collapsed':    { type: 'bool', def: false, scope: 'device' },
  // N13(80-multihost-agents.md §3) — 옵트인 스크롤백 영속화. 기본 OFF.
  // 재접속 시 클라이언트가 받는 양(256KB)은 이 설정과 무관하게 항상 그대로다 —
  // 이건 그 뒤의 "더 불러오기"/검색을 위해 서버 디스크에 계속 쌓을지 여부다.
  'scrollback.persist':     { type: 'bool', def: false },

  // N36/N35가 정식 스토어가 생기기 전까지 localStorage 전용으로 뒀던 값들
  // (Rail.tsx/Dock.tsx 커밋 주석 "N3 전이라 임시로 localStorage" 참고).
  // migrate로 그 키들을 그대로 흡수한다 — 이관 후에도 원본 키는 지우지 않는다
  // (위 파일 상단 규칙과 동일).
  'ui.rail.collapsed':      { type: 'bool', def: false, migrate: 'vt-rail-collapsed', parse: (v) => v === '1', scope: 'device' },
  'ui.rail.width':          { type: 'int',  def: 252, min: 240, max: 480, migrate: 'vt-rail-w', scope: 'device' },
  'ui.dock.collapsed':      { type: 'bool', def: false, migrate: 'vt-dock-collapsed', parse: (v) => v === '1', scope: 'device' },
  'ui.dock.width':          { type: 'int',  def: 392, min: 320, max: 560, migrate: 'vt-dock-w', scope: 'device' },
  'ui.dock.tab':            { type: 'str',  def: 'scm', migrate: 'vt-dock-tab', scope: 'device' },

  // N38(70-mobile.md §1) — compact 하단 내비의 시작 탭. 기기 스코프인 이유는
  // rail/dock 폭과 같다: 폰에서 고른 첫 화면이 맥 터미널 시작 화면에 새면 안 된다.
  'ui.mobile.home':         { type: 'enum', def: 'fleet', values: ['fleet', 'terminal'], scope: 'device' },
};

function _scopeOf(key) {
  return SCHEMA[key]?.scope === 'device' ? 'device' : 'global';
}

// 스코프 하나의 상태 뭉치. 두 스코프가 "값 보관 + 캐시 키 + 엔드포인트"라는
// 같은 모양이라 객체 하나로 묶는다 — get/set/load가 이 객체를 스코프로
// 골라 쓰기만 하면 되므로 로직이 중복되지 않는다.
const _stores = {
  global: { values: {}, cacheKey: 'vt-settings-v1' },
  device: { values: {}, cacheKey: 'vt-settings-device-v1' },
};

let _loaded = false;
const _listeners = new Set();

function _coerce(key, raw) {
  const spec = SCHEMA[key];
  if (!spec) return undefined;
  if (raw === undefined || raw === null) return undefined;
  switch (spec.type) {
    case 'int': {
      const n = parseInt(raw, 10);
      if (Number.isNaN(n)) return undefined;
      return Math.min(spec.max ?? n, Math.max(spec.min ?? n, n));
    }
    case 'bool':
      return typeof raw === 'boolean' ? raw : raw === 'true' || raw === '1' || raw === 'on';
    case 'enum':
      return spec.values.includes(raw) ? raw : undefined;
    default:
      return String(raw);
  }
}

export function get(key) {
  const spec = SCHEMA[key];
  if (!spec) {
    // 오타를 조용히 넘기지 않는다 — 없는 키는 설계상 존재할 수 없다.
    console.warn(`[settings] 알 수 없는 키: ${key}`);
    return undefined;
  }
  const values = _stores[_scopeOf(key)].values;
  return key in values ? values[key] : spec.def;
}

// 사용자가 **명시적으로 정한 값이 있는가**(기본값과 구분). 기기별 기본이 따로
// 있는 항목(모바일 폰트 12 vs 데스크톱 14)에서 "설정 없음"과 "설정이 기본값과
// 같음"을 구분해야 해서 필요하다.
export function has(key) {
  return key in _stores[_scopeOf(key)].values;
}

// 화면 2b의 읽기 전용 배지("[기기]"/"[전체]")가 쓴다.
export function scopeOf(key) {
  return _scopeOf(key);
}

function getAll() {
  const out = {};
  for (const key of Object.keys(SCHEMA)) out[key] = get(key);
  return out;
}

export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

function _notify(changed) {
  for (const fn of _listeners) {
    try { fn(changed); } catch (_) { /* 소비자 하나가 터져도 나머지는 받는다 */ }
  }
}

function _writeCache(scope) {
  const s = _stores[scope];
  try { localStorage.setItem(s.cacheKey, JSON.stringify(s.values)); } catch (_) {}
}

// 값 하나를 바꾼다. 반환은 서버 저장까지 끝난 Promise지만, **화면 반영은
// 기다리지 않는다**(구독자 통지가 먼저다 — 설정은 즉시 반영이 원칙).
export function set(key, value) {
  const spec = SCHEMA[key];
  if (!spec) { console.warn(`[settings] 알 수 없는 키: ${key}`); return Promise.resolve(false); }
  const v = _coerce(key, value);
  if (v === undefined) { console.warn(`[settings] ${key}에 맞지 않는 값: ${value}`); return Promise.resolve(false); }
  if (get(key) === v) return Promise.resolve(true);
  const scope = _scopeOf(key);
  _stores[scope].values[key] = v;
  _writeCache(scope);
  _notify({ [key]: v });
  return scope === 'device' ? _pushDevice() : _pushGlobal();
}

function _pushFailToast() {
  if (typeof window.showToast === 'function') {
    window.showToast('설정을 서버에 저장하지 못했습니다 (이 기기에서만 적용됨)', 'warn');
  }
}

function _pushGlobal() {
  return vtFetch('/api/workspace', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: _stores.global.values }),
  }).then(() => true).catch(() => {
    // 조용히 재시도 큐에 넣지 않는다 — 사용자는 "저장됐다"고 믿고 다른 기기에서
    // 열었다가 값이 없는 것을 보게 된다. 그 자리에서 알린다.
    _pushFailToast();
    return false;
  });
}

// device 스토어는 **이 기기만** 쓰므로 /ws-workspace 같은 push 브로드캐스트가
// 없다(파일 상단 주석) — 그냥 이 기기의 저장 요청 하나로 끝난다.
function _pushDevice() {
  return vtFetch('/api/device-settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: _stores.device.values }),
  }).then(() => true).catch(() => {
    _pushFailToast();
    return false;
  });
}

// 기존 localStorage 키에서 1회 이관. 이미 값이 있는 키는 건드리지 않는다.
// 스코프별로 이관 여부를 따로 추적해 반환한다 — global/device가 서로 다른
// 엔드포인트로 올라가므로 "막 이관했으니 올려야 한다" 판단도 스코프별이다.
function _migrate() {
  const migrated = { global: false, device: false };
  for (const [key, spec] of Object.entries(SCHEMA)) {
    const scope = _scopeOf(key);
    if (!spec.migrate || key in _stores[scope].values) continue;
    let raw = null;
    try { raw = localStorage.getItem(spec.migrate); } catch (_) { continue; }
    if (raw === null) continue;
    const v = _coerce(key, spec.parse ? spec.parse(raw) : raw);
    if (v !== undefined) { _stores[scope].values[key] = v; migrated[scope] = true; }
  }
  return migrated;
}

// 캐시 + 마이그레이션은 **모듈 평가 시점에 동기로** 끝낸다. get()을 부르는
// 쪽(xterm 인스턴스 생성, 테마 적용 등)이 부팅 아주 초기에 돌기 때문에, 서버
// 왕복을 기다리면 첫 화면이 기본값으로 그려졌다가 값이 튀어 보인다.
let _migratedAtBoot = { global: false, device: false };
function _initSync() {
  for (const scope of ['global', 'device']) {
    try {
      const raw = localStorage.getItem(_stores[scope].cacheKey);
      if (raw) _stores[scope].values = { ...JSON.parse(raw) };
    } catch (_) { _stores[scope].values = {}; }
  }
  _migratedAtBoot = _migrate();
  for (const scope of ['global', 'device']) if (_migratedAtBoot[scope]) _writeCache(scope);
}
_initSync();

// 서버 응답 하나를 스코프에 반영. 반환값은 "뭔가 바뀌었나"(호출자가 이관분을
// 올릴지 판단하는 데 쓴다).
function _applyRemote(scope, remote) {
  if (!remote || typeof remote !== 'object') return false;
  const next = {};
  for (const [k, v] of Object.entries(remote)) {
    if (_scopeOf(k) !== scope) continue;   // 스키마가 다른 스코프로 옮긴 키 방어
    const c = _coerce(k, v);
    if (c !== undefined) next[k] = c;
  }
  _stores[scope].values = next;
  _writeCache(scope);
  return true;
}

// 서버 동기화. 캐시로 이미 그려진 뒤에 돌고, **서버 값이 이긴다**(ADR-5).
// 두 스코프를 병렬로 가져온다 — 서로 다른 엔드포인트라 순서는 상관없다.
export async function load() {
  const migrated = _migratedAtBoot;
  _loaded = true;
  const [globalResult, deviceResult] = await Promise.allSettled([
    vtFetch('/api/workspace'),
    vtFetch('/api/device-settings'),
  ]);

  if (globalResult.status === 'fulfilled') {
    const remote = globalResult.value && globalResult.value.settings;
    const hadRemote = remote && Object.keys(remote).length > 0;
    if (hadRemote || !migrated.global) _applyRemote('global', remote || {});
    else if (migrated.global) _pushGlobal();   // 서버엔 없고 방금 이관만 됐다 → 올린다
  }
  if (deviceResult.status === 'fulfilled') {
    const remote = deviceResult.value && deviceResult.value.settings;
    const hadRemote = remote && Object.keys(remote).length > 0;
    if (hadRemote || !migrated.device) _applyRemote('device', remote || {});
    else if (migrated.device) _pushDevice();
  }
  // 둘 다 실패해도(오프라인 등) 캐시 값으로 계속 동작한다 — 설정 화면도 그대로 열린다.
  _notify(getAll());
  return getAll();
}

// isLoaded()와 _reset()이 여기 있었다. _reset은 "테스트 전용"이라는 주석을
// 달고 있었지만 frontend/tests/ 어디에서도 쓰이지 않았고, isLoaded는 소비처가
// 아예 없었다 — 2026-09-08 삭제. 테스트에서 싱글톤 초기화가 다시 필요해지면
// 그때 그 테스트와 함께 되살린다.

// N35/N43 — shell/*.tsx(Rail.tsx·Dock.tsx)는 지연 청크라 이 모듈을 정적
// import하면 안 된다(core/store.js와 같은 이유 — Vite lib 모드가 청크마다
// 복제해 별개 싱글톤이 된다). 그 파일들이 window로만 읽도록 브리지를 둔다.
window.vtSettingsGet = get;
window.vtSettingsSet = set;
window.vtSettingsHas = has;
window.vtSettingsSubscribe = subscribe;
