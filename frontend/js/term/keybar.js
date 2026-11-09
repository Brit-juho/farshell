// 모바일 특수키 바(keybar). F4에서 terminal.js(구 :1842-2018)에서 분리.
// 소프트 키보드에 없는 특수키/조합키를 활성 PTY에 원시 시퀀스로 주입한다.
// 수식키(Ctrl/Alt/Shift): 탭 = 다음 한 키에만, 한 번 더 탭 = 고정, 누르고 있는
// 동안 = 적용(다른 손가락으로 키바 키나 소프트 키보드를 치면 동시 입력).
// 키바 버튼 · 소프트 키보드 문자 양쪽 모두에 붙는다.
// 순수 키-시퀀스 로직(_KEYBAR_SEQ, ctrlByte, Ctrl+화살표, sticky 변환)은
// lib/keyseq.js(window.VTKeySeq)로 분리돼 있다 — DOM/세션 상태가 없어 단위 테스트 대상.
// VTKeySeq는 아직 UMD(globalThis.VTKeySeq)라 bare identifier로 읽는다(F2 판단 유지).
import { activeSessionId, activeSession, getSession } from '../core/store.js';
import { copyToClipboard } from './clipboard.js';
import { _isCoarsePointer } from '../core/env.js';
import { t } from '../core/i18n.js';

// 보이는 높이가 이보다 작으면 포커스 모드. 가로 폰(≈400px)에 키보드가 뜨면 200px 안팎, 세로 폰은 훨씬 크다.
// 300px = 상단 바 + 키바(≈96px) + 터미널 몇 줄이 겨우 들어가는 하한이라 그 아래에서는 크롬이 터미널을 먹는다.
export const FOCUS_MAX_VISIBLE_H = 300;

// 수식키 상태. latch: 'off' | 'once'(다음 한 키) | 'lock'(다시 탭할 때까지).
// held: 지금 그 버튼을 누르고 있는 포인터 id 집합(멀티터치 동시 입력).
export const MODS = ['ctrl', 'alt', 'shift'];
export const _mods = Object.fromEntries(MODS.map((m) => [m, { latch: 'off', held: new Set(), usedWhileHeld: false }]));
let _selectionSessionId = null;

// 2026-09-23 — 예전엔 브라우저 네이티브 Range(window.getSelection())를 먼저
// 보고 xterm 자신의 선택(term.getSelection())으로 폴백했다. 이제 모바일 선택은
// 전부 term/touch-select.js가 xterm의 select() 모델로만 만든다(왜: 그 파일
// 머리말 참고 — 접근성 레이어 재렌더가 네이티브 Range를 끊는다) — 네이티브
// Range가 채워질 길이 없어졌으므로 그 분기를 걷어냈다.
function _activeSelectionText(term) {
  return String(term?.getSelection?.() || '');
}

export function isTerminalSelectionMode() {
  return Boolean(_selectionSessionId);
}

export function setTerminalSelectionMode(on) {
  const current = activeSession();
  const previousId = _selectionSessionId;
  const previous = previousId && document.getElementById(`term-${previousId}`);
  previous?.classList.remove('vt-term-select-mode');
  const previousHelper = previous?.querySelector?.('.xterm-helper-textarea');
  if (previousHelper) previousHelper.disabled = false;
  // xterm 자신의 하이라이트(term.select 모델)는 클래스를 떼도 저절로 안
  // 사라진다 — 명시적으로 지운다. `on`/`off` 어느 방향이든 지워야 할 대상은
  // "방금까지 선택 모드였던 세션"(previousId)뿐이다 — 그게 없으면(막 처음
  // 켜는 참이라 지울 하이라이트가 아직 없다) 아무것도 하지 않는다.
  try { if (previousId) getSession(previousId)?.term?.clearSelection?.(); } catch (_) {}

  _selectionSessionId = on && current ? activeSessionId() : null;
  const wrapper = _selectionSessionId && document.getElementById(`term-${_selectionSessionId}`);
  wrapper?.classList.add('vt-term-select-mode');
  const helper = wrapper?.querySelector?.('.xterm-helper-textarea');
  if (helper) helper.disabled = true;
  document.body.classList.toggle('vt-term-selecting', Boolean(_selectionSessionId));

  const actions = document.getElementById('keybar-selection-actions');
  if (actions) actions.hidden = !_selectionSessionId;

  if (_selectionSessionId) {
    resetKeybarMods();
    try { current.term.blur(); } catch (_) {}
    try { document.activeElement?.blur?.(); } catch (_) {}
    try { navigator.virtualKeyboard?.hide?.(); } catch (_) {}
  }
  document.dispatchEvent(new CustomEvent('vt:terminal-selection', { detail: { active: Boolean(_selectionSessionId) } }));
}

