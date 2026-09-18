import { loadImportedSkin, saveImportedSkin, clearImportedSkin, applyImportedTokens } from '../../../theme-custom.js';
import { setVtSkin, vtSkins, vtSkinLabel, getVtSkin } from '../../../theme.js';
import { toast } from '../controls.js';

// 2026-09-18 — 스킨 칩(#theme-row)의 유일한 자리였다. 48px 아이콘 레일
// (#vt-rail)이 새 레일(shell/Rail.tsx)로 대체되며 display:none이 됐는데, 그 ⚙
// 버튼이 열던 플라이아웃 안에만 이 칩이 있었다 — 화면에서 도달할 방법이 완전히
// 없어졌다(Mod+K 팔레트로 `.theme-chip`을 직접 읽어 우회만 가능했다). 정식
// 자리인 이 섹션으로 옮긴다. index.html의 옛 #theme-row는 함께 지웠다.
//
// theme.js의 syncThemeChips()를 여기서 부르지 않는다 — 이 함수가 반환하는
// row는 호출 시점엔 아직 문서에 안 붙은 DocumentFragment 안이라
// document.getElementById('theme-row')가 못 찾는다(settings.js가 반환값을
// 나중에 appendChild한다). 그래서 선택 표시(.sel)와 가져온 스킨 라벨을 이
// 루프에서 직접 계산한다 — vtSkins()가 imported 스킨도 이미 포함해서 주므로
// 7번째 칩도 따로 만들 필요가 없다. 스킨을 바꾼 뒤(클릭 이후)의 재동기화는
// theme.js의 registerAction('theme.set') → setVtSkin → _syncThemeChips가
// 그때는 문서에 붙어 있는 이 같은 #theme-row를 찾아 정상 처리한다.
function renderSkinRow() {
  const row = document.createElement('div');
  row.className = 'theme-row';
  row.id = 'theme-row';
  const current = getVtSkin();
  for (const skin of vtSkins()) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'theme-chip';
    if (skin === current) chip.classList.add('sel');
    chip.dataset.skin = skin;
    chip.dataset.action = 'theme.set';
    chip.innerHTML = '<span class="dot"></span>';
    chip.appendChild(document.createTextNode(vtSkinLabel(skin)));
    row.appendChild(chip);
  }
  return row;
}

// ── 「모양」 (N14 · Ghostty/Warp 테마 가져오기) ─────────────────────────────
//
// 파서·추론(theme-import.js)은 **지연 로드**한다 — 이 화면을 열기 전에는
// 필요 없는 코드이고, app.js 상한(300KiB)에 여유가 많지 않다.
export function renderAppearanceSection() {
  const frag = document.createDocumentFragment();

  const skinTitle = document.createElement('div');
  skinTitle.className = 'vt-set-label';
  skinTitle.textContent = '스킨';
  frag.appendChild(skinTitle);
  frag.appendChild(renderSkinRow());
  // registerAction('theme.set', ...)이 data-action 위임으로 클릭을 처리한다
  // (core/dom.js) — 여기서 직접 리스너를 달 필요가 없다.

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
  applyBtn.type = 'button'; applyBtn.className = 'vt-btn sm quiet vt-set-reset'; applyBtn.textContent = '가져와서 적용';
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button'; clearBtn.className = 'vt-btn sm quiet vt-set-reset'; clearBtn.textContent = '가져온 테마 삭제';
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
      const mod = await import('../../../theme-import.js');
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

