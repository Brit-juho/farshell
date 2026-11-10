// FarShell 드롭다운 — 브라우저 기본 <select> 팝업 대신 `.vt-menu` 모양의 목록을 쓴다.
//
// 네이티브 <select>는 **지우지 않고 값·이벤트의 정본으로 남긴다**(숨김). 그래서 기존 코드가 읽는
// `sel.value`, 듣는 `change`, 바꾸는 `sel.innerHTML`·`sel.disabled`가 전부 그대로 동작하고,
// 템플릿 문자열·Solid 컴포넌트·document.createElement로 만든 select도 한 번에 바뀐다.
// 보이는 것은 형제 `<button class="vt-dd">`와 body에 붙는 목록(`.vt-dd-list`)뿐이다.
//
// 목록을 body에 붙이는 이유: 레일(`.vt-sidepanel`)처럼 쌓임 맥락이나 overflow를 가진 조상 안에서는
// 팝업이 잘리거나 다른 오버레이에 가려진다(Rail.tsx의 Portal 주석과 같은 이유).
// 끄는 법: `<select data-native>` — 기본 select를 그대로 쓴다(multiple·size>1은 자동 제외).
import { icon } from './icons.js';

const enhanced = new Map();   // select -> { button, destroy }
let current = null;           // 지금 열려 있는 목록 { select, close }
let booted = false;

const optionsOf = (sel) => Array.from(sel.querySelectorAll('option'));

function labelOf(sel) {
  const opt = sel.options[sel.selectedIndex];
  return opt ? opt.textContent.trim() : '';
}

export function closeSelect() { current?.close(); }

