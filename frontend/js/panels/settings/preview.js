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
import { createActivityOrderEditor } from './sections/activity-order.js';

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
  const focus = focusPreview();
  f.root.appendChild(focus.el);
  return {
    el: f.root,
    dispose: () => {
      off();
      focus.dispose();
      content.removeEventListener('pointerover', over);
      content.removeEventListener('pointerout', out);
      content.removeEventListener('focusin', over);
      content.removeEventListener('focusout', out);
    },
  };
}

// 모양 › 집중 모드 — 정적인 작은 그림(시안 O04-B 오른쪽). 칸 둘과 「개입 1」 알약, 가장자리 감지 띠(왼쪽 · 위 · 아래)를 그리고,
// 「가장자리에 막대 띄우기」·「개입 표시」·「가장자리 반응 시간」을 바꾸면 그림과 아래 설명이 바로 따라 바뀐다.
// 실제 집중 모드를 켜지 않는다(이 창 뒤 화면을 바꾸지 않는다). 글자는 장식이라 보조 기술에는 설명 한 줄만 읽힌다.
function focusPreview() {
  const box = document.createElement('div');
  box.className = 'vt-setpv-focus';
  const cap = document.createElement('div');
  cap.className = 'vt-setpv-cap';
  const name = document.createElement('span');
  name.textContent = t('집중 모드 미리보기');
  cap.appendChild(name);
  const stage = document.createElement('div');
  stage.className = 'vt-setpv-fm';
  stage.setAttribute('role', 'img');
  const pane = (cls, lines) => {
    const p = document.createElement('div');
    p.className = `vt-setpv-fm-pane ${cls}`;
    for (const [tone, text] of lines) {
      const l = document.createElement('div');
      if (tone) l.dataset.tone = tone;
      l.textContent = text;
      p.appendChild(l);
    }
    return p;
  };
  // 고정 샘플(사용자의 실제 출력이 아니다) — 시안 O04-B와 같은 글.
  stage.append(
    pane('a', [['ok', '● Bash(npm test)'], ['ok', 'PASS session.test'], ['err', 'FAIL expiry.test'], ['warn', '✻ Refactoring…']]),
    pane('b', [['mag', '• Edited guide.md'], ['cyan', '■ Working']]),
  );
  const pill = document.createElement('span');
  pill.className = 'vt-setpv-fm-pill';
  pill.textContent = t('개입 {n}', { n: 1 });
  const edges = ['left', 'top', 'bottom'].map((side) => {
    const e = document.createElement('i');
    e.className = 'vt-setpv-fm-edge';
    e.dataset.side = side;
    return e;
  });
  stage.append(pill, ...edges);
  const foot = document.createElement('div');
  foot.className = 'vt-setpv-foot';
  box.append(cap, stage, foot);
  const paint = () => {
    const reveal = !!setting('ui.focus.edgeReveal');
    const showPill = !!setting('ui.focus.pill');
    const delay = String(setting('ui.focus.edgeDelay'));
    pill.hidden = !showPill;
    for (const e of edges) e.hidden = !reveal;
    const parts = [reveal
      ? (delay === '0'
        ? t('강조색 띠가 가장자리 감지 영역(8px)입니다. 닿는 즉시 막대가 나옵니다.')
        : t('강조색 띠가 가장자리 감지 영역(8px)입니다. {ms}ms 머무르면 막대가 나옵니다.', { ms: delay }))
      : t('가장자리에서 막대가 나오지 않습니다. 단축키나 개입 표시로만 돌아옵니다.')];
    if (!showPill) parts.push(t('승인 대기가 생겨도 알약을 띄우지 않습니다.'));
    parts.push(t('토스트는 집중 모드에서도 뜹니다.'));
    foot.textContent = parts.join(' ');
    stage.setAttribute('aria-label', t('집중 모드 미리보기'));
  };
  paint();
  const off = subscribe((changed) => {
    if (changed && Object.keys(changed).some((k) => k.startsWith('ui.focus.'))) paint();
  });
  return { el: box, dispose: off };
}

// 상태 막대 · 활동 막대 — 오른쪽 칸은 미리보기가 아니라 활동 막대 순서 편집기다(시안 O05). 칸 뼈대만 같이 쓴다.
function activityOrderSide() {
  const root = document.createElement('aside');
  root.className = 'vt-set-preview vt-set-side-actcfg';
  root.setAttribute('aria-label', t('활동 막대 순서'));
  const ed = createActivityOrderEditor('side');
  root.appendChild(ed.el);
  return { el: root, dispose: ed.dispose };
}

const BUILDERS = { terminal: terminalPreview, appearance: appearancePreview, hud: activityOrderSide };

/** 섹션에 미리보기가 있으면 { el, dispose }, 없으면 null. content는 섹션 내용 칸(칩 이벤트를 듣는다). */
export function createSectionPreview(sectionId, content) {
  const build = BUILDERS[sectionId];
  return build ? build(content) : null;
}
