// 감사 타임라인 — 최근 순. 서버가 해시 체인을 검증한 결과(chain)를 배지로 보이고, 내용(입력·화면·메시지)은 어디에도 그리지 않는다.
// 항목 필드는 data.auditView 의 허용 목록만 쓴다.
import { vtEsc } from '../core/api.js';
import { t } from '../core/i18n.js';
import { authzApi } from './api.js';
import { auditView, chainBadge, RANGES, sinceFor, stamp, errorText } from './data.js';

export const PAGE = 50;
export const FETCH_LIMIT = 500;
export const FILTER_DEBOUNCE_MS = 250;

function rowHTML(e) {
  const v = auditView(e);
  const tail = [v.scopes.join(', '), v.reason].filter(Boolean).join(' · ');
  return `<li class="vt-authz-audit-row" data-source="${v.source}">
    <div class="vt-authz-audit-top"><span class="vt-authz-audit-ts">${vtEsc(stamp(v.ts))}</span>
      <span class="vt-authz-audit-act">${vtEsc(v.action)}</span>
      ${v.outcome ? `<span class="vt-authz-audit-out">${vtEsc(v.outcome)}</span>` : ''}
      ${v.source === 'denials' ? `<span class="vt-authz-flag">${t('거부 기록')}</span>` : ''}</div>
    <div class="vt-authz-audit-who">${vtEsc(v.principal)}${v.resource ? ` <span class="vt-authz-sub">→</span> ${vtEsc(v.resource)}` : ''}</div>
    ${tail ? `<div class="vt-authz-row-detail">${vtEsc(tail)}</div>` : ''}
  </li>`;
}

export function mountAudit(host) {
  host.innerHTML = `
    <div class="vt-authz-chain" data-state="unknown" role="status"></div>
    <div class="vt-authz-filters" role="search">
      <input type="search" class="vt-authz-in" data-f="principal" placeholder="${vtEsc(t('주체(앞부분)'))}" aria-label="${vtEsc(t('주체로 거르기'))}" autocomplete="off">
      <input type="search" class="vt-authz-in" data-f="resource" placeholder="${vtEsc(t('리소스(앞부분)'))}" aria-label="${vtEsc(t('리소스로 거르기'))}" autocomplete="off">
      <select class="vt-authz-in" data-f="range" aria-label="${vtEsc(t('기간'))}">${RANGES.map((r) => `<option value="${r.id}">${vtEsc(r.label)}</option>`).join('')}</select>
      <label class="vt-authz-check"><input type="checkbox" data-f="denials" checked><span>${t('거부 기록 포함')}</span></label>
    </div>
    <div class="vt-authz-err" role="alert"></div>
    <div class="vt-authz-list" aria-live="polite"></div>
    <p class="vt-authz-note">${t('기록에는 글자 수와 해시 앞 8자리만 남고 입력한 글·화면·메시지 자체는 남지 않습니다.')}</p>`;
  const list = host.querySelector('.vt-authz-list');
  const errEl = host.querySelector('.vt-authz-err');
  const chainEl = host.querySelector('.vt-authz-chain');
  const f = { principal: '', resource: '', range: 'all', denials: true };
  let entries = [];
  let shown = PAGE;
  let seq = 0;
  let timer = null;

  function paintChain(res) {
    const b = chainBadge(res);
    chainEl.dataset.state = b.state;
    chainEl.textContent = b.text;
    if (b.state === 'broken') chainEl.setAttribute('role', 'alert'); else chainEl.setAttribute('role', 'status');
  }

  function paintList() {
    const rows = f.denials ? entries : entries.filter((e) => e.source !== 'denials');
    if (!rows.length) {
      list.innerHTML = `<p class="vt-authz-empty">${t('조건에 맞는 기록이 없습니다.')}</p>`;
      return;
    }
    const slice = rows.slice(0, shown);
    const more = rows.length - slice.length;
    list.innerHTML = `<ol class="vt-authz-audit">${slice.map(rowHTML).join('')}</ol>
      ${more > 0 ? `<button type="button" class="vt-btn lg vt-authz-more">${t('더 보기 ({n}건 남음)', { n: more })}</button>` : ''}
      ${entries.length >= FETCH_LIMIT && more <= 0 ? `<p class="vt-authz-note">${t('가장 최근 {n}건까지 보입니다. 더 오래된 기록은 거르는 기간을 좁혀 보세요.', { n: FETCH_LIMIT })}</p>` : ''}`;
  }

  async function load() {
    const mine = ++seq;
    shown = PAGE;
    list.innerHTML = `<p class="vt-authz-empty">${t('불러오는 중…')}</p>`;
    try {
      const res = await authzApi.audit({ principal: f.principal, resource: f.resource, since: sinceFor(f.range), limit: FETCH_LIMIT });
      if (mine !== seq || !host.isConnected) return;
      entries = (res && res.entries) || [];
      errEl.textContent = '';
      paintChain(res);
      paintList();
    } catch (e) {
      if (mine !== seq || !host.isConnected) return;
      entries = [];
      paintChain(null);
      list.innerHTML = `<p class="vt-authz-empty">${t('감사 기록을 불러오지 못했습니다.')}</p><button type="button" class="vt-btn lg vt-authz-retry">${t('다시 시도')}</button>`;
      errEl.textContent = errorText(e);
    }
  }

  host.addEventListener('input', (ev) => {
    const k = ev.target.dataset && ev.target.dataset.f;
    if (k === 'principal' || k === 'resource') {
      f[k] = ev.target.value.trim();
      clearTimeout(timer);
      timer = setTimeout(load, FILTER_DEBOUNCE_MS);
    }
  });
  host.addEventListener('change', (ev) => {
    const k = ev.target.dataset && ev.target.dataset.f;
    if (k === 'range') { f.range = ev.target.value; void load(); }
    if (k === 'denials') { f.denials = ev.target.checked; shown = PAGE; paintList(); }
  });
  host.addEventListener('click', (ev) => {
    if (ev.target.closest('.vt-authz-retry')) { void load(); return; }
    if (ev.target.closest('.vt-authz-more')) { shown += PAGE; paintList(); }
  });
  void load();
  return { reload: load, dispose: () => { clearTimeout(timer); seq++; } };
}
