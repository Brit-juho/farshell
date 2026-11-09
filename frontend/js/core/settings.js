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
//     같은 화면에서 다시 로드하거나 값을 재선택하면 저장을 다시 시도한다.
//   - `migrate`가 지정된 키는 **기존 localStorage 값에서 1회 이관**한다.
//     이관 후 원본 키는 지우지 않는다(롤백 여지 — 2.0이 끝나면 정리한다).
//   - **스코프는 스키마가 정한다 — 사용자가 바꾸지 않는다**(문서 §1의 화면
//     2b 규칙: 배지는 읽기 전용).
import { vtFetch } from './api.js';
import { hostKey, isRelayed } from './host-route.js';

// 브라우저 등록 쿠키가 바뀌어도 같은 종류의 화면 설정을 복원한다.
import { settingsProfile } from './platform.js';
import { t } from './i18n.js';
export const DEVICE_PROFILE = settingsProfile;
const DEVICE_ENDPOINT = `/api/device-settings?profile=${DEVICE_PROFILE}`;

// parse: 옛 localStorage 문자열 → 새 값. 없으면 그대로 쓴다.
const onOff = (v) => v !== 'off';

export const SCHEMA = {
  // scope 없음 = 'global'(문서 §1: "기본은 global").
  // ⚠ `scope: 'device'`를 빼지 말 것 — 폰에서 핀치 줌으로 키운 글씨가 맥
  // 터미널까지 키운다(`term/touch.js`의 `_setGlobalFontSize`가 이 키에 쓴다).
  // `frontend/tests/settings.test.js`가 스코프와 **실제 라우팅**을 함께 잠근다.
  'terminal.fontSize':      { type: 'int',  def: 14, min: 8, max: 28, migrate: 'vt_font_size', scope: 'device' },
  'terminal.cursorStyle':   { type: 'enum', def: 'block', values: ['block', 'underline', 'bar'] },
  'terminal.cursorBlink':   { type: 'bool', def: true },
  'terminal.scrollback':    { type: 'int',  def: 2000, min: 500, max: 10000 },
  // S1 spike 성공 — off면 앱의 마우스 리포팅을 무시하고 항상 로컬 선택(iTerm2 기본 동작).
  // 문서 §1 표의 "mouse.forward"에 대응 — 기기 성능/입력 방식에 달린 값이라 device.
  'mouse.forwardToApp':     { type: 'bool', def: true, scope: 'device' },
  'mouse.autocopyOnSelect': { type: 'bool', def: true, migrate: 'vt_autocopy_on_select', parse: onOff },
  // "맥에서도 열기" — 새 세션을 만들 때 서버가 고른 메인 터미널(VT_TERMINAL)로 창도 함께 연다.
  // E2(2026-09-08)에서 스토어로 승격했다. 그전엔 ui/moreMenu.js가 localStorage를
  // 직접 읽고, 소비처(term/session.js·term/tmux-panel.js)는 체크박스 엘리먼트의
  // .checked를 매번 읽는 구조였다 — 값의 주인이 DOM이라 폰에서 켠 게 맥북에
  // 안 넘어갔다. autocopyOnSelect가 S2에서 같은 이유로 먼저 옮겨온 자리다.
  // 기본값은 off — 새 세션마다 맥 터미널 창이 뜨는 걸 원치 않는 사용자가 다수다.
  'session.openOnMac':      { type: 'bool', def: false, migrate: 'vt_auto_mac', parse: onOff },
  // 터치 기기에서 짧은 탭을 앱으로 합성 전달하는 기존 동작(term/xterm-setup.js).
  // 지금까지 끌 방법이 없었다.
  'mouse.touchTapToApp':    { type: 'bool', def: true },
  // 음성 — STT 모델과 작업 완료 알림. 서버(stt_models.py·voice_notify.py)가 같은 키를
  // workspace.json에서 읽는다: 서버는 값의 의미를 이 스키마가 아니라 자기 검증으로 다시 본다.
  // sttModel은 서버 카탈로그의 모델 id 또는 'custom:<엔진>:<owner/repo>'. 빈 값이면 서버가
  // 환경별 권장 모델로 해석한다. 형식 검증은 서버가 한다.
  'voice.sttModel':         { type: 'str',  def: '' },
  // 확인 후 전송 — off 확인 없이 입력 · shell 일반 셸(또는 판별 불가)이면 확인, AI 에이전트 pane은 바로 입력 · on 항상 확인.
  // 위험 명령 패턴은 off가 아니면 항상 확인한다. 서버(voice_confirm.py)가 같은 키를 workspace.json에서 읽고 값을 다시 검증한다.
  'voice.confirm':          { type: 'enum', def: 'shell', values: ['off', 'shell', 'on'] },
  'voice.notifyMode':       { type: 'enum', def: 'off', values: ['off', 'sound', 'speak'] },
  'voice.notifyMinSec':     { type: 'int',  def: 0, min: 0, max: 600 },
  'voice.notifySuccessOnly':   { type: 'bool', def: true },
  'voice.notifyOnlyUnfocused': { type: 'bool', def: false },
  // 녹음 중 처리(청크 업로드 경로만). 서버 voice_live.py가 녹음 시작에 같은 키를 workspace.json에서 읽는다.
  // autoStop: 말이 끝나고 1초 조용하면 자동 종료 — 말하는 중 끊길 수 있어 기본 off.
  // partialResults: 녹음 중 미리 보기 — 입력하지 않고 표시만 한다. 서버 STT를 더 쓴다(docs/ref/voice.md).
  'voice.autoStop':         { type: 'bool', def: false },
  'voice.partialResults':   { type: 'bool', def: true },
  'a11y.screenReader':      { type: 'enum', def: 'auto', values: ['auto', 'on', 'off'], migrate: 'vt-a11y',
                              parse: (v) => (v === '1' ? 'on' : v === '0' ? 'off' : 'auto') },
  'theme.skin':             { type: 'str',  def: 'farshell', migrate: 'vt-skin' },
  // S3: 키맵 재정의({id: {binding, passthrough}})를 JSON 문자열로 한 덩어리
  // 저장한다. 항목마다 스키마 키를 만들면 스키마가 키맵의 사본이 되어 반드시
  // 어긋난다 — 키맵의 진짜 스키마는 core/keymap.js의 ACTIONS 하나뿐이다.
  'keymap.overrides':       { type: 'str', def: '{}' }, // V1 읽기 호환
  'keymap.bindingsV2':      { type: 'str', def: '', scope: 'device' },
  // 기본은 meta: Option+P(모델 선택)·Option+B/F 같은 터미널 앱 조합이 π·∫ 같은 문자로 바뀌지 않게 한다.
  // 특수문자를 Option으로 입력하려면 설정 → 키맵에서 unicode로 바꾼다(저장된 선택은 그대로 유지된다).
  'terminal.optionMode':    { type: 'enum', def: 'meta', values: ['unicode', 'meta'], scope: 'device' },
  // 2026-09-23(2.1 D6) — 빠른 열기(팔레트) 기본 화면의 "최근 사용 기능" 2개.
  // action id(레이블 아님 — 레이블은 바뀔 수 있다) 배열을 최신순 JSON
  // 문자열로 저장한다. 기기마다 쓰는 기능이 달라 device 스코프다.
  'palette.recentCommands': { type: 'str', def: '[]', scope: 'device' },
  // L8: 우측 레일(사용량) 접힘 상태. 폭은 지금 섹션이 하나뿐이라 고정으로 두고,
  // 섹션이 늘어나면(변경 파일·최근 diff) 그때 폭도 설정으로 뺀다.
  // 문서 §1 표: "ui.rail.collapsed/width · ui.dock.* → device"와 같은 부류.
  'rightRail.collapsed':    { type: 'bool', def: false, scope: 'device' },
  // ADR-43(2.3.0) — 옵트인 스크롤백 영속화 자체를 없앴다(한 번도 켜진 적
  // 없어 위험만 있고 값어치가 없었다). 스키마에서 뺐다 — 이미 이 키를 저장한
  // workspace.json이 있어도 무해하다(서버는 값의 의미를 모르고 보관만 하고,
  // 프런트도 스키마에 없는 키는 무시한다. 마이그레이션 불필요).

  // N36/N35가 정식 스토어가 생기기 전까지 localStorage 전용으로 뒀던 값들
  // (Rail.tsx/Dock.tsx 커밋 주석 "N3 전이라 임시로 localStorage" 참고).
  // migrate로 그 키들을 그대로 흡수한다 — 이관 후에도 원본 키는 지우지 않는다
  // (위 파일 상단 규칙과 동일).
  'ui.rail.collapsed':      { type: 'bool', def: false, migrate: 'vt-rail-collapsed', parse: (v) => v === '1', scope: 'device' },
  'ui.rail.width':          { type: 'int',  def: 252, min: 240, max: 480, migrate: 'vt-rail-w', scope: 'device' },
  'ui.dock.collapsed':      { type: 'bool', def: false, migrate: 'vt-dock-collapsed', parse: (v) => v === '1', scope: 'device' },
  'ui.dock.width':          { type: 'int',  def: 392, min: 320, max: 560, migrate: 'vt-dock-w', scope: 'device' },
  'ui.dock.tab':            { type: 'str',  def: 'scm', migrate: 'vt-dock-tab', scope: 'device' },
  // 리디자인 1단계(작업대) — 사이드바 하나. 폭은 **활동 전체가 공유**한다(활동을 바꿔도 칸 크기가
  // 그대로라 터미널 refit이 없다). 기본값·폴백(새 키 → ui.dock.width → ui.rail.width, 접힘은
  // ui.rail.collapsed → 티어 기본)은 shell/side-prefs.js가 읽을 때 적용한다 — 옛 키를 옮겨 쓰지
  // 않으므로 migrate가 없다. 위 ui.rail.*·ui.dock.*는 그 폴백 때문에 스키마에 남는다.
  // 활동 값 목록은 shell/activity-data.ts의 ACTIVITIES와 같아야 한다(테스트가 대조).
  'ui.side.width':          { type: 'int',  def: 360, min: 240, max: 480, scope: 'device' },
  'ui.side.activity':       { type: 'enum', def: 'sessions', values: ['sessions', 'scm', 'dir', 'queue', 'files', 'snippets', 'ports', 'usage', 'system'], scope: 'device' },
  'ui.side.collapsed':      { type: 'bool', def: false, scope: 'device' },

  // N38(70-mobile.md §1) — compact 하단 내비의 시작 탭. 기기 스코프인 이유는
  // rail/dock 폭과 같다: 폰에서 고른 첫 화면이 맥 터미널 시작 화면에 새면 안 된다.
  // 2026-09-29 — 모바일 키바 열림 여부. 헤더의 키보드 버튼으로만 바뀐다(소프트
  // 키보드가 떠도 저절로 열지 않는다 — 사용자 결정). 기본은 닫힘.
  'keybar.open':            { type: 'bool', def: false, scope: 'device' },
  'ui.mobile.home':         { type: 'enum', def: 'fleet', values: ['fleet', 'terminal'], scope: 'device' },
  // 파일 개편 단계 5 — 폰 아래 탭 「파일」 안의 「파일 | 첨부」 전환. 마지막에 본 쪽을 이 기기에서 다시 연다.
  'ui.mobile.filesSegment': { type: 'enum', def: 'tree', values: ['tree', 'attach'], scope: 'device' },

  // ADR-44 — 파일 뷰어 표면. `pane`은 2.2.0까지의
  // 동작(터미널 옆을 쪼갠다)이라 값으로 남긴다. 표면·높이·창 위치가 폰과 맥 사이에 새면
  // 안 되므로 셋 다 device. 높이를 %로 두는 건 외부 모니터에서 맞춘 px 값이 노트북에서
  // 화면을 다 덮지 않게 하려는 것이고, 창 위치를 JSON 한 덩어리로 두는 건 부분 저장된
  // 중간 상태를 막으려는 것이다(keymap.overrides와 같은 이유).
  'viewer.surface':         { type: 'enum', def: 'sheet', values: ['sheet', 'float', 'pane'], scope: 'device' },
  'viewer.sheet.height':    { type: 'int',  def: 45, min: 20, max: 85, scope: 'device' },
  'viewer.float.rect':      { type: 'str',  def: '{}', scope: 'device' },
  // 파일 트리 행 높이 — 끄면 28px(기본), 켜면 22px(사용자 선택 4 「가」). 폰은 이 값과 무관하게 44px.
  // 화면 밀도는 기기마다 다르므로 device.
  'files.tree.dense':       { type: 'bool', def: false, scope: 'device' },

  // C1(80-multihost-agents.md §1) — 레일이 지금 보고 있는 호스트. **device 스코프**인
  // 이유가 이 파일의 존재 이유 그 자체다: `/api/workspace`는 모든 기기가 공유하는
  // 파일 하나라, 전역에 두면 폰에서 gpu-box로 바꾸는 순간 맥 화면의 레일까지 같이
  // 바뀐다. 기본값 `local`은 서버의 예약 id(`agent_status.LOCAL_HOST`)와 같은 값이고,
  // 등록이 풀린 id가 남아 있으면 shell/host-data.js의 resolveActiveHost가 로컬로
  // 되돌린다(값 자체는 지우지 않는다 — 호스트를 다시 추가하면 그대로 살아난다).
  // 2026-09-28부터 읽는 곳 없음 — 호스트 선택은 core/host-route.js(localStorage)로 옮겼다.
  // 이미 저장된 기기 설정과 어긋나지 않게 스키마에서만 남겨 둔다.
  'ui.activeHostId':        { type: 'str',  def: 'local', scope: 'device' },

  // 레일(Session and view rules — Rail). **device 스코프**다: 폰에서 접은 것·끈 것이
  // 맥에 새면 안 된다(ui.rail.collapsed와 같은 이유). 같은 시트의 「저장소 표시」는
  // 반대로 서버에 둔다 — 숨김은 "이게 내 프로젝트인가"라는 사람의 판단이다.
  // 기본값은 shell/rail-data.ts의 RAIL_PREF_DEFAULTS와 같아야 한다(테스트가 대조).
  // 「개입 필요」 구역: 표시 여부와 접힘. 접힘은 구역에 waiting 세션이 있어도 유지된다 —
  // 접은 채 숨은 입력 대기는 구역 제목의 개수와 행의 상태 아이콘이 알린다.
  'rail.attention.show':      { type: 'bool', def: true,  scope: 'device' },
  'rail.attention.collapsed': { type: 'bool', def: false, scope: 'device' },
  // 만든 곳별 표시(local은 끌 수 없다). 끄더라도 열려 있는 세션·입력 대기 세션은 남는다.
  'rail.origin.peer.show':     { type: 'bool', def: true, scope: 'device' },
  'rail.origin.external.show': { type: 'bool', def: true, scope: 'device' },
  // 메뉴바(HUD)에 어떤 칩을 어느 쪽에 어떤 순서로 둘지 — `{"left":[…],"right":[…]}` JSON 한 덩어리.
  // 칩마다 키를 만들면 순서를 표현할 수 없고 일부만 저장된 중간 상태가 생긴다(keymap과 같은 이유).
  // 비어 있으면 shell/hud-data.ts의 DEFAULT_HUD_LAYOUT. 폰에는 HUD가 없고 기기마다 화면 폭이
  // 달라 device 스코프다.
  'ui.hud.layout': { type: 'str', def: '', scope: 'device' },
  'rail.order':   { type: 'str', def: '' },
  'rail.lineage': { type: 'str', def: '', scope: 'device' },
  'rail.view':    { type: 'str', def: '', scope: 'device' },
};

