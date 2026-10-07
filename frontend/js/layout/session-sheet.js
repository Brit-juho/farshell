// 모바일 세션 시트의 **목록 렌더링 전부**(평소 목록 + 뷰 편집 모드 분기). 시트를 열 때만 받는 지연 청크
// (`screens`) — picker.js(상시)는 열기/닫기·포커스 트랩·토글 배선과 import() 호출만 갖는다.
//
// 평소 목록은 레일과 같은 구조 감각이다: 뷰 순서대로, 묶음 뷰(구성원 2개 이상)는 뷰 라벨 헤더 + 구성원 행
// 한 덩어리, 단독 뷰는 행 하나. 열려 있지 않은 tmux 세션은 그 아래 이름순 구역. 순서의 출처는
// layout/compact.js openSessionOrder() 하나라 스와이프·키·`n/m`과 항상 같다.
import { activeSessionId, sessionDisplayName, getSession } from '../core/store.js';
import { getActiveTabId, getClosedViews, closedViewMembers } from './store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { getAction } from '../core/dom.js';
import { revealSession, renameSession } from '../term/session.js';
import { closeSession, openSessionFromRail, confirmAndKillSession, renameView } from '../term/session-actions.js';
import { promptDialog } from '../core/dialog.js';
import { icon } from '../ui/icons.js';
import { orderedViews, renderOrderEditor } from './session-order.js';
import { contextLabel } from '../ui/session-context.js';

// ADR-29 E — tmux 목록 팝업을 없애면서 그게 하던 일(열리지 않은 tmux 세션 보기 + 열기 + 종료)을 이 시트가
// 흡수했다. `/api/tmux/sessions`는 이 브라우저 탭에 열리지 않은 것까지 내려주므로, 열린 세션의 tmux 이름과
// 겹치는 것만 걸러내면 "열리지 않은 것"만 남는다 — 레일의 판정과 같은 생각이다.
async function fetchTmuxSessions() {
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    if (!res.ok) return null;
    const list = await res.json();
    return Array.isArray(list) ? list : null;
  } catch (_) { return null; }
}

const btn = (cls, html, label) => {
  const b = document.createElement('button');
  b.type = 'button'; b.className = cls; b.innerHTML = html;
  if (label) b.setAttribute('aria-label', label);
  return b;
};

/** 열린 세션 한 줄. `inGroup`이면 묶음 안 구성원 행(테두리는 묶음이 갖는다). */
function openRow(id, ctx, inGroup, metadata) {
  const s = getSession(id);
  const name = sessionDisplayName(id);
  const tmuxName = s && (s.tmuxName || s.tmux_name);
  const activeNow = id === activeSessionId();
  const row = document.createElement('div');
  row.className = 'vt-session-row' + (inGroup ? ' in-group' : '') + (activeNow ? ' active' : '');
  const select = document.createElement('button');
  select.type = 'button'; select.className = 'vt-name-btn vt-session-select'; select.textContent = name;
  select.setAttribute('aria-current', activeNow ? 'true' : 'false');
  // 레일(Rail.tsx openRow)과 같은 이유로 revealSession — "가서 보여줘"지 "배정"이 아니다.
  select.onclick = () => { revealSession(id); ctx.close(); };
  const rename = btn('vt-icon-btn xl vt-session-action', icon('pencil', 16), `${name} 이름 변경`);
  rename.onclick = async () => {
    const next = await promptDialog('새 세션 이름', { title: '이름 변경', defaultValue: name });
    if (next === null) return;
    if (await renameSession(id, next)) ctx.refresh();
  };
  // 닫기 — 레일·칸 ×와 같은 함수(term/session-actions.js closeSession).
  const close = btn('vt-icon-btn xl danger vt-session-action', icon('x', 16), `${name} 닫기`);
  close.onclick = async () => { await closeSession(id); ctx.refresh(); };
  row.append(sessionBody(select, metadata), rename, close);
  // tmux 세션은 완전 종료도 여기서 — 모바일에서 이 동작에 닿는 유일한 길이다(ADR-29 E).
  if (tmuxName) {
    const kill = btn('vt-icon-btn xl danger vt-session-action', icon('trash-2', 16), `${tmuxName} 완전 종료`);
    kill.onclick = async () => { if (await confirmAndKillSession(tmuxName, id)) ctx.refresh(); };
    row.appendChild(kill);
  }
  return row;
}

