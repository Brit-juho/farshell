// S3 — 키맵 레지스트리. 지금까지 단축키는 6곳에 하드코딩돼 있었고, 그 결과
// **커스터마이즈 불가 · 충돌 감지 불가 · 무엇이 바인딩됐는지 볼 방법도 없었다**
// (가이드 모달에 일부만 산문으로 적혀 있었다).
//
// 특히 `Mod+F`는 셸에서 `forward-char`인데 웹이 가로채고 사용자가 되돌릴 방법이
// 없었다 — 그래서 이 파일의 핵심 기능은 재바인딩이 아니라 **`passthrough`**다:
// 기존 passthrough는 앱과 터미널 양쪽 실행이다. terminal 전달 모드는 앱을 실행하지 않는다.
//
// 표기법: `Mod+Shift+F` 형태. `Mod`는 macOS에서 Cmd, 그 외에서 Ctrl로 해석된다
// 명시적 Meta/Ctrl은 구분하고, 사용자 설정은 기기 종류별 V2로 저장한다.
import { icon } from '../ui/icons.js';
import { get as setting, set as setSetting, subscribe as onSettings } from './settings.js';

import { isMacKeyboard as IS_MAC, isStandalone } from './platform.js';
import { physicalKey, isComposingKey } from './keyboard-event.js';
import { decideKey, wireKeyboardRouter, forwardTerminalKey } from './keyboard-router.js';
import { t } from './i18n.js';
export { isStandalone };

// 브라우저가 먼저 소비해 일반 탭에서는 가로챌 수 없는 조합.
// (`Mod+W` 탭 닫기, `Mod+N` 새 창, `Mod+T` 새 탭, `Mod+Q` 종료,
//  `Mod+Shift+W` **창** 닫기, `Mod+Shift+N` 시크릿 창, `Mod+Shift+T` 탭 복원)
//
// ⚠ 비교는 **정규형으로** 한다. 설정 화면의 재바인딩은 `comboFromEvent`가 만든
// 'mod+shift+w' 같은 소문자 표기를 저장하는데, 예전에는 이 Set을 원문 문자열로
// 검사해서(`has('mod+shift+w')` → false) 사용자가 직접 고른 예약 조합만 쏙 빠졌다
// — 경고도 없이 조용히 안 되는, 이 목록이 막으려던 바로 그 상태다.
const _RESERVED_SPECS = ['Mod+W', 'Mod+N', 'Mod+T', 'Mod+Q', 'Mod+Shift+W', 'Mod+Shift+N', 'Mod+Shift+T'];
function isBrowserReserved(combo) {
  const c = normalize(combo);
  return _RESERVED_SPECS.some((spec) => normalize(spec) === c);
}

