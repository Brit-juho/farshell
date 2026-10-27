// 설정 화면 오른쪽 「미리보기」 칸 — 값을 바꾸는 순간 결과를 옆에서 보여 준다.
//
// 섹션마다 의미 있는 곳에만 붙인다(터미널·모양). 알림·보안처럼 미리 볼 것이 없는 섹션은
// null을 돌려줘 칸 자체가 열리지 않는다 — 빈 칸을 "미리보기 없음"으로 채우지 않는다.
//
// 이 모듈은 값을 **읽기만** 한다. 미리보기 때문에 설정을 바꾸거나 앱의 스킨을 일시로
// 바꾸지 않는다(스킨 토큰은 html[data-skin]에 걸려 있어 한 칸만 따로 입힐 수 없고, 앱 전체를
// 잠깐 바꾸면 화면이 번쩍인다). 대신 theme.js가 이미 가진 스킨별 xterm 팔레트로 칸 안에서만 그린다.
import { get as setting, subscribe } from '../../core/settings.js';
import { vtSkins, vtSkinLabel, getVtSkin, getVtXtermTheme, getVtXtermFont } from '../../theme.js';
import { t } from '../../core/i18n.js';
import { estimateCols, cursorClass, SAMPLE_LINES, resolveSkin } from './preview-data.js';

function mockTerminal({ theme, font, fontSize, cursor, blink }) {
  const box = document.createElement('div');
  box.className = 'vt-setpv-term';
  box.style.background = theme.background;
  box.style.color = theme.foreground;
  box.style.fontFamily = font;
  box.style.fontSize = `${fontSize}px`;
  for (const line of SAMPLE_LINES) {
    const l = document.createElement('div');
    l.className = 'vt-setpv-line';
    for (const tok of line) {
      if (tok.cursor) {
        const c = document.createElement('span');
        // is- 접두사: 맨 `.block` 클래스는 전역 유틸리티(display:block)와 겹쳐 커서가 한 줄 전체로 늘어난다.
        c.className = `vt-setpv-cursor is-${cursorClass(cursor)}${blink ? ' blink' : ''}`;
        c.style.setProperty('--vt-cursor', theme.cursor);
        c.style.setProperty('--vt-cursor-ink', theme.cursorAccent || theme.background);
        c.textContent = ' ';
        l.appendChild(c);
        continue;
      }
      const s = document.createElement('span');
      s.textContent = tok.text;
      if (tok.color) s.style.color = theme[tok.color];
      l.appendChild(s);
    }
    box.appendChild(l);
  }
  return box;
}

function frame(title) {
  const root = document.createElement('aside');
  root.className = 'vt-set-preview';
  root.setAttribute('aria-label', t('미리보기'));
  const cap = document.createElement('div');
  cap.className = 'vt-setpv-cap';
  const name = document.createElement('span');
  name.textContent = title;
  const note = document.createElement('span');
  note.className = 'vt-setpv-note';
  cap.append(name, note);
  const stage = document.createElement('div');
  stage.className = 'vt-setpv-stage';
  const foot = document.createElement('div');
  foot.className = 'vt-setpv-foot';
  root.append(cap, stage, foot);
  return { root, stage, note, foot };
}

// 터미널 — 글자 크기·커서 모양·깜빡임·스크롤백이 바뀌면 바로 다시 그린다.
function terminalPreview() {
  const f = frame(t('터미널 미리보기'));
  const paint = () => {
    const fontSize = Number(setting('terminal.fontSize')) || 14;
    const skin = getVtSkin();
    f.stage.replaceChildren(mockTerminal({
      theme: getVtXtermTheme(skin), font: getVtXtermFont(skin), fontSize,
      cursor: setting('terminal.cursorStyle'), blink: !!setting('terminal.cursorBlink'),
    }));
    const cols = estimateCols(f.stage.clientWidth, fontSize);
    f.note.textContent = `${fontSize}px`;
    f.foot.textContent = cols
      ? t('이 칸 기준 한 줄에 약 {n}자 · 스크롤백 {lines}줄', { n: cols, lines: Number(setting('terminal.scrollback')).toLocaleString() })
      : '';
  };
  const off = subscribe((changed) => {
    if (Object.keys(changed).some((k) => k.startsWith('terminal.') || k === 'theme.skin')) paint();
  });
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(paint) : null;
  Promise.resolve().then(() => { paint(); ro?.observe(f.stage); });
  return { el: f.root, dispose: () => { off(); ro?.disconnect(); } };
}

// 모양 — 스킨 칩에 마우스를 올리거나 포커스를 두면 그 스킨의 터미널이 보이고, 벗어나면 지금 스킨으로 돌아온다.
// 칩을 누르면 적용되는 것은 기존대로(theme.set 액션) — 이 칸은 누르기 전에 보여 주기만 한다.
function appearancePreview(content) {
  const f = frame(t('스킨 미리보기'));
  let hovered = null;
  const paint = () => {
    const skins = vtSkins();
    const current = getVtSkin();
    const skin = resolveSkin(hovered || current, skins);
    f.stage.replaceChildren(mockTerminal({
      theme: getVtXtermTheme(skin), font: getVtXtermFont(skin), fontSize: 14,
      cursor: setting('terminal.cursorStyle'), blink: false,
    }));
    f.note.textContent = vtSkinLabel(skin);
    f.foot.textContent = skin === current ? t('지금 쓰는 스킨') : t('누르면 적용됩니다');
  };
  const chipOf = (ev) => ev.target.closest?.('.theme-chip');
  const over = (ev) => { const c = chipOf(ev); if (c) { hovered = c.dataset.skin; paint(); } };
  const out = (ev) => { if (chipOf(ev)) { hovered = null; paint(); } };
  content.addEventListener('pointerover', over);
  content.addEventListener('pointerout', out);
  content.addEventListener('focusin', over);
  content.addEventListener('focusout', out);
  const off = subscribe((changed) => { if ('theme.skin' in changed) { hovered = null; paint(); } });
  Promise.resolve().then(paint);
  return {
    el: f.root,
    dispose: () => {
      off();
      content.removeEventListener('pointerover', over);
      content.removeEventListener('pointerout', out);
      content.removeEventListener('focusin', over);
      content.removeEventListener('focusout', out);
    },
  };
}

const BUILDERS = { terminal: terminalPreview, appearance: appearancePreview };

/** 섹션에 미리보기가 있으면 { el, dispose }, 없으면 null. content는 섹션 내용 칸(칩 이벤트를 듣는다). */
export function createSectionPreview(sectionId, content) {
  const build = BUILDERS[sectionId];
  return build ? build(content) : null;
}
