// window.confirm/alert/prompt 대체 — 네이티브 브라우저 다이얼로그는 스킨을
// 못 입고(항상 OS 기본 회색 팝업) 모바일에서 레이아웃을 깨뜨린다. 새 오버레이를
// 처음부터 짜지 않고 openPanel()의 뼈대(backdrop/카드/닫기X/Esc/배경클릭/
// 포커스트랩/포커스복귀, panels/panel.js)를 그대로 빌려 쓰고, 그 위에
// Promise 기반 API만 얹는다.
import { openPanel, closePanel } from '../panels/panel.js';
import { vtEsc } from './api.js';
import { t } from './i18n.js';
import { icon } from '../ui/icons.js';

let seq = 0;
export const nextId = () => `vt-dialog-${++seq}`;

/** 확인/입력/알림 셋에 안 맞는 커스텀 다이얼로그용 하부 조립 — 호출부가 직접
 * 마크업과 이벤트를 붙인다(포트 노출의 모드 선택처럼 라디오+선택 조합이 필요할 때). */
export function openDialog({ id, title, bodyHTML, onClose, iconName = '', extraClass = '' }) {
  // 리디자인 6단계(시안 O16-G · O11-C) — 머리 왼쪽에 16px 아이콘을 둘 수 있다(비밀번호 확인의 자물쇠 등). 이름은 코드가 고른 고정값이다.
  const ic = iconName ? `<span class="vt-dialog-ic" aria-hidden="true">${icon(iconName, 16)}</span>` : '';
  const r = openPanel({
    id,
    ariaLabel: title,
    extraClass: 'vt-dialog' + (extraClass ? ' ' + extraClass : ''),
    headHTML: `${ic}<div class="vt-vw-title">${vtEsc(title)}</div>`,
    bodyId: `${id}-body`,
    bodyHTML,
    onClose,
  });
  // 같은 id가 이미 열려 있으면 openPanel이 토글-닫기만 하고 null을 돌려준다
  // (동시에 두 번 못 부르게 하는 게 아니라 nextId()가 매번 새 id를 주므로
  // 실제로는 겹칠 일이 없다 — 그래도 계약대로 null 방어).
  return r || { body: null };
}

/** 파괴적/비파괴적 확인. danger=true면 확인 버튼을 강조하지 않는다(components.css
 * "위험한 동작은 채움으로 만들지 않는다" 규칙, .vt-btn.danger는 outline).
 * 위험 동작은 아무 버튼도 채우지 않고 기본 초점을 「취소」에 둔다(시안 O16-G) — Enter 한 번으로 지워지지 않게. */
export function confirmDialog(message, opts = {}) {
  const { title = t('확인'), okLabel = t('확인'), cancelLabel = t('취소'), danger = false } = opts;
  const id = nextId();
  return new Promise((resolve) => {
    let result = false;
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(result); } };
    const { body } = openDialog({
      id, title, onClose: finish,
      bodyHTML: `
        <p class="vt-dialog-msg">${vtEsc(message)}</p>
        <div class="vt-dialog-actions">
          <button type="button" class="vt-btn vt-dialog-cancel">${vtEsc(cancelLabel)}</button>
          <button type="button" class="vt-btn ${danger ? 'danger' : 'solid'} vt-dialog-ok">${vtEsc(okLabel)}</button>
        </div>`,
    });
    if (!body) { resolve(false); return; }
    if (danger) body.querySelector('.vt-dialog-cancel').focus();
    body.querySelector('.vt-dialog-ok').addEventListener('click', () => { result = true; closePanel(id); });
    body.querySelector('.vt-dialog-cancel').addEventListener('click', () => closePanel(id));
  });
}

/** 텍스트 입력. 취소/배경클릭/Esc는 null, 확인은 입력값(빈 문자열 가능)을 돌려준다. */
export function promptDialog(message, opts = {}) {
  const { title = t('입력'), okLabel = t('확인'), cancelLabel = t('취소'), defaultValue = '', password = false } = opts;
  const id = nextId();
  return new Promise((resolve) => {
    let result = null;
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(result); } };
    const { body } = openDialog({
      id, title, onClose: finish, iconName: password ? 'lock' : '',
      bodyHTML: `
        <p class="vt-dialog-msg">${vtEsc(message)}</p>
        <input type="${password ? 'password' : 'text'}" class="vt-dialog-input" value="${vtEsc(defaultValue)}" autocomplete="off" autocapitalize="off" spellcheck="false">
        <div class="vt-dialog-actions">
          <button type="button" class="vt-btn vt-dialog-cancel">${vtEsc(cancelLabel)}</button>
          <button type="button" class="vt-btn solid vt-dialog-ok">${vtEsc(okLabel)}</button>
        </div>`,
    });
    if (!body) { resolve(null); return; }
    const input = body.querySelector('.vt-dialog-input');
    const submit = () => { result = input.value; closePanel(id); };
    body.querySelector('.vt-dialog-ok').addEventListener('click', submit);
    body.querySelector('.vt-dialog-cancel').addEventListener('click', () => closePanel(id));
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && !ev.isComposing && ev.keyCode !== 229) { ev.preventDefault(); submit(); } });
    // openPanel의 기본 초기 포커스는 첫 포커스 가능 요소(닫기 X) — 입력창으로 되돌린다.
    input.focus();
    input.select();
  });
}

/** 정보 알림. 버튼 하나, 닫히면(어느 경로로든) resolve. */
export function alertDialog(message, opts = {}) {
  const { title = t('알림'), okLabel = t('확인') } = opts;
  const id = nextId();
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(); } };
    const { body } = openDialog({
      id, title, onClose: finish,
      bodyHTML: `
        <p class="vt-dialog-msg">${vtEsc(message)}</p>
        <div class="vt-dialog-actions">
          <button type="button" class="vt-btn solid vt-dialog-ok">${vtEsc(okLabel)}</button>
        </div>`,
    });
    if (!body) { resolve(); return; }
    body.querySelector('.vt-dialog-ok').addEventListener('click', () => closePanel(id));
  });
}
