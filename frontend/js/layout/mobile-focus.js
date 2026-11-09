// 리디자인 8단계 — 폰(compact, 폭 < 720)의 수동 집중 모드(시안 M14·M14-a·M14-c)와, 두 폭이 함께 쓰는 개입 알약.
//
// 켜고 끄는 스위치는 layout/focus.js 하나다(`ui.focus.active` → `html.vt-focus`, `focus.toggle` 액션도 거기서 한 번만
// 등록한다). 이 파일은 폰 몫과 알약만 맡는다:
//
// 들어가기: 상단바의 집중 모드 버튼(#vt-focus-btn, data-action="focus.toggle")만. 두 손가락 두 번 탭은 두지 않는다
// (사용자 결정 5 — 핀치 확대와 겹친다). 켜지면 상단바와 아래 탭을 숨기고 키바만 남긴다(98-mobile.css — 닫혀 있어도
// 보인다, 여닫기 버튼이 상단바와 함께 사라지므로). 마이크는 떠 있는 자리에서 키바 첫 키로 옮겨 간다(term/keybar.js
// placeMicButton이 'vt:focus-change'를 듣는다).
//
// 나가기: (1) 알약의 나가기 버튼, (2) 위 가장자리 24px에서 아래로 64px 넘게 끌었다 놓기(덜 끌면 제자리),
// (3) 터미널 탭을 떠날 때, (4) 폭이 720을 넘나들 때(focus.js).
//
// 개입 알약(#vt-focus-pill): 승인을 기다리는 세션이 있으면 「개입 N · 이름」이 보이고, 누르면 가장 오래 기다린 세션으로
// 간다(상태 막대 칩의 agents.jump와 같은 고르기 — focus.js oldestWaiting). 폰은 오른쪽 위 28(터치 44) · 나가기는 아이콘,
// 데스크톱 집중 모드(98-focus.css)는 오른쪽 12 · 위 40, 종류 키워드와 「⌘. 끝내기」를 더 보인다 — 같은 요소에 두 모양.
//
// 자동 집중(보이는 높이 < 300, term/keybar-ui.js의 body.vt-kb-focus)은 그대로 둔다 — 상태가 없고 키보드가 내려가면
// 저절로 풀린다. 이 파일의 수동 집중과 겹쳐도 둘 다 같은 것(상단바·아래 탭)을 숨길 뿐이라 충돌하지 않는다.
import { t } from '../core/i18n.js';
import { icon, stateIcon } from '../ui/icons.js';
import { onStatusChange } from '../agent/state.js';
import { getBinding, displayCombo } from '../core/keymap.js';
import { get as setting, subscribe as onSettings } from '../core/settings.js';
import {
  FOCUS_CLASS, FOCUS_EVENT, isCompactWidth, isFocusActive, setFocus, oldestWaiting, jumpToWaiting,
} from './focus.js';

export { FOCUS_CLASS };
/** 위 가장자리 띠 높이 — 이 안에서 시작한 끌기만 나가기 제스처로 본다. */
export const EDGE_PX = 24;
/** 이만큼 넘게 끌었다 놓아야 나간다. 덜 끌면 제자리. */
export const PULL_EXIT_PX = 64;
/** 서버가 정한 waiting 종류의 3글자 키워드(shell/rail-data.ts KIND_LABEL과 같은 문장 — 영어 사전 키가 같다). */
const KIND_LABEL = { approval: t('승인 요청'), choice: t('선택 요청'), plan: t('계획 확인'), question: t('질문') };

const root = document.documentElement;
let _pill = null;
let _edge = null;
let _hint = null;

export function isCompact() { return isCompactWidth(); }
export function isMobileFocus() { return isFocusActive() && isCompact(); }

function _paintButton(on) {
  const btn = document.getElementById('vt-focus-btn');
  if (!btn) return;
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  const label = on ? t('집중 모드 끝내기') : t('집중 모드');
  btn.setAttribute('aria-label', label);
  btn.dataset.tip = label;
}

function _span(cls, text) {
  const s = document.createElement('span');
  s.className = cls;
  if (text != null) s.textContent = text;
  return s;
}

function _buildPill() {
  if (_pill) return _pill;
  const pill = document.createElement('div');
  pill.id = 'vt-focus-pill';
  pill.setAttribute('role', 'group');
  pill.setAttribute('aria-label', t('집중 모드'));
  const go = document.createElement('button');
  go.type = 'button';
  go.className = 'vt-focus-pill-go';
  go.addEventListener('click', () => jumpToWaiting());
  const exit = document.createElement('button');
  exit.type = 'button';
  exit.className = 'vt-focus-pill-exit';
  exit.setAttribute('aria-label', t('집중 모드 끝내기'));
  exit.dataset.tip = t('집중 모드 끝내기');
  // 폰은 아이콘(터치), 데스크톱은 「⌘. 끝내기」 글자 — 둘 다 넣고 폭별 CSS가 하나를 보인다.
  const ico = _span('vt-focus-pill-exit-ico');
  ico.innerHTML = icon('minimize', 16);
  exit.append(ico, _span('vt-focus-pill-keys'), _span('vt-focus-pill-exit-label', t('끝내기')));
  exit.addEventListener('click', () => setFocus(false));
  pill.append(go, exit);
  document.body.appendChild(pill);
  _pill = pill;
  return pill;
}

