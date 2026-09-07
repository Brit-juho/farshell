// S3 — 키맵 레지스트리. 지금까지 단축키는 6곳에 하드코딩돼 있었고, 그 결과
// **커스터마이즈 불가 · 충돌 감지 불가 · 무엇이 바인딩됐는지 볼 방법도 없었다**
// (가이드 모달에 일부만 산문으로 적혀 있었다).
//
// 특히 `Mod+F`는 셸에서 `forward-char`인데 웹이 가로채고 사용자가 되돌릴 방법이
// 없었다 — 그래서 이 파일의 핵심 기능은 재바인딩이 아니라 **`passthrough`**다:
// "이 키는 내가 안 먹고 터미널에 그대로 넘긴다"를 사용자가 선택할 수 있어야 한다.
//
// 표기법: `Mod+Shift+F` 형태. `Mod`는 macOS에서 Cmd, 그 외에서 Ctrl로 해석된다
// (두 플랫폼에 각각 기본값을 적지 않아도 되고, 사용자가 바꾼 값도 그대로 이식된다).
import { get as setting, set as setSetting, subscribe as onSettings } from './settings.js';

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '');

// PWA standalone(홈 화면에서 실행)인지. 일반 브라우저 탭에서는 `Mod+W`처럼
// 브라우저가 먼저 먹는 키를 **가로챌 수 없다** — 조용히 안 되는 게 최악이므로
// 그런 바인딩은 아예 비활성으로 표시하고 설정 화면에서 그 사실을 알린다.
export function isStandalone() {
  try {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  } catch (_) {
    return false;
  }
}

// 브라우저가 먼저 소비해 일반 탭에서는 가로챌 수 없는 조합.
// (`Mod+W` 탭 닫기, `Mod+N` 새 창, `Mod+T` 새 탭, `Mod+Q` 종료)
const BROWSER_RESERVED = new Set(['Mod+W', 'Mod+N', 'Mod+T', 'Mod+Q']);

// 기본 바인딩. `passthrough: true`면 동작을 실행한 뒤에도 터미널로 키를 흘린다
// (기본은 false = 우리가 먹는다).
export const ACTIONS = [
  { id: 'search',     def: 'Mod+F',        label: '터미널 내 검색' },
  { id: 'palette',    def: 'Mod+K',        label: '커맨드 팔레트' },
  { id: 'viewer',     def: 'Ctrl+Shift+E', label: '코드 뷰어' },
  { id: 'paste',      def: 'Ctrl+Shift+V', label: '붙여넣기' },
  { id: 'tabPrev',    def: 'Mod+Shift+ArrowLeft',  label: '이전 탭' },
  { id: 'tabNext',    def: 'Mod+Shift+ArrowRight', label: '다음 탭' },
  { id: 'settings',   def: 'Mod+,',        label: '설정' },
  { id: 'splitRight', def: 'Mod+D',        label: '오른쪽 분할' },
  { id: 'splitDown',  def: 'Mod+Shift+D',  label: '아래 분할' },
  // 계획서 경고 그대로: `Mod+W`는 브라우저 탭 닫기와 충돌한다. PWA에서만 가로챌
  // 수 있으므로 **기본값을 `Mod+Shift+W`로** 둔다(조용히 안 되는 것이 최악).
  { id: 'paneClose',  def: 'Mod+Shift+W',  label: 'pane 닫기' },
  { id: 'railToggle', def: 'Mod+B',        label: '사이드바 토글' },
];

const BY_ID = new Map(ACTIONS.map((a) => [a.id, a]));

// ── 설정 저장 ─────────────────────────────────────────────────────────────
// 키맵은 설정 스토어(S2)에 한 덩어리로 저장한다. 키 하나에 항목이 십수 개라
// 스키마를 항목마다 만들면 스키마가 키맵의 사본이 되어 반드시 어긋난다.
const STORE_KEY = 'keymap.overrides';

