import { loadImportedSkin, saveImportedSkin, clearImportedSkin, applyImportedTokens } from '../../../theme-custom.js';
import { setVtSkin } from '../../../theme.js';
import { toast } from '../controls.js';

// ── 「모양」 (N14 · Ghostty/Warp 테마 가져오기) ─────────────────────────────
//
// 파서·추론(theme-import.js)은 **지연 로드**한다 — 이 화면을 열기 전에는
// 필요 없는 코드이고, app.js 상한(300KiB)에 여유가 많지 않다.
export function renderAppearanceSection() {
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

