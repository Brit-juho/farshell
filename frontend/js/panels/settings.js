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
import { activeSessionId, getSession } from '../core/store.js';
import { mountClients } from '../layout/clients.js';
import { loadImportedSkin, saveImportedSkin, clearImportedSkin, applyImportedTokens } from '../theme-custom.js';
import { setVtSkin } from '../theme.js';

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
  // N42(60-settings-palette.md §7) — 알림·음성 진단. 다섯 항목 전부 서버
  // API는 이미 있고(push.py·voice.py·system.py) 이 섹션은 그 배선일 뿐이다.
  { id: 'voice', label: '음성', custom: renderVoiceSection },
  // N9/N45(80-multihost-agents.md §2) — CLI별 승인 대기 감지 커버리지 표.
  { id: 'agents', label: '에이전트', custom: renderAgentsSection },
  { id: 'keymap', label: '키맵', custom: renderKeymapSection },
  // N14 — Ghostty/Warp 테마 가져오기. 기본 6스킨을 고르는 칩은 예전부터
  // 다른 자리(테마 줄·팔레트)에 있고, 여기는 "가져오기"만 다룬다.
  { id: 'appearance', label: '모양', custom: renderAppearanceSection },
  { id: 'security', label: '보안', custom: renderSecuritySection },
  // N13(80-multihost-agents.md §3) — 토글 자체는 스키마 항목이지만, 디스크
  // 사용량은 서버에 물어봐야 하는 값이라(보안 섹션과 같은 이유로) custom.
  { id: 'scrollback', label: '스크롤백', custom: renderScrollbackSection },
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
      // S5 검증에서 발견: 액션 id('palette')를 그대로 보여주고 있었다. 사용자는
      // id를 본 적이 없다 — 같은 화면에 있는 라벨('커맨드 팔레트')로 말해야 한다.
      const others = conflictIds
        .filter((x) => x !== b.id)
        .map((x) => keymap.getBinding(x)?.label || x);
      help.push(`충돌: '${others.join("', '")}'와 같은 조합입니다.`);
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

// ── 「음성」 (N42 · 알림·음성 진단) ──────────────────────────────────────
function toast(msg, type) {
  if (typeof window.showToast === 'function') window.showToast(msg, type);
}

function statusLine(r) {
  const el = document.createElement('div');
  el.className = 'vt-set-help';
  el.textContent = r;
  return el;
}

// 세션 패널의 「연결된 화면」(C3, clients.js)과 같은 mountClients()를 그대로
// 재사용한다 — 컴포넌트를 두 벌 만들지 않는다(작업 지시). 이 섹션을 떠날 때
// (다른 탭 클릭 · 패널 닫기) 반드시 cleanup을 불러야 폴링 타이머가 안 샌다.
let _voiceClientsCleanup = null;

