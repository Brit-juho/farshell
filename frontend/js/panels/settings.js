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
import { renderItems, group } from './settings/controls.js';
import { createSectionPreview } from './settings/preview.js';
import { TERMINAL_GROUPS, SECTION_META, SEARCH_ITEMS, SEARCH_ALIASES } from './settings/index-data.js';
import { renderKeymapSection } from './settings/sections/keymap.js';
import { renderVoiceSection, cleanupVoiceClients } from './settings/sections/voice.js';
import { renderAppearanceSection } from './settings/sections/appearance.js';
import { renderAgentsSection } from './settings/sections/agents.js';
import { renderSecuritySection } from './settings/sections/security.js';
import { renderPermissionsSection } from './settings/sections/permissions.js';
import { renderAboutSection } from './settings/sections/about.js';
import { renderUpdateSection } from './settings/sections/update.js';
import { renderHudSection } from './settings/sections/hud.js';
import { getAction } from '../core/dom.js';
import { subscribePersistence } from '../core/settings.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';

const PANEL_ID = 'vt-settings';

// 섹션 정의 — 이름·설명·터미널 항목·검색 색인은 settings/index-data.js(순수 데이터, 빠른 열기
// 팔레트와 공유)에 있고, 여기서는 id로 렌더 함수만 붙인다. 「세션」은 rail 자체가 그 화면이라
// 여기서 중복 노출하지 않는다. 「음성」의 푸시 구독 토글·음성 전용 모드는 2026-09-18 레일 교체로
// 원래 자리(⋯ 메뉴 → legacy rail 플라이아웃)가 도달 불가능해져 이 섹션들로 옮겼다(voice.js/appearance.js 참고).
// 「테마 가져오기」(N14, Ghostty/Warp 파싱)는 2026-09-22 실사용 없어 제거.
function renderTerminalSection() {
  const frag = document.createDocumentFragment();
  for (const part of TERMINAL_GROUPS) {
    frag.appendChild(group(part.label, renderItems(part)));
  }
  return frag;
}

function renderAgentSection() {
  const frag = document.createDocumentFragment();
  frag.appendChild(group(t('감지 상태'), renderAgentsSection()));
  return frag;
}

// N42 알림·음성 진단(서버 API는 이미 있고 이 섹션은 배선), N9/N45 CLI별 승인 대기 감지 커버리지 표,
// N13 디스크 사용량(서버에 물어야 하는 값이라 보안 섹션과 같은 이유로 custom).
const SECTION_RENDERERS = {
  terminal: renderTerminalSection,
  voice: renderVoiceSection,
  agents: renderAgentSection,
  keymap: () => renderKeymapSection(rerender),
  appearance: renderAppearanceSection,
  hud: renderHudSection,
  permissions: renderPermissionsSection,
  security: renderSecuritySection,
  update: renderUpdateSection,
  about: renderAboutSection,
};
const SECTIONS = SECTION_META.map((meta) => ({ ...meta, custom: SECTION_RENDERERS[meta.id] }));


// ── 패널 ──────────────────────────────────────────────────────────────────
let _activeSection = 'terminal';
let _mobileHome = true;
let _persistenceUnsub = null;
// 오른쪽 미리보기 칸의 구독·옵서버 정리. 섹션을 옮기거나 패널이 닫힐 때 반드시 부른다.
let _previewDispose = null;
function disposePreview() { if (_previewDispose) { _previewDispose(); _previewDispose = null; } }

function isCompact() { return window.matchMedia('(max-width:719px)').matches; }
// 좁은 화면에는 메뉴바(HUD) 자체가 없다(DESIGN.md 「메뉴바 28px」) — 거기서 구성하는 섹션은 목록에서 뺀다.
const shownSections = () => (isCompact() ? SECTIONS.filter((s) => !s.desktopOnly) : SECTIONS);

