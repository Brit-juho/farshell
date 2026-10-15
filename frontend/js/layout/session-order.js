// 모바일 「세션」 탭의 「뷰 편집」(shell/Fleet.tsx가 마운트) — 뷰 단위로 위/아래 버튼으로 순서를 옮기고, 닫기 버튼으로 뷰를 닫는다
// (10-session-order.md C안 + 2026-10-06 뷰 닫기). 닫기는 탭 ×와 같은 closeView(묶음 구성 기억 포함).
// 순서의 출처는 저장되는 레이아웃 하나라서(layout/compact.js openSessionOrder) 여기서 하는 일은
// `moveLayoutTab`(데스크톱 탭 드래그와 같은 함수) + `saveLayoutNow` 뿐이다. 활성 탭은 id로 다시
// 찾으므로 순서가 바뀌어도 활성 뷰·포커스는 그대로다. 열 때만 받는 지연 청크(`screens`).
import { openSessionOrder } from './compact.js';
import { getTabsWithTrees, getActiveTabId, moveLayoutTab } from './store.js';
import { saveLayoutNow } from './persist.js';
import { sessionDisplayName } from '../core/store.js';
import { icon } from '../ui/icons.js';
import { closeView } from '../term/session-actions.js';
import { t } from '../core/i18n.js';

let _focus = null;   // 이동·닫기 직후 다시 그릴 때 포커스를 돌려줄 버튼 {tabId, dir} | {close:true, index}
let _note = null;    // 스크린리더용 결과 안내 한 줄 {text, until} — 한 동작이 시트를 여러 번 다시 그리므로 1.5초간 유지

/** 열린 세션이 있는 뷰만, 화면 순서대로. 빈 뷰는 순서 편집 대상이 아니다. */
export function orderedViews() {
  const order = openSessionOrder();
  return getTabsWithTrees()
    .map((t, index) => ({ id: t.id, index, label: t.label || '', members: order.filter((e) => e.tabId === t.id).map((e) => e.id) }))
    .filter((v) => v.members.length > 0);
}

/** 뷰를 한 칸 옮긴다(delta -1 위, +1 아래). 끝에서는 멈춘다(순환 없음). 옮겼으면 true. */
export function moveView(tabId, delta) {
  const views = orderedViews();
  const i = views.findIndex((v) => v.id === tabId);
  const j = i + (delta < 0 ? -1 : 1);
  if (i < 0 || j < 0 || j >= views.length) return false;
  // 사이에 낀 빈 뷰까지 넘어가도록 전체 탭 배열에서의 이웃 위치로 보낸다.
  if (!moveLayoutTab(tabId, views[j].index)) return false;
  saveLayoutNow();
  return true;
}

function viewTitle(v) {
  return v.label || (v.members.length > 1 ? t('묶음 뷰') : sessionDisplayName(v.members[0]));
}

export function renderOrderEditor(list) {
  const views = orderedViews();
  const activeTab = getActiveTabId();
  list.replaceChildren();
  const note = document.createElement('p');
  note.className = 'vt-order-note';
  note.setAttribute('role', 'status');
  note.textContent = (_note && Date.now() < _note.until && _note.text) || t('위·아래 버튼으로 뷰 순서를 바꾸고, 닫기 버튼으로 뷰를 닫습니다. 묶음 뷰는 함께 움직이고 닫아도 세션은 살아 있습니다.');
  list.appendChild(note);
  views.forEach((v, i) => {
    const block = document.createElement('div');
    block.className = 'vt-session-row vt-order-view' + (v.id === activeTab ? ' active' : '');
    const body = document.createElement('div');
    body.className = 'vt-order-body';
    const grouped = v.members.length > 1;
    if (grouped) {
      const head = document.createElement('span');
      head.className = 'vt-order-label';
      head.textContent = viewTitle(v);
      body.appendChild(head);
    }
    for (const id of v.members) {
      const name = document.createElement('span');
      name.className = grouped ? 'vt-order-member' : 'vt-order-single';
      name.textContent = sessionDisplayName(id);
      body.appendChild(name);
    }
    block.appendChild(body);
    for (const dir of [-1, 1]) {
      const b = document.createElement('button');
      const off = dir < 0 ? i === 0 : i === views.length - 1;
      b.type = 'button';
      b.className = 'vt-icon-btn xl vt-order-btn' + (dir < 0 ? ' vt-order-up' : '');
      b.dataset.tab = v.id; b.dataset.dir = String(dir);
      b.innerHTML = icon('chevron-down', 18);
      const label = grouped ? viewTitle(v) : sessionDisplayName(v.members[0]);
      b.setAttribute('aria-label', dir < 0 ? t('{label} 위로 옮기기', { label }) : t('{label} 아래로 옮기기', { label }));
      if (off) b.setAttribute('aria-disabled', 'true');
      b.onclick = () => {
        if (off) return;
        _focus = { tabId: v.id, dir };
        _note = { text: t('{label}: {pos}번째 / 전체 {n}개', { label, pos: i + 1 + dir, n: views.length }), until: Date.now() + 1500 };
        if (!moveView(v.id, dir)) { _focus = null; _note = null; }
      };
      block.appendChild(b);
    }
    const label = viewTitle(v);
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'vt-icon-btn xl danger vt-order-close';
    x.dataset.tab = v.id;
    x.innerHTML = icon('x', 18);
    // 라벨이 이미 '…뷰'로 끝나면(예: 「묶음 뷰」) '뷰'를 또 붙이지 않는다.
    const viewName = /뷰$/.test(label) ? label : t('{label} 뷰', { label });
    x.setAttribute('aria-label', t('{name} 닫기', { name: viewName }));
    x.onclick = async () => {
      _focus = { close: true, index: i, until: Date.now() + 1500 };
      _note = { text: t('{name}를 닫았습니다. 세션은 그대로 남아 있습니다.', { name: viewName }), until: Date.now() + 1500 };
      await closeView(v.id);
      // closeView 뒤에도 세션 정리·저장이 시트를 다시 그린다 — 닫기 포커스는 1.5초간 유지해 그 그림마다 되돌린다.
      if (list.isConnected) renderOrderEditor(list);
    };
    block.appendChild(x);
    list.appendChild(block);
  });
  if (_focus && _focus.close && Date.now() > _focus.until) _focus = null;
  if (_focus && _focus.close) {
    const xs = [...list.querySelectorAll('.vt-order-close')];
    // 닫힌 자리로 올라온 이웃 뷰의 닫기 버튼, 없으면 마지막 것. 뷰가 없으면 편집 토글로.
    const want = xs[Math.min(_focus.index, xs.length - 1)]
      || list.closest('#vt-fleet')?.querySelector('.vt-fleet-edit-btn');
    if (want) want.focus();
  } else if (_focus) {
    const f = _focus; _focus = null;
    const btns = [...list.querySelectorAll('.vt-order-btn')].filter((b) => b.dataset.tab === f.tabId);
    // 끝에 닿아 비활성이 된 쪽이면 반대쪽 버튼으로 — 포커스가 body로 빠지지 않게.
    const want = btns.find((b) => b.dataset.dir === String(f.dir) && b.getAttribute('aria-disabled') !== 'true')
      || btns.find((b) => b.getAttribute('aria-disabled') !== 'true') || btns[0];
    if (want) want.focus();
  }
}
