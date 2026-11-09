import { get as settingGet, set as settingSet } from '../../../core/settings.js';
import { icon } from '../../../ui/icons.js';
import { vtEsc } from '../../../core/api.js';
import { t } from '../../../core/i18n.js';
import {
  parseHudLayout, serializeHudLayout, hudZoneOf, moveHudKind, stepHudKind, HUD_KINDS, PINNED_HUD_KINDS,
} from '../../../shell/hud-layout.js';

// ── 「상태 막대」 ──────────────────────────────────────────────────────────
// 맨 아래 상태 막대(옛 메뉴바·하단바, HUD)에 어떤 칩을 어느 쪽에 어떤 순서로 둘지 정한다(iTerm2의 상태바 구성처럼).
// 에이전트 칩은 맨 왼쪽 고정이라 「왼쪽」 맨 위에 「고정」 표시로만 보이고 옮길 수 없다(hud-layout.js 세대 3).
//
// 규칙은 전부 shell/hud-data.ts의 순수 함수에 있다. 이 화면은 그걸 부르고 결과를 저장할 뿐이다 —
// 드래그·위아래 버튼·구역 선택 세 경로가 모두 `moveHudKind`/`stepHudKind` 하나를 거친다.
// (세 경로가 각자 배열을 만지면 서로 다른 규칙이 생기고, 키보드만 쓰는 사람의 경로가 가장 먼저 어긋난다.)
//
// 값이 없는 칩은 배치와 무관하게 그려지지 않는다(예: 터널이 없으면 터널 칩 없음). 여기서
// 「왼쪽/오른쪽」에 둔다는 건 「값이 있을 때 이 자리에 보인다」는 뜻이다.
//
// 미리보기는 실제 HUD와 같은 칩 마크업(.vt-hud-chip)에 **예시 값**을 채워 그린다 — 실제 값을
// 폴링하지 않는다(이 화면이 열려 있는 동안 서버를 두드릴 이유가 없다).

const KEY = 'ui.hud.layout';

function kindInfo() {
  // t()는 호출 시점에 평가해야 언어가 바뀐 뒤에도 맞는다(모듈 평가 시점에 고정하지 않는다).
  return {
    server: { name: t('서버'), desc: t('서버 포트. 이 화면이 보이면 서버는 떠 있으므로 기본에서는 뺐습니다.'), sample: { label: t('서버'), value: ':7777', dot: true, tone: 'ok' } },
    screens: { name: t('연결된 화면'), desc: t('이 세션에 붙어 있는 화면 수. 둘 이상이면 주의 색입니다.'), sample: { label: t('연결된 화면'), value: '1', tone: 'plain' } },
    e2e: { name: 'E2E', desc: t('E2E 모드가 켜져 있을 때만 보입니다.'), sample: { label: 'E2E', value: 'ON', tone: 'ok' } },
    'safe-mode': { name: t('세이프모드'), desc: t('세이프모드가 켜져 있을 때만 보입니다.'), sample: { label: t('세이프모드'), value: 'ON', tone: 'ok' } },
    cpu: { name: 'CPU', desc: t('CPU 사용률. 75%부터 주의, 90%부터 경고 색입니다.'), sample: { label: 'CPU', value: '17%', tone: 'plain' } },
    gpu: { name: 'GPU', desc: t('GPU 사용률. 읽을 수 없는 기기에서는 보이지 않습니다.'), sample: { label: 'GPU', value: '0%', tone: 'plain' } },
    ram: { name: 'RAM', desc: t('메모리 사용률. 80%부터 주의, 90%부터 경고 색입니다.'), sample: { label: 'RAM', value: '72%', tone: 'plain' } },
    net: { name: t('네트워크'), desc: t('초당 내려받기와 올리기 속도.'), sample: { label: 'NET', value: '↓12K ↑3K', tone: 'plain' } },
    usage: { name: t('사용량'), desc: t('AI 사용량 한도. 프로필마다 하나씩, 한도가 가까우면 앞자리로 올라옵니다.'), sample: { label: 'claude', value: '42%', hint: '· 4h12m', tone: 'plain' } },
    agents: { name: t('에이전트'), desc: t('입력을 기다리는·작업 중인·확인하지 않은 완료 세션 수. 개입이 필요하면 눌러서 가장 오래 기다린 세션으로 갑니다. 맨 왼쪽에 고정돼 옮길 수 없습니다.'), sample: { label: t('개입'), value: '2', hint: `· ${t('작업')} 1 · ${t('완료')} 1`, tone: 'warn' } },
    push: { name: t('알림'), desc: t('이 브라우저의 푸시 알림 켜짐·꺼짐. 눌러서 바꿉니다. 이 브라우저가 푸시를 못 쓰면 보이지 않습니다.'), sample: { label: t('알림'), value: t('켜짐'), tone: 'ok' } },
    clock: { name: t('시계'), desc: t('날짜와 시각. 오른쪽 끝에 두면 맥 메뉴바의 시계와 같은 자리입니다.'), sample: { label: '', value: t('11월 3일 (화) 14:05'), tone: 'plain' } },
    version: { name: t('버전'), desc: t('FarShell 버전. 자리가 모자라면 가장 먼저 사라집니다.'), sample: { label: '', value: 'v2.6.0', tone: 'plain' } },
  };
}

