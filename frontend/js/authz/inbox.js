// 승인함 — 모든 종류(share·msg·peer·generic)의 대기 요청을 한 목록으로 보이고, 한 번에 하나씩 결정하는 모달.
//
// 오조작 방지 — 이 창은 사용자가 터미널에 치던 도중에 뜰 수 있다(옛 승인 모달의 규칙을 그대로 지킨다):
//  1. 기본 포커스는 「거부」다(안전한 쪽). Enter 한 번으로는 아무것도 허용되지 않는다.
//  2. 창이 뜬 뒤 ARM_MS 동안은 어떤 결정 버튼도 눌려도 무시한다 — 치던 Enter·Space 가 이어서 들어와도 결정이 되지 않는다.
//  3. Esc·배경·X 는 결정이 아니라 「나중에」다(요청은 서버에 남는다, 설정의 「권한」에서 다시 열 수 있다).
//  4. 「모두 허용」은 없다 — 뒤늦게 온 요청까지 허용해 버린다. 결정은 항상 지금 보이는 요청 하나에만 간다.
// 요청 대상·scope·조건·에이전트가 쓴 사유는 서버가 준 글자를 가공 없이(이스케이프만) 보인다.
import { openPanel, closePanel } from '../panels/panel.js';
import { vtEsc } from '../core/api.js';
import { t } from '../core/i18n.js';
import { authzApi } from './api.js';
import { withElevation } from './elevate.js';
import { pending, onChange, setPending, removePending, refresh } from './store.js';
import {
  askSentence, kindLabel, rowDetail, detailLine, requester, lineageText, ageText, expiryText, choicesFor, errorText, resourceText,
  pairErrorText, PAIR_CLOSED_CODES,
} from './data.js';
import { pairBodyHTML, bindPair } from './pair-approval.js';

export const ARM_MS = 400;
const PANEL_ID = 'vt-authz-approval';

let snoozed = false;     // 「나중에」 — 새 요청이 오면 풀린다
let focusId = null;      // 목록에서 골라 연 요청
let el = null;           // 열린 모달 루트
let armedAt = 0;
let inflight = false;
let lastError = '';
let ticks = new Set();     // 페어: 사람이 직접 체크한 높은 권한 항목(요청마다 새로 — 미리 체크하지 않는다)
let ticksFor = null;
let internalClose = false;
let wired = false;

const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

function current() {
  const q = pending();
  return q.find((r) => r.id === focusId) || q[0] || null;
}

export function pendingRequests() { return pending(); }

/** 서버 목록으로 맞춘다(부팅·재연결 sync 용). */
export { setPending };

/** 설정의 「권한」·배지가 부른다. id 를 주면 그 요청부터 보인다. */
export function showPending(id) { snoozed = false; focusId = id || null; render(); }

/** 다른 화면에서 처리됐다. */
export function resolveRequest(id) { removePending(id); }

function snooze() {
  const n = pending().length;
  if (!n) return;
  snoozed = true;
  toast(t('승인 대기 {n}건 — 설정의 「권한」에서 다시 볼 수 있습니다', { n }), 'info');
}

function close() {
  if (!el) return;
  internalClose = true;
  closePanel(el);
  internalClose = false;
}

function choiceButtons(req) {
  const cs = choicesFor(req);
  const btn = (c) => `<button type="button" class="vt-btn lg vt-authz-act" data-act="${c.choice}"${c.available ? '' : ' disabled aria-disabled="true"'}>${vtEsc(c.label)}</button>`;
  const order = ['deny', 'once', 'session'];       // 거부가 맨 앞 — 기본 포커스도 여기
  const why = cs.filter((c) => !c.available).map((c) => `<p class="vt-authz-why">${vtEsc(c.label)}: ${vtEsc(c.why)}</p>`).join('');
  return `<div class="vt-dialog-actions vt-authz-acts">${order.map((k) => btn(cs.find((c) => c.choice === k))).join('')}</div>${why}`;
}