function _scopeOf(key) {
  return SCHEMA[key]?.scope === 'device' ? 'device' : 'global';
}

// 스코프 하나의 상태 뭉치. 두 스코프가 "값 보관 + 캐시 키 + 엔드포인트"라는
// 같은 모양이라 객체 하나로 묶는다 — get/set/load가 이 객체를 스코프로
// 골라 쓰기만 하면 되므로 로직이 중복되지 않는다.
const _stores = {
  // global은 그 맥의 /api/workspace에 저장된다 — 호스트 전환 시 캐시도 맥마다.
  global: { values: {}, cacheKey: hostKey('vt-settings-v1') },
  device: { values: {}, cacheKey: 'vt-settings-device-v1' },
};

const _pending = { global: {}, device: {} };
const _writes = { global: Promise.resolve(), device: Promise.resolve() };
let _revision = 0;
const _keyRevision = {};
const _listeners = new Set();
const _persistenceListeners = new Set();

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

// 설정 화면의 읽기 전용 저장 범위 배지가 쓴다.
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

// 설정 화면의 고정 저장 상태 표시가 쓴다. 토스트는 실패를 알리지만 사라지므로,
// 패널 안에서도 마지막 저장 결과를 계속 확인할 수 있어야 한다.
export function subscribePersistence(fn) {
  _persistenceListeners.add(fn);
  return () => _persistenceListeners.delete(fn);
}