function renderVoiceSection() {
  const frag = document.createDocumentFragment();

  // 1. 웹 푸시
  const pushBtn = document.createElement('button');
  pushBtn.type = 'button'; pushBtn.className = 'vt-set-reset'; pushBtn.textContent = '테스트 발송';
  const pushRow = row('웹 푸시', pushBtn);
  const pushStatus = statusLine('확인 중…');
  pushRow.querySelector('.vt-set-label').appendChild(pushStatus);
  frag.appendChild(pushRow);
  vtFetch('/api/push/status').then((r) => {
    if (!r) return;
    pushStatus.textContent = r.available
      ? `구독 ${r.subscriptions}대 · VAPID ${r.configured ? '확인됨' : '미설정'}`
      : '사용 불가 (pywebpush 미설치)';
  }).catch(() => { pushStatus.textContent = '상태를 확인할 수 없습니다.'; });
  pushBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/api/push/test', { method: 'POST' });
      toast(r.ok ? `발송됨 · 구독 ${r.sent}건` : '발송 실패');
    } catch (e) { toast(e.message || '발송 실패', 'error'); }
  });

  // 2. 작업 완료 알림 (Stop 훅 TTS 요약)
  const notifyBtn = document.createElement('button');
  notifyBtn.type = 'button'; notifyBtn.className = 'vt-set-reset'; notifyBtn.textContent = '소리 듣기';
  const notifyRow = row('작업 완료 알림', notifyBtn);
  const notifyStatus = statusLine('확인 중…');
  notifyRow.querySelector('.vt-set-label').appendChild(notifyStatus);
  frag.appendChild(notifyRow);
  vtFetch('/api/hooks/status').then((r) => {
    const events = r && r.events ? Object.entries(r.events) : [];
    if (!events.length) { notifyStatus.textContent = '훅 상태를 확인할 수 없습니다.'; return; }
    const ok = events.filter(([, s]) => s === 'ok').length;
    notifyStatus.textContent = `TTS 요약 · 훅 ${ok}/${events.length} 설치됨`;
  }).catch(() => { notifyStatus.textContent = '훅 상태를 확인할 수 없습니다.'; });
  notifyBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/api/notify/test', { method: 'POST' });
      toast(r.ok ? '알림을 보냈습니다' : (r.configured ? '발송 실패' : 'ntfy/텔레그램이 설정되지 않았습니다'));
    } catch (e) { toast(e.message || '발송 실패', 'error'); }
  });

  // 3. Whisper 모델 (STT 메모리 상주 여부)
  const preloadBtn = document.createElement('button');
  preloadBtn.type = 'button'; preloadBtn.className = 'vt-set-reset'; preloadBtn.textContent = '미리 적재';
  const unloadBtn = document.createElement('button');
  unloadBtn.type = 'button'; unloadBtn.className = 'vt-set-reset'; unloadBtn.textContent = '내리기';
  const sttBtns = document.createElement('div');
  sttBtns.className = 'vt-set-btns';
  sttBtns.append(preloadBtn, unloadBtn);
  const sttRow = row('Whisper 모델', sttBtns);
  const sttStatus = statusLine('확인 중…');
  sttRow.querySelector('.vt-set-label').appendChild(sttStatus);
  frag.appendChild(sttRow);
  function refreshStt() {
    vtFetch('/voice/stt/status').then((r) => {
      if (!r) return;
      sttStatus.textContent = !r.available ? '사용 불가' : (r.loaded ? `메모리 상주 · ${r.engine}` : '미적재');
      preloadBtn.disabled = !r.available || r.loaded;
      unloadBtn.disabled = !r.loaded;
    }).catch(() => { sttStatus.textContent = '상태를 확인할 수 없습니다.'; });
  }
  refreshStt();
  preloadBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/voice/stt/preload', { method: 'POST' });
      toast(r.loaded ? `적재됨 · ${r.engine}` : '적재 실패');
    } catch (e) { toast(e.message || '적재 실패', 'error'); }
    refreshStt();
  });
  unloadBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/voice/stt/unload', { method: 'POST' });
      toast(r.unloaded ? '내렸습니다' : '이미 내려가 있습니다');
    } catch (e) { toast(e.message || '내리기 실패', 'error'); }
    refreshStt();
  });

  // 4. 맥에서 음성만 쓰기 (로컬 마이크 — 서버에 상태 조회 API가 없어
  //    버튼 라벨은 클라이언트가 마지막 응답을 기억해 토글한다)
  const localBtn = document.createElement('button');
  localBtn.type = 'button'; localBtn.className = 'vt-set-reset'; localBtn.textContent = '시작';
  let localRunning = false;
  localBtn.addEventListener('click', async () => {
    try {
      if (!localRunning) {
        const r = await vtFetch('/voice/local/start', { method: 'POST' });
        if (r && r.error) { toast(r.reason || '시작할 수 없습니다', 'error'); return; }
        localRunning = true; localBtn.textContent = '중지';
        toast('맥 로컬 음성 입력을 시작했습니다');
      } else {
        const r = await vtFetch('/voice/local/stop', { method: 'POST' });
        localRunning = false; localBtn.textContent = '시작';
        toast(r && r.text ? `인식됨: ${r.text}` : '중지했습니다');
      }
    } catch (e) { toast(e.message || '실패', 'error'); }
  });
  frag.appendChild(row('맥에서 음성만 쓰기', localBtn,
    '터미널 화면 없이 맥 마이크만 켭니다 — 이어폰으로 조작할 때 씁니다.'));

  // 5. 연결된 화면 — clients.js의 기존 렌더러를 그대로 이식(중복 구현 금지).
  const clientsHost = document.createElement('div');
  frag.appendChild(clientsHost);
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
  const activeSess = getSession(activeSessionId());
  const activeTmux = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
  if (activeTmux) _voiceClientsCleanup = mountClients(clientsHost, activeTmux, activeSess && activeSess.remote);

  return frag;
}

