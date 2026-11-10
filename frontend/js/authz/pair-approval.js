// 페어 승인(kind=`pair`)의 한 화면 — 누가 누구와, 어떤 묶음을, 어떤 역할(프리셋)로, 확인 문구 둘, 높은 권한 항목(체크 박스, 기본 꺼짐).
// 승인함 모달(inbox.js)이 body 에 이 HTML 을 올리고, 체크를 이 파일의 bind 가 받는다. 서버가 준 글자는 모두 이스케이프만 한다.
// 확인 문구는 사람이 입력하지 않는다 — 두 에이전트 화면에 뜬 문구와 눈으로 비교한다(서버 phrase 검증은 선택).
import { vtEsc } from '../core/api.js';
import { t } from '../core/i18n.js';
import { pairParties, rolePreset, roleLabel, pairSentence, pairAllowLabel } from './data.js';

const kindText = (k) => (k === 'human' ? t('사람') : t('에이전트'));

function partiesHTML(req) {
  return `<ul class="vt-pair-parties">${pairParties(req).map((p) => {
    const pr = rolePreset(req, p.role);
    return `<li class="vt-pair-party" data-seat="${vtEsc(p.seat)}" data-role="${vtEsc(p.role)}">
      <div class="vt-pair-party-top"><span class="vt-authz-who">${vtEsc(p.name)}</span><span class="vt-authz-sub">${vtEsc(kindText(p.kind))} · ${vtEsc(roleLabel(p.role))}</span></div>
      <div class="vt-pair-preset">${vtEsc(pr.name)}</div>
      <div class="vt-authz-row-detail">${vtEsc(pr.detail)}</div>
    </li>`;
  }).join('')}</ul>`;
}

function bundleHTML(req) {
  const b = (req.pair && req.pair.bundle) || {};
  const sessions = b.sessions || [];
  const dirs = b.dirs || [];
  return `<dl class="vt-authz-facts vt-pair-bundle">
    <dt>${t('세션')}</dt><dd>${sessions.length ? sessions.map((s) => `<span class="vt-pair-chip">${vtEsc(s)}</span>`).join('') : vtEsc(t('없음'))}</dd>
    <dt>${t('폴더')}</dt><dd>${dirs.length ? dirs.map((d) => `<span class="vt-authz-code">${vtEsc(d)}</span>`).join('<br>') : vtEsc(t('없음'))}</dd>
    <dt>${t('폴더 점유(lease)')}</dt><dd>${vtEsc(b.leases ? t('폴더마다 운전자 이름으로 점유합니다') : t('점유 안 함'))}</dd>
  </dl>`;
}

function phrasesHTML(req) {
  const ph = (req.pair && req.pair.phrases) || {};
  const [a, b] = pairParties(req);
  const same = String(ph.initiator || '') === String(ph.joiner || '');
  const cell = (who, text) => `<div class="vt-pair-phrase-cell"><span class="vt-authz-sub">${vtEsc(t('{who} 쪽 문구', { who }))}</span><strong class="vt-pair-phrase">${vtEsc(text || '—')}</strong></div>`;
  return `<div class="vt-pair-phrases" role="group" aria-label="${vtEsc(t('확인 문구'))}">
    <div class="vt-pair-phrase-grid">${cell(a.name, ph.initiator)}${cell(b.name, ph.joiner)}</div>
    <p class="vt-authz-note">${t('두 에이전트가 각자 화면에 보여 준 문구와 위 문구를 하나씩 비교하세요. 하나라도 다르면 허용하지 말고 거부하세요.')}</p>
    ${same ? '' : `<p class="vt-authz-warn" role="alert">${t('두 문구가 서로 다릅니다 — 거부하세요.')}</p>`}
  </div>`;
}

function elevatedHTML(req, ticks) {
  const items = (req.pair && req.pair.elevatedItems) || [];
  if (!items.length) return '';
  const driver = pairParties(req).find((p) => p.role === 'driver');
  return `<fieldset class="vt-pair-elev">
    <legend>${t('높은 권한 — 체크한 것만 줍니다')}</legend>
    ${items.map((it) => `<label class="vt-pair-tick"><input type="checkbox" data-tick="${vtEsc(it.id)}"${ticks.has(it.id) ? ' checked' : ''}>
      <span class="vt-pair-tick-text"><span class="vt-pair-tick-name">${vtEsc(it.label || it.id)}</span><span class="vt-authz-code">${vtEsc((it.reasons || []).join(', '))}</span></span></label>`).join('')}
    <p class="vt-authz-note">${vtEsc(t('체크한 항목은 {who}에게만 더해집니다. 체크한 것이 하나라도 있으면 허용할 때 비밀번호를 한 번 다시 확인합니다.', { who: driver ? driver.name : t('운전자') }))}</p>
  </fieldset>`;
}

/** 모달 body 의 HTML. ticks: 체크한 항목 id 의 Set(아무것도 미리 체크하지 않는다). */
export function pairBodyHTML(req, ticks, errorMsg) {
  return `
    <p class="vt-authz-ask">${vtEsc(pairSentence(req))}</p>
    <h4 class="vt-authz-h">${t('각자 받는 권한')}</h4>
    ${partiesHTML(req)}
    <h4 class="vt-authz-h">${t('함께 쓰는 묶음')}</h4>
    ${bundleHTML(req)}
    <h4 class="vt-authz-h">${t('확인 문구 비교')}</h4>
    ${phrasesHTML(req)}
    ${elevatedHTML(req, ticks)}
    <div class="vt-authz-err" role="alert">${vtEsc(errorMsg || '')}</div>
    <p class="vt-authz-note">${vtEsc((req.pair && req.pair.note) || t('승인하면 이 묶음 안에서 운전자(driver)와 길잡이(navigator) 중 하나를 각자 갖습니다. 나중에 권한을 넓히려면 새로 승인해야 합니다.'))}</p>
    <p class="vt-authz-note">${t('허용한 권한은 설정의 「권한」에서 언제든 회수할 수 있습니다.')}</p>
    <div class="vt-dialog-actions vt-authz-acts vt-authz-foot vt-pair-acts">
      <button type="button" class="vt-btn lg vt-authz-act" data-act="deny">${t('거부')}</button>
      <span class="vt-authz-gap"></span>
      <button type="button" class="vt-btn lg vt-authz-act vt-pair-allow" data-act="allow">${vtEsc(pairAllowLabel(req, [...ticks]))}</button>
      <button type="button" class="vt-btn lg quiet vt-authz-snooze">${t('나중에')}</button>
    </div>`;
}

/** 체크 박스를 묶는다 — 체크가 바뀌면 허용 버튼 글자(= 무엇을 주는지)를 바로 고친다. */
export function bindPair(body, req, ticks) {
  const allow = body.querySelector('.vt-pair-allow');
  body.querySelectorAll('input[data-tick]').forEach((cb) => {
    cb.addEventListener('change', () => {
      if (cb.checked) ticks.add(cb.dataset.tick); else ticks.delete(cb.dataset.tick);
      if (allow) allow.textContent = pairAllowLabel(req, [...ticks]);
    });
  });
}