const ZONES = () => [
  { id: 'left', title: t('왼쪽'), empty: t('비어 있음 — 여기로 끌어 놓으세요') },
  { id: 'right', title: t('오른쪽'), empty: t('비어 있음 — 여기로 끌어 놓으세요') },
  { id: 'off', title: t('사용 안 함'), empty: t('모든 칩을 쓰고 있습니다') },
];

const current = () => parseHudLayout(settingGet(KEY));

function save(layout, rerender) {
  settingSet(KEY, serializeHudLayout(layout));
  rerender();
}

function chipHTML(sample, kind) {
  const parts = [];
  if (sample.dot) parts.push('<span class="vt-hud-dot"></span>');
  if (sample.label) parts.push(`<span class="vt-hud-label">${vtEsc(sample.label)}</span>`);
  if (sample.value) parts.push(`<span class="vt-hud-value">${vtEsc(sample.value)}</span>`);
  if (sample.hint) parts.push(`<span class="vt-hud-hint">${vtEsc(sample.hint)}</span>`);
  return `<span class="vt-hud-chip tone-${sample.tone}" data-chip="${vtEsc(kind || '')}">${parts.join('')}</span>`;
}

function previewEl(layout, info) {
  const bar = document.createElement('div');
  bar.className = 'vt-hudcfg-preview';
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', t('상태 막대 미리보기'));
  const side = (kinds) => kinds.map((k) => chipHTML(info[k].sample, k)).join('');
  // 고정 칩(에이전트)은 배치와 상관없이 늘 맨 왼쪽이다 — 실제 막대(hud-data.ts)와 같은 순서로 그린다.
  bar.innerHTML = `${side(PINNED_HUD_KINDS)}${side(layout.left)}<div class="vt-hud-spacer"></div>${side(layout.right)}`;
  return bar;
}

// 드롭 위치: 포인터가 각 항목의 가운데보다 위면 그 항목 앞에 들어간다.
function dropIndex(zoneEl, clientY) {
  const items = [...zoneEl.querySelectorAll('.vt-hudcfg-item:not(.dragging):not(.pinned)')];
  for (let i = 0; i < items.length; i += 1) {
    const r = items[i].getBoundingClientRect();
    if (clientY < r.top + r.height / 2) return i;
  }
  return items.length;
}

function itemEl(kind, zone, layout, info, rerender) {
  const li = document.createElement('li');
  li.className = 'vt-hudcfg-item';
  li.draggable = true;
  li.dataset.kind = kind;
  const label = info[kind].name;

  const grip = document.createElement('span');
  grip.className = 'vt-hudcfg-grip';
  grip.setAttribute('aria-hidden', 'true');
  grip.innerHTML = icon('grip-vertical', 14);

  const text = document.createElement('div');
  text.className = 'vt-hudcfg-text';
  const name = document.createElement('b');
  name.textContent = label;
  const desc = document.createElement('span');
  desc.textContent = info[kind].desc;
  text.append(name, desc);

  // 구역 선택 — 드래그를 못 쓰는 사람(키보드·터치패드)이 같은 일을 할 수 있는 경로.
  const wrap = document.createElement('div');
  wrap.className = 'vt-select-wrap';
  const sel = document.createElement('select');
  sel.className = 'vt-select';
  sel.setAttribute('aria-label', t('{name} 위치', { name: label }));
  for (const z of ZONES()) {
    const o = document.createElement('option');
    o.value = z.id;
    o.textContent = z.title;
    sel.appendChild(o);
  }
  sel.value = zone;
  sel.addEventListener('change', () => save(moveHudKind(layout, kind, sel.value), rerender));
  wrap.appendChild(sel);
  wrap.insertAdjacentHTML('beforeend', icon('chevron-down', 12, 2));

  const ctrl = document.createElement('div');
  ctrl.className = 'vt-hudcfg-ctrl';
  ctrl.appendChild(wrap);
  if (zone !== 'off') {
    const list = layout[zone];
    const idx = list.indexOf(kind);
    for (const [dir, ic, aria] of [[-1, 'arrow-up', t('{name} 위로', { name: label })], [1, 'arrow-down', t('{name} 아래로', { name: label })]]) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'vt-icon-btn';
      b.setAttribute('aria-label', aria);
      b.title = aria;
      b.disabled = dir < 0 ? idx === 0 : idx === list.length - 1;
      b.innerHTML = icon(ic, 14);
      b.addEventListener('click', () => save(stepHudKind(layout, kind, dir), rerender));
      ctrl.appendChild(b);
    }
  }

  li.addEventListener('dragstart', (ev) => {
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('text/plain', kind);
    li.classList.add('dragging');
  });
  li.addEventListener('dragend', () => li.classList.remove('dragging'));

  li.append(grip, text, ctrl);
  return li;
}