function sleepingRow(s, ctx) {
  const row = document.createElement('div');
  row.className = 'vt-session-row sleeping';
  const wake = document.createElement('button');
  wake.type = 'button'; wake.className = 'vt-name-btn vt-session-select'; wake.textContent = s.name;
  wake.title = '열기';
  // 레일 클릭과 같은 규칙 — 기억된 묶음 뷰가 있으면 통째로, 없으면 새 단독 뷰.
  wake.onclick = async () => { ctx.close(); await openSessionFromRail({ tmuxName: s.name }); };
  const kill = btn('vt-icon-btn xl danger vt-session-action', icon('trash-2', 16), `${s.name} 완전 종료`);
  kill.onclick = async () => { if (await confirmAndKillSession(s.name, s.web_session_id || null)) ctx.refresh(); };
  row.append(sessionBody(wake, s), kill);
  return row;
}

/** 열린 세션 구역 — 뷰 순서대로. 묶음은 한 덩어리, 단독은 행 하나. 돌려주는 값: [노드들, 열린 세션들의 tmux 이름]. */
function sessionBody(select, session) {
  const body = document.createElement('div');
  body.className = 'vt-session-body';
  body.appendChild(select);
  const label = session && contextLabel(session);
  if (label) {
    const meta = document.createElement('span');
    meta.className = 'vt-session-meta'; meta.textContent = label;
    meta.setAttribute('data-tip', session.cwd || '');
    body.appendChild(meta);
  }
  return body;
}

function groupHead(id, label, count, ctx, open) {
  const head = document.createElement('div');
  head.className = 'vt-view-group-head';
  const title = document.createElement('button');
  title.type = 'button'; title.className = 'vt-session-group-label vt-view-group-title';
  title.textContent = label; title.onclick = open;
  const edit = document.createElement('button');
  edit.type = 'button'; edit.className = 'vt-view-group-rename'; edit.textContent = '편집';
  edit.setAttribute('aria-label', `${label} 묶음 이름 변경`);
  edit.onclick = async () => {
    const next = await promptDialog('묶음 뷰 제목', { title: '묶음 이름 변경', defaultValue: label });
    if (next === null || !next.trim()) return;
    if (renameView(id, next.trim())) ctx.refresh();
    else showToast('묶음 이름을 저장하지 못했습니다. 제목은 128자까지 입력할 수 있습니다.');
  };
  const total = document.createElement('span');
  total.className = 'vt-view-group-count'; total.textContent = String(count);
  total.setAttribute('aria-label', `${count}개 세션`);
  head.append(title, total, edit);
  return head;
}

function openSection(ctx, metadata) {
  const activeTab = getActiveTabId();
  const nodes = [];
  const awake = new Set();
  for (const v of orderedViews()) {
    for (const id of v.members) {
      const s = getSession(id);
      const t = s && (s.tmuxName || s.tmux_name);
      if (t) awake.add(t);
    }
    const rowFor = (id, inGroup) => { const s = getSession(id); return openRow(id, ctx, inGroup, metadata.get(s && (s.tmuxName || s.tmux_name))); };
    if (v.members.length < 2) { nodes.push(rowFor(v.members[0], false)); continue; }
    const group = document.createElement('div');
    group.className = 'vt-session-group' + (v.id === activeTab ? ' active' : '');
    group.setAttribute('role', 'group');
    const label = v.label || '묶음 뷰';
    group.setAttribute('aria-label', label);
    const head = groupHead(v.id, label, v.members.length, ctx, () => { revealSession(v.members[0]); ctx.close(); });
    group.appendChild(head);
    for (const id of v.members) group.appendChild(rowFor(id, true));
    nodes.push(group);
  }
  return { nodes, awake };
}

/**
 * 시트 내용을 (다시) 그린다. 새 내용을 떼어낸 채 만들어 한 번에 바꿔 끼운다 — tmux 목록을 기다리는 동안
 * 이전 목록이 그대로 남아 빈 시트가 깜빡이지 않고, 겹쳐 호출돼도 마지막 호출만 반영한다.
 * 편집 모드 상태는 시트(backdrop)에 달려 있어 시트를 닫으면 함께 사라진다.
 * @param ctx {{ close(): void, refresh(): void }} picker.js가 넘기는 닫기·다시 그리기
 */
