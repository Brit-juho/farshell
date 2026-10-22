// 「에이전트와 공유…」/「공유 설정…」 대화상자. 뼈대(backdrop·포커스 트랩·Esc·포커스 복귀)는
// core/dialog.js의 openDialog(openPanel) 그대로, 여기는 내용과 검증만 얹는다.
import { openDialog, nextId, confirmDialog } from '../core/dialog.js';
import { closePanel } from '../panels/panel.js';
import { vtEsc } from '../core/api.js';
import { shareApi } from './share-api.js';
import { mountGrants } from '../authz/grants.js';
import {
  validateNick, suggestNick, normalizeMode, SESSION_NAME_RE, MODE_LABEL, MODE_HELP, WARNING_LINES, UNSHARE_NOTE,
  errorText,
} from './share-data.js';
import { t } from '../core/i18n.js';

function modeRadio(group, value, checked) {
  return `
    <label class="vt-share-mode">
      <input type="radio" name="${group}" value="${value}"${checked ? ' checked' : ''}>
      <span class="vt-share-mode-text">
        <span class="vt-share-mode-name">${vtEsc(MODE_LABEL[value])}</span>
        <span class="vt-share-mode-help">${vtEsc(MODE_HELP[value])}</span>
      </span>
    </label>`;
}

/**
 * 공유 지정/변경. `share`가 있으면 설정 변경(기존 닉네임·모드로 시작), 없으면 새 공유(세션 이름에서 닉네임 제안).
 * 성공하면 `{nick, mode}`, 취소·닫기면 null 로 resolve 한다.
 */
export function openSessionAccess(name, share) {
  return openShareDialog(name, share, { access: true });
}

