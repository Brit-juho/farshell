// S4 — 설정 화면. 진입: `Mod+,`(키맵 레지스트리) 또는 rail ⚙.
//
// 지금까지 설정 표면은 **없었다**. 값은 있는데 UI가 없는 항목이 여럿이었고
// (`screenReaderMode`는 콘솔로만 바꿀 수 있었다), 있는 것도 ⋯ 메뉴 체크박스와
// 헤더 버튼으로 흩어져 있었다. 이 화면이 그 단일 표면이 된다.
//
// 렌더링 원칙:
//   - **스키마에서 그린다.** 항목을 여기 하드코딩하면 core/settings.js 스키마와
//     두 벌이 되어 어긋난다. 섹션 정의는 "어떤 키를 어떤 컨트롤로 보여줄지"만
//     정하고, 값·범위·기본값은 전부 스토어에서 읽는다.
//   - **변경 즉시 반영.** 저장 버튼이 없다(스토어가 즉시 반영 + 서버 저장).
//   - 못 하는 것은 **숨기지 말고 이유와 함께 보여준다** — 일반 브라우저 탭에서
//     쓸 수 없는 키 바인딩, 새 탭부터 적용되는 항목 등.
import { openPanel, closePanel } from './panel.js';
import { get as setting, set as setSetting, SCHEMA } from '../core/settings.js';
import * as keymap from '../core/keymap.js';
import { vtFetch } from '../core/api.js';
import { registerAction } from '../core/dom.js';
import { register as registerKey } from '../core/keymap.js';

const PANEL_ID = 'vt-settings';

// 섹션 정의 — 계획서(50-settings-keymap.md §3)의 9개 섹션 중, 이번 범위에서
// 실제로 조작 가능한 것만 넣는다. 「음성」·「알림」·「세션」은 각자 자기 패널이
// 이미 있거나(푸시 토글·음성 바) 서버 설정이라 여기서 중복 노출하지 않는다 —
// 빈 섹션을 보여주는 것보다 없는 편이 낫다(§4 "빈 패널을 보여주지 않는다").
const SECTIONS = [
  {
    id: 'terminal', label: '터미널',
    items: [
      { key: 'terminal.fontSize', label: '글자 크기', kind: 'range' },
      { key: 'terminal.cursorStyle', label: '커서 모양', kind: 'select',
        labels: { block: '블록', underline: '밑줄', bar: '막대' } },
      { key: 'terminal.cursorBlink', label: '커서 깜빡임', kind: 'bool' },
      { key: 'terminal.scrollback', label: '스크롤백 줄 수', kind: 'range', step: 500 },
    ],
  },
  {
    id: 'mouse', label: '마우스 · 선택',
    items: [
      { key: 'mouse.forwardToApp', label: '앱에 마우스 이벤트 전달',
        // S1 spike로 구현 가능함이 확인된 항목. 끄면 iTerm2 기본 동작이 된다.
        help: '끄면 vim·tmux가 마우스를 잡아도 항상 드래그로 선택할 수 있습니다 (iTerm2 기본 동작).',
        kind: 'bool' },
      { key: 'mouse.autocopyOnSelect', label: '드래그 시 자동 복사', kind: 'bool' },
      { key: 'mouse.touchTapToApp', label: '터치 탭을 앱에 전달',
        help: '터치 기기에서 짧은 탭을 앱으로 넘겨 커서를 옮깁니다.', kind: 'bool' },
    ],
  },
  {
    id: 'a11y', label: '접근성',
    items: [
      { key: 'a11y.screenReader', label: '스크린 리더 모드', kind: 'select',
        labels: { auto: '자동 (터치 기기에서 켬)', on: '항상 켬', off: '항상 끔' },
        help: '새로 여는 탭부터 적용됩니다 — 터미널 내부 구조가 달라져 실행 중에는 바꿀 수 없습니다.' },
    ],
  },
  { id: 'keymap', label: '키맵', custom: renderKeymapSection },
  { id: 'about', label: '정보', custom: renderAboutSection },
];

