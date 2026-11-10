// 설정 컨트롤 계약 — 스위치 · 세그먼트는 공용 빌더(panels/settings/controls.js) 한 곳에서만 나온다.
// 섹션이 자기 컨트롤을 따로 그리면(ON | OFF 알약·네이티브 체크박스·짧은 select) 이 시험이 잡는다.
// CSS 값(터치 44px · 포커스 링 · 상태 토큰)은 jsdom 이 스타일시트를 안 읽으므로 CSS 원문으로 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const ROOT = path.join(__dirname, '..', '..');
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const SETTINGS_PANEL_JS = path.join(__dirname, '../js/panels/settings.js');
const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');
const KEYMAP_JS = path.join(__dirname, '../js/core/keymap.js');
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');
const CONTROLS_JS = path.join(__dirname, '../js/panels/settings/controls.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = () => new Promise((r) => setImmediate(r));

async function build() {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = ''; window._tokenQuery = ''; window.fitAndResize = () => {};
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'PUT') return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  await importFresh(TOAST_JS, env.context, cache);
  const S = await importFresh(SETTINGS_JS, env.context, cache);
  await importFresh(KEYMAP_JS, env.context, cache);
  const P = await importFresh(SETTINGS_PANEL_JS, env.context, cache);
  const C = await importFresh(CONTROLS_JS, env.context, cache);
  return { window, document: window.document, S, P, C };
}

// 좁은 칸·동적 목록·행마다 열이 맞춰진 자리라 select 를 그대로 두는 곳(이유는 각 줄).
const SELECT_EXCEPTIONS = [
  '.vt-set-pt',            // 키맵 전달 대상 — 시안 O04-C 의 132px 고정 select
  '.vt-hudcfg-ctrl',       // 상태 막대 칩 구역 선택 — 드래그를 못 쓰는 사람의 키보드 대안(좁은 칸)
  '.vt-set-langsel',       // 화면 언어 — 시안이 select 로 둔다(바꾸면 페이지를 다시 불러온다)
  '.vt-authz-host',        // 권한 화면의 거르기 select(동적 종류 목록)
];

test('모든 설정 섹션: 옛 ON | OFF 알약·네이티브 체크박스가 없고, 짧은 3개 이하 select 도 없다', async () => {
  const { document: d, P, C } = await build();
  P.showSettings();
  const names = Array.from(d.querySelectorAll('.vt-set-navitem')).map((b) => b.textContent);
  assert.ok(names.length >= 9, names.join(','));
  for (const name of names) {
    Array.from(d.querySelectorAll('.vt-set-navitem')).find((b) => b.textContent === name).click();
    await flush(); await flush();
    const body = d.querySelector('.vt-set-content');
    assert.strictEqual(body.querySelectorAll('.vt-toggle').length, 0, `${name}: 옛 ON|OFF 알약(.vt-toggle)이 남아 있다`);
    const boxes = Array.from(body.querySelectorAll('input[type="checkbox"]')).filter((i) => !i.closest('.vt-authz-check'));
    assert.strictEqual(boxes.length, 0, `${name}: 네이티브 체크박스가 남아 있다(스위치를 쓴다)`);
    for (const sel of body.querySelectorAll('select')) {
      if (SELECT_EXCEPTIONS.some((x) => sel.closest(x))) continue;
      const texts = Array.from(sel.options).map((o) => o.textContent);
      assert.ok(!C.prefersSegment(texts), `${name}: 선택지 ${texts.join('/')} 는 세그먼트여야 한다`);
    }
  }
});

test('스위치: button · role=switch · aria-checked · 클릭으로 뒤집히고 스토어에 반영, 비활성이면 눌리지 않는다', async () => {
  const { document, C, S } = await build();
  const sw = C.boolControl('terminal.cursorBlink');
  document.body.appendChild(sw);
  assert.strictEqual(sw.tagName, 'BUTTON');             // 스페이스·엔터는 button 의 기본 동작
  assert.strictEqual(sw.type, 'button');
  assert.strictEqual(sw.getAttribute('role'), 'switch');
  assert.strictEqual(sw.className, 'vt-switch');
  assert.ok(sw.tabIndex >= 0);
  const before = S.get('terminal.cursorBlink');
  assert.strictEqual(sw.getAttribute('aria-checked'), String(before));
  sw.click();
  assert.strictEqual(sw.getAttribute('aria-checked'), String(!before));
  assert.strictEqual(S.get('terminal.cursorBlink'), !before);
  const off = C.switchButton({ checked: false, disabled: true });
  assert.strictEqual(off.disabled, true);
  let hit = 0;
  const live = C.switchButton({ checked: false, onChange: () => { hit += 1; } });
  live.click(); live.click();
  assert.strictEqual(hit, 2);
  assert.strictEqual(live.getAttribute('aria-checked'), 'false');
});

