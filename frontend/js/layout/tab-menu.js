// 뷰 탭 우클릭 메뉴 — layout/tabbar.js가 contextmenu 때 지연 import한다(초기 페이로드 상한: vite.config.js의
// 'screens' 청크). 이름 변경 · 뷰 닫기 둘뿐이다. 묶음 해제는 일부러 없다 — 칸 ×가 그 일을 한다
// (docs/ref/terminal.md 「Session and view rules」: "ungroup 동작은 없다").
import { tabDisplayLabel } from './store.js';
import { closeView, renameView } from '../term/session-actions.js';
import { promptDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';

let _menuEl = null;
function closeTabMenu() {
  if (!_menuEl) return;
  _menuEl.remove();
  _menuEl = null;
  document.removeEventListener('pointerdown', _menuOutside, true);
  document.removeEventListener('keydown', _menuKey, true);
  window.removeEventListener('blur', closeTabMenu);
}
function _menuOutside(e) { if (_menuEl && !_menuEl.contains(e.target)) closeTabMenu(); }
function _menuKey(e) {
  if (!_menuEl) return;
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeTabMenu(); return; }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  e.preventDefault();
  const items = [..._menuEl.querySelectorAll('button')];
  const i = items.indexOf(document.activeElement);
  items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
}

async function renameTabViaDialog(tab) {
  const next = await promptDialog(t('뷰 이름'), { title: t('뷰 이름 변경'), defaultValue: tabDisplayLabel(tab) });
  if (next === null || !next.trim()) return;
  if (!renameView(tab.id, next.trim())) window.showToast?.(t('뷰 이름을 저장하지 못했습니다. 이름은 128자까지 입력할 수 있습니다.'));
}

export function openTabMenu(tab, x, y) {
  closeTabMenu();
  const menu = document.createElement('div');
  menu.className = 'vt-menu vt-wtab-menu';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', tabDisplayLabel(tab));
  const add = (label, run, danger = false) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'menuitem');
    b.className = 'vt-menu-item' + (danger ? ' danger' : '');
    b.textContent = label;
    b.addEventListener('click', () => { closeTabMenu(); run(); });
    menu.appendChild(b);
  };
  add(t('이름 변경'), () => { void renameTabViaDialog(tab); });
  add(t('뷰 닫기'), () => { closeView(tab.id); }, true);
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - r.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - r.height - 8))}px`;
  _menuEl = menu;
  document.addEventListener('pointerdown', _menuOutside, true);
  document.addEventListener('keydown', _menuKey, true);
  window.addEventListener('blur', closeTabMenu);
  menu.querySelector('button')?.focus();
}
