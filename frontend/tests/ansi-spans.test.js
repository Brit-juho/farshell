// lib/ansi-spans.js — 세션 미리보기(팔레트 @ · 빈 칸 세션 선택)를 터미널 색 토큰 클래스로 그리는 해석기(리디자인 6단계).
// 입력은 세션 안 프로그램이 쓴 글자라 신뢰하지 않는다: 글자는 textContent, 색은 정해진 클래스 이름으로만.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const wins = [];
after(() => wins.forEach((w) => w.close()));

async function load() {
  const env = createDomEnv('<!doctype html><body><div id="host"></div></body>');
  wins.push(env.window);
  const mod = await importFresh(path.join(__dirname, '../js/lib/ansi-spans.js'), env.context, new Map());
  return { mod, document: env.window.document };
}

test('16색 · 굵게 · 흐림 · 반전 · 리셋을 토큰 클래스로 옮긴다', async () => {
  const { mod } = await load();
  const [line] = mod.parseAnsi('\x1b[32mok\x1b[0m \x1b[1;31mFAIL\x1b[22m x\x1b[39m \x1b[2mdim\x1b[0m \x1b[7mrev\x1b[27m \x1b[93mY\x1b[97mW');
  const pick = (text) => line.find((s) => s.text === text).cls;
  assert.equal(pick('ok'), 'ta-green');
  assert.equal(pick('FAIL'), 'ta-red ta-bold');
  assert.equal(pick(' x'), 'ta-red');
  assert.equal(pick('dim'), 'ta-dim');
  assert.equal(pick('rev'), 'ta-rev');
  assert.equal(pick('Y'), 'ta-amber');
  assert.equal(pick('W'), 'ta-bright');
});

test('256색 · 트루컬러는 가장 가까운 토큰으로 접고, 배경은 뜻 있는 넷만 남긴다', async () => {
  const { mod } = await load();
  const [line] = mod.parseAnsi('\x1b[38;5;196mR\x1b[38;5;46mG\x1b[38;2;80;140;255mB\x1b[38;5;244mgray\x1b[0m\x1b[42m+\x1b[45mm\x1b[0m');
  const cls = Object.fromEntries(line.map((s) => [s.text, s.cls]));
  assert.equal(cls.R, 'ta-red');
  assert.equal(cls.G, 'ta-green');
  assert.equal(cls.B, 'ta-blue');
  assert.ok(['', 'ta-dim'].includes(cls.gray));
  assert.equal(cls['+'], 'ta-bg-add');
  assert.equal(cls.m, '', '자홍 배경은 버린다');
});

test('만드는 클래스는 허용 목록 밖으로 나가지 않는다(임의 숫자 · 깨진 시퀀스)', async () => {
  const { mod } = await load();
  const noisy = Array.from({ length: 300 }, (_, i) => `\x1b[${i};${(i * 7) % 300}m${i}`).join('') + '\x1b[38;5m\x1b[38;2;1mX\x1b[';
  const allowed = new Set(mod.TOKEN_CLASSES);
  for (const line of mod.parseAnsi(noisy, { maxCols: 5000 })) {
    for (const s of line) for (const c of s.cls.split(' ').filter(Boolean)) assert.ok(allowed.has(c), c);
  }
});

test('SGR 밖의 이스케이프 · OSC · 제어 문자는 지우고, 빈 줄 뭉치는 하나로, 줄 수 · 줄 길이에 상한', async () => {
  const { mod } = await load();
  const text = '\x1b]0;title\x07\x1b]8;;https://x\x1b\\link\x1b]8;;\x1b\\\x1b[2J\x1b[Hab\x07c\n\n\n\nnext\n\n';
  const lines = mod.parseAnsi(text);
  assert.equal(JSON.stringify(lines.map((l) => l.map((s) => s.text).join(''))), JSON.stringify(['linkabc', '', 'next']));
  const many = Array.from({ length: 100 }, (_, i) => `line${i}`).join('\n');
  const tail = mod.parseAnsi(many, { maxLines: 6 });
  assert.equal(tail.length, 6);
  assert.equal(tail[5][0].text, 'line99');
  assert.equal(mod.parseAnsi('x'.repeat(1000), { maxCols: 50 })[0][0].text.length, 50);
});

test('renderAnsi는 글자를 textContent로만 넣어 마크업이 해석되지 않는다', async () => {
  const { mod, document } = await load();
  const host = document.getElementById('host');
  const n = mod.renderAnsi(host, '\x1b[31m<img src=x onerror=alert(1)>\x1b[0m<b>bold?</b>\n\x1b[32m"quoted"');
  assert.equal(n, 2);
  assert.equal(host.querySelector('img, b'), null);
  assert.equal(host.querySelector('.ta-red').textContent, '<img src=x onerror=alert(1)>');
  assert.ok(host.classList.contains('vt-ansi'));
  assert.equal(host.querySelectorAll('.vt-ansi-line').length, 2);
  assert.equal(host.querySelector('[style]'), null, '인라인 색을 쓰지 않는다');
});

test('fetchPreview는 짧은 사이 같은 세션을 다시 묻지 않고, 실패는 null', async () => {
  const { mod } = await load();
  let calls = 0;
  const ok = async (url) => { calls++; assert.match(url, /^\/api\/tmux\/preview\/a%20b\?lines=6&ansi=1$/); return { available: true, content: 'x' }; };
  assert.equal(await mod.fetchPreview(ok, 'a b', { lines: 6 }), 'x');
  assert.equal(await mod.fetchPreview(ok, 'a b', { lines: 6 }), 'x');
  assert.equal(calls, 1);
  assert.equal(await mod.fetchPreview(async () => { throw new Error('x'); }, 'zz', { lines: 6 }), null);
  assert.equal(await mod.fetchPreview(async () => ({ available: false, content: '' }), 'yy', { lines: 6 }), null);
});