// ── 「모양」 (N14 · Ghostty/Warp 테마 가져오기) ─────────────────────────────
//
// 파서·추론(theme-import.js)은 **지연 로드**한다 — 이 화면을 열기 전에는
// 필요 없는 코드이고, app.js 상한(300KiB)에 여유가 많지 않다.
function renderAppearanceSection() {
  const frag = document.createDocumentFragment();

  const title = document.createElement('div');
  title.className = 'vt-set-label';
  title.textContent = '테마 가져오기';
  frag.appendChild(title);

  const help = document.createElement('div');
  help.className = 'vt-set-help';
  help.textContent = 'Ghostty config나 Warp 테마 YAML을 붙여넣으면 터미널 팔레트와 '
    + 'UI 색을 함께 만들어 7번째 스킨으로 추가합니다. 대비가 모자란 색은 자동으로 보정하고, '
    + '그래도 기준에 못 미치면 아래에 알려 줍니다.';
  frag.appendChild(help);

  const ta = document.createElement('textarea');
  ta.className = 'vt-set-themeinput';
  ta.rows = 6;
  ta.placeholder = 'background = #1d2021\nforeground = #ebdbb2\npalette = 0=#282828 ...';
  frag.appendChild(ta);

  const actions = document.createElement('div');
  actions.className = 'vt-set-themeactions';
  const applyBtn = document.createElement('button');
  applyBtn.type = 'button'; applyBtn.className = 'vt-set-reset'; applyBtn.textContent = '가져와서 적용';
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button'; clearBtn.className = 'vt-set-reset'; clearBtn.textContent = '가져온 테마 삭제';
  actions.append(applyBtn, clearBtn);
  frag.appendChild(actions);

  const result = document.createElement('div');
  result.className = 'vt-set-help';
  frag.appendChild(result);

  const current = loadImportedSkin();
  clearBtn.disabled = !current;
  if (current) result.textContent = `현재 가져온 테마: ${current.name}`;

  applyBtn.addEventListener('click', async () => {
    const text = ta.value;
    if (!text.trim()) { toast('테마 내용을 붙여넣어 주세요', 'error'); return; }
    applyBtn.disabled = true;
    try {
      const mod = await import('../theme-import.js');
      const built = mod.buildImportedSkin(text);
      if (!built.ok) { result.textContent = built.reason; toast('가져오기 실패', 'error'); return; }
      saveImportedSkin(built.skin);
      applyImportedTokens(built.skin.tokens);
      setVtSkin(mod.IMPORTED_SKIN);
      clearBtn.disabled = false;
      const issues = built.issues || [];
      result.textContent = issues.length
        ? `적용했습니다(${built.skin.name}). 다만 대비가 모자란 색이 있습니다: `
          + issues.map((i) => `${i.label} ${i.ratio}:1(기준 ${i.target})`).join(', ')
        : `적용했습니다 — ${built.skin.name} (대비 기준 통과)`;
      toast('테마를 가져왔습니다');
    } catch (e) {
      result.textContent = '테마를 처리하지 못했습니다.';
      toast('가져오기 실패', 'error');
    } finally {
      applyBtn.disabled = false;
    }
  });

  clearBtn.addEventListener('click', () => {
    clearImportedSkin();
    setVtSkin('farshell');
    clearBtn.disabled = true;
    result.textContent = '가져온 테마를 삭제했습니다.';
  });

  return frag;
}

// ── 「에이전트」 (N9/N45 · 감지 커버리지 표) ────────────────────────────────
// N9 — `report`는 `fsh pane report`로 에이전트가 직접 알려주는 경로다.
// 훅과 같은 1차 신호라 신뢰도도 같은 「높음」이다.
const AGENT_PATH_LABEL = { hook: '훅', report: '자기보고', pty: 'PTY 패턴', none: '없음' };
const AGENT_TRUST_LABEL = { high: '높음', mid: '보통', low: '낮음' };

