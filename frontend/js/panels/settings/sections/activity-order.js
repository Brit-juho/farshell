import { get as settingGet, set as settingSet, subscribe } from '../../../core/settings.js';
import { shellIcon } from '../../../ui/icons-shell.js';
import { t } from '../../../core/i18n.js';
import {
  ACTIVITY_LAYOUT_KEY, FIXED_ACTIVITY, parseActivityLayout, serializeActivityLayout,
  moveActivity, stepActivity, setActivityHidden,
} from '../../../shell/activity-layout.js';

// ── 「활동 막대 순서」 (시안 O05 오른쪽 칸) ─────────────────────────────────
// 설정 › 상태 막대 · 활동 막대의 오른쪽 칸. 위 무리(활동)는 순서와 숨김, 아래 무리(MCP·음성·설정)는 순서만.
// 규칙은 전부 shell/activity-layout.js의 순수 함수에 있다 — 드래그와 ↑↓ 버튼이 같은 `moveActivity`를 거친다.
//
// 같은 편집기가 두 자리에 그려진다: 창이 넓으면(≥1100) 오른쪽 칸(preview.js), 좁으면 상태 막대 구역 아래(hud.js).
// 보이는 쪽은 CSS가 고른다(.vt-set-preview는 좁으면 숨는다). 둘 다 설정 구독으로 다시 그려 서로 어긋나지 않는다.
//
// 이름·아이콘은 shell/activity-data.ts의 ACTIVITIES와 같은 문장이다 — 이 설정 화면은 .ts를 못 읽는 단위 테스트
// 하네스로 시험해서 거기서 가져오지 못한다(frontend/tests/activity-layout.test.js가 id 목록을 대조한다).

const ITEM_MIME = 'application/x-vt-activity';

export function activityInfo() {
  // t()는 호출 시점에 평가한다(언어가 바뀐 뒤에도 맞게).
  return {
    sessions: [t('세션'), 'list'],
    scm: [t('변경'), 'git-branch'],
    dir: [t('파일'), 'folder'],
    queue: [t('큐'), 'clipboard'],
    files: [t('첨부'), 'paperclip'],
    snippets: [t('스니펫'), 'braces'],
    ports: [t('포트'), 'plug'],
    usage: [t('사용량'), 'gauge'],
    system: [t('시스템'), 'bar-chart'],
    mcp: [t('MCP · 스킬 관리'), 'box'],
    mic: [t('음성 입력'), 'mic'],
    settings: [t('설정'), 'settings'],
  };
}

const current = () => parseActivityLayout(settingGet(ACTIVITY_LAYOUT_KEY));
const save = (layout) => settingSet(ACTIVITY_LAYOUT_KEY, serializeActivityLayout(layout));

function iconBtn(ic, label, onClick, { disabled = false, pressed } = {}) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'vt-icon-btn sm';
  b.setAttribute('aria-label', label);
  b.title = label;
  if (pressed !== undefined) b.setAttribute('aria-pressed', String(pressed));
  b.disabled = disabled;
  b.innerHTML = shellIcon(ic, 14, 2);
  b.addEventListener('click', onClick);
  return b;
}

// 드롭 위치: 포인터가 각 항목의 가운데보다 위면 그 항목 앞(hud.js와 같은 규칙). 고정 행은 세지 않는다 —
// 목록의 0번 자리는 늘 「세션」이라 자리 번호에 1을 더한다.
function dropIndex(ul, clientY, offset) {
  const items = [...ul.querySelectorAll('.vt-actcfg-item:not(.dragging):not(.pinned)')];
  for (let i = 0; i < items.length; i += 1) {
    const r = items[i].getBoundingClientRect();
    if (clientY < r.top + r.height / 2) return i + offset;
  }
  return items.length + offset;
}

