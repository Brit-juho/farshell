// 설정 「브라우저」 — 에이전트용 격리 브라우저 어댑터 켜기/끄기와 탭 소유 목록(소유자·상태·닫기).
// 서버의 브라우저 기능(VT_AUTHZ_ENFORCE 의 browser)이 꺼져 있으면 라우트가 404 라서 오류가 아니라 한 줄 안내다.
// 어댑터를 처음 켤 때는 동의 문구를 보여 주고 확인을 받는다. 사용자 Chrome 프로필에는 연결하지 않는다 — 격리된 탭만 있다.
import { vtEsc } from '../core/api.js';
import { confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { authzApi } from './api.js';
import { errorText } from './data.js';

const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

const REASONS = {
  not_implemented: () => t('아직 구현되지 않았습니다'),
  playwright_not_installed: () => t('Playwright 가 설치돼 있지 않습니다'),
  browser_binary_not_found: () => t('브라우저 실행 파일을 찾지 못했습니다'),
  profile_user_unsupported: () => t('사용자 프로필 연결은 지원하지 않습니다'),
  websockets_not_installed: () => t('websockets 가 설치돼 있지 않습니다'),
};
const reasonText = (r) => (REASONS[r] ? REASONS[r]() : String(r || ''));
const STATES = { open: () => t('열림'), orphaned: () => t('끊김(어댑터 내려감)'), released: () => t('소유권 풀림') };

export const isOff = (e) => !!e && e.status === 404 && !(e.data && e.data.error);

function adapterRow(a) {
  const state = a.enabled ? t('켜짐') : (a.available ? t('꺼짐') : t('사용 불가'));
  const why = !a.available ? `<div class="vt-authz-row-meta">${vtEsc(reasonText(a.reason))}</div>` : '';
  const btn = a.available
    ? `<button type="button" class="vt-btn lg vt-br-toggle" data-on="${a.enabled ? '1' : ''}">${a.enabled ? t('끄기') : t('켜기')}</button>` : '';
  return `<li class="vt-authz-row vt-br-adapter" data-adapter="${vtEsc(a.name)}">
    <div class="vt-authz-row-main">
      <div class="vt-authz-row-top"><span class="vt-authz-kind">${vtEsc(a.name)}</span><span class="vt-authz-preset">${vtEsc(state)}</span></div>
      <div class="vt-authz-row-meta">${t('프로필')} · ${vtEsc(a.profile === 'user' ? t('사용자') : t('격리'))}</div>${why}
    </div>${btn ? `<div class="vt-authz-row-acts">${btn}</div>` : ''}
  </li>`;
}

function tabRow(x) {
  const owner = x.humanTaken ? t('사람이 이어받음') : (x.ownerName || x.owner || t('소유자 없음'));
  const left = x.lastUsed ? Math.max(0, Math.round(Date.now() / 1000 - x.lastUsed)) : 0;
  return `<li class="vt-authz-row vt-br-tab" data-tab="${vtEsc(x.id)}">
    <div class="vt-authz-row-main">
      <div class="vt-authz-row-top"><span class="vt-authz-kind">${vtEsc(x.adapter)}</span><span class="vt-authz-preset">${vtEsc(STATES[x.state] ? STATES[x.state]() : x.state)}</span></div>
      <div class="vt-authz-row-ask"><span class="vt-authz-who">${vtEsc(owner)}</span><span class="vt-authz-sub">${t('탭')}</span><span class="vt-authz-code">${vtEsc(x.id)}</span></div>
      <div class="vt-authz-row-meta">${vtEsc(t('{n}초 전 사용', { n: left }))}${x.pair ? ` · ${vtEsc(t('페어 {id}', { id: x.pair }))}` : ''}</div>
    </div>
    <div class="vt-authz-row-acts"><button type="button" class="vt-btn lg danger vt-br-close">${t('닫기')}</button></div>
  </li>`;
}

export function mountBrowser(host) {
  host.innerHTML = `<p class="vt-authz-note">${vtEsc(t('에이전트가 쓰는 격리된 브라우저입니다. 당신의 Chrome 프로필과 로그인에는 연결하지 않고, 기본으로는 FarShell 이 노출한 localhost 포트만 엽니다.'))}</p>
    <div class="vt-authz-err" role="alert"></div>
    <div class="vt-br-adapters" aria-live="polite"></div>
    <div class="vt-br-tabs" aria-live="polite"></div>`;
  const aEl = host.querySelector('.vt-br-adapters');
  const tEl = host.querySelector('.vt-br-tabs');
  const errEl = host.querySelector('.vt-authz-err');
  let seq = 0;
  let busy = false;
  let keepErr = '';          // 동작이 실패했을 때 다시 읽은 뒤에도 이유를 남긴다

  async function load() {
    const mine = ++seq;
    try {
      const ad = await authzApi.browserAdapters();
      const tb = await authzApi.browserTabs();
      if (mine !== seq || !host.isConnected) return;
      errEl.textContent = keepErr; keepErr = '';
      host.dataset.consented = ad.consented ? '1' : '';
      host._consentText = ad.consentText || '';
      aEl.innerHTML = `<ul class="vt-authz-rows">${(ad.adapters || []).map(adapterRow).join('')}</ul>`;
      const rows = tb.tabs || [];
      tEl.innerHTML = rows.length
        ? `<h4 class="vt-authz-sub">${t('열린 탭')}</h4><ul class="vt-authz-rows">${rows.map(tabRow).join('')}</ul>`
        : `<p class="vt-authz-empty">${t('열린 브라우저 탭이 없습니다.')}</p>`;
    } catch (e) {
      if (mine !== seq || !host.isConnected) return;
      if (isOff(e)) {
        aEl.innerHTML = `<p class="vt-authz-empty" data-disabled="1">${vtEsc(t('브라우저 기능이 꺼져 있습니다. 서버 설정 {env} 에 {tok} 를 더하면 켜집니다.', { env: 'VT_AUTHZ_ENFORCE', tok: 'browser' }))}</p>`;
        tEl.innerHTML = '';
        errEl.textContent = '';
        return;
      }
      aEl.innerHTML = `<p class="vt-authz-empty">${t('브라우저 상태를 불러오지 못했습니다.')}</p><button type="button" class="vt-btn lg vt-br-retry">${t('다시 시도')}</button>`;
      tEl.innerHTML = '';
      errEl.textContent = errorText(e);
    }
  }

  host.addEventListener('click', async (ev) => {
    if (ev.target.closest('.vt-br-retry')) { void load(); return; }
    if (busy) return;
    const tog = ev.target.closest('.vt-br-toggle');
    const cls = ev.target.closest('.vt-br-close');
    if (!tog && !cls) return;
    busy = true;
    try {
      if (tog) {
        const name = tog.closest('.vt-br-adapter').dataset.adapter;
        const turnOn = !tog.dataset.on;
        let consent = false;
        if (turnOn && !host.dataset.consented) {
          if (!(await confirmDialog(host._consentText || t('브라우저 기능을 처음 켭니다. 계속할까요?'), { title: t('브라우저 켜기'), okLabel: t('동의하고 켜기') }))) return;
          consent = true;
        }
        await authzApi.browserEnable(name, turnOn, consent);
        toast(turnOn ? t('어댑터를 켰습니다') : t('어댑터를 껐습니다'), 'success');
      } else {
        const row = cls.closest('.vt-br-tab');
        if (!(await confirmDialog(t('이 탭을 닫습니다. 에이전트가 하던 일은 중단됩니다. 계속할까요?'), { title: t('탭 닫기'), okLabel: t('닫기'), danger: true }))) return;
        await authzApi.browserClose(row.dataset.tab);
        toast(t('탭을 닫았습니다'), 'success');
      }
      errEl.textContent = '';
    } catch (e) {
      keepErr = errorText(e);
      errEl.textContent = keepErr;
    } finally {
      busy = false;
    }
    void load();
  });

  void load();
  return { reload: load, dispose: () => { seq++; } };
}
