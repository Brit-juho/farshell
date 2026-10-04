// 모바일 세션 시트의 「순서 편집」 — 뷰 단위로 위/아래 버튼으로 순서를 옮긴다(10-session-order.md C안).
// 순서의 출처는 저장되는 레이아웃 하나라서(layout/compact.js openSessionOrder) 여기서 하는 일은
// `moveLayoutTab`(데스크톱 탭 드래그와 같은 함수) + `saveLayoutNow` 뿐이다. 활성 탭은 id로 다시
// 찾으므로 순서가 바뀌어도 활성 뷰·포커스는 그대로다. 열 때만 받는 지연 청크(`screens`).
import { openSessionOrder } from './compact.js';
import { getTabsWithTrees, getActiveTabId, moveLayoutTab } from './store.js';
import { saveLayoutNow } from './persist.js';
import { sessionDisplayName } from '../core/store.js';
import { icon } from '../ui/icons.js';

let _focus = null;   // 이동 직후 다시 그릴 때 포커스를 돌려줄 버튼 {tabId, dir}
let _note = '';      // 스크린리더용 결과 안내 한 줄

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
  return v.label || (v.members.length > 1 ? '묶음 뷰' : sessionDisplayName(v.members[0]));
}

export function renderOrderEditor(list) {
  const views = orderedViews();
  const activeTab = getActiveTabId();
  list.replaceChildren();
  const note = document.createElement('p');
  note.className = 'vt-order-note';
  note.setAttribute('role', 'status');
  note.textContent = _note || '위·아래 버튼으로 뷰 순서를 바꿉니다. 묶음 뷰는 함께 움직입니다.';
  _note = '';
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
      b.setAttribute('aria-label', `${label} ${dir < 0 ? '위로' : '아래로'} 옮기기`);
      if (off) b.setAttribute('aria-disabled', 'true');
      b.onclick = () => {
        if (off) return;
        _focus = { tabId: v.id, dir };
        _note = `${label}: ${i + 1 + dir}번째 / 전체 ${views.length}개`;
        if (!moveView(v.id, dir)) { _focus = null; _note = ''; }
      };
      block.appendChild(b);
    }
    list.appendChild(block);
  });
  if (_focus) {
    const f = _focus; _focus = null;
    const btns = [...list.querySelectorAll('.vt-order-btn')].filter((b) => b.dataset.tab === f.tabId);
    // 끝에 닿아 비활성이 된 쪽이면 반대쪽 버튼으로 — 포커스가 body로 빠지지 않게.
    const want = btns.find((b) => b.dataset.dir === String(f.dir) && b.getAttribute('aria-disabled') !== 'true')
      || btns.find((b) => b.getAttribute('aria-disabled') !== 'true') || btns[0];
    if (want) want.focus();
  }
}
