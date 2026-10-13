// 에이전트 접근 승인 — 모달 큐.
//
// 에이전트가 공유 세션을 처음 쓰면 서버가 대기 요청을 만들고 `/ws-workspace` 로
// `agent_share_request` 를 쏜다. 여기서 그 요청을 하나씩 모달로 보여 사람이 허용/거부한다.
//
// 오조작 방지 — 이 창은 사용자가 터미널에 치던 도중에 뜰 수 있다:
//  1. 기본 포커스는 「거부」다(안전한 쪽). Enter 한 번으로는 아무것도 허용되지 않는다.
//  2. 창이 뜬 뒤 ARM_MS 동안은 어떤 결정 버튼도 눌려도 무시한다 — 치던 Enter·Space 가 이어서 들어와도
//     결정이 되지 않는다.
//  3. Esc·배경·X 는 결정이 아니라 「나중에」다(요청은 서버에 24시간 남는다, 「에이전트 권한」에서 다시 열 수 있다).
import { openPanel, closePanel } from '../panels/panel.js';
import { vtEsc } from '../core/api.js';
import { shareApi } from './share-api.js';
import { requestSentence, kindHelp, APPROVAL_NOTE, errorText, MODE_LABEL } from './share-data.js';
import { t } from '../core/i18n.js';

export const ARM_MS = 400;
const PANEL_ID = 'vt-share-approval';

let queue = [];          // 대기 요청(created 오름차순)
let snoozed = false;     // 「나중에」 — 새 요청이 오면 풀린다
let el = null;           // 열린 모달 루트
let armedAt = 0;
let inflight = false;
let lastError = '';
let internalClose = false;

const byCreated = (a, b) => (a.created || 0) - (b.created || 0);
const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

export function pendingRequests() { return queue.slice(); }

/** 새 요청 하나(이벤트). 이미 있으면 무시한다. */
export function addRequest(req) {
  if (!req || !req.id || queue.some((r) => r.id === req.id)) return;
  queue.push(req); queue.sort(byCreated);
  snoozed = false;
  render();
}

/** 서버의 대기 목록으로 통째로 맞춘다(부팅·재연결). 이미 처리된 것은 빠지고 새 것이 있으면 다시 띄운다. */
export function setPending(list) {
  const next = (list || []).filter((r) => r && r.id && (!r.state || r.state === 'pending')).sort(byCreated);
  const known = new Set(queue.map((r) => r.id));
  if (next.some((r) => !known.has(r.id))) snoozed = false;
  queue = next;
  render();
}

/** 다른 화면·다른 경로에서 처리됐다(`agent_share_request_resolved`). */
export function resolveRequest(id) {
  const before = queue.length;
  queue = queue.filter((r) => r.id !== id);
  if (queue.length !== before) { lastError = ''; render(); }
}

/** 「에이전트 권한」 패널의 「승인 대기 N건」이 부른다. */
export function showPending() { snoozed = false; render(); }

function snooze() {
  if (!queue.length) return;
  snoozed = true;
  toast(t('승인 대기 {n}건 — 행 메뉴의 「에이전트 권한…」에서 다시 볼 수 있습니다', { n: queue.length }), 'info');
}

function close() {
  if (!el) return;
  internalClose = true;
  closePanel(el);
  internalClose = false;
}

function levelButtons(req) {
  const btn = (act, label, extra = '') =>
    `<button type="button" class="vt-btn lg vt-share-act${extra}" data-act="${act}">${label}</button>`;
  return req.kind === 'write'
    ? btn('deny', t('거부')) + btn('read', t('읽기만 허용')) + btn('readwrite', t('쓰기까지 허용'))
    : btn('deny', t('거부')) + btn('read', t('허용'));
}

function draw() {
  const req = queue[0];
  const body = el.querySelector('.vt-share-approval-body');
  const nick = (req.target && req.target.nick) || '?';
  const mode = req.target && req.target.mode;
  el.querySelector('.vt-share-count').textContent = queue.length > 1 ? `1 / ${queue.length}` : '';
  body.innerHTML = `
    <p class="vt-share-ask">${vtEsc(requestSentence(req))}</p>
    <p class="vt-share-help">${vtEsc(kindHelp(req.kind))}</p>
    <dl class="vt-share-facts">
      <dt>${t('요청한 세션')}</dt><dd>${vtEsc((req.agent && req.agent.name) || '?')}</dd>
      <dt>${t('대상')}</dt><dd>${vtEsc(nick)}${req.target && req.target.name && req.target.name !== nick ? ` (${vtEsc(req.target.name)})` : ''}${mode ? ` · ${vtEsc(MODE_LABEL[mode] || mode)}` : ''}</dd>
    </dl>
    <p class="vt-share-note">${vtEsc(APPROVAL_NOTE)}</p>
    <div class="vt-share-err" role="alert">${vtEsc(lastError)}</div>
    <div class="vt-dialog-actions vt-share-acts">${levelButtons(req)}</div>
    <div class="vt-share-later"><button type="button" class="vt-btn quiet vt-share-snooze">${t('나중에')}</button></div>`;
  body.querySelectorAll('.vt-share-act').forEach((b) => b.addEventListener('click', () => onAct(b.dataset.act)));
  body.querySelector('.vt-share-snooze').addEventListener('click', () => { snooze(); close(); });
  armedAt = Date.now() + ARM_MS;
  // 기본 포커스는 안전한 쪽(거부).
  const deny = body.querySelector('[data-act="deny"]');
  if (deny) deny.focus();
}

async function onAct(act) {
  if (inflight || Date.now() < armedAt) return;   // 오조작 방지(위 머리말 2번)
  const req = queue[0];
  if (!req) return;
  inflight = true;
  el.querySelectorAll('.vt-share-act').forEach((b) => { b.disabled = true; });
  try {
    await shareApi.decide(req.id, act === 'deny' ? 'deny' : 'allow', act === 'deny' ? undefined : act);
    lastError = '';
    queue = queue.filter((r) => r.id !== req.id);
  } catch (e) {
    if (e && e.status === 404) {          // 이미 처리됨·만료·세션 종료 — 목록에서 뺀다
      lastError = '';
      queue = queue.filter((r) => r.id !== req.id);
    } else {
      lastError = errorText(e);
    }
  } finally {
    inflight = false;
  }
  render();
}

function render() {
  if (!queue.length) { if (el) close(); return; }
  if (snoozed) { if (el) close(); return; }
  if (!el) {
    const r = openPanel({
      id: PANEL_ID,
      ariaLabel: t('에이전트 접근 요청'),
      extraClass: 'vt-dialog vt-share-approval',
      headHTML: '<div class="vt-vw-title">' + t('에이전트 접근 요청') + '</div><span class="vt-share-count" aria-live="polite"></span>',
      bodyId: `${PANEL_ID}-body`,
      bodyHTML: '<div class="vt-share-approval-body"></div>',
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

/** 테스트·재시작용. */
export function _reset() { queue = []; snoozed = false; el = null; inflight = false; lastError = ''; }