export async function _copyTerminalSelection() {
  const term = activeSession()?.term;
  const text = _activeSelectionText(term);
  if (!text.trim()) {
    showToast(t('복사할 텍스트 범위를 먼저 선택하세요.'));
    return false;
  }
  const ok = await copyToClipboard(text);
  // 성공은 말하지 않는다 — 바로 아래에서 선택 모드가 닫히는 것(선택 바가
  // 사라지고 키들 줄이 돌아온다)이 증거고, 폰에서 그 토스트는 방금 복사한
  // 그 선택 영역 위에 떴다.
  if (!ok) showToast(t('클립보드에 복사하지 못했습니다.'), 'error');
  if (ok) setTerminalSelectionMode(false);
  return ok;
}

// "모두 선택" — 화면에 보이는 범위 전체(뷰포트, term.rows×term.cols)를 고른다.
// xterm의 selectAll()은 스크롤백을 포함한 버퍼 전체를 고르는데, 여기서
// "모두"는 사용자가 지금 보고 있는 화면 전체를 뜻한다 — 안 보이는 스크롤백
// 수천 줄까지 복사되면 "모두 선택"을 누른 사람이 기대한 결과가 아니다.
export function _selectAllVisible() {
  const s = activeSession();
  if (!s || !s.term) return;
  try { s.term.select(0, 0, s.term.rows * s.term.cols); } catch (_) {}
  // term/touch-select.js가 자기 핸들을 이 새 범위로 다시 그리도록 알린다 —
  // 이 파일이 그 파일을 직접 import하면 순환 참조가 생긴다(그 파일은 이미
  // 이 파일의 setTerminalSelectionMode를 쓴다), 그래서 이벤트로 느슨하게 묶는다.
  document.dispatchEvent(new CustomEvent('vt:terminal-selection-changed'));
}

// L7: 마이크의 물리적 위치 결정 — voice.js가 `#mic-btn-wrap`을 모듈 최상위에서
// `const`로 한 번만 캐시하므로(voice/recording.js), *어디에 있든* id만 유지되면
// 문제없다. 데스크톱(사용자 확인, 문서에 명시적 근거는 없었음)은 rail 설정
// 패널(#vt-rail-mic-slot, index.html 정적 마크업)에 그대로 둔다 — 아래
// initKeybar()의 이른 return보다 먼저 실행해야 한다(키바가 없는 환경에서도
// 마이크는 제자리를 찾아가야 함).
// 2026-09-18 — 데스크톱 자리가 바뀌었다. 예전엔 숨은 옛 레일의 설정 플라이아웃
// (#vt-rail-mic-slot)에 있었고 `⋯ 더보기` 메뉴로만 닿을 수 있었다 — 그 메뉴의
// 나머지 항목이 전부 dock·팔레트·설정으로 옮겨가면서, ⋯는 사실상 **마이크 하나
// 때문에** 남아 있는 메뉴가 됐다. 그래서 마이크를 좌측 레일 바닥(#vt-rail-mic-home,
// 설정 버튼 옆)으로 꺼내고 ⋯를 없앴다.
// 2026-09-18(2차) — 사용자 요청으로 설정·마이크를 좌측 레일에서 우측 dock
// (#vt-dock-mic-home, 탭 줄 바로 아래)으로 다시 옮긴다.
//
// dock은 지연 로드되는 Solid 컴포넌트라 이 모듈이 평가될 때는 아직 없다 —
// Workbench.tsx가 마운트 직후 placeMicButton()을 한 번 더 부른다. 그래서 이 함수는
// **여러 번 불려도 안전**해야 하고(이미 제자리면 아무것도 안 한다), dock이 아직
// 없으면 옛 슬롯에 그대로 둔다(음성이 조용히 사라지지 않게).
// 2026-11(리디자인 8단계) — 폰(compact, 폭 < 720)에는 활동 막대가 없어(작업대가 CSS로 숨는다) 마이크가
// 보이는 자리가 하나도 없었다. 폰에서는 떠 있는 56px 자리(#vt-mic-fab, index.html)로, 폰 집중 모드
// (html.vt-focus)에서는 키바 첫 키(#keybar-mic-slot)로 옮긴다. 폭·집중 모드가 바뀔 때마다 다시 부른다(아래).
// 폭 문턱은 CSS(60-mobile-hud.css)·mobile-nav.js와 같은 719px. 음성 전용 모드는 마이크를 #topbar에
// 데려다 놓고(voice/media-session.js) 끌 때 되돌리므로 그동안은 손대지 않는다.
const COMPACT_QUERY = '(max-width:719px)';
function _isCompactWidth() {
  try { return Boolean(window.matchMedia?.(COMPACT_QUERY).matches); } catch (_) { return false; }
}