function _notifyPersistence(detail) {
  for (const fn of _persistenceListeners) {
    try { fn(detail); } catch (_) { /* 상태 표시 하나가 저장을 막으면 안 된다 */ }
  }
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
  if (has(key) && get(key) === v && !(key in _pending[_scopeOf(key)])) return Promise.resolve(true);
  const scope = _scopeOf(key);
  _stores[scope].values[key] = v;
  _pending[scope][key] = v;
  _keyRevision[key] = ++_revision;
  _writeCache(scope);
  _notify({ [key]: v });
  _notifyPersistence({ status: 'saving', key, scope });
  const push = _push(scope, { [key]: v });
  return push.then((ok) => {
    _notifyPersistence({ status: ok ? 'saved' : 'failed', key, scope });
    return ok;
  });
}

function _pushFailToast() {
  if (typeof window.showToast === 'function') {
    window.showToast(t('설정을 서버에 저장하지 못했습니다 (이 기기에서만 적용됨)'), 'warn');
  }
}

// 항목 단위 patch를 순서대로 저장한다. 다른 탭의 최신 값을 캐시로 덮지 않는다.
function _push(scope, patch) {
  const run = async () => {
    try {
      await vtFetch(scope === 'device' ? DEVICE_ENDPOINT : '/api/workspace', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: patch }),
      });
      for (const [key, value] of Object.entries(patch)) {
        if (_pending[scope][key] === value) delete _pending[scope][key];
      }
      return true;
    } catch (_) { _pushFailToast(); return false; }
  };
  _writes[scope] = _writes[scope].then(run, run);
  return _writes[scope];
}

