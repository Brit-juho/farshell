// 접힌 세션 띠(링형, 시안 rail-specimen-v2 3-B) · 메뉴 크기(ui.side.scale) 규칙.
// 링 모양, 서브에이전트 배지 값, 크기별 치수(CSS ↔ side-prefs.js 일치), 애니메이션·폭 transition 규칙.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });
let _mod = null;
async function mod() { if (!_mod) { _mod = await loadSolidBuild(); _doms.push(_mod); } return _mod; }
const SIDE = pathToFileURL(path.join(__dirname, '../js/shell/side-prefs.js')).href;
const css = (f) => fs.readFileSync(path.join(__dirname, '../../styles/screens', f), 'utf8');
const store = (values) => ({ get: (k) => values[k], has: (k) => Object.prototype.hasOwnProperty.call(values, k) });

test('ringSvg — 상태마다 링 모양이 다르다(점선 점 · 파선 · 회전 호 · 실선), 알 수 없는 상태는 유휴', async () => {
  const { ringSvg } = await mod();
  const shapes = ['idle', 'waiting', 'working', 'done'].map((s) => ringSvg(s));
  assert.strictEqual(new Set(shapes).size, 4, '네 상태가 서로 다른 모양');
  assert.match(ringSvg('idle'), /stroke-dasharray="0\.1 4\.4"[^>]*stroke-linecap="round"/);
  assert.match(ringSvg('waiting'), /stroke-dasharray="5 3\.6"/);
  assert.match(ringSvg('working'), /<path d="M16 2a14 14 0 1 0 14 14"/);
  assert.ok(!/dasharray/.test(ringSvg('done')) && /<circle/.test(ringSvg('done')));
  assert.strictEqual(ringSvg('error'), ringSvg('idle'));
  assert.strictEqual(ringSvg(undefined), ringSvg('idle'));
  for (const s of shapes) assert.match(s, /aria-hidden="true"/, '링 도형은 장식 — 이름은 바깥 role=img가 준다');
});

test('subBadge — 작업 중 N(9+까지), 0이면 없음, 한도 도달은 「+」', async () => {
  const { subBadge } = await mod();
  const j = (v) => JSON.stringify(v);
  assert.strictEqual(subBadge(0, false), null);
  assert.strictEqual(j(subBadge(2, false)), j({ text: '2', limited: false }));
  assert.strictEqual(j(subBadge(9, false)), j({ text: '9', limited: false }));
  assert.strictEqual(j(subBadge(12, false)), j({ text: '9+', limited: false }));
  assert.strictEqual(j(subBadge(0, true)), j({ text: '+', limited: true }));
  assert.strictEqual(j(subBadge(3, true)), j({ text: '+', limited: true }));
});