export function openShareDialog(name, share, { access = false } = {}) {
  const id = nextId();
  const group = `${id}-mode`;
  const editing = !!share;
  const shareable = SESSION_NAME_RE.test(name);
  const initialMode = normalizeMode(share && share.mode);
  const initialNick = share ? share.nick : suggestNick(name);
  return new Promise((resolve) => {
    let result = null;
    let settled = false;
    let busy = false;
    let grants;
    const finish = () => { grants?.dispose(); if (!settled) { settled = true; resolve(result); } };
    const { body } = openDialog({
      id,
      title: access ? t('공유 및 권한 · {name}', { name }) : editing ? t('공유 설정 · {name}', { name }) : t('에이전트와 공유 · {name}', { name }),
      onClose: finish,
      bodyHTML: `
        <div class="vt-session-share" ${access && !shareable ? 'hidden' : ''}>
        <div class="vt-share-warn" role="note">${WARNING_LINES.map((l) => `<p>${vtEsc(l)}</p>`).join('')}</div>
        <div class="vt-share-field">
          <label class="vt-share-label" for="${id}-nick">${t('닉네임')}</label>
          <input id="${id}-nick" class="vt-input lg vt-share-nick" type="text" value="${vtEsc(initialNick)}"
            maxlength="64" autocomplete="off" autocapitalize="off" spellcheck="false"
            aria-describedby="${id}-nick-msg">
          <div id="${id}-nick-msg" class="vt-share-msg" aria-live="polite">${t('영문·숫자·_·- 1~32자. 에이전트는 이 이름으로 세션을 부릅니다.')}</div>
        </div>
        <div class="vt-share-field" role="radiogroup" aria-label="${t('공유 모드')}">
          <div class="vt-share-label">${t('모드')}</div>
          ${modeRadio(group, 'read', initialMode === 'read')}
          ${modeRadio(group, 'readwrite', initialMode === 'readwrite')}
          <div class="vt-share-msg vt-share-lower" hidden>${t('읽기 전용으로 낮추면 이미 허용한 쓰기 권한이 다음 호출부터 멈춥니다.')}</div>
        </div>
        ${editing ? `<p class="vt-share-note">${vtEsc(UNSHARE_NOTE)}</p>` : ''}
        <div class="vt-dialog-actions">
          <button type="button" class="vt-btn lg vt-share-cancel">${access ? t('닫기') : t('취소')}</button>
          ${access && editing && shareable ? `<button type="button" class="vt-btn lg danger vt-share-unshare">${t('공유 해제')}</button>` : ''}
          <button type="button" class="vt-btn lg solid vt-share-ok">${editing ? t('저장') : t('공유')}</button>
        </div></div>
        ${access ? `${!shareable ? `<p class="vt-authz-note">${t('이 세션 이름은 공유를 지원하지 않습니다. 권한은 아래에서 확인할 수 있습니다.')}</p>` : ''}
          <section class="vt-session-access-grants" aria-label="${t('이 세션에 허용된 권한')}">
            <h3 class="vt-authz-h">${t('이 세션에 허용된 권한')}</h3>
            <p class="vt-authz-note">${t('이 세션을 직접 대상으로 하는 권한입니다. 전체 권한과 승인 대기는 설정의 권한에서 확인하세요.')}</p>
            <div class="vt-session-grants"></div>
          </section>` : ''}`,
    });
    if (!body) { resolve(null); return; }

    const input = body.querySelector('.vt-share-nick');
    const msg = body.querySelector(`#${id}-nick-msg`);
    const ok = body.querySelector('.vt-share-ok');
    const lower = body.querySelector('.vt-share-lower');
    const hint = msg.textContent;
    const mode = () => normalizeMode((body.querySelector(`input[name="${group}"]:checked`) || {}).value);

    const showError = (text) => {
      msg.textContent = text || hint;
      msg.classList.toggle('error', !!text);
      if (text) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
    };
    const refresh = () => {
      const err = validateNick(input.value);
      // 아직 아무것도 안 쳤을 때의 "입력하세요"는 오류처럼 빨갛게 띄우지 않는다 — 버튼만 막는다.
      showError(err && input.value ? err : '');
      ok.disabled = busy || !!err;
      lower.hidden = !(editing && initialMode === 'readwrite' && mode() === 'read');
    };
    input.addEventListener('input', refresh);
    body.querySelectorAll(`input[name="${group}"]`).forEach((r) => r.addEventListener('change', refresh));

    const submit = async () => {
      if (busy) return;
      const nick = input.value;
      if (validateNick(nick)) { refresh(); input.focus(); return; }
      busy = true; refresh();
      try {
        const res = await shareApi.share(name, nick, mode());
        result = res.share || { nick, mode: mode() };
        if (typeof window.showToast === 'function') {
          window.showToast(t('공유함 · {nick} · {mode}', { nick: result.nick, mode: MODE_LABEL[result.mode] }), 'success');
        }
        closePanel(id);
      } catch (e) {
        busy = false;
        refresh();
        showError(errorText(e));
        input.focus();
      }
    };
    ok.addEventListener('click', submit);
    body.querySelector('.vt-share-cancel').addEventListener('click', () => closePanel(id));
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } });

    if (access) {
      grants = mountGrants(body.querySelector('.vt-session-grants'), { session: name });
      body.querySelector('.vt-share-unshare')?.addEventListener('click', async () => {
        if (busy) return;
        busy = true; refresh();
        const confirmed = await confirmDialog(UNSHARE_NOTE, { title: t('공유 해제'), okLabel: t('공유 해제'), danger: true });
        if (!confirmed || !body.isConnected) { busy = false; refresh(); return; }
        body.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        if (await unshareSession(name)) closePanel(id);
        else { busy = false; body.querySelectorAll('button').forEach((b) => { b.disabled = false; }); refresh(); }
      });
    }
    refresh();
    if (access && !shareable) return;
    // openPanel의 첫 포커스는 닫기 X 다 — 입력창으로 옮긴다(모바일에서는 키보드가 뜨는 대신 곧바로 고칠 수 있다).
    input.focus();
    input.select();
  });
}

/** 공유 해제. 서버가 그 세션의 권한·대기 요청을 같이 지운다. */
export async function unshareSession(name) {
  try {
    const res = await shareApi.unshare(name);
    if (typeof window.showToast === 'function') {
      window.showToast(t('공유 해제함 · 권한 {n}개 삭제', { n: res.revokedGrants || 0 }), 'success');
    }
    return true;
  } catch (e) {
    if (typeof window.showToast === 'function') window.showToast(errorText(e), 'error');
    return false;
  }
}
