import { vtSkins, vtSkinLabel, getVtSkin } from '../../../theme.js';
import { renderItems, group, row, toast } from '../controls.js';
import { icon } from '../../../ui/icons.js';
import { t, lang } from '../../../core/i18n.js';
import { timedFetch } from '../../../core/api.js';

// 화면 언어 — 서버 전역 값(VT_LANG, server/i18n.py)이라 설정 스토어 스키마가 아니다.
// 서버가 index.html을 내줄 때 언어를 박으므로 바꾼 뒤에는 새로고침해야 적용된다.
// 선택지 이름은 번역하지 않는다 — 읽지 못하는 언어로 화면이 바뀌었을 때도 돌아갈 길을
// 찾을 수 있어야 한다.
const LANG_LABELS = { ko: '한국어', en: 'English' };

function renderLangControl() {
  const wrap = document.createElement('div');
  wrap.className = 'vt-select-wrap';
  const sel = document.createElement('select');
  sel.className = 'vt-select';
  for (const [v, label] of Object.entries(LANG_LABELS)) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = label;
    sel.appendChild(opt);
  }
  sel.value = lang();
  sel.addEventListener('change', async () => {
    const next = sel.value;
    sel.disabled = true;
    try {
      // vtFetch를 쓰지 않는다: 호스트 전환 중이면 다른 맥으로 중계되는데, 화면을 내주는
      // 것은 이 서버라 이 서버의 언어를 바꿔야 한다.
      const tq = (window._tokenQuery || '').replace(/^[?&]/, '');
      // timedFetch는 주소를 바꾸지 않는다(중계 없음) — 마감과 도달 판정만 붙는다.
      const r = await timedFetch(`${window.API_BASE}/api/lang${tq ? `?${tq}` : ''}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(window.VT_TOKEN ? { Authorization: `Bearer ${window.VT_TOKEN}` } : {}),
        },
        body: JSON.stringify({ lang: next }),
      });
      if (!r.ok) throw new Error(String(r.status));
      location.reload();
    } catch (_) {
      sel.value = lang();
      sel.disabled = false;
      toast(t('언어를 저장하지 못했습니다'), 'error');
    }
  });
  wrap.appendChild(sel);
  wrap.insertAdjacentHTML('beforeend', icon('chevron-down', 12, 2));
  return wrap;
}

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
  skinTitle.textContent = t('스킨');
  frag.appendChild(skinTitle);
  frag.appendChild(renderSkinRow());
  // registerAction('theme.set', ...)이 data-action 위임으로 클릭을 처리한다
  // (core/dom.js) — 여기서 직접 리스너를 달 필요가 없다.

  // ADR-44 — 시트 높이·창 위치는 여기 안 낸다. 드래그가 그 값의 입력 수단이다.
  frag.appendChild(group(t('언어'), row(t('화면 언어'), renderLangControl(),
    t('웹 화면과 fsh 명령이 함께 따릅니다. 바꾸면 페이지를 다시 불러옵니다.'), { scope: 'global' })));

  frag.appendChild(group(t('파일 뷰어'), renderItems({ items: [
    { key: 'viewer.surface', label: t('파일 뷰어 표시 방식'), kind: 'select',
      labels: { sheet: t('상단 시트'), float: t('떠 있는 창'), pane: t('페인 (터미널 옆을 쪼갬)') },
      help: t('파일을 열었을 때 어디에 띄울지 고릅니다. 시트와 창은 터미널 크기를 바꾸지 않습니다.') },
    { key: 'files.tree.dense', label: t('파일 트리 촘촘하게'), kind: 'bool',
      help: t('트리 행 높이를 28px에서 22px로 줄입니다. 폰은 터치를 위해 늘 44px입니다.') },
  ] })));

  return frag;
}