function openTool(action) {
  // 모바일 더보기는 브라우저 history에 한 단계로 올라간다. 여기서 도구 화면으로
  // 갈 때는 뒤에 빈 설정 패널 이력을 남기지 않고 같은 단계에서 교체한다.
  closePanel(PANEL_ID, { history: 'replace' });
  getAction(action)?.();
}

function renderSearch() {
  const root = document.createElement('div'); root.className = 'vt-set-search';
  const input = document.createElement('input');
  input.type = 'search'; input.className = 'vt-set-search-input';
  input.placeholder = t('설정 검색'); input.setAttribute('aria-label', t('설정 검색'));
  const results = document.createElement('div'); results.className = 'vt-set-search-results';
  const update = () => {
    const query = input.value.trim().toLocaleLowerCase();
    results.innerHTML = '';
    if (!query) { results.hidden = true; return; }
    // 폰에서 안 보이는 섹션(상태 막대 · 활동 막대)의 항목은 검색에도 안 나온다.
    const shown = shownSections();
    const matches = SEARCH_ITEMS.filter((item) => {
      const section = shown.find((s) => s.id === item.sectionId);
      if (!section) return false;
      return `${item.label} ${SEARCH_ALIASES[item.label] || ''} ${section.label} ${section.description || ''}`.toLocaleLowerCase().includes(query);
    });
    results.hidden = false;
    if (!matches.length) {
      const empty = document.createElement('div'); empty.className = 'vt-set-search-empty'; empty.textContent = t('일치하는 설정이 없습니다.');
      results.appendChild(empty); return;
    }
    for (const item of matches.slice(0, 8)) {
      const section = SECTIONS.find((s) => s.id === item.sectionId);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'vt-set-search-result';
      button.innerHTML = `<strong>${item.label}</strong><small>${section.label}</small>`;
      button.addEventListener('click', () => {
        _activeSection = item.sectionId; _mobileHome = false;
        rerender(item.label);
      });
      results.appendChild(button);
    }
  };
  input.addEventListener('input', update);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && input.value) { input.value = ''; update(); e.stopPropagation(); }
  });
  root.append(input, results);
  return root;
}

function renderMobileHome(body, focusSearch = false) {
  body.classList.add('mobile-home');
  const home = document.createElement('div');
  home.className = 'vt-set-mobile-home';
  home.appendChild(renderSearch());
  const addList = (title, items) => {
    const h = document.createElement('h2'); h.className = 'vt-set-mobile-group'; h.textContent = title; home.appendChild(h);
    for (const item of items) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'vt-set-mobile-link';
      // 승인 대기가 있으면 「권한」 줄이 스스로 말한다(하단 내비의 배지와 같은 숫자) — 모바일에서는 이 줄이 권한 화면의 입구다.
      const pend = item.id === 'permissions' ? Number(window.vtAuthzPending) || 0 : 0;
      const desc = pend > 0 ? `${t('승인 대기 {n}건', { n: pend })} · ${item.description}` : item.description;
      b.innerHTML = `<span><strong>${item.label}</strong>${desc ? `<small>${desc}</small>` : ''}</span><span aria-hidden="true">›</span>`;
      b.addEventListener('click', () => {
        if (item.action) openTool(item.action);
        else { _activeSection = item.id; _mobileHome = false; rerender(''); }
      });
      home.appendChild(b);
    }
  };
  addList(t('도구'), [
    { label: t('파일'), description: t('업로드와 공유 링크'), action: 'files.show' },
    { label: t('포트'), description: t('실행 중인 로컬 서비스'), action: 'ports.show' },
    { label: t('사용량'), description: t('AI 도구 한도와 누적 사용량'), action: 'usage.open' },
  ]);
  addList(t('설정'), shownSections());
  body.appendChild(home);
  if (focusSearch) Promise.resolve().then(() => home.querySelector('.vt-set-search-input')?.focus());
}

