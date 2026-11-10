// 새 세션 계정 선택(핫스왑 H1) — 선택기 목록을 가져오고 메뉴로 보여 준다.
//
// 이 모듈은 세션을 만들지 않는다(term/session.js가 만든다). 여기는 (1) 서버 목록 캐시,
// (2) 「이번만 다른 계정」·「기본 계정 바꾸기」 메뉴, (3) 기본 계정 변경 요청만 맡는다.
// 다른 맥(relay)을 보고 있으면 계정 기능 전체를 숨긴다 — 계정은 이 맥의 것이고 원격에서 고르지 못한다.
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { isRelayed } from '../core/host-route.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { showToast } from '../ui/toast.js';
import { EMPTY_ACCOUNTS, normalizeAccounts, chooserEntries, defaultLine, hasMarkedDefault, TOOL_LABEL } from './accounts-data.js';

const TTL_MS = 15000;
let _cache = { at: 0, value: EMPTY_ACCOUNTS };
let _inflight = null;

/** 마지막으로 받은 목록(동기). 아직 없으면 빈 목록. */
export function accountsSnapshot() {
  return isRelayed() ? EMPTY_ACCOUNTS : _cache.value;
}

/** 서버 선택기 목록. 15초 캐시, 실패(사람 세션 아님·옛 서버 등)는 「계정 없음」으로 둔다. */
export async function loadAccounts({ force = false } = {}) {
  if (isRelayed()) return EMPTY_ACCOUNTS;
  if (!force && Date.now() - _cache.at < TTL_MS) return _cache.value;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    try {
      const res = await apiFetch(`${API_BASE}/api/agent-profiles/read/picker`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      _cache = { at: Date.now(), value: res.ok ? normalizeAccounts(await res.json()) : EMPTY_ACCOUNTS };
    } catch (_) {
      _cache = { at: Date.now(), value: EMPTY_ACCOUNTS };
    }
    return _cache.value;
  })().finally(() => { _inflight = null; });
  return _inflight;
}

export function forgetAccounts() { _cache = { at: 0, value: _cache.value }; }

/** 이번 생성 요청에 실을 `profile` 값. 고른 것이 없고 기본 계정도 없으면 undefined(= 지금 동작 그대로). */
export async function profileForCreate(explicit) {
  if (typeof explicit === 'string' && explicit) return explicit;
  if (isRelayed()) return undefined;
  const acc = await loadAccounts();
  return acc.ok && hasMarkedDefault(acc) ? 'default' : undefined;
}

/** 기본 계정 변경(도구별). profileId null이면 해제. */
export async function setDefaultAccount(tool, profileId, name) {
  const res = await apiFetch(`${API_BASE}/api/agent-profiles/default`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tool, profile_id: profileId }),
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* 본문 없음 */ }
  if (!res.ok) {
    showToast(t('기본 계정을 바꾸지 못했습니다: {reason}', { reason: data?.reason || res.status }), 'error');
    return false;
  }
  forgetAccounts();
  await loadAccounts({ force: true });
  showToast(profileId
    ? t('{tool} 기본 계정: {name}. 이후 새 세션부터 적용됩니다', { tool: TOOL_LABEL[tool], name })
    : t('{tool} 기본 계정을 해제했습니다', { tool: TOOL_LABEL[tool] }), 'info');
  return true;
}

/** 서버가 계정 때문에 세션 생성을 거절했을 때의 토스트 문구. 기본 계정으로 대체하지 않았다는 것을 밝힌다. */
export function refusalText(data) {
  const reason = data?.reason || data?.error || '';
  return t('계정 문제로 세션을 만들지 않았습니다: {reason}', { reason });
}

const MENU_ID = 'account-menu';

/**
 * 계정 선택 메뉴. mode 'once' → onPick(profile id | 'none'), 'default' → 기본 계정 변경.
 * anchor가 없으면(팔레트) 화면 위쪽 가운데에 연다.
 */