function factsHTML(req) {
  const lin = lineageText(req);
  const rid = req.resource && typeof req.resource === 'object' ? req.resource.id : '';
  const label = resourceText(req);
  return `<dl class="vt-authz-facts">
      <dt>${t('종류')}</dt><dd>${vtEsc(kindLabel(req.kind))}</dd>
      <dt>${t('요청한 세션')}</dt><dd>${vtEsc(requester(req))}${lin ? `<span class="vt-authz-sub">${vtEsc(lin)}</span>` : ''}</dd>
      <dt>${t('대상')}</dt><dd><span>${vtEsc(label)}</span>${rid && rid !== label ? `<span class="vt-authz-code">${vtEsc(rid)}</span>` : ''}</dd>
      <dt>${t('요청 범위')}</dt><dd><span class="vt-authz-code">${vtEsc(detailLine(req))}</span></dd>
      ${req.reason ? `<dt>${t('에이전트가 적은 사유')}</dt><dd class="vt-authz-reason">${vtEsc(req.reason)}</dd>` : ''}
      <dt>${t('시각')}</dt><dd>${vtEsc(ageText(req.age))} · ${vtEsc(expiryText(req.expiresIn))}</dd>
    </dl>`;
}

function draw() {
  const q = pending();
  const req = current();
  const body = el.querySelector('.vt-authz-approval-body');
  const elevated = Array.isArray(req.elevated) && req.elevated.length > 0;
  el.querySelector('.vt-authz-count').textContent = q.length > 1 ? `${q.findIndex((r) => r.id === req.id) + 1} / ${q.length}` : '';
  body.dataset.kind = req.kind;
  body.dataset.elevated = elevated ? '1' : '0';
  if (req.kind === 'pair') {
    if (ticksFor !== req.id) { ticks = new Set(); ticksFor = req.id; }
    body.innerHTML = pairBodyHTML(req, ticks, lastError);
    bindPair(body, req, ticks);
  } else {
  body.innerHTML = `
    ${elevated ? `<p class="vt-authz-elevated" role="note"><strong>${t('높은 권한이 필요한 요청')}</strong> ${vtEsc(t('비밀번호를 다시 확인한 뒤에만 허용됩니다.'))}<span class="vt-authz-code">${vtEsc(req.elevated.join(', '))}</span></p>` : ''}
    <p class="vt-authz-ask">${vtEsc(askSentence(req))}</p>
    ${factsHTML(req)}
    <div class="vt-authz-err" role="alert">${vtEsc(lastError)}</div>
    ${choiceButtons(req)}
    <p class="vt-authz-note">${t('허용한 권한은 설정의 「권한」에서 언제든 회수할 수 있습니다.')}</p>
    <div class="vt-authz-later"><button type="button" class="vt-btn quiet vt-authz-snooze">${t('나중에')}</button></div>`;
  }
  body.querySelectorAll('.vt-authz-act').forEach((b) => b.addEventListener('click', () => onAct(b.dataset.act)));
  body.querySelector('.vt-authz-snooze').addEventListener('click', () => { snooze(); close(); });
  armedAt = Date.now() + ARM_MS;
  const deny = body.querySelector('[data-act="deny"]');
  if (deny) deny.focus({ preventScroll: true });   // 기본 포커스는 안전한 쪽(거부) — 긴 페어 화면이 중간부터 보이지 않게 스크롤은 안 한다
  body.scrollTop = 0; if (body.parentElement) body.parentElement.scrollTop = 0;
}

async function onAct(choice) {
  if (inflight || Date.now() < armedAt) return;   // 오조작 방지(위 머리말 2번)
  const req = current();
  if (!req) return;
  const spec = choicesFor(req).find((c) => c.choice === choice);
  if (!spec || !spec.available) return;
  inflight = true;
  el.querySelectorAll('.vt-authz-act').forEach((b) => { b.disabled = true; });
  const isPair = req.kind === 'pair';
  const ticked = isPair ? [...ticks] : [];
  const call = () => {
    if (choice === 'deny') return authzApi.decide(req.id, 'deny');
    return isPair ? authzApi.decidePair(req.id, ticked) : authzApi.decide(req.id, 'allow', choice);
  };
  try {
    // 페어: 높은 권한을 체크했을 때만 재인증을 묻는다(서버가 elevation_required 로 알린 뒤 한 번). 체크가 없으면 묻지 않는다.
    const askPw = choice !== 'deny' && (!isPair || ticked.length > 0);
    const res = askPw ? await withElevation(call, t('높은 권한을 허용하려면 비밀번호를 다시 확인해야 합니다.')) : await call();
    if (res !== null) { lastError = ''; focusId = null; removePending(req.id); }
  } catch (e) {
    if (e && e.status === 404) {          // 이미 처리됨·만료·세션 종료 — 목록에서 뺀다
      lastError = ''; focusId = null; removePending(req.id);
    } else if (isPair) {
      // 서버가 거절한 이유를 그대로 보이고 창은 열어 둔다. 서버가 요청 자체를 닫은 경우(확인 문구 3번 불일치·세션 사라짐)와
      // 이미 다른 결정이 난 경우는 목록을 다시 맞춘다.
      lastError = pairErrorText(e);
      const code = e && (e.code || (e.data && e.data.error));
      if (PAIR_CLOSED_CODES.includes(code)) { toast(lastError, 'error'); focusId = null; removePending(req.id); lastError = ''; }
      else if (code === 'already_decided') void refresh();
    } else {
      lastError = errorText(e);
    }
  } finally {
    inflight = false;
  }
  render();
}

