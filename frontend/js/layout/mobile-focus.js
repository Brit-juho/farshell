// 리디자인 8단계 — 폰(compact, 폭 < 720)의 수동 집중 모드(시안 M14·M14-a·M14-c).
//
// 들어가기: 상단바의 집중 모드 버튼(#vt-focus-btn, data-action="focus.toggle")만. 두 손가락 두 번 탭은 두지 않는다
// (사용자 결정 5 — 핀치 확대와 겹친다). 켜지면 `html.vt-focus` — 상단바와 아래 탭을 숨기고 키바만 남긴다
// (닫혀 있어도 보인다 — 여닫기 버튼이 상단바와 함께 사라지므로). 마이크는 떠 있는 자리에서 키바 첫 키로
// 옮겨 간다(term/keybar.js placeMicButton이 'vt:focus-change'를 듣는다).
//
// 나가기: (1) 오른쪽 위 알약의 나가기 버튼, (2) 위 가장자리 24px에서 아래로 64px 넘게 끌었다 놓기(덜 끌면 제자리),
// (3) 터미널 탭을 떠나거나 폭이 720 이상이 될 때(그 화면에는 숨길 상단바·아래 탭이 없다).
//
// 개입 알약: 승인을 기다리는 세션이 있으면 「개입 N · 이름」이 알약에 보이고, 누르면 그 세션으로 간다
// (레일·세션 탭의 행 열기와 같은 window 브리지 vtOpenSessionFromRail). 터치 영역은 44px.
//
// 자동 집중(보이는 높이 < 300, term/keybar-ui.js의 body.vt-kb-focus)은 그대로 둔다 — 상태가 없고 키보드가 내려가면
// 저절로 풀린다. 이 파일의 수동 집중과 겹쳐도 둘 다 같은 것(상단바·아래 탭)을 숨길 뿐이라 충돌하지 않는다.
//
// 데스크톱 집중 모드(리디자인 7단계)와 **같은 액션 이름(`focus.toggle`)과 같은 클래스(`html.vt-focus`)**를 쓴다.
// 이 파일은 compact 폭에서만 그 액션을 맡고, 넓은 화면에서는 먼저 등록된 처리기(데스크톱)에 넘긴다.
import { getAction, registerAction } from '../core/dom.js';
import { t } from '../core/i18n.js';
import { icon, stateIcon } from '../ui/icons.js';
import { allStatuses, onStatusChange } from '../agent/state.js';

export const FOCUS_CLASS = 'vt-focus';
/** 위 가장자리 띠 높이 — 이 안에서 시작한 끌기만 나가기 제스처로 본다. */
export const EDGE_PX = 24;
/** 이만큼 넘게 끌었다 놓아야 나간다. 덜 끌면 제자리. */
export const PULL_EXIT_PX = 64;

const COMPACT_QUERY = '(max-width:719px)';
const root = document.documentElement;
let _pill = null;
let _edge = null;
let _hint = null;

function _mq() {
  try { return window.matchMedia?.(COMPACT_QUERY) || null; } catch (_) { return null; }
}
export function isCompact() { return Boolean(_mq()?.matches); }
export function isMobileFocus() { return root.classList.contains(FOCUS_CLASS) && isCompact(); }

/** 승인을 기다리는 세션(tmux 이름) — 상태 저장소(agent/state.js) 순서 그대로. */
export function waitingSessions() {
  const out = [];
  for (const [name, st] of allStatuses()) if (st === 'waiting') out.push(name);
  return out;
}

function _paintButton(on) {
  const btn = document.getElementById('vt-focus-btn');
  if (!btn) return;
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  const label = on ? t('집중 모드 끝내기') : t('집중 모드');
  btn.setAttribute('aria-label', label);
  btn.dataset.tip = label;
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
  go.addEventListener('click', () => {
    const name = waitingSessions()[0];
    if (name) void window.vtOpenSessionFromRail?.({ tmuxName: name });
  });
  const exit = document.createElement('button');
  exit.type = 'button';
  exit.className = 'vt-focus-pill-exit';
  exit.setAttribute('aria-label', t('집중 모드 끝내기'));
  exit.dataset.tip = t('집중 모드 끝내기');
  exit.innerHTML = icon('minimize', 16);
  exit.addEventListener('click', () => setMobileFocus(false));
  pill.append(go, exit);
  document.body.appendChild(pill);
  _pill = pill;
  return pill;
}