// 기본 바인딩. `passthrough: true`면 동작을 실행한 뒤에도 터미널로 키를 흘린다
// (기본은 false = 우리가 먹는다).
//
// `defNonMac` — mac이 아닌 곳에서만 쓰는 기본값. 같은 `Mod`라도 mac에서는
// Cmd라 안전하지만 비-mac에서는 Ctrl이라 **셸/tmux가 실제로 쓰는 키를
// 뺏는다**(2026-09-18 점검): `Mod+F`=Ctrl+F(forward-char), `Mod+K`=Ctrl+K
// (kill-line), `Mod+D`=Ctrl+D(EOF), `Mod+B`=Ctrl+B(tmux prefix). 그리고
// `Mod+Shift+W/N`은 비-mac에서 브라우저의 창 닫기/시크릿 창이라 일반 탭에서는
// 아예 우리에게 오지 않는다.
// 그래서 비-mac은 터미널이 못 만드는 조합만 쓴다 — 1순위 `Ctrl+Shift+…`,
// 그 자리가 이미 찼거나 브라우저 예약이면 `Alt+Shift+…`(AltGr가 있는 유럽식
// 배열을 건드리는 `Ctrl+Alt`는 피한다).
const ACTIONS = [
  // N40/N46(60-settings-palette.md §3) — `Mod+F`는 팔레트를 `~`(스크롤백
  // 검색) 모드로 연다. 예전에 이 자리가 열던 인페인 검색바(xterm
  // searchAddon)는 `searchInPane`(Mod+Shift+F)으로 내려갔다 — R7: 근육기억이
  // 걸린 재배선이라 palette-lazy.js가 최초 1회 토스트를 띄운다.
  { id: 'search',       def: 'Mod+F',        defNonMac: 'Ctrl+Shift+F', label: t('스크롤백 검색') },
  // 리디자인 결정 4 · 파일 개편 단계 6(2026-11) — `Mod+Shift+F`(mac ⌘⇧F)는 파일 **내용 검색**(사이드바 「파일 ›
  // 내용 검색」)으로 넘어갔다. 그 자리에 있던 터미널 내 검색은 비-mac에서 이미 쓰던 `Alt+Shift+F`로 모든 플랫폼을
  // 맞췄다. 사용자가 직접 정한 바인딩은 그대로다(재정의는 id별로 저장되고, `match`가 재정의를 기본값보다 먼저 본다).
  // 비-mac의 `Mod+Shift+F`(Ctrl+Shift+F)는 스크롤백 검색이 쓰고 있어, 내용 검색은 `Ctrl+Shift+G`(grep)다 —
  // 터미널이 만들 수 없는 조합이고 브라우저 예약도 아니다.
  { id: 'searchInPane', def: 'Alt+Shift+F',  label: t('터미널 내 검색') },
  { id: 'contentSearch', def: 'Mod+Shift+F', defNonMac: 'Ctrl+Shift+G', label: t('파일 내용 검색') },
  { id: 'palette',    def: 'Mod+K',        defNonMac: 'Ctrl+Shift+K', label: t('커맨드 팔레트') },
  { id: 'viewer',     def: 'Ctrl+Shift+E', label: t('코드 뷰어') },
  { id: 'paste',      def: 'Ctrl+Shift+V', label: t('붙여넣기') },
  { id: 'settings',   def: 'Mod+,',        label: t('설정') },
  { id: 'splitRight', def: 'Mod+D',        defNonMac: 'Ctrl+Shift+D', label: t('오른쪽 분할') },
  { id: 'splitDown',  def: 'Mod+Shift+D',  defNonMac: 'Alt+Shift+D',  label: t('아래 분할') },
  // 계획서 경고 그대로: `Mod+W`는 브라우저 탭 닫기와 충돌한다 — 그런데 대안으로
  // 뒀던 `Mod+Shift+W`도 **창** 닫기라 일반 탭에서는 똑같이 우리에게 오지 않았다
  // (2026-09-18 점검). mac은 브라우저가 안 쓰는 `Ctrl+Shift+…`로, 비-mac은 그
  // 자리(Ctrl=Mod)가 바로 그 창 닫기라 `Alt+Shift+…`로 간다.
  { id: 'paneClose',  def: 'Ctrl+Shift+W', defNonMac: 'Alt+Shift+W',  label: t('pane 닫기') },
  // 2026-09-18 — 분할 비율은 드래그로만 바뀌고 되돌릴 방법이 없었다.
  // 분할선 더블클릭은 그 분할 하나만, 이 키는 트리 전체를 반반으로 돌린다.
  { id: 'paneEven',   def: 'Mod+Alt+D',    defNonMac: 'Ctrl+Alt+D',   label: t('분할 균등 정렬') },
  { id: 'railToggle', def: 'Mod+B',        defNonMac: 'Alt+Shift+B',  label: t('사이드바 토글') },
  // N44(30-worktree.md §3) — 워크트리 만들기. 시크릿 창이 양쪽 플랫폼에서 이
  // 자리를 먼저 먹는다(mac `Cmd+Shift+N`, 비-mac `Ctrl+Shift+N`) — paneClose와
  // 같은 이유로 갈라 둔다.
  { id: 'worktreeNew', def: 'Ctrl+Shift+N', defNonMac: 'Alt+Shift+N',  label: t('워크트리 만들기') },
  // 리디자인 결정 4 — 집중 모드(layout/focus.js). mac은 ⌘.(브라우저·터미널 어느 쪽도 쓰지 않는다), 그 밖은
  // `Alt+Shift+.` — 비-mac의 Ctrl+.는 터미널 앱이 받을 수 있는 조합이라 피한다. Shift를 누르면 `.`이 `>`로 바뀌므로
  // 글자가 아니라 **물리 키(Period)**로 맞춘다(keyKind 'code', core/keyboard-event.js physicalKey).
  { id: 'focusMode',  def: 'Mod+.',        defNonMac: 'Alt+Shift+.',  label: t('집중 모드') },
];

const BY_ID = new Map(ACTIONS.map((a) => [a.id, a]));

// ── 설정 저장 ─────────────────────────────────────────────────────────────
// 키맵은 설정 스토어(S2)에 한 덩어리로 저장한다. 키 하나에 항목이 십수 개라
// 스키마를 항목마다 만들면 스키마가 키맵의 사본이 되어 반드시 어긋난다.
const STORE_KEY = 'keymap.overrides';