export async function openAccountMenu({ mode, anchor = null, onPick = null }) {
  document.getElementById(MENU_ID)?.remove();
  document.getElementById('add-menu')?.remove();
  const acc = await loadAccounts({ force: true });
  if (!acc.ok || !acc.profiles.length) { showToast(t('등록된 계정이 없습니다'), 'info'); return; }

  let sort = 'server';
  const menu = document.createElement('div');
  menu.id = MENU_ID;
  menu.className = 'vt-menu';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', mode === 'once' ? t('이번 세션만 쓸 계정') : t('기본 계정 바꾸기'));
  const close = () => { menu.remove(); document.removeEventListener('keydown', onKey, true); };
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); anchor?.focus?.(); } };

  const render = () => {
    menu.textContent = '';
    const head = document.createElement('div');
    head.className = 'vt-menu-label';
    head.textContent = mode === 'once' ? t('이번 세션만 쓸 계정') : t('기본 계정 바꾸기');
    menu.appendChild(head);

    const sortBtn = document.createElement('div');
    sortBtn.className = 'vt-menu-item sm';
    sortBtn.setAttribute('role', 'menuitemcheckbox');
    sortBtn.setAttribute('aria-checked', String(sort === 'headroom'));
    sortBtn.tabIndex = 0;
    const sortLabel = document.createElement('span');
    sortLabel.className = 'vt-menu-item-label';
    sortLabel.textContent = t('여유 많은 순');
    const sortState = document.createElement('span');
    sortState.className = 'vt-menu-item-detail';
    sortState.textContent = sort === 'headroom' ? t('켜짐') : t('꺼짐');
    sortBtn.append(sortLabel, sortState);
    const toggle = () => { sort = sort === 'headroom' ? 'server' : 'headroom'; render(); menu.querySelector('.vt-menu-item')?.focus(); };
    sortBtn.onclick = toggle;
    sortBtn.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } };
    menu.appendChild(sortBtn);
    const sep = document.createElement('div');
    sep.className = 'vt-menu-sep';
    menu.appendChild(sep);

    for (const entry of chooserEntries(acc, { mode, sort })) {
      const it = document.createElement('div');
      it.className = 'vt-menu-item two';
      it.setAttribute('role', 'menuitem');
      if (entry.disabled) { it.setAttribute('aria-disabled', 'true'); it.classList.add('is-disabled'); }
      it.tabIndex = entry.disabled ? -1 : 0;
      if (entry.tip) it.setAttribute('data-tip', entry.tip);
      const ico = document.createElement('span');
      ico.className = 'vt-menu-item-ico';
      ico.innerHTML = icon(entry.kind === 'profile' ? 'user' : 'x', 14, 2);
      const body = document.createElement('span');
      body.className = 'vt-menu-item-body';
      const name = document.createElement('span');
      name.className = 'vt-menu-item-label';
      name.textContent = entry.name;
      const sub = document.createElement('span');
      sub.className = 'vt-menu-item-sub';
      sub.textContent = entry.sub;
      body.append(name, sub);
      it.append(ico, body);
      if (entry.isDefault) {
        const mark = document.createElement('span');
        mark.className = 'vt-menu-item-detail';
        mark.textContent = t('기본');
        it.appendChild(mark);
      }
      const run = async () => {
        if (entry.disabled) return;
        close();
        if (mode === 'once') { onPick?.(entry.id); return; }
        if (entry.kind === 'clear') await setDefaultAccount(entry.tool, null, '');
        else await setDefaultAccount(entry.tool, entry.id, entry.name);
      };
      it.onclick = run;
      it.onkeydown = (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); run(); }
        else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          const all = [...menu.querySelectorAll('.vt-menu-item:not([aria-disabled="true"])')];
          all[(all.indexOf(it) + (e.key === 'ArrowDown' ? 1 : -1) + all.length) % all.length]?.focus();
        }
      };
      menu.appendChild(it);
    }
  };
  render();

  document.body.appendChild(menu);
  const place = () => {
    const r = anchor ? anchor.getBoundingClientRect() : null;
    // 폰의 하단 내비가 화면 아래를 덮는다 — 그 위까지만 쓴다.
    const nav = document.getElementById('vt-mnav-bottom');
    const reserve = nav && nav.getClientRects().length ? nav.getBoundingClientRect().height : 0;
    const floor = window.innerHeight - reserve - 8;
    menu.style.right = 'auto';
    menu.style.maxWidth = `${Math.max(240, window.innerWidth - 16)}px`;
    menu.style.maxHeight = `${Math.max(200, floor - 8)}px`;
    menu.style.overflowY = 'auto';
    menu.style.left = `${Math.round(r ? r.left : Math.max(8, (window.innerWidth - menu.offsetWidth) / 2))}px`;
    menu.style.top = `${Math.round(r ? r.bottom + 6 : window.innerHeight * 0.18)}px`;
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) menu.style.left = `${Math.max(8, Math.round(window.innerWidth - 8 - rect.width))}px`;
    if (rect.bottom > floor) menu.style.top = `${Math.max(8, Math.round(floor - rect.height))}px`;
  };
  place();
  document.addEventListener('keydown', onKey, true);
  menu.querySelector('.vt-menu-item[role="menuitem"]:not([aria-disabled="true"])')?.focus();
  setTimeout(() => {
    document.addEventListener('click', function away(ev) {
      if (!document.body.contains(menu)) { document.removeEventListener('click', away); return; }
      // 정렬 토글처럼 메뉴가 줄을 다시 그리면 눌린 요소는 이미 떨어져 있다 — 눌린 경로(composedPath)로 안쪽인지 본다.
      if (!menu.contains(ev.target) && !ev.composedPath().includes(menu)) { close(); document.removeEventListener('click', away); }
    });
  }, 0);
}

/** 「+」 메뉴에 붙일 계정 줄의 글자(기본 계정 요약). */
export function menuDefaultLine(acc) { return defaultLine(acc); }