/** 알약 내용 — 개입이 있으면 「(대기 아이콘) 개입 N 이름」, 없으면 숨는다(나가기 버튼만 남는다). */
export function paintPill() {
  if (!_pill) return;
  const go = _pill.querySelector('.vt-focus-pill-go');
  const names = waitingSessions();
  _pill.dataset.waiting = String(names.length);
  if (!names.length) { go.hidden = true; go.replaceChildren(); return; }
  go.hidden = false;
  const ic = document.createElement('span');
  ic.className = 'vt-focus-pill-ic';
  ic.innerHTML = stateIcon('waiting', 12);
  const count = document.createElement('span');
  count.className = 'vt-focus-pill-n';
  count.textContent = t('개입 {n}', { n: names.length });
  const nm = document.createElement('span');
  nm.className = 'vt-focus-pill-name';
  nm.textContent = names[0];   // 사용자 입력(세션 이름)이라 textContent로만
  go.replaceChildren(ic, count, nm);
  go.setAttribute('aria-label', t('{name} 승인 요청 — 누르면 그 세션으로', { name: names[0] }));
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

/** 폰 집중 모드를 켜고 끈다. compact 폭이 아니면 켜지 않는다(끄기는 언제나 된다). */
export function setMobileFocus(on) {
  const next = Boolean(on) && isCompact();
  const was = root.classList.contains(FOCUS_CLASS);
  if (next === was) { _paintButton(next); return next; }
  root.classList.toggle(FOCUS_CLASS, next);
  _paintButton(next);
  if (next) paintPill();
  document.dispatchEvent(new CustomEvent('vt:focus-change', { detail: { on: next, source: 'mobile' } }));
  return next;
}

export function toggleMobileFocus() {
  return setMobileFocus(!root.classList.contains(FOCUS_CLASS));
}

// 같은 액션 이름을 데스크톱 집중 모드가 먼저 등록했을 수 있다 — 넓은 화면에서는 그쪽에 넘긴다.
const _desktopToggle = getAction('focus.toggle');
registerAction('focus.toggle', (...args) => {
  if (isCompact()) return toggleMobileFocus();
  return typeof _desktopToggle === 'function' ? _desktopToggle(...args) : undefined;
});
// 시험·콘솔용 브리지(e2e가 상태를 되돌릴 때 쓴다).
window.vtMobileFocus = (on) => setMobileFocus(on);

// 터미널 탭을 떠나면 끝낸다 — 숨긴 아래 탭 없이 다른 탭 화면에 갇히지 않게.
document.addEventListener('vt:mobile-tab', (e) => {
  if (e.detail?.tab !== 'terminal' && root.classList.contains(FOCUS_CLASS) && isCompact()) setMobileFocus(false);
});
// 폭이 720 이상이 되면(회전·창 크기) 폰 집중 모드를 푼다. 데스크톱 집중 모드는 그쪽 몫이라 건드리지 않는다.
try {
  _mq()?.addEventListener?.('change', (e) => {
    if (!e.matches && _pill && root.classList.contains(FOCUS_CLASS)) {
      root.classList.remove(FOCUS_CLASS);
      _paintButton(false);
      document.dispatchEvent(new CustomEvent('vt:focus-change', { detail: { on: false, source: 'mobile' } }));
    }
  });
} catch (_) { /* matchMedia 없는 환경 */ }
// 알약·가장자리는 처음부터 만들어 둔다 — 보이는 조건은 CSS(html.vt-focus, 또는 자동 집중 중 개입이 있을 때)가 정한다.
if (document.body) { _buildPill(); _buildEdge(); paintPill(); }
onStatusChange(() => paintPill());