test('메뉴 크기 — 크기별 치수(활동 막대 44/48/52 · 칸 36/40/44 · 기본 폭 320/360/392)가 CSS와 같다', async () => {
  const S = await import(SIDE);
  assert.deepStrictEqual(S.SIDE_SCALES, {
    small: { activity: 44, cell: 36, width: 320 },
    normal: { activity: 48, cell: 40, width: 360 },
    large: { activity: 52, cell: 44, width: 392 },
  });
  const wb = css('90-workbench.css');
  assert.match(wb, /:root \{ --vt-act-w:48px; --vt-cell:40px;/, '보통 = 지금 값');
  for (const [k, v] of Object.entries(S.SIDE_SCALES)) {
    if (k === 'normal') continue;
    assert.ok(wb.includes(`:root[data-vt-side-scale="${k}"] { --vt-act-w:${v.activity}px; --vt-cell:${v.cell}px; }`), `${k} 블록`);
  }
  // 하드코딩 48을 변수로 바꿨다 — 막대 폭·띠 폭이 같은 변수를 쓴다.
  assert.match(wb, /--vt-activity-nat:var\(--vt-act-w\)/);
  assert.match(wb, /data-vt-side="strip"\] \{ --vt-side-nat:var\(--vt-act-w\)/);
  assert.match(wb, /\.vt-activitybar \{\s*width:var\(--vt-act-w\)/);
  assert.ok(!/\.vt-activitybar \{\s*width:48px/.test(wb));
});

test('메뉴 크기 — 읽기: 모르는 값은 보통, 저장한 폭은 크기와 무관하게 그대로, 기본 폭만 따라간다', async () => {
  const S = await import(SIDE);
  assert.strictEqual(S.readSideScale(() => undefined), 'normal');
  assert.strictEqual(S.readSideScale(() => 'huge'), 'normal');
  assert.strictEqual(S.readSideScale((k) => (k === 'ui.side.scale' ? 'small' : undefined)), 'small');
  const none = store({});
  assert.deepStrictEqual(['small', 'normal', 'large'].map((sc) => S.readSideWidth(none.get, none.has, sc)), [320, 360, 392]);
  assert.strictEqual(S.readSideWidth(none.get, none.has), 360, '인자 없으면 지금과 같다');
  const saved = store({ 'ui.side.width': 300 });
  assert.deepStrictEqual(['small', 'normal', 'large'].map((sc) => S.readSideWidth(saved.get, saved.has, sc)), [300, 300, 300]);
  const fallback = store({ 'ui.dock.width': 420 });
  assert.strictEqual(S.readSideWidth(fallback.get, fallback.has, 'small'), 420, '옛 키 폴백 규칙 유지');
  const bad = store({ 'ui.side.width': 'x' });
  assert.strictEqual(S.readSideWidth(bad.get, bad.has, 'large'), 392, '깨진 저장값은 그 크기의 기본');
});

test('메뉴 크기 — 접힌 띠 폭은 크기를 따르고, 열린 폭·compact는 그대로', async () => {
  const S = await import(SIDE);
  assert.deepStrictEqual(['small', 'normal', 'large'].map((sc) => S.renderedSideWidth('strip', 360, 1440, 720, sc)), [44, 48, 52]);
  assert.strictEqual(S.renderedSideWidth('strip', 360, 1440), 48);
  assert.strictEqual(S.renderedSideWidth('open', 360, 1440, 720, 'large'), 360, '열린 폭은 저장값(기본은 readSideWidth가 정한다)');
  assert.strictEqual(S.renderedSideWidth('strip', 360, 390, 720, 'large'), 0);
});

test('메뉴 크기 — 설정 스키마(기기별 enum, 기본 보통)', async () => {
  const { SETTINGS_SCHEMA } = await mod();
  const spec = SETTINGS_SCHEMA['ui.side.scale'];
  assert.deepStrictEqual(Array.from(spec.values), ['small', 'normal', 'large']);
  assert.strictEqual(spec.def, 'normal');
  assert.strictEqual(spec.scope, 'device');
});

test('접힘 CSS — 애니메이션은 transform·opacity뿐, 모션 줄이기에서 정지, 폭·padding transition 없음', () => {
  const r = css('70-worktree-rail.css');
  const frames = [...r.matchAll(/@keyframes (vt-ring-\w+) \{([^@]*?)\}\s*(?=@|\/\*|\.|#|:)/g)];
  assert.deepStrictEqual(frames.map((m) => m[1]).sort(), ['vt-ring-breathe', 'vt-ring-spin']);
  assert.match(r, /@keyframes vt-ring-spin \{ to \{ transform:rotate\(360deg\); \} \}/);
  assert.match(r, /@keyframes vt-ring-breathe \{ 50% \{ opacity:\.55; \} \}/);
  assert.match(r, /@media \(prefers-reduced-motion: reduce\) \{ \.vt-wgrail-ring \.vt-ring \{ animation:none !important; \} \}/);
  const block = r.slice(r.indexOf('접힘(48px 띠, 링형'), r.indexOf('바닥 36'));
  assert.ok(!/transition\s*:/.test(block), '접힘 블록에 transition 없음');
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(block.replace(/rgb\([^)]*\)/g, '').replace(/\(#vt-wgrail[^)]*\)/g, '').replace(/#vt-wgrail/g, '')), '색 리터럴 없음');
});

test('접힘 CSS — 정렬(P안): 본문 위 여백이 펼침 첫 행 아이콘 중심에서 유도되고 구역 사이는 32', () => {
  const r = css('70-worktree-rail.css');
  assert.match(r, /padding-top:calc\(28px \+ 6px \+ var\(--vt-line1\) \/ 2 - var\(--vt-cell\) \/ 2\)/, '보통: 28 + 6 + 10 − 20 = 24');
  assert.match(r, /\.vt-wgrail-group-sep \{ flex:none; width:24px; height:28px;/, '28 + 칸 간격 2·2 = 32');
});

test('접힘 CSS — 에이전트 마크 래퍼는 margin:0 이다(펼침 행용 margin-right 6px가 마크를 링 중심에서 3px 밀어낸다)', () => {
  const r = css('70-worktree-rail.css');
  const m = r.match(/#vt-wgrail\.collapsed \.vt-srow\.vt-wgrail-row \.vt-wgrail-agent-compact \{([^}]*)\}/);
  assert.ok(m, '접힘 마크 규칙이 있다');
  assert.match(m[1], /inset:0/);
  assert.match(m[1], /margin:0/, 'components.css 의 .vt-wgrail-agent { margin-right } 를 접힘에서 지운다');
});