export function enhanceSelect(sel) {
  if (enhanced.has(sel) || sel.multiple || sel.size > 1 || sel.hasAttribute('data-native')) return null;
  if (typeof sel.after !== 'function') return null;

  const button = document.createElement('button');
  button.type = 'button';
  button.setAttribute('role', 'combobox');
  button.setAttribute('aria-haspopup', 'listbox');
  button.setAttribute('aria-expanded', 'false');
  const text = document.createElement('span');
  text.className = 'vt-dd-text';
  const caret = document.createElement('span');
  caret.className = 'vt-dd-caret';
  caret.setAttribute('aria-hidden', 'true');
  caret.innerHTML = icon('chevron-down', 12, 2);
  button.append(text, caret);

  const sync = () => {
    // 기본 select의 클래스(vt-input·vt-select·vt-authz-in…)가 크기·테두리를 정한다 — 버튼이 그대로 물려받는다.
    button.className = ('vt-dd ' + sel.className.replace(/\bvt-dd-native\b/g, '')).trim();
    text.textContent = labelOf(sel);
    button.disabled = sel.disabled;
    for (const [attr, to] of [['title', 'title'], ['aria-label', 'aria-label']]) {
      const v = sel.getAttribute(attr);
      if (v) button.setAttribute(to, v); else button.removeAttribute(to);
    }
    if (!sel.getAttribute('aria-label') && sel.id) {
      const label = document.querySelector(`label[for="${(window.CSS?.escape || ((v) => v))(sel.id)}"]`);
      if (label) {
        if (!label.id) label.id = `${sel.id}-label`;
        button.setAttribute('aria-labelledby', label.id);
      }
    }
  };

  // 코드가 `sel.value = x`로 바꿔도(change 이벤트 없음) 보이는 글자가 따라가게 한다.
  const protoValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  const protoIndex = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
  Object.defineProperty(sel, 'value', { configurable: true, get() { return protoValue.get.call(this); }, set(v) { protoValue.set.call(this, v); sync(); } });
  Object.defineProperty(sel, 'selectedIndex', { configurable: true, get() { return protoIndex.get.call(this); }, set(v) { protoIndex.set.call(this, v); sync(); } });

  sel.classList.add('vt-dd-native');
  sel.tabIndex = -1;
  sel.setAttribute('aria-hidden', 'true');
  sel.after(button);
  sync();

  const watcher = new MutationObserver(sync);
  watcher.observe(sel, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'disabled', 'title', 'aria-label', 'id', 'label'] });
  const onFormReset = () => queueMicrotask(sync);
  sel.form?.addEventListener('reset', onFormReset);

  // ── 목록 ──────────────────────────────────────────────────────────────
  let list = null, active = -1, typed = '', typedAt = 0;
  let items = [];   // { el, option }
  const enabled = () => items.filter((i) => !i.option.disabled);

  const place = () => {
    const r = button.getBoundingClientRect();
    const vh = window.visualViewport?.height || window.innerHeight;
    const vw = window.innerWidth;
    list.style.minWidth = `${Math.round(r.width)}px`;
    list.style.maxHeight = '';
    const h = Math.min(list.scrollHeight + 2, 320);
    const below = vh - r.bottom - 8, above = r.top - 8;
    const up = h > below && above > below;
    const room = Math.max(120, up ? above : below);
    list.style.maxHeight = `${Math.min(320, room)}px`;
    const shown = Math.min(h, room);
    list.style.top = `${Math.round(up ? r.top - shown - 4 : r.bottom + 4)}px`;
    list.style.left = `${Math.round(Math.max(8, Math.min(r.left, vw - list.offsetWidth - 8)))}px`;
    list.dataset.side = up ? 'up' : 'down';
  };

  const setActive = (index, scroll = true) => {
    active = index;
    items.forEach((it, i) => it.el.classList.toggle('active', i === index));
    const it = items[index];
    if (it) {
      button.setAttribute('aria-activedescendant', it.el.id);
      if (scroll) it.el.scrollIntoView?.({ block: 'nearest' });
    } else button.removeAttribute('aria-activedescendant');
  };

  const choose = (option) => {
    if (!option || option.disabled) return;
    const changed = sel.value !== option.value;
    close();
    if (changed) {
      sel.value = option.value;
      sel.dispatchEvent(new Event('input', { bubbles: true }));
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    button.focus({ preventScroll: true });
  };

  const onOutside = (e) => { if (!list.contains(e.target) && !button.contains(e.target)) close(); };
  const onScroll = (e) => { if (!list.contains(e.target)) close(); };

  function close() {
    if (!list) return;
    document.removeEventListener('pointerdown', onOutside, true);
    window.removeEventListener('scroll', onScroll, true);
    window.removeEventListener('resize', close);
    list.remove(); list = null; items = []; active = -1;
    button.setAttribute('aria-expanded', 'false');
    button.removeAttribute('aria-activedescendant');
    if (current?.select === sel) current = null;
  }

  function open() {
    if (list || button.disabled) return;
    closeSelect();
    list = document.createElement('div');
    list.className = 'vt-menu vt-dd-list';
    list.setAttribute('role', 'listbox');
    if (button.getAttribute('aria-label')) list.setAttribute('aria-label', button.getAttribute('aria-label'));
    // 목록의 pointerdown이 버튼 포커스를 빼앗지 않게 한다(빼앗으면 키보드 조작이 끊긴다).
    list.addEventListener('pointerdown', (e) => e.preventDefault());
    const uid = `vt-dd-${Math.random().toString(36).slice(2, 8)}`;
    items = [];
    const addOption = (option, parent) => {
      const el = document.createElement('div');
      el.className = 'vt-menu-item sm vt-dd-item';
      el.id = `${uid}-${items.length}`;
      el.setAttribute('role', 'option');
      const selected = option.selected;
      el.setAttribute('aria-selected', String(selected));
      if (option.disabled) el.setAttribute('aria-disabled', 'true');
      const label = document.createElement('span');
      label.className = 'vt-dd-item-text';
      label.textContent = option.textContent.trim();
      const mark = document.createElement('span');
      mark.className = 'vt-dd-check';
      mark.innerHTML = selected ? icon('check', 14, 2) : '';
      el.append(label, mark);
      el.addEventListener('click', () => choose(option));
      el.addEventListener('pointermove', () => { const i = items.findIndex((x) => x.el === el); if (i !== active && !option.disabled) setActive(i, false); });
      parent.append(el);
      items.push({ el, option });
    };
    for (const child of Array.from(sel.children)) {
      if (child.tagName === 'OPTGROUP') {
        const head = document.createElement('div');
        head.className = 'vt-menu-label vt-dd-group';
        head.textContent = child.label;
        list.append(head);
        for (const o of Array.from(child.children)) if (o.tagName === 'OPTION') addOption(o, list);
      } else if (child.tagName === 'OPTION') addOption(child, list);
    }
    document.body.append(list);
    button.setAttribute('aria-expanded', 'true');
    place();
    const selectedAt = items.findIndex((i) => i.option.selected && !i.option.disabled);
    setActive(selectedAt >= 0 ? selectedAt : items.findIndex((i) => !i.option.disabled));
    document.addEventListener('pointerdown', onOutside, true);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', close);
    current = { select: sel, close };
  }

  const move = (delta) => {
    const ok = enabled();
    if (!ok.length) return;
    const at = ok.findIndex((i) => i === items[active]);
    const next = ok[Math.max(0, Math.min(ok.length - 1, at < 0 ? 0 : at + delta))];
    setActive(items.indexOf(next));
  };

  button.addEventListener('click', () => { if (list) close(); else open(); });
  button.addEventListener('keydown', (e) => {
    const key = e.key;
    if (!list) {
      if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Enter' || key === ' ') { e.preventDefault(); open(); }
      return;
    }
    if (key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); return; }   // 바깥 다이얼로그가 같이 닫히지 않게
    if (key === 'Tab') { close(); return; }
    if (key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (key === 'Home') { e.preventDefault(); setActive(items.indexOf(enabled()[0])); }
    else if (key === 'End') { e.preventDefault(); const ok = enabled(); setActive(items.indexOf(ok[ok.length - 1])); }
    else if (key === 'Enter' || (key === ' ' && !typed)) { e.preventDefault(); choose(items[active]?.option); }
    else if (key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // 글자 입력으로 점프(네이티브 select와 같다): 600ms 안의 입력은 이어 붙여 접두사로 찾는다.
      const now = Date.now();
      typed = now - typedAt > 600 ? key.toLowerCase() : typed + key.toLowerCase();
      typedAt = now;
      setTimeout(() => { if (Date.now() - typedAt >= 600) typed = ''; }, 650);
      const hit = items.findIndex((i) => !i.option.disabled && i.option.textContent.trim().toLowerCase().startsWith(typed));
      if (hit >= 0) { e.preventDefault(); setActive(hit); }
    }
  });

  const entry = {
    button,
    destroy() {
      close();
      watcher.disconnect();
      sel.form?.removeEventListener('reset', onFormReset);
      delete sel.value; delete sel.selectedIndex;
      sel.classList.remove('vt-dd-native');
      sel.removeAttribute('aria-hidden');
      sel.removeAttribute('tabindex');
      button.remove();
      enhanced.delete(sel);
    },
  };
  enhanced.set(sel, entry);
  return entry;
}

/** 문서의 모든 <select>(지금 있는 것과 앞으로 생기는 것)를 드롭다운으로 바꾼다. 여러 번 불러도 한 번만 건다. */
export function autoEnhanceSelects(root = document) {
  const scan = (node) => {
    if (node.nodeType !== 1) return;
    if (node.tagName === 'SELECT') enhanceSelect(node);
    node.querySelectorAll?.('select').forEach(enhanceSelect);
  };
  scan(root.documentElement || root);
  if (booted) return;
  booted = true;
  new MutationObserver((records) => {
    for (const r of records) r.addedNodes.forEach(scan);
    // 사라진 select의 버튼·목록을 정리한다(select는 프레임워크가 지우므로 형제인 버튼은 우리가 치워야 한다).
    for (const [sel, entry] of enhanced) if (!sel.isConnected) entry.destroy();
  }).observe(document.body || document.documentElement, { childList: true, subtree: true });
}
