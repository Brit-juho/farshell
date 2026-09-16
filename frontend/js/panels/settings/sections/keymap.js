import * as keymap from '../../../core/keymap.js';
import { row } from '../controls.js';

// ── 「키맵」 ──────────────────────────────────────────────────────────────
// `rerender`는 settings.js(패널 껍데기)가 넘긴다 — 섹션이 패널을 다시 import하면
// 순환이 된다. 되감기가 필요한 건 재바인딩 취소(Escape) 한 곳뿐이다.
export function renderKeymapSection(rerender) {
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
    combo.addEventListener('click', () => startRebind(b.id, combo, rerender));
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
function startRebind(id, btn, rerender) {
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