/**
 * 알약 내용 — 개입이 있으면 「(대기 아이콘) 개입 N 이름」(+ 데스크톱: · 종류 ›), 없으면 go가 숨는다(나가기만 남는다).
 * 데스크톱에서 개입 표시를 끈 경우(`ui.focus.pill`)는 data-pill="off" — CSS가 데스크톱에서만 숨긴다.
 */
export function paintPill() {
  if (!_pill) return;
  const go = _pill.querySelector('.vt-focus-pill-go');
  const w = oldestWaiting();
  _pill.dataset.waiting = String(w ? w.count : 0);
  _pill.dataset.pill = setting('ui.focus.pill') === false ? 'off' : 'on';
  const b = getBinding('focusMode');
  _pill.querySelector('.vt-focus-pill-keys').textContent = b && !b.unavailable ? displayCombo(b.combo) : '';
  if (!w) { go.hidden = true; go.replaceChildren(); return; }
  go.hidden = false;
  const ic = _span('vt-focus-pill-ic');
  ic.innerHTML = stateIcon('waiting', 12);
  const chev = _span('vt-focus-pill-chev');
  chev.innerHTML = icon('chevron-right', 12);
  // 이름은 사용자 입력(세션 이름)이라 textContent로만.
  go.replaceChildren(
    ic, _span('vt-focus-pill-n', t('개입 {n}', { n: w.count })),
    _span('vt-focus-pill-sep', '·'), _span('vt-focus-pill-name', w.name),
    _span('vt-focus-pill-kind', KIND_LABEL[w.kind] || KIND_LABEL.question), chev,
  );
  go.setAttribute('aria-label', t('{name} 승인 요청 — 누르면 그 세션으로', { name: w.name }));
}

function _buildEdge() {
  if (_edge) return _edge;
  const edge = document.createElement('div');
  edge.id = 'vt-focus-edge';
  edge.setAttribute('aria-hidden', 'true');
  edge.title = t('아래로 끌면 상단바와 탭이 돌아옵니다');
  const grip = document.createElement('i');
  edge.appendChild(grip);
  const hint = document.createElement('div');
  hint.id = 'vt-focus-hint';
  hint.setAttribute('role', 'status');
  hint.hidden = true;
  document.body.append(edge, hint);
  _edge = edge;
  _hint = hint;

  let drag = null;
  const paint = (dy) => {
    const pulled = Math.max(0, dy);
    root.style.setProperty('--vt-focus-pull', `${Math.min(pulled, PULL_EXIT_PX * 1.5)}px`);
    const armed = pulled > PULL_EXIT_PX;
    _hint.hidden = pulled < 8;
    _hint.dataset.armed = armed ? 'true' : 'false';
    _hint.textContent = armed ? t('놓으면 집중 모드를 끝냅니다') : t('더 끌어내리면 집중 모드를 끝냅니다');
  };
  const end = (exit) => {
    drag = null;
    root.style.removeProperty('--vt-focus-pull');
    _hint.hidden = true;
    if (exit) setMobileFocus(false);
  };
  edge.addEventListener('pointerdown', (e) => {
    if (!isMobileFocus()) return;
    drag = { id: e.pointerId, y: e.clientY };
    try { edge.setPointerCapture(e.pointerId); } catch (_) { /* jsdom */ }
    e.preventDefault();
  });
  edge.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    paint(e.clientY - drag.y);
  });
  edge.addEventListener('pointerup', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    end(e.clientY - drag.y > PULL_EXIT_PX);
  });
  edge.addEventListener('pointercancel', () => { if (drag) end(false); });
  return edge;
}

/** 폰 집중 모드를 켜고 끈다. compact 폭이 아니면 켜지 않는다(끄기는 언제나 된다). 결과(켜졌는가)를 돌려준다. */
export function setMobileFocus(on) {
  if (on && !isCompact()) return false;
  setFocus(Boolean(on));
  return isFocusActive();
}

export function toggleMobileFocus() {
  return setMobileFocus(!isFocusActive());
}

// 스위치(focus.js)가 켜고 끌 때마다 — 상단바 버튼의 눌림 · 이름, 알약 내용.
document.addEventListener(FOCUS_EVENT, (e) => {
  const on = Boolean(e.detail?.on);
  _paintButton(on);
  if (on) paintPill();
});
// 시험·콘솔용 브리지(e2e가 상태를 되돌릴 때 쓴다).
window.vtMobileFocus = (on) => setMobileFocus(on);

// 터미널 탭을 떠나면 끝낸다 — 숨긴 아래 탭 없이 다른 탭 화면에 갇히지 않게.
document.addEventListener('vt:mobile-tab', (e) => {
  if (e.detail?.tab !== 'terminal' && isMobileFocus()) setMobileFocus(false);
});
// 알약·가장자리는 처음부터 만들어 둔다 — 보이는 조건은 CSS(html.vt-focus, 또는 자동 집중 중 개입이 있을 때)가 정한다.
if (document.body) { _buildPill(); _buildEdge(); paintPill(); }
onStatusChange(() => paintPill());
onSettings((changed) => {
  if (changed && typeof changed === 'object' && ('ui.focus.pill' in changed || 'keymap.bindingsV2' in changed)) paintPill();
});