function render() {
  if (!pending().length) { if (el) close(); return; }
  if (snoozed) { if (el) close(); return; }
  if (!el) {
    const r = openPanel({
      id: PANEL_ID,
      ariaLabel: t('에이전트 접근 요청'),
      extraClass: 'vt-dialog vt-authz-approval',
      headHTML: '<div class="vt-vw-title">' + t('에이전트 접근 요청') + '</div><span class="vt-authz-count" aria-live="polite"></span>',
      bodyId: `${PANEL_ID}-body`,
      bodyHTML: '<div class="vt-authz-approval-body"></div>',
      onClose: () => {
        const dismissed = !internalClose;
        el = null;
        if (dismissed) snooze();     // Esc·배경·X = 결정이 아니라 「나중에」
      },
    });
    if (!r) return;
    el = r.el;
    const card = el.querySelector('.vt-viewer-card');
    if (card) card.setAttribute('role', 'alertdialog');
  }
  draw();
}

/** 저장소 변경을 모달에 잇는다(한 번만). 새 요청이 오면 「나중에」가 풀린다. */
export function wire() {
  if (wired) return;
  wired = true;
  onChange((ev) => {
    if (ev.type !== 'pending') return;
    if (ev.added.length) snoozed = false;
    if (focusId && !pending().some((r) => r.id === focusId)) focusId = null;
    render();
  });
}

/** 테스트·재시작용. */
export function _reset() { snoozed = false; focusId = null; el = null; inflight = false; lastError = ''; wired = false; }

// ── 설정 「권한」 안의 대기 목록 ─────────────────────────────────────────────

/** `host` 에 대기 요청 목록을 그리고 변경을 따라간다. host 가 떨어지면 구독을 풀고 아무것도 하지 않는다. */
export function mountInboxList(host) {
  const paint = () => {
    if (!host.isConnected) return;
    const q = pending();
    if (!q.length) {
      host.innerHTML = `<p class="vt-authz-empty">${t('승인 대기 중인 요청이 없습니다. 에이전트가 권한이 필요하면 여기와 알림으로 알려 드립니다.')}</p>`;
      return;
    }
    host.innerHTML = `<ul class="vt-authz-rows">${q.map((r) => {
      const el_ = Array.isArray(r.elevated) && r.elevated.length;
      return `<li class="vt-authz-row" data-id="${vtEsc(r.id)}" data-kind="${vtEsc(r.kind)}"${el_ ? ' data-elevated="1"' : ''}>
        <div class="vt-authz-row-main">
          <div class="vt-authz-row-top"><span class="vt-authz-kind">${vtEsc(kindLabel(r.kind))}</span>${el_ ? `<span class="vt-authz-flag">${t('높은 권한')}</span>` : ''}</div>
          <div class="vt-authz-row-ask">${vtEsc(askSentence(r))}</div>
          <div class="vt-authz-row-detail">${vtEsc(rowDetail(r))}</div>
          <div class="vt-authz-row-meta">${vtEsc(ageText(r.age))} · ${vtEsc(expiryText(r.expiresIn))}${lineageText(r) ? ` · ${vtEsc(lineageText(r))}` : ''}</div>
        </div>
        <button type="button" class="vt-btn lg vt-authz-open">${t('결정하기')}</button>
      </li>`;
    }).join('')}</ul>`;
  };
  host.addEventListener('click', (ev) => {
    const b = ev.target.closest('.vt-authz-open');
    if (!b) return;
    showPending(b.closest('.vt-authz-row').dataset.id);
  });
  paint();
  const off = onChange((ev) => {
    if (!host.isConnected) { off(); return; }
    if (ev.type === 'pending') paint();
  });
  return { repaint: paint, dispose: off };
}
