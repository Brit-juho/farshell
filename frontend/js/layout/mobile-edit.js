// 폰(compact, 폭 < 720)의 파일 편집 화면 — 시안 files.html M-F5.
//
// 편집을 시작하면(panels/viewer/edit.js가 'vt:viewer-edit'를 쏘고 칸에 vt-vw-editing을 단다):
//   - 아래 탭을 숨긴다(html.vt-mobile-editing, 98-mobile.css) — 편집기가 그 높이를 쓴다.
//   - 소프트 키보드 바로 위에 작은 키 줄(#vt-edit-keys: Tab ← → ↑ ↓ Esc 저장)을 띄운다. 키 모양은 터미널 키바의 것을
//     그대로 쓴다. 자리는 visualViewport로 잰다(term/keybar-ui.js positionBar와 같은 식) — 키보드가 레이아웃 뷰포트를
//     덮어도 줄이 키보드 위에 붙어 있어서 「저장」이 늘 손에 닿는다(위 띠의 저장 버튼은 화면이 밀리면 가려질 수 있다).
// 편집을 끝내거나, 편집 칸이 사라지거나(닫기·다른 파일), 폭이 720을 넘으면 원래대로 돌아간다(아래 탭이 다시 보인다).
//
// 키는 편집기(CodeMirror) 본문에 keydown을 보내 편집기 자신의 키맵이 처리하게 한다(Tab = 들여쓰기, 화살표 = 커서,
// Esc = 선택 풀기). 저장은 같은 편집 화면 위 띠의 「저장」(.vt-vw-save)을 누른다 — 저장 경로(save_id·충돌·승격)가 하나다.
// 키를 누를 때 pointerdown 기본 동작을 막아 편집기 초점(=소프트 키보드)을 뺏지 않는다(키바와 같은 이유).
import { t } from '../core/i18n.js';

const COMPACT_QUERY = '(max-width:719px)';
export const EDITING_CLASS = 'vt-mobile-editing';

// [key, 보이는 글자, 접근성 이름(없으면 글자), keyCode]
const KEYS = [
  ['Tab', 'Tab', null, 9],
  ['ArrowLeft', '←', () => t('왼쪽 화살표'), 37],
  ['ArrowRight', '→', () => t('오른쪽 화살표'), 39],
  ['ArrowUp', '↑', () => t('위쪽 화살표'), 38],
  ['ArrowDown', '↓', () => t('아래쪽 화살표'), 40],
  ['Escape', 'Esc', null, 27],
];

const root = document.documentElement;
let _row = null;
let _save = null;
let _on = false;
let _pane = null;
let _watch = null;     // 편집 칸의 조상들(칸이 떨어져 나가는 것)과 위 띠(저장 버튼 상태)를 본다

function _isCompact() {
  try { return Boolean(window.matchMedia?.(COMPACT_QUERY).matches); } catch (_) { return false; }
}

/** 화면에 붙어 있고 보이는 편집 중인 칸. 없으면 null. */
export function editingPane() {
  for (const el of document.querySelectorAll('.vt-vw-code-pane.vt-vw-editing')) {
    if (el.isConnected && el.getClientRects().length) return el;
  }
  return null;
}

/** 칸과 같은 편집 화면의 「저장」 버튼(edit.js decorateBar가 위 띠에 단다). */
function _saveButtonFor(pane) {
  for (let n = pane; n && n !== document.body; n = n.parentElement) {
    const b = n.querySelector?.('.vt-vw-save');
    if (b) return b;
  }
  return null;
}

function _build() {
  if (_row) return _row;
  const row = document.createElement('div');
  row.id = 'vt-edit-keys';
  row.setAttribute('role', 'toolbar');
  row.setAttribute('aria-label', t('편집 보조 키'));
  row.hidden = true;
  for (const [key, label, aria, code] of KEYS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'kb';
    b.textContent = label;
    b.dataset.key = key;
    b.dataset.code = String(code);
    if (aria) b.setAttribute('aria-label', aria());
    row.appendChild(b);
  }
  const sp = document.createElement('span');
  sp.className = 'vt-edit-keys-sp';
  row.appendChild(sp);
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'kb vt-edit-keys-save';
  save.textContent = t('저장');
  save.dataset.save = '1';
  row.appendChild(save);
  // 누름에서 기본 동작을 막아야 편집기 초점이 남는다(소프트 키보드가 내려가지 않는다).
  row.addEventListener('pointerdown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
  row.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
  row.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.disabled) return;
    if (b.dataset.save) { pressSave(); return; }
    sendKey(b.dataset.key, Number(b.dataset.code));
  });
  document.body.appendChild(row);
  _row = row;
  _save = save;
  return row;
}

