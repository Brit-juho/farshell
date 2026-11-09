// 위험 포트 공개 확인(시안 P06-d) — 포트 번호를 그대로 입력해야 「공개」가 켜진다.
//
// 어떤 포트가 위험한지는 **서버만** 안다(port_share_store.danger_reason — 웹·CLI가 같은 판정).
// 화면은 목록을 따로 들고 있지 않고, 서버가 409 dangerous_port 로 이유를 돌려줄 때만 이 창을 연다.
// 여기서 「예」를 받아야 호출부가 force_danger:true 로 다시 보낸다 — 서버는 그 값 없이는 계속 거부한다.
//
// 기본 초점은 「취소」(시안 O16-G 위험 확인 규칙) — Enter 한 번으로 열리지 않게. Esc·배경 클릭·X는 취소.
// 붙여넣기는 막지 않는다(입력창 기본 동작). 앞뒤 공백만 무시하고 나머지는 글자 그대로 비교한다("09222"는 다르다).
import { closePanel } from '../panel.js';
import { openDialog, nextId } from '../../core/dialog.js';
import { vtEsc } from '../../core/api.js';
import { t } from '../../core/i18n.js';
import { icon } from '../../ui/icons.js';

/** 입력값이 포트 번호와 정확히 같은가 — 순수 규칙(테스트가 직접 본다). */
export function portTypedMatches(value, port) {
  return String(value == null ? '' : value).trim() === String(port);
}

/** 위험 포트 확인 대화상자. 번호를 맞게 입력하고 「공개」를 누르면 true, 그 밖의 모든 닫힘은 false. */
export function confirmDangerousPort(port, reason) {
  const id = nextId();
  const inputId = `${id}-port`;
  const descId = `${id}-desc`;
  return new Promise((resolve) => {
    let result = false;
    let settled = false;
    const finish = () => { if (!settled) { settled = true; resolve(result); } };
    const { body } = openDialog({
      id, title: t('포트 {port} 공개', { port }), onClose: finish, extraClass: 'vt-pt-danger-dialog',
      bodyHTML: `
        <div class="vt-pt-danger" role="alert">
          <div class="vt-pt-danger-head">
            <span class="vt-pt-danger-ic" aria-hidden="true">${icon('alert-triangle', 16)}</span>
            <span class="vt-pt-danger-title">${vtEsc(t('{port}는 위험 포트입니다', { port }))}</span>
          </div>
          <div class="vt-pt-danger-text" id="${descId}">
            <p class="vt-pt-danger-reason">${vtEsc(reason || '')}</p>
            <p>${vtEsc(t('공개 터널로 열면 이 포트가 원격 조작 통로가 됩니다. 로그인·접속 코드를 걸어도 위험이 사라지지 않습니다. 정말 공개하려면 포트 번호를 그대로 입력하세요.'))}</p>
          </div>
          <label class="vt-pt-danger-label" for="${inputId}">${vtEsc(t('포트 번호 입력'))}</label>
          <input id="${inputId}" type="text" class="vt-dialog-input vt-pt-danger-input" inputmode="numeric"
            autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${vtEsc(String(port))}"
            aria-describedby="${descId}">
        </div>
        <div class="vt-dialog-actions">
          <span class="vt-pt-danger-hint" aria-live="polite">${vtEsc(t('번호가 맞으면 버튼이 켜집니다'))}</span>
          <button type="button" class="vt-btn lg vt-dialog-cancel">${vtEsc(t('취소'))}</button>
          <button type="button" class="vt-btn lg danger vt-dialog-ok" disabled>${icon('globe', 14)}${vtEsc(t('공개'))}</button>
        </div>`,
    });
    if (!body) { resolve(false); return; }
    const input = body.querySelector('.vt-pt-danger-input');
    const ok = body.querySelector('.vt-dialog-ok');
    const sync = () => { ok.disabled = !portTypedMatches(input.value, port); };
    // 버튼이 꺼져 있어도 클릭 이벤트를 직접 쏘는 경로가 있을 수 있다 — 누를 때 한 번 더 본다.
    const submit = () => {
      if (!portTypedMatches(input.value, port)) { sync(); return; }
      result = true;
      closePanel(id);
    };
    input.addEventListener('input', sync);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.isComposing && ev.keyCode !== 229) { ev.preventDefault(); submit(); }
    });
    ok.addEventListener('click', submit);
    body.querySelector('.vt-dialog-cancel').addEventListener('click', () => closePanel(id));
    // openPanel 의 기본 초기 포커스(닫기 X) 대신 「취소」 — 위험 확인의 기본값은 취소다.
    body.querySelector('.vt-dialog-cancel').focus();
  });
}

/** 공개 요청을 보내고, 서버가 위험 포트라고 거부하면(409 dangerous_port) 번호 확인을 받은 뒤에만
 * `force_danger: true` 를 붙여 한 번 더 보낸다. send(extra) 는 요청 본문에 extra 를 얹어 보내는 함수.
 * 돌려주는 값: `{ result }`(성공 응답, 승격 취소면 null) 또는 `{ canceled: true }`. 그 밖의 실패는 그대로 던진다. */
export async function exposeWithDangerConfirm(port, send) {
  try {
    return { result: await send({}) };
  } catch (e) {
    if (!(e && e.status === 409 && e.data && e.data.error === 'dangerous_port')) throw e;
    if (!(await confirmDangerousPort(port, e.data.reason))) return { canceled: true };
    return { result: await send({ force_danger: true }) };
  }
}