test('세그먼트: radiogroup · 칸마다 role=radio · roving tabindex · 방향키로 이동하며 선택', async () => {
  const { document, C } = await build();
  const picked = [];
  const seg = C.segmentedControl({ label: '시험', options: [['a', '가'], ['b', '나'], ['c', '다']], value: 'a', onChange: (v) => picked.push(v) });
  document.body.appendChild(seg);
  assert.strictEqual(seg.getAttribute('role'), 'radiogroup');
  const opts = Array.from(seg.querySelectorAll('[role="radio"]'));
  assert.deepEqual(opts.map((o) => o.getAttribute('aria-checked')), ['true', 'false', 'false']);
  assert.deepEqual(opts.map((o) => o.tabIndex), [0, -1, -1]);
  const key = (el, k) => el.dispatchEvent(new document.defaultView.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  key(opts[0], 'ArrowRight');
  assert.deepEqual(picked, ['b']);
  assert.deepEqual(opts.map((o) => o.getAttribute('aria-checked')), ['false', 'true', 'false']);
  assert.deepEqual(opts.map((o) => o.tabIndex), [-1, 0, -1]);
  key(opts[1], 'End');
  key(opts[2], 'ArrowRight');   // 끝에서 처음으로 감싼다
  assert.deepEqual(picked, ['b', 'c', 'a']);
  opts[1].click();
  assert.strictEqual(picked.at(-1), 'b');
  seg.vtSetDisabled(true);
  assert.strictEqual(seg.getAttribute('aria-disabled'), 'true');
  assert.ok(opts.every((o) => o.disabled));
});

test('선택 컨트롤 선택 규칙: 3개 이하 + 짧은 글자만 세그먼트, 그 밖은 select', async () => {
  const { C } = await build();
  assert.strictEqual(C.prefersSegment(['블록', '밑줄', '막대']), true);
  assert.strictEqual(C.prefersSegment(['문자 입력', '터미널 Meta 키']), true);
  assert.strictEqual(C.prefersSegment(['자동 (edge → 로컬)', '로컬 음성만', 'edge-tts']), false);
  assert.strictEqual(C.prefersSegment(['a', 'b', 'c', 'd']), false);
  assert.strictEqual(C.prefersSegment(['하나']), false);
});

// ── CSS 계약(원문) ────────────────────────────────────────────────────────
const css = (f) => fs.readFileSync(path.join(ROOT, 'styles', f), 'utf8');
const esc = (sel) => sel.replace(/[.[\]()]/g, '\\$&');
const rule = (src, sel) => { const m = src.match(new RegExp(`${esc(sel)}\\s*\\{([^}]*)\\}`)); return m ? m[1] : ''; };

test('CSS: 터치에서 스위치 누르는 영역 52×44 · 세그먼트 44, 데스크톱 스위치 트랙 28×16 · 세그먼트 28', () => {
  const comp = css('layers/components.css');
  const touch = comp.slice(comp.indexOf('@media (max-width: 719px), (pointer: coarse)', comp.indexOf('.vt-switch {')));
  assert.match(rule(comp, '.vt-switch'), /width:\s*36px;\s*height:\s*28px/);
  assert.match(rule(comp, '.vt-switch::before'), /width:\s*28px;\s*height:\s*16px/);
  assert.match(rule(comp, '.vt-segmented'), /height:\s*28px/);
  assert.match(touch, /\.vt-switch\s*\{[^}]*width:\s*52px;\s*height:\s*44px/);
  assert.match(touch, /\.vt-segmented\s*\{[^}]*height:\s*44px/);
});

test('CSS: 호버·눌림은 상태 토큰, 켜짐은 액센트 면 + 손잡이 이동, 포커스 링은 공용 :focus-visible 한 곳', () => {
  const comp = css('layers/components.css');
  assert.match(comp, /\.vt-switch:hover:not\(:disabled\)::before[^}]*--color-state-hover/);
  assert.match(comp, /\.vt-switch:active:not\(:disabled\)::before[^}]*--color-state-press/);
  assert.match(comp, /\.vt-segmented-opt:hover[^}]*--color-state-hover/);
  assert.match(comp, /\.vt-segmented-opt:active[^}]*--color-state-press/);
  assert.match(rule(comp, '.vt-switch[aria-checked="true"]::before'), /--color-acc-surface/);
  assert.match(rule(comp, '.vt-switch[aria-checked="true"]::after'), /--color-acc-ink/);
  const sw = comp.slice(comp.indexOf('.vt-switch {'), comp.indexOf('.vt-segmented {'));
  assert.doesNotMatch(sw, /outline\s*:/, '포커스 링을 컴포넌트가 따로 그리지 않는다');
  const reset = css('screens/00-tokens-reset.css');
  assert.match(reset, /\.vt-switch:focus-visible/);
  assert.match(reset, /\.vt-segmented-opt:focus-visible/);
});

test('스니펫 편집기의 「이 프로젝트에만」도 옛 ON | OFF 알약(.vt-toggle)이 아니라 스위치(.vt-switch)다', () => {
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, '../js/snippets.js'), 'utf8');
  assert.match(src, /class="vt-switch" role="switch" aria-checked="false" id="vt-sn-proj"/);
  assert.doesNotMatch(src, /class="vt-toggle"/, '옛 알약 마크업이 남아 있으면 화면마다 켜기/끄기 모양이 어긋난다');
});