const DEVICE_KEY = 'keymap.bindingsV2';
function _overrides() {
  try {
    const v2 = JSON.parse(setting(DEVICE_KEY) || 'null');
    if (v2?.version === 2 && v2.bindings && typeof v2.bindings === 'object' && !Array.isArray(v2.bindings)) return v2.bindings;
  } catch (_) { /* 손상된 V2로 기존 바인딩까지 잃지 않는다. */ }
  try {
    const raw = setting(STORE_KEY);
    const old = typeof raw === 'string' ? JSON.parse(raw || '{}') : raw;
    return old && typeof old === 'object' && !Array.isArray(old) ? old : {};
  } catch (_) { return {}; }
}
function saveOverrides(bindings) {
  // 최초 편집 때 기존 global을 복사한다. 원본은 롤백/다른 기기용으로 남긴다.
  return setSetting(DEVICE_KEY, JSON.stringify({ version: 2, bindings }));
}
// 이 플랫폼의 기본 조합. 사용자가 바꾼 값(재정의)이 없을 때만 쓰인다 —
// 재정의는 플랫폼과 무관하게 저장한 그대로 이식된다(파일 머리 주석의 약속).
function _defaultCombo(spec) {
  return (!IS_MAC && spec.defNonMac) || spec.def;
}

// 조합의 마지막 키를 무엇으로 맞출지. 글자·숫자·`.`은 물리 키(code) — Option/Shift가 글자를 바꿔도(⌥⇧F → Ï,
// ⇧. → >) 같은 키로 잡힌다. 나머지 기호는 배열의 논리 문자(key)다.
function keyKindOf(combo) {
  return /^[a-z0-9.]$/i.test(normalize(combo).split('+').at(-1)) ? 'code' : 'key';
}