function rerender(focusTarget = null) {
  const body = document.getElementById('vt-set-body');
  if (!body) return;
  // 「음성」 섹션을 떠나면 clients.js 폴링 타이머를 반드시 끊는다(rail.js의
  // _clientsCleanup과 같은 규칙 — 안 그러면 패널을 여닫을 때마다 쌓인다).
  if (_activeSection !== 'voice') cleanupVoiceClients();
  disposePreview();
  body.innerHTML = '';
  body.classList.remove('mobile-home');
  if (isCompact() && _mobileHome) { renderMobileHome(body, focusTarget === 'home'); return; }
  const nav = document.createElement('nav');
  nav.className = 'vt-set-nav';
  nav.setAttribute('aria-label', t('설정 항목'));
  nav.appendChild(renderSearch());
  for (const s of shownSections()) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-menu-item sm vt-set-navitem' + (s.id === _activeSection ? ' active' : '');
    if (s.id === _activeSection) b.setAttribute('aria-current', 'page');
    b.textContent = s.label;
    b.addEventListener('click', () => { _activeSection = s.id; rerender(''); });
    nav.appendChild(b);
  }
  const content = document.createElement('div');
  content.className = 'vt-set-content';
  const section = SECTIONS.find((s) => s.id === _activeSection) || SECTIONS[0];
  const intro = document.createElement('div'); intro.className = 'vt-set-intro';
  if (isCompact()) {
    const back = document.createElement('button'); back.type = 'button'; back.className = 'vt-btn sm quiet vt-set-back'; back.textContent = t('‹ 더보기');
    back.addEventListener('click', () => { _mobileHome = true; rerender('home'); }); intro.appendChild(back);
  }
  const h = document.createElement('h2'); h.className = 'vt-set-title'; h.tabIndex = -1; h.textContent = section.label;
  const p = document.createElement('p'); p.className = 'vt-set-description'; p.textContent = section.description;
  intro.append(h, p);
  content.appendChild(intro);
  content.appendChild(section.custom ? section.custom() : renderItems(section));
  body.append(nav, content);
  // 좁은 화면에는 미리보기 칸을 두지 않는다(CSS가 숨기지만 구독도 아예 만들지 않는다).
  const preview = isCompact() ? null : createSectionPreview(section.id, content);
  if (preview) { body.appendChild(preview.el); _previewDispose = preview.dispose; }
  if (focusTarget !== null) Promise.resolve().then(() => {
    const target = focusTarget && Array.from(content.querySelectorAll('.vt-set-label'))
      .find((el) => el.firstChild?.textContent === focusTarget);
    if (target) { target.tabIndex = -1; target.focus(); target.scrollIntoView?.({ block: 'center' }); }
    else h.focus();
  });
}

export function showSettings(section) {
  _mobileHome = !section;
  if (section) _activeSection = section;
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: t('설정'),
    // 머리: 톱니 16 + 「설정」 15/600, 오른쪽에 자동 저장 상태(리디자인 9단계, 시안 O04).
    headHTML: `<div class="vt-vw-title">${icon('settings', 16)}${t('설정')}</div><div class="vt-set-save" id="vt-set-save" role="status" aria-live="polite" data-state="saved">${t('변경 사항 자동 저장')}</div>`,
    bodyId: 'vt-set-body',
    extraClass: 'vt-settings vt-wide',
    onClose: () => { cleanupVoiceClients(); disposePreview(); if (_persistenceUnsub) { _persistenceUnsub(); _persistenceUnsub = null; } },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  _persistenceUnsub = subscribePersistence(({ status }) => {
    const el = document.getElementById('vt-set-save'); if (!el) return;
    el.dataset.state = status;
    el.textContent = status === 'saving' ? t('저장 중…') : status === 'failed' ? t('서버 저장 실패 · 다시 열면 되돌아갈 수 있음') : t('저장됨');
  });
  rerender();
}

// `settings.show` 액션과 `Mod+,` 바인딩 등록은 panels/settings-lazy.js에 있다
// — 부팅 시점에 등록돼야 하므로 지연 청크 안으로 들어가면 안 된다.