function renderAgentsSection() {
  const frag = document.createDocumentFragment();

  const title = document.createElement('div');
  title.className = 'vt-set-label';
  title.textContent = '감지 커버리지';
  frag.appendChild(title);

  const status = statusLine('확인 중…');
  frag.appendChild(status);

  const tableHost = document.createElement('div');
  tableHost.className = 'vt-set-covtable-host';
  frag.appendChild(tableHost);

  const note = document.createElement('div');
  note.className = 'vt-set-help';
  note.textContent = '"보통" 이하는 레일에서 승인 대기를 놓칠 수 있다는 뜻입니다. '
    + '훅이 없는 CLI는 래퍼에서 fsh pane report로 상태를 알리면 "높음"이 됩니다.';
  frag.appendChild(note);

  vtFetch('/api/agents/coverage').then((rows) => {
    if (!Array.isArray(rows) || !rows.length) {
      status.textContent = '커버리지 정보를 확인할 수 없습니다.';
      return;
    }
    status.remove();
    tableHost.appendChild(renderCoverageTable(rows));
  }).catch(() => { status.textContent = '커버리지 정보를 확인할 수 없습니다.'; });

  return frag;
}

function renderCoverageTable(rows) {
  const table = document.createElement('table');
  table.className = 'vt-set-covtable';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['CLI', '경로', '패턴 줄 수', '상태', '신뢰도']) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.dataset.trust = r.trust;

    const cli = document.createElement('td');
    cli.textContent = r.cli;
    cli.className = 'vt-set-covcli';

    const path = document.createElement('td');
    path.textContent = AGENT_PATH_LABEL[r.path] || r.path;
    // 자기보고는 "마지막으로 보고받은 때"가 곧 그 경로가 살아 있다는 증거다.
    if (r.path === 'report' && r.lastReport) {
      path.title = `마지막 보고: ${new Date(r.lastReport * 1000).toLocaleString()}`;
    }

    const lines = document.createElement('td');
    lines.textContent = String(r.patternLines);

    const states = document.createElement('td');
    states.textContent = (r.states && r.states.length) ? r.states.join(', ') : '—';

    const trust = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'vt-set-covtrust';
    badge.dataset.trust = r.trust;
    badge.textContent = AGENT_TRUST_LABEL[r.trust] || r.trust;
    trust.appendChild(badge);

    tr.append(cli, path, lines, states, trust);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

// ── 「보안」 ──────────────────────────────────────────────────────────────
// 60-settings-palette.md §2 — 비밀번호 · OTP · 기기 · 승격 상태.
//
// **전부 읽기 전용이다.** 비밀번호 설정·OTP 활성화·기기 폐기는 `fsh` CLI에서만
// 한다. 공개 터널 너머에서 인증 수단 자체를 바꿀 수 있으면, 그 인증으로 들어온
// 사람이 그 인증을 풀 수 있다는 뜻이라 여기서는 상태와 명령어만 안내한다.
function secRow(label, value, state, help) {
  const el = document.createElement('div');
  el.className = 'vt-set-secrow';
  const left = document.createElement('div');
  left.className = 'vt-set-label';
  left.textContent = label;
  if (help) {
    const h = document.createElement('div');
    h.className = 'vt-set-help';
    h.textContent = help;
    left.appendChild(h);
  }
  const badge = document.createElement('span');
  badge.className = 'vt-set-secstate';
  badge.dataset.state = state;
  badge.textContent = value;
  el.append(left, badge);
  return el;
}