// ── 컨트롤 ────────────────────────────────────────────────────────────────
function row(label, controlEl, help) {
  const el = document.createElement('div');
  el.className = 'vt-set-row';
  const left = document.createElement('div');
  left.className = 'vt-set-label';
  left.textContent = label;
  if (help) {
    const h = document.createElement('div');
    h.className = 'vt-set-help';
    h.textContent = help;
    left.appendChild(h);
  }
  el.append(left, controlEl);
  return el;
}

function boolControl(key) {
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.className = 'vt-set-check';
  cb.checked = !!setting(key);
  cb.addEventListener('change', () => setSetting(key, cb.checked));
  return cb;
}

function rangeControl(key, step) {
  const spec = SCHEMA[key] || {};
  const wrap = document.createElement('div');
  wrap.className = 'vt-set-range';
  const input = document.createElement('input');
  input.type = 'range';
  input.min = spec.min ?? 0;
  input.max = spec.max ?? 100;
  input.step = step || 1;
  input.value = setting(key);
  const out = document.createElement('span');
  out.className = 'vt-set-value';
  out.textContent = input.value;
  input.addEventListener('input', () => { out.textContent = input.value; });
  // 드래그 중에는 화면만 갱신하고, 놓을 때 저장한다 — 안 그러면 슬라이더 한 번에
  // PUT이 수십 번 나간다(설정 스토어에는 디바운스가 없다. 있어야 할 곳은
  // 여기가 아니라 이 컨트롤이다).
  input.addEventListener('change', () => setSetting(key, input.value));
  wrap.append(input, out);
  return wrap;
}

function selectControl(key, labels) {
  const spec = SCHEMA[key] || {};
  const sel = document.createElement('select');
  sel.className = 'vt-set-select';
  for (const v of spec.values || []) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = (labels && labels[v]) || v;
    sel.appendChild(opt);
  }
  sel.value = setting(key);
  sel.addEventListener('change', () => setSetting(key, sel.value));
  return sel;
}

function renderItems(section) {
  const frag = document.createDocumentFragment();
  for (const item of section.items) {
    let control;
    if (item.kind === 'bool') control = boolControl(item.key);
    else if (item.kind === 'range') control = rangeControl(item.key, item.step);
    else control = selectControl(item.key, item.labels);
    frag.appendChild(row(item.label, control, item.help));
  }
  return frag;
}

// ── 「키맵」 ──────────────────────────────────────────────────────────────
function renderKeymapSection() {
  const frag = document.createDocumentFragment();
  const conflicts = keymap.conflicts();

  if (!keymap.isStandalone()) {
    const note = document.createElement('p');
    note.className = 'vt-set-note';
    note.textContent = '일부 조합(⌘W·⌘T·⌘N 등)은 브라우저가 먼저 사용해 일반 탭에서는 지정할 수 없습니다. 홈 화면에 추가해 앱으로 실행하면 사용할 수 있습니다.';
    frag.appendChild(note);
  }

  for (const b of keymap.list()) {
    const control = document.createElement('div');
    control.className = 'vt-set-keyrow';

    const combo = document.createElement('button');
    combo.type = 'button';
    combo.className = 'vt-set-combo';
    combo.textContent = keymap.displayCombo(b.combo);
    combo.title = '클릭한 뒤 새 조합을 누르세요';
    combo.addEventListener('click', () => startRebind(b.id, combo));
    if (b.unavailable) combo.classList.add('unavailable');

    // passthrough — 이 화면에서 가장 중요한 컨트롤. `Mod+F` 같은 셸 키를
    // 사용자가 되찾을 수 있는 유일한 경로다.
    const pt = document.createElement('label');
    pt.className = 'vt-set-pt';
    const ptBox = document.createElement('input');
    ptBox.type = 'checkbox';
    ptBox.checked = b.passthrough;
    ptBox.addEventListener('change', () => keymap.setPassthrough(b.id, ptBox.checked).then(rerender));
    pt.append(ptBox, document.createTextNode(' 터미널에도 전달'));

    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'vt-set-reset';
    reset.textContent = '기본값';
    reset.addEventListener('click', () => keymap.reset(b.id).then(rerender));

    control.append(combo, pt, reset);

    const conflictIds = conflicts[keymap.normalize(b.combo)];
    const help = [];
    if (b.unavailable) help.push('이 브라우저 탭에서는 사용할 수 없습니다.');
    if (conflictIds && conflictIds.length > 1) {
      help.push(`충돌: ${conflictIds.filter((x) => x !== b.id).join(', ')}와 같은 조합입니다.`);
    }
    const r = row(b.label, control, help.join(' ') || undefined);
    if (conflictIds && conflictIds.length > 1) r.classList.add('conflict');
    frag.appendChild(r);
  }
  return frag;
}

