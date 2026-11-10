// 계정 선택기 메뉴와 기본 계정 변경 — 상태 막대 칩·설정 「계정」·「+ 새 세션」 메뉴의 「이번만 다른 계정」이 쓴다.
// `shell` 청크(vite.config.mjs)라 초기 페이로드에 안 실린다: term/session.js·palette-lazy.js는 import()로만 부른다.
// 목록 캐시와 생성 요청에 싣는 값은 accounts.js(core)에 있다.
import { apiFetch } from '../core/api.js';
// API_BASE는 부를 때마다 window에서 읽는다(core/api.js와 같은 규칙). core/env.js를 import하면 그 순간 window.API_BASE를
// 다시 써서, 이 모듈을 불러오는 것만으로 다른 화면(설정 › 업데이트 시험)의 요청 주소가 바뀌었다.
const apiBase = () => window.API_BASE || '';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { showToast } from '../ui/toast.js';
import { loadAccounts, forgetAccounts } from './accounts.js';
import { TOOL_LABEL } from './accounts-data.js';
import { chooserEntries } from './accounts-text.js';

/** 기본 계정이 바뀌면 window에 이 이벤트를 쏜다 — 상태 막대 칩·설정 「계정」이 다시 읽는다. */
export const ACCOUNTS_CHANGED = 'vt:accounts-changed';

/** 기본 계정 변경(도구별). profileId null이면 해제. */
export async function setDefaultAccount(tool, profileId, name) {
  const res = await apiFetch(`${apiBase()}/api/agent-profiles/default`, {
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
  window.dispatchEvent(new CustomEvent(ACCOUNTS_CHANGED));
  showToast(profileId
    ? t('{tool} 기본 계정: {name}. 이후 새 세션부터 적용됩니다', { tool: TOOL_LABEL[tool], name })
    : t('{tool} 기본 계정을 해제했습니다', { tool: TOOL_LABEL[tool] }), 'info');
  return true;
}

const MENU_ID = 'account-menu';

/**
 * 계정 선택 메뉴. mode 'once' → onPick(profile id | 'none'), 'default' → 기본 계정으로 지정·해제.
 * anchor가 없으면 화면 위쪽 가운데에 연다. placement 'above'는 앵커 위로 펼친다(화면 맨 아래 상태 막대의 칩).
 * @param {{ mode: 'once' | 'default', anchor?: HTMLElement | null, onPick?: ((id: string) => void) | null, placement?: 'above' | 'below' }} opts
 */
export async function openAccountMenu({ mode, anchor = null, onPick = null, placement = 'below' }) {
  const open = document.getElementById(MENU_ID);
  open?.remove();
  document.getElementById('add-menu')?.remove();
  // 같은 칩을 다시 누르면 닫기만 한다(토글).
  if (open && anchor && open._anchor === anchor) return;
  const acc = await loadAccounts({ force: true });
  if (!acc.ok || !acc.profiles.length) { showToast(t('등록된 계정이 없습니다'), 'info'); return; }

  let sort = 'server';
  const menu = document.createElement('div');
  menu.id = MENU_ID;
  menu.className = 'vt-menu';
  menu._anchor = anchor;
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', mode === 'once' ? t('이번 세션만 쓸 계정') : t('기본 계정으로 지정'));
  const close = () => { menu.remove(); document.removeEventListener('keydown', onKey, true); };
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); anchor?.focus?.(); } };

  const render = () => {
    menu.textContent = '';
    const head = document.createElement('div');
    head.className = 'vt-menu-label';
    head.textContent = mode === 'once' ? t('이번 세션만 쓸 계정') : t('기본 계정으로 지정');
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
    menu.style.top = `${Math.round(r ? (placement === 'above' ? r.top - menu.offsetHeight - 6 : r.bottom + 6) : window.innerHeight * 0.18)}px`;
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) menu.style.left = `${Math.max(8, Math.round(window.innerWidth - 8 - rect.width))}px`;
    if (rect.bottom > floor) menu.style.top = `${Math.max(8, Math.round(floor - rect.height))}px`;
    if (rect.top < 8) menu.style.top = '8px';
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
