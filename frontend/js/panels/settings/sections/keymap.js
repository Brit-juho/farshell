import { pushScope } from '../../../core/keyboard-scopes.js';
import * as keymap from '../../../core/keymap.js';
import { row, selectControl } from '../controls.js';
import { icon } from '../../../ui/icons.js';
import { t } from '../../../core/i18n.js';

// ── 「키맵」 ──────────────────────────────────────────────────────────────
// `rerender`는 settings.js(패널 껍데기)가 넘긴다 — 섹션이 패널을 다시 import하면
// 순환이 된다. 되감기가 필요한 건 재바인딩 취소(Escape) 한 곳뿐이다.
export function renderKeymapSection(rerender) {
  const frag = document.createDocumentFragment();
  const conflicts = keymap.conflicts();

  if (!keymap.isStandalone()) {
    const note = document.createElement('p');
    note.className = 'vt-set-note';
    const combos = ['Mod+W', 'Mod+T', 'Mod+N'].map((c) => keymap.displayComboHtml(c)).join('·');
    note.innerHTML = t('일부 조합({combos} 등)은 브라우저가 먼저 사용해 일반 탭에서는 지정할 수 없습니다. 앱으로 실행해도 OS나 브라우저가 먼저 사용하는 조합은 보장되지 않습니다.', { combos });
    frag.appendChild(note);
  }

  const note = document.createElement('p');
  note.className = 'vt-set-note';
  note.textContent = t('단축키는 이 기기 종류에 저장됩니다. Mac·Windows·Linux·모바일 설정은 서로 독립적입니다.');
  frag.appendChild(note);
  const option = selectControl('terminal.optionMode', { unicode: t('문자 입력'), meta: t('터미널 Meta 키') });
  frag.appendChild(row(t('Mac Option 키'), option, t('Meta 모드는 Option 조합을 터미널 명령에 사용합니다. 일반 특수문자 입력은 문자 입력 모드를 선택하세요.')));
  for (const b of keymap.list()) {
    const control = document.createElement('div');
    control.className = 'vt-set-keyrow';
    const browserPaste = keymap.isNativePasteBinding(b);

    const combo = document.createElement('button');
    combo.type = 'button';
    combo.className = 'vt-set-combo';
    combo.innerHTML = keymap.displayComboHtml(b.combo);
    combo.setAttribute('aria-label', keymap.displayCombo(b.combo));
    combo.title = t('클릭한 뒤 새 조합을 누르세요');
    combo.addEventListener('click', () => startRebind(b.id, combo, rerender));
    if (b.unavailable) combo.classList.add('unavailable');

    // passthrough — 이 화면에서 가장 중요한 컨트롤. `Mod+F` 같은 셸 키를
    // 사용자가 되찾을 수 있는 유일한 경로다.
    const pt = document.createElement('select');
    pt.className = 'vt-select vt-set-pt';
    pt.setAttribute('aria-label', t('{label} 전달 대상', { label: b.label }));
    for (const [value, label] of [['app', t('앱 동작')], ['app-and-terminal', t('앱 + 터미널')], ['terminal', t('터미널만')]]) {
      const o = document.createElement('option'); o.value = value; o.textContent = label; pt.appendChild(o);
    }
    pt.value = b.delivery;
    pt.addEventListener('change', () => keymap.setDelivery(b.id, pt.value).then(rerender));

    const deliveryWrap = document.createElement('div');
    deliveryWrap.className = 'vt-select-wrap';
    deliveryWrap.appendChild(pt);
    deliveryWrap.insertAdjacentHTML('beforeend', icon('chevron-down', 12, 2));

    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'vt-btn sm quiet vt-set-reset';
    reset.textContent = t('기본값');
    reset.addEventListener('click', () => keymap.reset(b.id).then(rerender));

    if (browserPaste) control.append(combo, reset);
    else control.append(combo, deliveryWrap, reset);

    const conflictIds = conflicts[keymap.normalize(b.combo)];
    const help = [];
    if (browserPaste) help.push(t('브라우저 붙여넣기를 우선하고, 지원하지 않으면 클립보드를 읽습니다.'));
    if (b.unavailable) help.push(t('이 브라우저 탭에서는 사용할 수 없습니다.'));
    if (conflictIds && conflictIds.length > 1) {
      // S5 검증에서 발견: 액션 id('palette')를 그대로 보여주고 있었다. 사용자는
      // id를 본 적이 없다 — 같은 화면에 있는 라벨('커맨드 팔레트')로 말해야 한다.
      const others = conflictIds
        .filter((x) => x !== b.id)
        .map((x) => keymap.getBinding(x)?.label || x);
      help.push(t("충돌: '{others}'와 같은 조합입니다.", { others: others.join("', '") }));
    }
    const r = row(b.label, control, help.join(' ') || undefined);
    if (conflictIds && conflictIds.length > 1) r.classList.add('conflict');
    frag.appendChild(r);
  }
  return frag;
}

// 재바인딩 — 버튼을 누르면 다음 키 조합 하나를 그대로 받는다.
let cancelRecording = null;
function startRebind(id, btn, rerender) {
  cancelRecording?.();
  btn.classList.add('recording');
  btn.textContent = t('키를 누르세요…');
  let dispose = () => {};
  const finish = () => { dispose(); btn.classList.remove('recording'); cancelRecording = null; };
  cancelRecording = finish;
  const onKey = (e) => {
    // 수식키만 눌린 상태는 무시한다(⌘를 누르는 도중에 확정되면 못 쓴다).
    if (['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return;
    e.preventDefault();
    e.stopPropagation();
    finish();
    if (e.key === 'Escape') { rerender(); return; }   // 취소
    const combo = keymap.comboFromEvent(e);
    const conflict = keymap.list().find((b) => b.id !== id && keymap.normalize(b.combo) === keymap.normalize(combo));
    if (conflict) {
      window.showToast?.(t('{label}와 같은 조합입니다. 다른 키를 선택하세요.', { label: conflict.label }), 'warn'); rerender(); return;
    }
    keymap.setBinding(id, combo).then(rerender);
  };
  dispose = pushScope({ id: 'keymap-recording', kind: 'recorder', element: btn, onKey,
    restoreFocus: false, onDispose: () => { btn.classList.remove('recording'); cancelRecording = null; } });
}