// 고정 칩 — 끌 수도 옮길 수도 없다. 손잡이·선택·버튼 없이 「고정」 표시만 둔다(시안 O05).
function pinnedItemEl(kind, info) {
  const li = document.createElement('li');
  li.className = 'vt-hudcfg-item pinned';
  li.dataset.kind = kind;
  const text = document.createElement('div');
  text.className = 'vt-hudcfg-text';
  const name = document.createElement('b');
  name.textContent = info[kind].name;
  const desc = document.createElement('span');
  desc.textContent = info[kind].desc;
  text.append(name, desc);
  const tag = document.createElement('span');
  tag.className = 'vt-badge vt-hudcfg-pin';
  tag.textContent = t('고정');
  li.append(text, tag);
  return li;
}

function zoneEl(z, layout, info, rerender) {
  const section = document.createElement('section');
  section.className = 'vt-hudcfg-zone';
  section.dataset.zone = z.id;
  const h = document.createElement('h3');
  h.className = 'vt-set-group-title';
  h.textContent = z.title;
  const ul = document.createElement('ul');
  ul.className = 'vt-hudcfg-list';
  const kinds = z.id === 'off' ? HUD_KINDS.filter((k) => hudZoneOf(layout, k) === 'off') : layout[z.id];
  const pinned = z.id === 'left' ? PINNED_HUD_KINDS : [];
  for (const k of pinned) ul.appendChild(pinnedItemEl(k, info));
  for (const k of kinds) ul.appendChild(itemEl(k, z.id, layout, info, rerender));
  if (!kinds.length && !pinned.length) {
    const empty = document.createElement('li');
    empty.className = 'vt-hudcfg-empty';
    empty.textContent = z.empty;
    ul.appendChild(empty);
  }

  ul.addEventListener('dragover', (ev) => {
    if (![...ev.dataTransfer.types].includes('text/plain')) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    ul.classList.add('drop');
  });
  ul.addEventListener('dragleave', (ev) => { if (!ul.contains(ev.relatedTarget)) ul.classList.remove('drop'); });
  ul.addEventListener('drop', (ev) => {
    ev.preventDefault();
    ul.classList.remove('drop');
    const kind = ev.dataTransfer.getData('text/plain');
    if (!HUD_KINDS.includes(kind)) return;
    save(moveHudKind(layout, kind, z.id, z.id === 'off' ? undefined : dropIndex(ul, ev.clientY)), rerender);
  });

  section.append(h, ul);
  return section;
}

export function renderHudSection() {
  const root = document.createElement('div');
  root.className = 'vt-hudcfg';

  const draw = () => {
    const layout = current();
    const info = kindInfo();
    root.replaceChildren();

    const note = document.createElement('p');
    note.className = 'vt-set-note';
    note.textContent = t('화면 맨 아래 상태 막대에 보일 칩을 고릅니다. 에이전트 칩은 맨 왼쪽에 고정됩니다. 값이 없는 칩은 자리에 상관없이 보이지 않고, 자리가 모자라면 중요도가 낮은 칩부터 숨습니다. 이 기기에만 저장됩니다.');

    const previewWrap = document.createElement('div');
    previewWrap.className = 'vt-hudcfg-previewwrap';
    const cap = document.createElement('div');
    cap.className = 'vt-hudcfg-cap';
    cap.textContent = t('미리보기 (예시 값)');
    previewWrap.append(cap, previewEl(layout, info));

    root.append(note, previewWrap);
    for (const z of ZONES()) root.appendChild(zoneEl(z, layout, info, draw));

    const foot = document.createElement('div');
    foot.className = 'vt-hudcfg-foot';
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'vt-btn sm quiet';
    reset.textContent = t('기본값으로');
    reset.disabled = !String(settingGet(KEY) || '').trim();
    reset.addEventListener('click', () => { settingSet(KEY, ''); draw(); });
    foot.appendChild(reset);
    root.appendChild(foot);
  };

  draw();
  return root;
}