export function micHome() {
  if (_isCompactWidth()) {
    const focus = document.documentElement.classList.contains('vt-focus');
    const phone = (focus && document.getElementById('keybar-mic-slot')) || document.getElementById('vt-mic-fab');
    if (phone) return phone;
  }
  // 2026-09-29 — 터치 기기의 키바 슬롯을 없앴다(사용자: 안 쓰는데 키 자리만
  // 먹는다). 넓은 화면에서는 포인터 종류와 무관하게 활동 막대에 둔다.
  // 2026-11 — 데스크톱 첫 자리는 작업대 활동 막대의 아래 무리(#vt-activity-mic-home, Workbench.tsx).
  // dock 탭 줄(#vt-dock-mic-home)은 활동 막대로 옮겨 가며 없어졌다.
  // 작업대가 아직 없으면(지연 로드 전) 숨은 대기 자리(#vt-mic-park, index.html)에 그대로 둔다.
  return document.getElementById('vt-activity-mic-home') || document.getElementById('vt-mic-park');
}

export function placeMicButton() {
  const mic = document.getElementById('mic-btn-wrap');
  if (!mic) return; // .needs-voice로 이미 숨겨졌거나(음성 미설치) 마크업 자체가 없는 테스트 환경
  if (document.body?.classList.contains('voice-only-mode')) return;
  const target = micHome();
  if (target && mic.parentElement !== target) target.appendChild(mic);
  window.dispatchEvent(new CustomEvent('vt:mic-placed'));
}
placeMicButton();
try {
  window.matchMedia?.(COMPACT_QUERY)?.addEventListener?.('change', () => placeMicButton());
} catch (_) { /* matchMedia 없는 환경 */ }
// 폰 집중 모드가 켜지고 꺼질 때(layout/mobile-focus.js가 쏜다) — 떠 있는 자리 ↔ 키바 첫 키.
document.addEventListener('vt:focus-change', () => placeMicButton());

export function _activeMods() {
  const out = {};
  for (const m of MODS) out[m] = _mods[m].latch !== 'off' || _mods[m].held.size > 0;
  return out;
}

export function _renderMods() {
  for (const m of MODS) {
    const btn = document.querySelector(`#keybar .kb-mod[data-mod="${m}"]`);
    if (!btn) continue;
    const st = _mods[m];
    const on = st.latch !== 'off' || st.held.size > 0;
    btn.classList.toggle('armed', on);
    btn.classList.toggle('locked', st.latch === 'lock');
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

// 키 하나를 보낸 뒤: 'once'는 소비, 누르고 있는 수식키는 "조합에 쓰였다"로 표시
// (손을 뗄 때 탭으로 오인해 latch를 바꾸지 않게).
export function _consumeMods() {
  for (const m of MODS) {
    const st = _mods[m];
    if (st.latch === 'once') st.latch = 'off';
    if (st.held.size > 0) st.usedWhileHeld = true;
  }
  _renderMods();
}

export function resetKeybarMods() {
  for (const m of MODS) { _mods[m].latch = 'off'; _mods[m].held.clear(); _mods[m].usedWhileHeld = false; }
  _renderMods();
}

// term.onData 경로(ws.js)에서 호출 — 켜진 수식키를 소프트 키보드 입력에 적용.
export function applyStickyMod(data) {
  const mods = _activeMods();
  if (!data || data.length !== 1 || data.charCodeAt(0) >= 128 || !MODS.some((m) => mods[m])) return data;
  _consumeMods();
  return VTKeySeq.applyModsToInput(data, mods);
}

export function _focusActiveTerm() {
  const term = activeSession()?.term;
  if (term) { try { term.focus(); } catch (_) {} }
}

let initialization = null;
export function initKeybar() {
  let force = false;
  try { force = new URLSearchParams(location.search).get('keybar') === '1' || localStorage.getItem('vt_keybar') === 'on'; } catch (_) {}
  if (!_isCoarsePointer() && !force) return Promise.resolve();
  if (!initialization) initialization = import('./keybar-ui.js').then((m) => m.initKeybarUI()).catch(() => {
    initialization = null; window.showToast?.(t('키바를 불러오지 못했습니다. 화면을 새로고침하세요.'), 'warn');
  });
  return initialization;
}
initKeybar();