function itemEl(id, group, layout, info) {
  const li = document.createElement('li');
  li.className = 'vt-actcfg-item';
  li.dataset.activity = id;
  const [name, ic] = info[id];
  const fixed = id === FIXED_ACTIVITY;
  const hidden = layout.hidden.includes(id);
  li.classList.toggle('pinned', fixed);
  li.classList.toggle('off', hidden);

  if (!fixed) {
    li.draggable = true;
    const grip = document.createElement('span');
    grip.className = 'vt-actcfg-grip';
    grip.setAttribute('aria-hidden', 'true');
    grip.innerHTML = shellIcon('grip-vertical', 14, 2);
    li.appendChild(grip);
    li.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData(ITEM_MIME, id);
      li.classList.add('dragging');
    });
    li.addEventListener('dragend', () => li.classList.remove('dragging'));
  }
  const ico = document.createElement('span');
  ico.className = 'vt-actcfg-ico';
  ico.setAttribute('aria-hidden', 'true');
  ico.innerHTML = shellIcon(ic, 14, 2);
  const label = document.createElement('span');
  label.className = 'vt-actcfg-name';
  label.textContent = name;
  li.append(ico, label);

  if (fixed) {
    const tag = document.createElement('span');
    tag.className = 'vt-badge vt-actcfg-tag';
    tag.textContent = t('맨 위 고정');
    li.appendChild(tag);
    return li;
  }
  if (hidden) {
    const tag = document.createElement('span');
    tag.className = 'vt-badge vt-actcfg-tag';
    tag.textContent = t('숨김');
    li.appendChild(tag);
  }
  const list = layout[group];
  const idx = list.indexOf(id);
  const min = group === 'order' ? 1 : 0;
  if (group === 'order') {
    li.appendChild(iconBtn(hidden ? 'eye-off' : 'eye',
      hidden ? t('{name} 활동 막대에 다시 보이기', { name }) : t('{name} 활동 막대에서 숨기기', { name }),
      () => save(setActivityHidden(current(), id, !hidden)), { pressed: hidden }));
  }
  li.appendChild(iconBtn('arrow-up', t('{name} 위로', { name }), () => save(stepActivity(current(), id, -1)), { disabled: idx <= min }));
  li.appendChild(iconBtn('arrow-down', t('{name} 아래로', { name }), () => save(stepActivity(current(), id, 1)), { disabled: idx >= list.length - 1 }));
  return li;
}

function listEl(group, layout, info) {
  const ul = document.createElement('ul');
  ul.className = 'vt-actcfg-list';
  ul.dataset.group = group;
  ul.setAttribute('aria-label', group === 'order' ? t('활동') : t('아래쪽'));
  for (const id of layout[group]) ul.appendChild(itemEl(id, group, layout, info));
  const accepts = (ev) => [...ev.dataTransfer.types].includes(ITEM_MIME);
  ul.addEventListener('dragover', (ev) => {
    if (!accepts(ev)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    ul.classList.add('drop');
  });
  ul.addEventListener('dragleave', (ev) => { if (!ul.contains(ev.relatedTarget)) ul.classList.remove('drop'); });
  ul.addEventListener('drop', (ev) => {
    if (!accepts(ev)) return;
    ev.preventDefault();
    ul.classList.remove('drop');
    const id = ev.dataTransfer.getData(ITEM_MIME);
    const now = current();
    // 다른 무리로는 옮기지 않는다(moveActivity가 id의 무리를 스스로 정하므로 목록이 다르면 무시).
    if (!now[group].includes(id)) return;
    save(moveActivity(now, id, dropIndex(ul, ev.clientY, group === 'order' ? 1 : 0)));
  });
  return ul;
}

/**
 * 편집기 하나. `{ el, dispose }` — dispose는 설정 구독을 끊는다. `variant`는 자리(side: 오른쪽 칸, inline: 구역 아래).
 * @param {'side'|'inline'} variant
 */
export function createActivityOrderEditor(variant) {
  const root = document.createElement('section');
  root.className = `vt-actcfg vt-actcfg-${variant}`;
  root.setAttribute('aria-label', t('활동 막대 순서'));
  const draw = () => {
    const layout = current();
    const info = activityInfo();
    root.replaceChildren();
    const head = document.createElement('div');
    head.className = 'vt-actcfg-head';
    const title = document.createElement('h3');
    title.className = 'vt-actcfg-title';
    title.textContent = t('활동 막대 순서');
    const hint = document.createElement('span');
    hint.className = 'vt-actcfg-hint';
    hint.textContent = t('끌어서 바꾸기');
    head.append(title, hint);
    const sub = document.createElement('div');
    sub.className = 'vt-actcfg-sub';
    sub.textContent = t('아래쪽');
    const note = document.createElement('p');
    note.className = 'vt-actcfg-note';
    note.textContent = t('숨긴 활동은 팔레트에서 그대로 엽니다. 아래쪽 버튼은 순서만 바꿉니다. 이 기기에만 저장됩니다.');
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'vt-btn sm quiet vt-actcfg-reset';
    reset.textContent = t('기본값으로');
    reset.disabled = !String(settingGet(ACTIVITY_LAYOUT_KEY) || '').trim();
    reset.addEventListener('click', () => settingSet(ACTIVITY_LAYOUT_KEY, ''));
    root.append(head, listEl('order', layout, info), sub, listEl('bottom', layout, info), note, reset);
  };
  draw();
  // 섹션을 떠나 DOM에서 빠진 편집기는 다음 변경 때 스스로 구독을 끊는다 — 설정 섹션에는 정리 고리가 없다
  // (오른쪽 칸은 preview.js의 dispose가 먼저 끊는다).
  const off = subscribe((changed) => {
    if (!root.isConnected) { off(); return; }
    if (changed && ACTIVITY_LAYOUT_KEY in changed) draw();
  });
  return { el: root, dispose: off };
}