function fmtWhen(ts) {
  if (!ts) return '기록 없음';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '기록 없음';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function renderSecuritySection() {
  const frag = document.createDocumentFragment();

  const authHost = document.createElement('div');
  authHost.className = 'vt-set-sechost';
  authHost.appendChild(statusLine('인증 상태 확인 중…'));
  frag.appendChild(authHost);

  const devTitle = document.createElement('div');
  devTitle.className = 'vt-set-label';
  devTitle.textContent = '등록 기기';
  frag.appendChild(devTitle);

  const devHost = document.createElement('div');
  devHost.className = 'vt-set-sechost';
  devHost.appendChild(statusLine('기기 목록 확인 중…'));
  frag.appendChild(devHost);

  const elevHost = document.createElement('div');
  elevHost.className = 'vt-set-sechost';
  elevHost.appendChild(statusLine('승격 상태 확인 중…'));
  frag.appendChild(elevHost);

  vtFetch('/api/auth/status').then((r) => {
    authHost.innerHTML = '';
    const pw = !!(r && r.password_set);
    authHost.appendChild(secRow(
      '웹 로그인 비밀번호',
      pw ? '설정됨' : '설정 안 됨',
      pw ? 'on' : 'off',
      pw ? null : "터미널에서 'fsh password'로 설정합니다.",
    ));
    const otp = !!(r && r.otp_enabled);
    authHost.appendChild(secRow(
      'OTP (새 기기 등록 관문)',
      otp ? '활성' : '비활성',
      otp ? 'on' : 'off',
      otp ? '처음 보는 기기를 등록할 때만 OTP를 요구합니다.'
          : "터미널에서 'fsh otp setup'으로 활성화합니다.",
    ));
  }).catch(() => { authHost.innerHTML = ''; authHost.appendChild(statusLine('인증 상태를 확인할 수 없습니다.')); });

  vtFetch('/api/devices').then((r) => {
    const rows = (r && Array.isArray(r.devices)) ? r.devices : [];
    devHost.innerHTML = '';
    if (!rows.length) {
      devHost.appendChild(statusLine('등록된 기기가 없습니다. 휴대폰에서 QR로 처음 접속하면 등록됩니다.'));
      return;
    }
    devHost.appendChild(renderDeviceTable(rows));
    devHost.appendChild(statusLine("기기 폐기는 터미널에서 'fsh device revoke <id>'로 합니다 — 그 기기의 세션도 함께 끊깁니다."));
  }).catch(() => { devHost.innerHTML = ''; devHost.appendChild(statusLine('기기 목록을 확인할 수 없습니다.')); });

  vtFetch('/api/auth/elevation').then((r) => {
    const until = (r && Number(r.elevated_until)) || 0;
    const left = until ? Math.max(0, Math.round((until * 1000 - Date.now()) / 60000)) : 0;
    elevHost.innerHTML = '';
    elevHost.appendChild(secRow(
      '승격 세션',
      until && left ? `승격됨 · ${left}분 남음` : '승격 안 됨',
      until && left ? 'on' : 'off',
      // ADR-27 — push/PR을 만들지 않기로 해서 승격을 요구하는 경로가 지금은 없다.
      // 이 문장이 없으면 "왜 항상 비활성이지?"로 읽힌다.
      '현재 이 기능을 쓰는 경로가 없습니다(ADR-27) — 승격을 요구하는 조작이 아직 없습니다.',
    ));
  }).catch(() => { elevHost.innerHTML = ''; elevHost.appendChild(statusLine('승격 상태를 확인할 수 없습니다.')); });

  return frag;
}

function renderDeviceTable(rows) {
  const table = document.createElement('table');
  table.className = 'vt-set-covtable vt-set-devtable';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['기기', 'id', '등록', '마지막 사용']) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const d of rows) {
    const tr = document.createElement('tr');
    if (d.current) tr.dataset.current = '1';

    const label = document.createElement('td');
    label.textContent = d.label || '기기';
    if (d.current) {
      const badge = document.createElement('span');
      badge.className = 'vt-set-devme';
      badge.textContent = '이 기기';
      label.appendChild(badge);
    }

    const id = document.createElement('td');
    id.className = 'vt-set-covcli';
    id.textContent = d.id || '—';

    const added = document.createElement('td');
    added.textContent = fmtWhen(d.added_at);

    const seen = document.createElement('td');
    seen.textContent = fmtWhen(d.last_seen);

    tr.append(label, id, added, seen);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

// ── 「스크롤백」 ──────────────────────────────────────────────────────────
// N13(80-multihost-agents.md §3) — 토글 자체는 core/settings.js가 즉시 반영하지만
// (다른 항목과 동일), 디스크 사용량은 서버 상태라 매번 새로 물어봐야 한다.
function fmtBytes(n) {
  if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + 'GB';
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + 'MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + 'KB';
  return n + 'B';
}

function renderScrollbackSection() {
  const frag = document.createDocumentFragment();
  frag.appendChild(row(
    '스크롤백 영속화',
    boolControl('scrollback.persist'),
    '재접속할 때: 최근 256KB만 (현재 — 이 설정과 무관하게 항상 그대로입니다). '
      + '켜면 출력을 서버 디스크에도 이어붙여 "더 불러오기"로 더 과거 출력을 볼 수 있게 됩니다. '
      + '타이핑한 입력은 저장되지 않습니다 — 출력 스트림만 기록합니다. 7일 뒤 자동 삭제.',
  ));
  const usageHost = document.createElement('div');
  usageHost.className = 'vt-set-sechost';
  usageHost.appendChild(statusLine('디스크 사용량 확인 중…'));
  frag.appendChild(usageHost);
  vtFetch('/api/scrollback/usage').then((r) => {
    usageHost.innerHTML = '';
    usageHost.appendChild(statusLine(`디스크 사용량: ${fmtBytes(r.bytes || 0)}`));
  }).catch(() => {
    usageHost.innerHTML = '';
    usageHost.appendChild(statusLine('디스크 사용량을 확인할 수 없습니다.'));
  });
  return frag;
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

  // U1 — 사용량 소스가 없으면 탭도 HUD 칩도 통째로 사라진다(2.0 게이팅 규칙).
  // 조용히 사라지는 건 의도지만 "왜 사라졌는지"를 볼 곳이 한 군데는 있어야
  // 한다 — 실제로 clauth가 schema 2로 올라가며 꺼진 걸 몇 주 동안 아무도
  // 몰랐다. 켜져 있으면 한 줄, 꺼져 있으면 이유까지 적는다.
  const usage = document.createElement('div');
  usage.className = 'vt-set-about';
  usage.textContent = '사용량 소스 확인 중…';
  frag.appendChild(usage);

  vtFetch('/api/capabilities').then((r) => {
    const cap = (r && r.usage) || {};
    usage.textContent = '';
    const title = document.createElement('div');
    title.className = 'vt-set-label';
    title.textContent = '사용량 소스';
    usage.appendChild(title);

    const line = document.createElement('div');
    line.className = 'vt-set-hookrow';
    line.dataset.state = cap.available ? 'ok' : 'add';
    line.textContent = cap.available
      ? `${cap.provider} — 사용 중 (프로필 ${cap.profiles || 0}개)`
      : `${cap.provider || 'none'} — 표시 안 함`;
    usage.appendChild(line);

    const reason = cap.available ? null : cap.reason;
    const hint = reason === 'disabled' ? '설정에서 껐습니다 (VT_USAGE_PROVIDER=none).'
      : reason === 'schema' ? `사용량 피드 형식(schema ${cap.schema_seen ?? '?'})을 이 버전이 모릅니다. `
        + `지원: ${(cap.schema_supported || []).join(', ') || '-'}. clauth 또는 FarShell을 올리세요.`
      : reason === 'permission' ? '사용량 피드를 읽을 권한이 없습니다 (~/.clauth/status.json).'
      : reason === 'broken' ? '사용량 피드가 깨져 있습니다 (쓰는 중일 수 있습니다).'
      : reason ? '사용량 피드(~/.clauth/status.json)가 없습니다.'
      : null;
    if (hint) {
      const h = document.createElement('div');
      h.className = 'vt-set-help';
      h.textContent = hint;
      usage.appendChild(h);
    }
  }).catch(() => { usage.textContent = '사용량 소스를 확인할 수 없습니다.'; });

  return frag;
}

// ── 패널 ──────────────────────────────────────────────────────────────────
let _activeSection = 'terminal';

function rerender() {
  const body = document.getElementById('vt-set-body');
  if (!body) return;
  // 「음성」 섹션을 떠나면 clients.js 폴링 타이머를 반드시 끊는다(rail.js의
  // _clientsCleanup과 같은 규칙 — 안 그러면 패널을 여닫을 때마다 쌓인다).
  if (_activeSection !== 'voice' && _voiceClientsCleanup) {
    _voiceClientsCleanup(); _voiceClientsCleanup = null;
  }
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
    onClose: () => { if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; } },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  rerender();
}


registerAction('settings.show', showSettings);
// S3에서 잡아둔 `Mod+,` 바인딩의 실제 주인이 이제 생겼다(그전엔 rail ⚙를
// 눌러주는 임시 배선이었다).
registerKey('settings', () => showSettings());