export async function renderSessionSheet(backdrop, ctx) {
  const sheet = backdrop.querySelector?.('.vt-session-sheet');
  if (!sheet) return;
  const seq = (backdrop._seq = (backdrop._seq || 0) + 1);
  const viewCount = orderedViews().length;
  // 편집 중에는 뷰가 하나 남아도 모드를 유지한다(마저 닫을 수 있게). 마지막 뷰를 닫아 0개가 되면
  // 시트를 닫는다 — 뒤에는 온보딩이 떠 있다.
  if (backdrop._ordering && viewCount < 1) { ctx.close(); return; }
  const ordering = !!backdrop._ordering;

  const toggle = (viewCount >= 2 || ordering)
    ? btn(`vt-icon-btn xl vt-session-order-toggle${ordering ? ' active' : ''}`, icon('square-pen', 16), '뷰 편집') : null;
  if (toggle) toggle.setAttribute('aria-pressed', String(ordering));
  const closeBtn = btn('vt-icon-btn xl vt-session-close', icon('x', 16), '세션 관리 닫기');
  closeBtn.onclick = ctx.close;
  const head = document.createElement('div');
  head.className = 'vt-session-head';
  const h2 = document.createElement('h2');
  h2.id = 'session-manager-title';
  h2.textContent = ordering ? '뷰 편집' : '세션 관리';
  const actions = document.createElement('div');
  actions.className = 'vt-session-head-actions';
  actions.append(...(toggle ? [toggle] : []), closeBtn);
  head.append(h2, actions);
  const list = document.createElement('div');
  list.className = 'vt-session-list';
  if (toggle) toggle.onclick = () => { backdrop._ordering = !backdrop._ordering; backdrop._refocusToggle = true; ctx.refresh(); };

  const first = !backdrop._rendered;
  const finish = (...tail) => {
    if (seq !== backdrop._seq || !backdrop.isConnected) return false;
    sheet.removeAttribute('aria-busy');
    sheet.replaceChildren(head, list, ...tail);
    backdrop._rendered = true;
    if (first) closeBtn.focus();
    else if (backdrop._refocusToggle) (toggle || closeBtn).focus();
    backdrop._refocusToggle = false;
    return true;
  };

  if (ordering) {
    // 편집 모드에서는 세션 행(클릭 이동)을 아예 그리지 않는다 — 오조작 방지.
    if (finish()) renderOrderEditor(list);
    return;
  }

  const pending = backdrop._snapshot;
  backdrop._snapshot = null;
  const fetched = pending || await fetchTmuxSessions();
  if (seq !== backdrop._seq) return;
  if (fetched) backdrop._tmux = fetched;
  const tmux = backdrop._tmux || [];
  const { nodes, awake } = openSection(ctx, new Map(tmux.map((s) => [s.name, s])));
  list.append(...nodes);
  const sleeping = tmux
    .filter((s) => s && s.name && !awake.has(s.name))
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  if (seq !== backdrop._seq) return;
  if (sleeping.length) {
    const h = document.createElement('p');
    h.className = 'vt-session-section';
    h.textContent = '열려 있지 않은 세션';
    list.appendChild(h);
    const remaining = new Map(sleeping.map((s) => [s.name, s]));
    for (const v of getClosedViews()) {
      const members = closedViewMembers(v).map((n) => remaining.get(n)).filter(Boolean);
      if (members.length < 2) continue;
      const group = document.createElement('div');
      group.className = 'vt-session-group closed';
      const label = v.label || members.map((s) => s.name).join(' · ');
      group.setAttribute('role', 'group'); group.setAttribute('aria-label', label);
      group.appendChild(groupHead(v.id, label, members.length, ctx, async () => { ctx.close(); await openSessionFromRail({ tmuxName: members[0].name }); }));
      for (const s of members) { remaining.delete(s.name); group.appendChild(sleepingRow(s, ctx)); }
      list.appendChild(group);
    }
    for (const s of remaining.values()) list.appendChild(sleepingRow(s, ctx));
  }
  if (!nodes.length && !sleeping.length && fetched) list.innerHTML = '<p class="vt-session-empty">열려 있는 세션이 없습니다.</p>';
  if (!fetched) {
    const error = document.createElement('p'); error.className = 'vt-session-error';
    error.setAttribute('role', 'status'); error.textContent = '세션 정보를 갱신하지 못했습니다. ';
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'vt-btn sm';
    retry.textContent = '다시 시도'; retry.onclick = ctx.refresh; error.appendChild(retry); list.appendChild(error);
  }

  // "맥에서도 열기"는 특정 세션이 아니라 앞으로 만들 모든 세션에 적용되는 전역 설정이라 ⋯의 「설정」 그룹 쪽에 남긴다.
  const footer = document.createElement('div');
  footer.className = 'vt-session-footer';
  const mac = document.createElement('button');
  mac.type = 'button'; mac.className = 'vt-menu-item vt-session-footer-item'; mac.id = 'session-open-mac-btn';
  mac.innerHTML = `${icon('terminal', 16)}<span class="vt-menu-item-label">지금 이 세션 맥에서 열기</span>`;
  mac.onclick = () => { getAction('session.open-on-mac')?.(); };
  footer.appendChild(mac);
  finish(footer);
}