// 기존 localStorage 키에서 1회 이관. 이미 값이 있는 키는 건드리지 않는다.
// 스코프별로 이관 여부를 따로 추적해 반환한다 — global/device가 서로 다른
// 엔드포인트로 올라가므로 "막 이관했으니 올려야 한다" 판단도 스코프별이다.
function _migrate() {
  const migrated = { global: false, device: false };
  for (const [key, spec] of Object.entries(SCHEMA)) {
    const scope = _scopeOf(key);
    if (!spec.migrate || key in _stores[scope].values) continue;
    // 옛 localStorage 키는 이 맥(로그인한 서버) 시절의 값이다 — 다른 맥의
    // 설정으로 올리지 않는다.
    if (scope === 'global' && isRelayed()) continue;
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
function _applyRemote(scope, remote, revision) {
  if (!remote || typeof remote !== 'object') return false;
  const next = {};
  for (const [k, v] of Object.entries(remote)) {
    if (_scopeOf(k) !== scope) continue;   // 스키마가 다른 스코프로 옮긴 키 방어
    const c = _coerce(k, v);
    if (c !== undefined) next[k] = c;
  }
  for (const [key, value] of Object.entries(_stores[scope].values)) {
    if (key in _pending[scope] || (_keyRevision[key] || 0) > revision) next[key] = value;
  }
  _stores[scope].values = next;
  _writeCache(scope);
  return true;
}

// 서버 동기화. 캐시로 이미 그려진 뒤에 돌고, **서버 값이 이긴다**(ADR-5).
// 두 스코프를 병렬로 가져온다 — 서로 다른 엔드포인트라 순서는 상관없다.
export async function load() {
  const revision = _revision;
  const candidates = {};
  for (const scope of ['global', 'device']) {
    candidates[scope] = Object.fromEntries(Object.entries(_stores[scope].values)
      .filter(([key]) => SCHEMA[key]?.migrate && _scopeOf(key) === scope
        && !(scope === 'global' && isRelayed())));
  }
  const results = await Promise.allSettled([
    vtFetch('/api/workspace'), vtFetch(DEVICE_ENDPOINT),
  ]);
  for (const [index, scope] of ['global', 'device'].entries()) {
    if (results[index].status !== 'fulfilled') continue;
    const remote = results[index].value?.settings || {};
    // 항목별 이관: 서버에 다른 설정 하나가 있어도 누락된 옛 스킨을 버리지 않는다.
    const missing = Object.fromEntries(Object.entries(candidates[scope])
      .filter(([key]) => !(key in remote) && !(_keyRevision[key] > revision)));
    _applyRemote(scope, { ...missing, ...remote }, revision);
    const patch = { ...missing, ..._pending[scope] };
    if (Object.keys(patch).length) await _push(scope, patch);
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
//
// 먼저 뜬 사본이 브리지를 소유한다. voice.js는 별도 Rollup 빌드라 이 모듈을 자기 안에
// 한 벌 더 들고 있고 부팅 뒤에 로드된다 — 그 사본이 브리지를 덮으면 Rail·Dock의 쓰기가
// 다른 저장소로 가서 두 사본이 서로의 기기 설정을 서버에서 덮어쓴다(2026-09-23 e2e로 발견).
if (typeof window.vtSettingsGet !== 'function') {
  window.vtSettingsGet = get;
  window.vtSettingsSet = set;
  window.vtSettingsHas = has;
  window.vtSettingsSubscribe = subscribe;
  window.vtSettingsSubscribePersistence = subscribePersistence;
}