function _overrides() {
  const raw = setting(STORE_KEY);
  if (!raw) return {};
  try {
    return typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch (_) {
    return {};
  }
}

// { binding?: string, passthrough?: boolean } 형태의 사용자 재정의
export function getBinding(id) {
  const spec = BY_ID.get(id);
  if (!spec) return null;
  const ov = _overrides()[id] || {};
  return {
    id,
    label: spec.label,
    combo: ov.binding || spec.def,
    passthrough: ov.passthrough ?? false,
    isDefault: !ov.binding && ov.passthrough === undefined,
    // 일반 브라우저 탭에서 이 조합이 가로채지지 않는다면 그 사실을 함께 알린다.
    unavailable: BROWSER_RESERVED.has(ov.binding || spec.def) && !isStandalone(),
  };
}

export function list() {
  return ACTIONS.map((a) => getBinding(a.id));
}

// 같은 조합에 둘 이상 바인딩됐는지. { 'Mod+F': ['search', 'other'] } 형태로
// 충돌만 반환한다(정상 바인딩은 안 담는다).
export function conflicts() {
  const byCombo = new Map();
  for (const b of list()) {
    const key = normalize(b.combo);
    if (!byCombo.has(key)) byCombo.set(key, []);
    byCombo.get(key).push(b.id);
  }
  const out = {};
  for (const [combo, ids] of byCombo) if (ids.length > 1) out[combo] = ids;
  return out;
}

export function setBinding(id, combo) {
  if (!BY_ID.has(id)) return Promise.resolve(false);
  const ov = { ..._overrides() };
  ov[id] = { ...(ov[id] || {}), binding: combo || undefined };
  if (!combo) delete ov[id].binding;
  return setSetting(STORE_KEY, JSON.stringify(ov));
}

export function setPassthrough(id, on) {
  if (!BY_ID.has(id)) return Promise.resolve(false);
  const ov = { ..._overrides() };
  ov[id] = { ...(ov[id] || {}), passthrough: !!on };
  return setSetting(STORE_KEY, JSON.stringify(ov));
}

export function reset(id) {
  const ov = { ..._overrides() };
  delete ov[id];
  return setSetting(STORE_KEY, JSON.stringify(ov));
}

// ── 매칭 ──────────────────────────────────────────────────────────────────
// 조합 문자열을 정렬된 표준형으로. 'shift+mod+f' 와 'Mod+Shift+F' 가 같은 것으로
// 비교돼야 충돌 감지가 의미를 갖는다.
export function normalize(combo) {
  if (!combo) return '';
  const parts = String(combo).split('+').map((p) => p.trim()).filter(Boolean);
  const mods = new Set();
  let key = '';
  for (const p of parts) {
    const low = p.toLowerCase();
    if (low === 'mod' || low === 'ctrl' || low === 'control' || low === 'cmd' || low === 'meta' || low === 'shift' || low === 'alt' || low === 'option') {
      mods.add(low === 'control' ? 'ctrl' : low === 'cmd' || low === 'meta' ? 'mod' : low === 'option' ? 'alt' : low);
    } else {
      key = p.length === 1 ? low : p;   // 한 글자는 소문자, ArrowLeft 등은 원형
    }
  }
  const order = ['mod', 'ctrl', 'alt', 'shift'];
  return [...order.filter((m) => mods.has(m)), key].join('+');
}

// KeyboardEvent → 표준형 조합 문자열.
export function comboFromEvent(e) {
  const mods = [];
  const modPressed = IS_MAC ? e.metaKey : e.ctrlKey;
  if (modPressed) mods.push('mod');
  if (!IS_MAC && e.metaKey) mods.push('mod');           // 리눅스/윈도의 Meta도 Mod로
  if (e.ctrlKey && IS_MAC) mods.push('ctrl');           // macOS에서 Ctrl은 Mod와 별개
  if (e.altKey) mods.push('alt');
  if (e.shiftKey) mods.push('shift');
  let key = e.key;
  if (key === ' ') key = 'Space';
  if (key && key.length === 1) key = key.toLowerCase();
  const order = ['mod', 'ctrl', 'alt', 'shift'];
  return [...order.filter((m) => mods.includes(m)), key].join('+');
}

// 눌린 키가 어떤 액션인지. 없으면 null.
export function match(e) {
  const combo = comboFromEvent(e);
  for (const b of list()) {
    if (b.unavailable) continue;      // 일반 탭에서 못 잡는 조합은 매칭도 안 한다
    if (normalize(b.combo) === combo) return b;
  }
  return null;
}

// ── 실행 ──────────────────────────────────────────────────────────────────
const _handlers = new Map();

// 액션 하나에 실행 함수를 등록한다. 같은 id에 두 번 등록하면 나중 것이 이긴다
// (모듈 재평가 대비 — 테스트에서 특히).
export function register(id, fn) {
  _handlers.set(id, fn);
  return () => { if (_handlers.get(id) === fn) _handlers.delete(id); };
}

// document capture 단계에서 한 번만 듣는다. 여기저기서 각자 keydown을 듣던 것을
// 이 한 곳으로 모으는 것이 S3의 목적이다.
export function handleKeydown(e) {
  const b = match(e);
  if (!b) return false;
  const fn = _handlers.get(b.id);
  if (!fn) return false;
  // passthrough면 동작은 하되 키를 터미널로도 흘린다 — `Mod+F`를 셸의
  // forward-char로 되돌리고 싶은 사용자를 위한 탈출구.
  if (!b.passthrough) {
    e.preventDefault();
    e.stopPropagation();
  }
  try {
    fn(e);
  } catch (err) {
    console.warn(`[keymap] ${b.id} 실행 실패:`, err);
  }
  return !b.passthrough;
}

let _wired = false;
export function wire() {
  if (_wired || typeof document === 'undefined') return;
  _wired = true;
  document.addEventListener('keydown', handleKeydown, true);
}

// 모듈 평가 시점에 바로 건다. 등록(register) 순서와 무관하다 — handleKeydown이
// 키를 받은 **그 순간** 핸들러 맵을 조회하기 때문이다. 이렇게 해두면 이 모듈을
// import한 것만으로 단축키가 살아나서, 일부 모듈만 로드하는 단위 테스트에서도
// 실제와 같은 경로가 성립한다(quickopen.test.js가 그 케이스).
wire();

// 설정이 바뀌면(다른 기기에서 재바인딩 등) 다음 키 입력부터 자동으로 새 값이
// 적용된다 — list()/match()가 매번 스토어를 읽기 때문에 별도 갱신이 필요 없다.
// 구독은 충돌 경고를 콘솔에 한 번 남기기 위한 것뿐이다.
onSettings((changed) => {
  if (!changed || !(STORE_KEY in changed)) return;
  const c = conflicts();
  if (Object.keys(c).length) console.warn('[keymap] 충돌:', c);
});

// 사람이 읽는 표기 — 팔레트·설정 화면이 쓴다(`Mod` → 플랫폼 기호).
export function displayCombo(combo) {
  return String(combo || '')
    .split('+')
    .map((p) => {
      const low = p.toLowerCase();
      if (low === 'mod') return IS_MAC ? '⌘' : 'Ctrl';
      if (low === 'shift') return IS_MAC ? '⇧' : 'Shift';
      if (low === 'alt') return IS_MAC ? '⌥' : 'Alt';
      if (low === 'ctrl') return IS_MAC ? '⌃' : 'Ctrl';
      if (p === 'ArrowLeft') return '←';
      if (p === 'ArrowRight') return '→';
      if (p === 'ArrowUp') return '↑';
      if (p === 'ArrowDown') return '↓';
      return p.length === 1 ? p.toUpperCase() : p;
    })
    .join(IS_MAC ? '' : '+');
}