// 재바인딩 — 버튼을 누르면 다음 키 조합 하나를 그대로 받는다.
function startRebind(id, btn) {
  btn.classList.add('recording');
  btn.textContent = '키를 누르세요…';
  const onKey = (e) => {
    // 수식키만 눌린 상태는 무시한다(⌘를 누르는 도중에 확정되면 못 쓴다).
    if (['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return;
    e.preventDefault();
    e.stopPropagation();
    document.removeEventListener('keydown', onKey, true);
    btn.classList.remove('recording');
    if (e.key === 'Escape') { rerender(); return; }   // 취소
    keymap.setBinding(id, keymap.comboFromEvent(e)).then(rerender);
  };
  document.addEventListener('keydown', onKey, true);
}

// ── 「정보」 ──────────────────────────────────────────────────────────────
function renderAboutSection() {
  const frag = document.createDocumentFragment();
  const hooks = document.createElement('div');
  hooks.className = 'vt-set-about';
  hooks.textContent = 'Claude Code 훅 상태 확인 중…';
  frag.appendChild(hooks);

  // A0 연동 — 훅이 등록돼 있지 않으면 상태 배지·큐 자동 투입·TTS가 전부 조용히
  // 동작하지 않는다. "왜 아무 일도 안 일어나지"의 1번 원인이라 여기 보여준다.
  vtFetch('/api/hooks/status').then((r) => {
    const rows = r && r.events ? Object.entries(r.events) : [];
    if (!rows.length) { hooks.textContent = '훅 상태를 확인할 수 없습니다.'; return; }
    hooks.textContent = '';
    const title = document.createElement('div');
    title.className = 'vt-set-label';
    title.textContent = 'Claude Code 훅';
    hooks.appendChild(title);
    for (const [event, state] of rows) {
      const line = document.createElement('div');
      line.className = 'vt-set-hookrow';
      line.textContent = `${event} — ${state === 'ok' ? '등록됨' : state === 'add' ? '미등록' : '다른 경로'}`;
      line.dataset.state = state;
      hooks.appendChild(line);
    }
    if (rows.some(([, s]) => s !== 'ok')) {
      const hint = document.createElement('div');
      hint.className = 'vt-set-help';
      hint.textContent = "터미널에서 'fsh hooks install'을 실행하면 등록됩니다. 등록 전에는 상태 배지·프롬프트 큐 자동 투입·TTS 요약이 동작하지 않습니다.";
      hooks.appendChild(hint);
    }
  }).catch(() => { hooks.textContent = '훅 상태를 확인할 수 없습니다.'; });

  return frag;
}

// ── 패널 ──────────────────────────────────────────────────────────────────
let _activeSection = 'terminal';

function rerender() {
  const body = document.getElementById('vt-set-body');
  if (!body) return;
  body.innerHTML = '';
  const nav = document.createElement('div');
  nav.className = 'vt-set-nav';
  for (const s of SECTIONS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-set-navitem' + (s.id === _activeSection ? ' active' : '');
    b.textContent = s.label;
    b.addEventListener('click', () => { _activeSection = s.id; rerender(); });
    nav.appendChild(b);
  }
  const content = document.createElement('div');
  content.className = 'vt-set-content';
  const section = SECTIONS.find((s) => s.id === _activeSection) || SECTIONS[0];
  content.appendChild(section.custom ? section.custom() : renderItems(section));
  body.append(nav, content);
}

export function showSettings() {
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: '설정',
    headHTML: '<div class="vt-vw-title">설정</div>',
    bodyId: 'vt-set-body',
    extraClass: 'vt-settings',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  rerender();
}

export function closeSettings() { closePanel(PANEL_ID); }

registerAction('settings.show', showSettings);
// S3에서 잡아둔 `Mod+,` 바인딩의 실제 주인이 이제 생겼다(그전엔 rail ⚙를
// 눌러주는 임시 배선이었다).
registerKey('settings', () => showSettings());
