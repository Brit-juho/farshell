// S2 — 설정 스토어. **설정의 단일 진실은 `/api/workspace`의 `settings`**(ADR-5).
// localStorage는 서버 응답 도착 전 깜빡임을 막는 **프리렌더 캐시로만** 쓴다.
//
// 왜 필요한가: 지금 설정은 세 군데로 흩어져 있고(각 모듈이 직접
// `localStorage.getItem`), 일부는 UI가 아예 없다. 같은 키를 두 파일이 각자 읽는
// 코드가 실제로 있었다(`vt_autocopy_on_select` — selection.js·moreMenu.js·
// voice/notify.js 세 곳). 그래서 "폰에서 바꾼 설정이 맥에 반영되지 않는다"는
// 문제 이전에, 한 화면 안에서도 값이 갈릴 수 있는 구조였다.
//
// 규칙:
//   - 변경은 **즉시 반영**된다(재시작 없음). 구독자에게 알린 뒤 서버에 PUT.
//   - **쓰기 실패는 조용히 큐잉하지 않는다.** 토스트로 알리고 캐시만 갱신하며,
//     다음 로드 때 서버 값이 이긴다. 조용한 유실보다 눈에 보이는 실패가 낫다.
//   - `migrate`가 지정된 키는 **기존 localStorage 값에서 1회 이관**한다.
//     이관 후 원본 키는 지우지 않는다(롤백 여지 — 2.0이 끝나면 정리한다).
import { vtFetch } from './api.js';

const CACHE_KEY = 'vt-settings-v1';

// parse: 옛 localStorage 문자열 → 새 값. 없으면 그대로 쓴다.
const onOff = (v) => v !== 'off';

export const SCHEMA = {
  'terminal.fontSize':      { type: 'int',  def: 14, min: 8, max: 28, migrate: 'vt_font_size' },
  'terminal.cursorStyle':   { type: 'enum', def: 'block', values: ['block', 'underline', 'bar'] },
  'terminal.cursorBlink':   { type: 'bool', def: true },
  'terminal.scrollback':    { type: 'int',  def: 2000, min: 500, max: 10000 },
  // S1 spike 성공 — off면 앱의 마우스 리포팅을 무시하고 항상 로컬 선택(iTerm2 기본 동작).
  'mouse.forwardToApp':     { type: 'bool', def: true },
  'mouse.autocopyOnSelect': { type: 'bool', def: true, migrate: 'vt_autocopy_on_select', parse: onOff },
  // 터치 기기에서 짧은 탭을 앱으로 합성 전달하는 기존 동작(term/xterm-setup.js).
  // 지금까지 끌 방법이 없었다.
  'mouse.touchTapToApp':    { type: 'bool', def: true },
  'a11y.screenReader':      { type: 'enum', def: 'auto', values: ['auto', 'on', 'off'], migrate: 'vt-a11y',
                              parse: (v) => (v === '1' ? 'on' : v === '0' ? 'off' : 'auto') },
  'theme.skin':             { type: 'str',  def: 'farshell', migrate: 'vt-skin' },
  'keybar.collapsed':       { type: 'bool', def: false, migrate: 'vt_keybar_collapsed', parse: (v) => v === '1' },
  // S3: 키맵 재정의({id: {binding, passthrough}})를 JSON 문자열로 한 덩어리
  // 저장한다. 항목마다 스키마 키를 만들면 스키마가 키맵의 사본이 되어 반드시
  // 어긋난다 — 키맵의 진짜 스키마는 core/keymap.js의 ACTIONS 하나뿐이다.
  'keymap.overrides':       { type: 'str', def: '{}' },
  // L8: 우측 레일(사용량) 접힘 상태. 폭은 지금 섹션이 하나뿐이라 고정으로 두고,
  // 섹션이 늘어나면(변경 파일·최근 diff) 그때 폭도 설정으로 뺀다.
  'rightRail.collapsed':    { type: 'bool', def: false },
};

let _values = {};              // 사용자가 실제로 바꾼 값만 (기본값은 안 담는다)
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
  return key in _values ? _values[key] : spec.def;
}

