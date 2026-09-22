import { vtSkins, vtSkinLabel, getVtSkin } from '../../../theme.js';

// 2026-09-18 — 스킨 칩(#theme-row)의 유일한 자리였다. 48px 아이콘 레일
// (#vt-rail)이 새 레일(shell/Rail.tsx)로 대체되며 display:none이 됐는데, 그 ⚙
// 버튼이 열던 플라이아웃 안에만 이 칩이 있었다 — 화면에서 도달할 방법이 완전히
// 없어졌다(Mod+K 팔레트로 `.theme-chip`을 직접 읽어 우회만 가능했다). 정식
// 자리인 이 섹션으로 옮긴다. index.html의 옛 #theme-row는 함께 지웠다.
//
// theme.js의 syncThemeChips()를 여기서 부르지 않는다 — 이 함수가 반환하는
// row는 호출 시점엔 아직 문서에 안 붙은 DocumentFragment 안이라
// document.getElementById('theme-row')가 못 찾는다(settings.js가 반환값을
// 나중에 appendChild한다). 그래서 선택 표시(.sel)를 이 루프에서 직접
// 계산한다. 스킨을 바꾼 뒤(클릭 이후)의 재동기화는 theme.js의
// registerAction('theme.set') → setVtSkin → _syncThemeChips가 그때는
// 문서에 붙어 있는 이 같은 #theme-row를 찾아 정상 처리한다.
// 2026-09-22 — 「테마 가져오기」(N14, Ghostty/Warp 파싱) 섹션을 통째로
// 지웠다. 8일간 구현돼 있었지만 실사용 흔적이 없었고(워크스페이스에 저장된
// theme.skin이 한 번도 imported였던 적이 없음), 파서·저장·부팅 인라인
// 스크립트까지 걸친 별도 서브시스템이라 죽은 채로 두는 비용이 컸다
// (theme-import.js·theme-custom.js·해당 테스트 파일 전부 삭제).
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

export function renderAppearanceSection() {
  const frag = document.createDocumentFragment();

  const skinTitle = document.createElement('div');
  skinTitle.className = 'vt-set-label';
  skinTitle.textContent = '스킨';
  frag.appendChild(skinTitle);
  frag.appendChild(renderSkinRow());
  // registerAction('theme.set', ...)이 data-action 위임으로 클릭을 처리한다
  // (core/dom.js) — 여기서 직접 리스너를 달 필요가 없다.

  return frag;
}