/** 편집기 본문에 키 하나를 보낸다. 보냈으면 true. */
export function sendKey(key, keyCode = 0) {
  const pane = editingPane();
  const content = pane && pane.querySelector('.cm-content');
  if (!content) return false;
  if (document.activeElement !== content) { try { content.focus({ preventScroll: true }); } catch (_) {} }
  const ev = new KeyboardEvent('keydown', { key, code: key === 'Escape' ? 'Escape' : key, keyCode, which: keyCode, bubbles: true, cancelable: true });
  content.dispatchEvent(ev);
  return true;
}

/** 위 띠의 「저장」을 누른다. 눌렀으면 true. */
export function pressSave() {
  const pane = editingPane();
  const btn = pane && _saveButtonFor(pane);
  if (!btn || btn.disabled) return false;
  btn.click();
  return true;
}

function _syncSave() {
  if (!_save) return;
  const btn = _pane && _saveButtonFor(_pane);
  _save.disabled = !btn || btn.disabled;
}

// 키보드가 레이아웃 뷰포트를 덮은 높이 — 줄을 그만큼 올리고, 편집 화면 바닥도 같은 값으로 올린다(CSS --vt-edit-overlap).
function _position() {
  if (!_row) return;
  const vv = window.visualViewport;
  const overlap = vv ? Math.max(0, Math.round(window.innerHeight - (vv.height + vv.offsetTop))) : 0;
  _row.style.transform = overlap > 0 ? `translateY(${-overlap}px)` : '';
  root.style.setProperty('--vt-edit-overlap', `${overlap}px`);
  if (_row.offsetHeight) root.style.setProperty('--vt-edit-keys-h', `${_row.offsetHeight}px`);
}

function _unwatch() {
  if (_watch) { try { _watch.disconnect(); } catch (_) {} }
  _watch = null;
}

// 칸이 통째로 빠지는 것(닫기 · 다른 파일 열기 · 탭 바꾸기)은 편집 끝내기 알림 없이 일어난다 — 칸의 조상들에서
// 직계 자식이 바뀌는 것만 본다(편집기 안의 글자 변화는 칸 안쪽이라 걸리지 않는다). 위 띠는 저장 버튼 상태를 따라가려고 본다.
function _watchPane(pane) {
  _unwatch();
  if (typeof MutationObserver === 'undefined') return;
  _watch = new MutationObserver(() => sync());
  for (let n = pane.parentElement; n && n !== document.documentElement; n = n.parentElement) {
    _watch.observe(n, { childList: true, attributes: true, attributeFilter: ['hidden', 'class', 'style'] });
  }
  const save = _saveButtonFor(pane);
  const bar = save && save.parentElement && save.parentElement.parentElement;
  if (bar) _watch.observe(bar, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
}

/** 지금 상태(폭 · 편집 중인 칸)에 맞춰 켜고 끈다. 여러 번 불러도 안전하다. */
export function sync() {
  const pane = _isCompact() ? editingPane() : null;
  const on = !!pane;
  if (pane !== _pane) {
    _pane = pane;
    if (pane) _watchPane(pane); else _unwatch();
  }
  if (on !== _on) {
    _on = on;
    root.classList.toggle(EDITING_CLASS, on);
    if (on) _build();
    if (_row) _row.hidden = !on;
    if (!on) { root.style.removeProperty('--vt-edit-overlap'); root.style.removeProperty('--vt-edit-keys-h'); }
  }
  if (on) { _syncSave(); _position(); }
}

export function isMobileEditing() { return _on; }

document.addEventListener('vt:viewer-edit', () => sync());
document.addEventListener('vt:mobile-tab', () => sync());
try { window.matchMedia?.(COMPACT_QUERY)?.addEventListener?.('change', () => sync()); } catch (_) { /* matchMedia 없는 환경 */ }
if (window.visualViewport) {
  const onViewport = () => { if (_on) _position(); };
  window.visualViewport.addEventListener('resize', onViewport);
  window.visualViewport.addEventListener('scroll', onViewport);
}
window.addEventListener('resize', () => { if (_on) _position(); });