// 사용자가 **명시적으로 정한 값이 있는가**(기본값과 구분). 기기별 기본이 따로
// 있는 항목(모바일 폰트 12 vs 데스크톱 14)에서 "설정 없음"과 "설정이 기본값과
// 같음"을 구분해야 해서 필요하다.
export function has(key) {
  return key in _values;
}

export function getAll() {
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

function _writeCache() {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(_values)); } catch (_) {}
}

// 값 하나를 바꾼다. 반환은 서버 저장까지 끝난 Promise지만, **화면 반영은
// 기다리지 않는다**(구독자 통지가 먼저다 — 설정은 즉시 반영이 원칙).
export function set(key, value) {
  const spec = SCHEMA[key];
  if (!spec) { console.warn(`[settings] 알 수 없는 키: ${key}`); return Promise.resolve(false); }
  const v = _coerce(key, value);
  if (v === undefined) { console.warn(`[settings] ${key}에 맞지 않는 값: ${value}`); return Promise.resolve(false); }
  if (get(key) === v) return Promise.resolve(true);
  _values[key] = v;
  _writeCache();
  _notify({ [key]: v });
  return _push();
}

function _push() {
  return vtFetch('/api/workspace', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: _values }),
  }).then(() => true).catch(() => {
    // 조용히 재시도 큐에 넣지 않는다 — 사용자는 "저장됐다"고 믿고 다른 기기에서
    // 열었다가 값이 없는 것을 보게 된다. 그 자리에서 알린다.
    if (typeof window.showToast === 'function') {
      window.showToast('설정을 서버에 저장하지 못했습니다 (이 기기에서만 적용됨)', 'warn');
    }
    return false;
  });
}

// 기존 localStorage 키에서 1회 이관. 이미 값이 있는 키는 건드리지 않는다.
function _migrate() {
  let migrated = false;
  for (const [key, spec] of Object.entries(SCHEMA)) {
    if (!spec.migrate || key in _values) continue;
    let raw = null;
    try { raw = localStorage.getItem(spec.migrate); } catch (_) { continue; }
    if (raw === null) continue;
    const v = _coerce(key, spec.parse ? spec.parse(raw) : raw);
    if (v !== undefined) { _values[key] = v; migrated = true; }
  }
  return migrated;
}

// 캐시 + 마이그레이션은 **모듈 평가 시점에 동기로** 끝낸다. get()을 부르는
// 쪽(xterm 인스턴스 생성, 테마 적용 등)이 부팅 아주 초기에 돌기 때문에, 서버
// 왕복을 기다리면 첫 화면이 기본값으로 그려졌다가 값이 튀어 보인다.
let _migratedAtBoot = false;
function _initSync() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (raw) _values = { ...JSON.parse(raw) };
  } catch (_) { _values = {}; }
  _migratedAtBoot = _migrate();
  if (_migratedAtBoot) _writeCache();
}
_initSync();

// 서버 동기화. 캐시로 이미 그려진 뒤에 돌고, **서버 값이 이긴다**
// (ADR-5: 단일 진실은 `/api/workspace`).
export async function load() {
  const migrated = _migratedAtBoot;
  _loaded = true;
  try {
    const ws = await vtFetch('/api/workspace');
    const remote = ws && ws.settings;
    if (remote && typeof remote === 'object') {
      const next = {};
      for (const [k, v] of Object.entries(remote)) {
        const c = _coerce(k, v);
        if (c !== undefined) next[k] = c;
      }
      // 서버에 아직 아무것도 없고 로컬에서 막 이관한 값이 있으면 그걸 올린다
      // (이관 결과가 이 기기에만 남지 않게).
      if (Object.keys(next).length === 0 && migrated) {
        _push();
      } else {
        _values = next;
        _writeCache();
        _notify(getAll());
      }
    } else if (migrated) {
      _push();
    }
  } catch (_) {
    // 서버 미응답 — 캐시 값으로 계속 동작한다. 설정 화면도 그대로 열린다.
  }
  return getAll();
}

export function isLoaded() {
  return _loaded;
}

// 테스트 전용 — 모듈 스코프 싱글톤을 초기화한다.
export function _reset() {
  _values = {};
  _loaded = false;
  _listeners.clear();
}