// { binding?: string, passthrough?: boolean } 형태의 사용자 재정의
export function getBinding(id) {
  const spec = BY_ID.get(id);
  if (!spec) return null;
  const ov = _overrides()[id] || {};
  const def = _defaultCombo(spec);
  return {
    id,
    label: spec.label,
    combo: ov.binding || def,
    passthrough: ov.delivery === 'app-and-terminal' || (ov.delivery == null && (ov.passthrough ?? false)),
    delivery: ov.delivery || (ov.passthrough ? 'app-and-terminal' : 'app'),
    keyKind: ov.keyKind || keyKindOf(ov.binding || def),
    isDefault: !ov.binding && ov.passthrough === undefined && !ov.delivery,
    customCombo: !!ov.binding,
    // 일반 브라우저 탭에서 이 조합이 가로채지지 않는다면 그 사실을 함께 알린다.
    unavailable: isBrowserReserved(ov.binding || def),
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

export function setBinding(id, combo, keyKind) {
  if (!BY_ID.has(id)) return Promise.resolve(false);
  const ov = { ..._overrides() };
  ov[id] = { ...(ov[id] || {}), binding: combo || undefined, keyKind: keyKind || keyKindOf(combo) };
  if (!combo) delete ov[id].binding;
  return saveOverrides(ov);
}

export function setPassthrough(id, on) {
  if (!BY_ID.has(id)) return Promise.resolve(false);
  const ov = { ..._overrides() };
  ov[id] = { ...(ov[id] || {}), delivery: on ? 'app-and-terminal' : 'app' };
  return saveOverrides(ov);
}

export function reset(id) {
  const ov = { ..._overrides() };
  delete ov[id];
  return saveOverrides(ov);
}

// ── 매칭 ──────────────────────────────────────────────────────────────────
// 조합 문자열을 정렬된 표준형으로. 'shift+mod+f' 와 'Mod+Shift+F' 가 같은 것으로
// 비교돼야 충돌 감지가 의미를 갖는다.
//
// ⚠ `Ctrl`은 **비-mac에서 `Mod`와 같은 키다**. comboFromEvent가 비-mac의
// ctrlKey를 `mod`로 내보내므로, 여기서 `Ctrl` 표기를 `ctrl`로 남겨두면
// `Ctrl+Shift+V`(붙여넣기)·`Ctrl+Shift+E`(코드 뷰어)가 Windows/Linux
// 브라우저에서 **영영 매칭되지 않는다**(실측: mac은 'ctrl+shift+v'로 잡히고
// linux는 'mod+shift+v'라 null). 그 결과 우리가 preventDefault를 못 해
// 크롬의 "서식 없이 붙여넣기"가 그대로 발동, 붙여넣기가 두 번 들어갔다.
// mac에서만 Ctrl이 Mod(Cmd)와 별개 키라 구분을 유지한다.
export function normalize(combo) {
  if (!combo) return '';
  const parts = String(combo).split('+').map((p) => p.trim()).filter(Boolean);
  const mods = new Set();
  let key = '';
  for (const p of parts) {
    const low = p.toLowerCase();
    if (low === 'mod' || low === 'ctrl' || low === 'control' || low === 'cmd' || low === 'meta' || low === 'shift' || low === 'alt' || low === 'option') {
      let m = low === 'control' ? 'ctrl' : low === 'cmd' || low === 'meta' ? (IS_MAC ? 'mod' : 'meta') : low === 'option' ? 'alt' : low;
      if (m === 'ctrl' && !IS_MAC) m = 'mod';
      mods.add(m);
    } else {
      key = p.length === 1 ? low : p;   // 한 글자는 소문자, ArrowLeft 등은 원형
    }
  }
  const order = ['mod', 'meta', 'ctrl', 'alt', 'shift'];
  return [...order.filter((m) => mods.has(m)), key].join('+');
}

// KeyboardEvent → 표준형 조합 문자열.
export function comboFromEvent(e, keyKind = 'code') {
  const mods = [];
  const modPressed = IS_MAC ? e.metaKey : e.ctrlKey;
  if (modPressed) mods.push('mod');
  if (!IS_MAC && e.metaKey) mods.push('meta');           // 리눅스/윈도의 Meta도 Mod로
  if (e.ctrlKey && IS_MAC) mods.push('ctrl');           // macOS에서 Ctrl은 Mod와 별개
  if (e.altKey) mods.push('alt');
  if (e.shiftKey) mods.push('shift');
  let key = keyKind === 'code' ? physicalKey(e) : e.key;
  if (key === ' ') key = 'Space';
  if (key && key.length === 1) key = key.toLowerCase();
  const order = ['mod', 'meta', 'ctrl', 'alt', 'shift'];
  return [...order.filter((m) => mods.includes(m)), key].join('+');
}

// 눌린 키가 어떤 액션인지. 없으면 null.
// 사용자가 직접 정한 조합이 기본 조합보다 먼저다 — 기본값이 바뀌어(예: `Mod+Shift+F`가 내용 검색으로 넘어감) 사용자가
// 다른 동작에 묶어 둔 같은 조합과 겹쳐도 사용자의 선택이 이긴다.
export function match(e) {
  if (isComposingKey(e) || e.getModifierState?.('AltGraph') || e.altGraph) return null;
  const all = list();
  for (const pass of [true, false]) {
    for (const b of all) {
      if (b.unavailable || b.customCombo !== pass) continue;      // 일반 탭에서 못 잡는 조합은 매칭도 안 한다
      if (normalize(b.combo) === comboFromEvent(e, b.keyKind)) return b;
    }
  }
  return null;
}

// 브라우저가 paste 이벤트를 발생시키는 조합은 키맵 액션으로 다시 읽지 않는다.
// 기본 Ctrl+Shift+V와 사용자가 macOS Cmd+V로 재지정한 경우도 포함한다.
export function isNativePasteBinding(binding) {
  if (binding?.id !== 'paste') return false;
  const combo = normalize(binding.combo);
  const native = IS_MAC
    ? ['mod+v', 'mod+shift+v', 'ctrl+shift+v']
    : ['mod+shift+v']; // Ctrl+V는 xterm/셸의 quoted-insert 몫이다.
  return native.includes(combo);
}

export function isNativePasteShortcut(e, binding) {
  return isNativePasteBinding(binding)
    && normalize(binding.combo) === comboFromEvent(e);
}

function isTextField(target) {
  return target instanceof Element && !target.closest('.xterm')
    && Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}

// ── 실행 ──────────────────────────────────────────────────────────────────
const _handlers = new Map();

// 액션 하나에 실행 함수를 등록한다. 같은 id의 중복 등록은 경고하고 마지막 함수를 사용한다
// (모듈 재평가 대비 — 테스트에서 특히).
export function register(id, fn) {
  if (_handlers.has(id) && _handlers.get(id) !== fn) console.warn(`[keymap] ${id} 실행 함수가 중복 등록되었습니다`);
  _handlers.set(id, fn);
  return () => { if (_handlers.get(id) === fn) _handlers.delete(id); };
}

// N46 — 팔레트 `:` 모드(키맵 레지스트리 명령)가 행을 고르면 그 액션을 직접
// 실행하기 위한 공개 진입점. register()의 핸들러 맵은 지금까지 handleKeydown
// 내부에서만 조회됐다 — 키 입력 없이도 "이 액션을 지금 실행해줘"를 표현할
// 방법이 없었다.
export function invoke(id) {
  const fn = _handlers.get(id);
  if (typeof fn === 'function') fn();
}

// document capture 단계에서 한 번만 듣는다. 여기저기서 각자 keydown을 듣던 것을
// 이 한 곳으로 모으는 것이 S3의 목적이다.
function dispatchKey(e, decision, allowed) {
  const b = match(decision.stroke);
  if (!b || (allowed && !allowed.includes(b.id))) return;
  if (isTextField(e.target) && !allowed?.includes(b.id) && !['palette', 'settings'].includes(b.id)) { decision.owner = 'editor'; return; }
  if (e.repeat) { decision.owner = 'app'; decision.terminal = 'block'; decision.consumed = true; return; }
  const fn = _handlers.get(b.id);
  if (!fn && b.delivery !== 'terminal') return;
  const nativePaste = isNativePasteShortcut(e, b);
  const target = e.target;
  Object.assign(decision, { owner: nativePaste ? 'native' : 'app', actionId: b.id,
    terminal: nativePaste ? 'default' : 'block', consumed: !nativePaste });
  if (b.delivery !== 'terminal') {
    try { fn?.(e); } catch (err) { console.warn(`[keymap] ${b.id} 실행 실패:`, err); }
  }
  if (!nativePaste && b.delivery !== 'app') forwardTerminalKey(target, e, decision.stroke, decision.targetContext);
}
export function setDelivery(id, delivery) {
  if (!BY_ID.has(id) || !['app', 'app-and-terminal', 'terminal'].includes(delivery)) return Promise.resolve(false);
  const ov = { ..._overrides() };
  ov[id] = { ...(ov[id] || {}), delivery };
  return saveOverrides(ov);
}
export function handleKeydown(e) { return decideKey(e).consumed; }
export function wire() { wireKeyboardRouter(dispatchKey); }
wire();

// 설정이 바뀌면(다른 기기에서 재바인딩 등) 다음 키 입력부터 자동으로 새 값이
// 적용된다 — list()/match()가 매번 스토어를 읽기 때문에 별도 갱신이 필요 없다.
// 구독은 충돌 경고를 콘솔에 한 번 남기기 위한 것뿐이다.
onSettings((changed) => {
  if (!changed || !(STORE_KEY in changed) && !(DEVICE_KEY in changed)) return;
  const c = conflicts();
  if (Object.keys(c).length) console.warn('[keymap] 충돌:', c);
});

// 사람이 읽는 표기 — 팔레트·설정 화면이 쓴다(`Mod` → 플랫폼 기호).
// displayCombo의 HTML판 — 기호 키(⌘⇧⌥⌃, 방향키, Enter)는 SVG로, 나머지 글자는
// 텍스트로. 유니코드 기호는 모노 글꼴에 없는 것이 섞여 기기마다 모양이 갈렸다.
// Mac이 아니면 수식키는 글자(Ctrl/Shift/Alt)이고 방향키·Enter만 SVG다.
const _KEY_ICON = { ArrowLeft: 'arrow-left', ArrowRight: 'arrow-right', ArrowUp: 'arrow-up', ArrowDown: 'arrow-down', Enter: 'key-enter' };
const _MOD_ICON = { mod: 'key-command', shift: 'key-shift', alt: 'key-option', ctrl: 'key-ctrl' };
const _esc = (t) => String(t).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
function comboTokens(combo) {
  return String(combo || '').split('+').filter(Boolean).map((p) => {
    let low = p.toLowerCase();
    if (low === 'cmd') low = 'meta';
    if (low === 'control') low = 'ctrl';
    if (low === 'option') low = 'alt';
    const modifier = low === 'mod' ? (IS_MAC ? 'meta' : 'ctrl') : low;
    const glyphs = { meta: '⌘', ctrl: '⌃', alt: '⌥', shift: '⇧' };
    const names = { meta: 'Meta', ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift' };
    const arrows = { ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓' };
    return { text: names[modifier] || (p.length === 1 ? p.toUpperCase() : p),
      glyph: (IS_MAC && glyphs[modifier]) || arrows[p],
      icon: _KEY_ICON[p] || (IS_MAC ? (modifier === 'meta' ? 'key-command' : _MOD_ICON[modifier]) : null) };
  });
}
export function displayComboHtml(combo) {
  return comboTokens(combo).map((p) => p.icon
    ? `<span class="vt-key vt-key-icon">${icon(p.icon, 13, 2)}</span>`
    : `<span class="vt-key">${_esc(p.text)}</span>`).join(IS_MAC ? '' : '<span class="vt-key-sep">+</span>');
}
export function displayCombo(combo) {
  return comboTokens(combo).map((p) => p.glyph || p.text).join(IS_MAC ? '' : '+');
}
