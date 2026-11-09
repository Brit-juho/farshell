// F0: js/ui/toast.js — showToast 통합본.
//
// 이 파일이 지키는 실버그(회귀 방지):
//   예전엔 showToast 가 picker.js:71(타입 지원)과 grid.js:377(#agent-toast 싱글톤,
//   타입 미지원)에 두 벌 있었고 bootstrap manifest 순서상 grid.js 가 이겼다. 그래서
//   picker/terminal/snippets 11곳이 넘기던 'error'/'success' 가 조용히 버려졌고
//   (에러 토스트가 중립색으로 떴다), 싱글톤이라 연속 토스트가 서로를 덮어썼다.
//   → 아래 테스트는 "타입이 클래스로 반영되는가"와 "쌓이는가"를 둘 다 고정한다.
//   key 옵션(잦은 에이전트 도구 이벤트용 제자리 교체)도 함께 고정한다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

// 테스트 하네스 재설계(F5 백로그): stripEsm(import/export 구문을 정규식으로
// 지운 뒤 classic <script>로 주입)을 걷어내고, vm.SourceTextModule로 실제
// js/ui/toast.js를 그대로 링크·평가한다.
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// hud=true 면 #vt-hud(=regular 티어 이상)가 있는 화면을 흉내 낸다. 2.2.1부터
// info/success는 카드가 아니라 그 띠를 덮는다 — 터미널을 1px도 가리지 않는
// 자리다. #vt-hud가 없으면(compact) 예전처럼 전부 카드로 떨어진다.
async function buildWindow(opts) {
  const body = opts && opts.hud ? '<footer id="vt-hud"></footer>' : '';
  const env = createDomEnv(`<!doctype html><html><body>${body}</body></html>`);
  _doms.push(env.dom);
  const ns = await importFresh(TOAST_JS, env.context, new Map());
  return { window: env.window, showToast: ns.showToast, dismissToast: ns.dismissToast };
}

const toasts = (w) => [...w.document.querySelectorAll('#vt-toasts .vt-toast')];
const hudMsg = (w) => w.document.getElementById('vt-hud-msg');

test('타입이 CSS 클래스로 반영된다 (덮어써지던 실버그)', async () => {
  const { window, showToast } = await buildWindow();
  showToast('중립');
  showToast('실패', 'error');
  showToast('성공', 'success');

  const els = toasts(window);
  assert.strictEqual(els.length, 3);
  assert.ok(els[0].classList.contains('info'), 'info 클래스');
  assert.ok(els[1].classList.contains('err'), 'error → err 클래스');
  assert.ok(els[2].classList.contains('ok'), 'success → ok 클래스');
});

test('알 수 없는 타입은 info 로 떨어진다', async () => {
  const { window, showToast } = await buildWindow();
  showToast('??', 'nonsense');
  assert.ok(toasts(window)[0].classList.contains('info'));
});

test('연속 토스트가 서로를 덮어쓰지 않고 쌓인다', async () => {
  const { window, showToast } = await buildWindow();
  showToast('첫째');
  showToast('둘째');
  showToast('셋째');

  const els = toasts(window);
  assert.strictEqual(els.length, 3, '싱글톤이면 1개가 된다');
  assert.deepStrictEqual(els.map((e) => e.textContent), ['첫째', '둘째', '셋째']);
});

test('같은 key 는 제자리에서 교체된다 (잦은 에이전트 이벤트용)', async () => {
  const { window, showToast } = await buildWindow();
  const a = showToast('🔧 Bash 실행 중...', 'info', { key: 'agent' });
  const b = showToast('🔧 Edit 실행 중...', 'info', { key: 'agent' });

  assert.strictEqual(a, b, '같은 엘리먼트를 재사용해야 한다');
  const els = toasts(window);
  assert.strictEqual(els.length, 1);
  assert.strictEqual(els[0].textContent, '🔧 Edit 실행 중...');
});

test('key 있는 것과 없는 것이 공존한다', async () => {
  const { window, showToast } = await buildWindow();
  showToast('에이전트', 'info', { key: 'agent' });
  showToast('일반1');
  showToast('에이전트2', 'info', { key: 'agent' });
  showToast('일반2');

  const els = toasts(window);
  assert.strictEqual(els.length, 3, 'key 하나 + 일반 둘');
  assert.deepStrictEqual(els.map((e) => e.textContent), ['에이전트2', '일반1', '일반2']);
});

test('duration 이 지나면 사라지고, 0 이면 남는다', async () => {
  const { window, showToast } = await buildWindow();
  showToast('잠깐', 'info', { duration: 20 });
  showToast('영구', 'info', { duration: 0 });
  assert.strictEqual(toasts(window).length, 2);

  // 2.2.1 — 자동 퇴장은 즉시 제거가 아니라 120ms 페이드(.leaving)를 거친다.
  // 페이드가 도는 동안에도 합치기·교체 대상에서는 이미 빠져 있어야 한다.
  await new Promise((r) => setTimeout(r, 60));
  const leaving = toasts(window).filter((e) => e.classList.contains('leaving'));
  assert.deepStrictEqual(leaving.map((e) => e.textContent), ['잠깐'],
    'duration 이 지난 것만 퇴장에 들어간다');

  await new Promise((r) => setTimeout(r, 160));
  const els = toasts(window);
  assert.strictEqual(els.length, 1, '페이드가 끝나면 DOM에서도 사라진다');
  assert.strictEqual(els[0].textContent, '영구');
});

test('교체되면 기존 타이머가 갱신된다 (옛 타이머가 새 내용을 지우면 안 됨)', async () => {
  const { window, showToast } = await buildWindow();
  showToast('옛것', 'info', { key: 'k', duration: 30 });
  await new Promise((r) => setTimeout(r, 20));
  showToast('새것', 'info', { key: 'k', duration: 200 });

  await new Promise((r) => setTimeout(r, 40));   // 옛 타이머(30ms)는 이미 지났을 시점
  const els = toasts(window);
  assert.strictEqual(els.length, 1, '옛 타이머가 새 토스트를 지우면 안 된다');
  assert.strictEqual(els[0].textContent, '새것');
});

test('상태 막대 메시지 칸만 role=status + aria-live=polite (막대 전체는 아니다)', async () => {
  const { window, showToast } = await buildWindow({ hud: true });
  window.innerWidth = 1440;
  showToast('복사했습니다', 'success');
  const el = hudMsg(window);
  assert.ok(el, '넓은 화면의 success는 상태 막대 메시지 칸으로 간다');
  assert.strictEqual(el.getAttribute('role'), 'status');
  assert.strictEqual(el.getAttribute('aria-live'), 'polite');
  assert.strictEqual(window.document.getElementById('vt-hud').getAttribute('role'), null);
  assert.ok(window.document.getElementById('vt-hud').classList.contains('vt-hud-busy'));
});

test('에러만 role=alert, 나머지는 role=status (aria-live 보완)', async () => {
  const { window, showToast } = await buildWindow();
  showToast('보통');
  showToast('실패', 'error');
  const els = toasts(window);
  assert.strictEqual(els[0].getAttribute('role'), 'status');
  assert.strictEqual(els[1].getAttribute('role'), 'alert');
  assert.strictEqual(window.document.getElementById('vt-toasts').getAttribute('aria-live'), 'polite');
});

test('null/undefined 메시지에 죽지 않는다', async () => {
  const { window, showToast } = await buildWindow();
  showToast(null);
  showToast(undefined, 'error');
  assert.deepStrictEqual(toasts(window).map((e) => e.textContent), ['', '']);
});

test('dismissToast 로 즉시 제거할 수 있고, 같은 key 를 다시 쓸 수 있다', async () => {
  const { window, showToast, dismissToast } = await buildWindow();
  const a = showToast('A', 'info', { key: 'k', duration: 0 });
  dismissToast(a);
  assert.strictEqual(toasts(window).length, 0);

  const b = showToast('B', 'info', { key: 'k', duration: 0 });
  assert.notStrictEqual(a, b, '제거된 엘리먼트를 재사용하면 안 된다');
  assert.deepStrictEqual(toasts(window).map((e) => e.textContent), ['B']);
});

// ── 2.2.1 ────────────────────────────────────────────────────────────────
// 하단 중앙 고정 카드를 버리고 "등급에 따라 목적지를 나눈다"로 바꾼 판.
// 여기서 고정하는 계약은 넷이다 — warn 등급, 등급별 지속, 카드 상한과 축출
// 순서, 그리고 info/success의 HUD 줄 라우팅.

const alive = (w) => toasts(w).filter((e) => !e.classList.contains('leaving'));

test('warn 은 고유 등급이다 — info 로 떨어지지 않고 role=alert 를 받는다', async () => {
  const { window, showToast } = await buildWindow();
  showToast('설정을 서버에 저장하지 못했습니다', 'warn');
  const el = toasts(window)[0];
  assert.ok(el.classList.contains('warn'), 'warn 클래스');
  assert.ok(!el.classList.contains('info'), 'info 로 떨어지면 실패가 중립색으로 뜬다');
  assert.strictEqual(el.getAttribute('role'), 'alert');
});

test('기본 지속은 등급이 정한다 — 실패는 길게, 성공은 짧게', async () => {
  const { window, showToast } = await buildWindow();
  assert.strictEqual(showToast('정보')._vtMs, 3000);
  assert.strictEqual(showToast('성공', 'success')._vtMs, 3000);
  assert.strictEqual(showToast('경고', 'warn')._vtMs, 6000);
  assert.strictEqual(showToast('실패', 'error')._vtMs, 6000);
  assert.strictEqual(showToast('직접', 'error', { duration: 50 })._vtMs, 50,
    '명시한 duration 이 등급 기본값을 이긴다');
});

test('카드는 3개를 넘지 않고, 실패보다 정보를 먼저 비운다', async () => {
  const { window, showToast } = await buildWindow();
  showToast('실패', 'error');
  showToast('정보1');
  showToast('정보2');
  assert.strictEqual(alive(window).length, 3);

  showToast('정보3');
  assert.deepStrictEqual(alive(window).map((e) => e.textContent), ['실패', '정보2', '정보3'],
    '가장 오래된 정보가 빠진다 — 정보가 에러를 밀어내면 등급이 뒤집힌다');
});

test('탭/클릭하면 그 자리에서 사라진다', async () => {
  const { window, showToast } = await buildWindow();
  const el = showToast('치울 수 있어야 한다', 'error', { duration: 0 });
  el.dispatchEvent(new window.Event('click'));
  assert.strictEqual(alive(window).length, 0);
});

test('HUD 가 있으면 info/success 는 카드가 아니라 HUD 줄로 간다', async () => {
  const { window, showToast } = await buildWindow({ hud: true });
  showToast('복사됨', 'success');

  assert.strictEqual(toasts(window).length, 0, '터미널을 가리는 카드는 안 뜬다');
  const msg = hudMsg(window);
  assert.strictEqual(msg.hidden, false);
  assert.strictEqual(msg.textContent, '복사됨');
  assert.strictEqual(msg.dataset.tone, 'ok');
  assert.strictEqual(msg.getAttribute('data-tip'), '복사됨', '잘렸을 때 읽을 전문');
  assert.ok(window.document.getElementById('vt-hud').classList.contains('vt-hud-busy'),
    '칩은 DOM에서 빼지 않고 클래스로만 숨긴다(Solid <For> 노드)');
});

test('HUD 가 있어도 warn/error 는 카드로 뜬다 — 읽어야 하는 것만 터미널을 덮는다', async () => {
  const { window, showToast } = await buildWindow({ hud: true });
  showToast('세션 생성 실패', 'error');
  assert.deepStrictEqual(toasts(window).map((e) => e.textContent), ['세션 생성 실패']);
  assert.strictEqual(hudMsg(window), null, 'HUD 줄은 만들지도 않는다');
});

test('HUD 줄도 같은 문구를 합쳐 횟수를 붙인다', async () => {
  const { window, showToast } = await buildWindow({ hud: true });
  showToast('클립보드 동기화됨');
  showToast('클립보드 동기화됨');
  const msg = hudMsg(window);
  assert.strictEqual(msg.querySelector('.vt-toast-count').textContent, '×2');

  showToast('다른 말');
  assert.strictEqual(msg.querySelector('.vt-toast-count'), null, '문구가 바뀌면 횟수는 사라진다');
});

test('같은 key 는 등급이 달라도 한 마디 — 진행(HUD)이 실패(카드)로 끝나면 줄이 비워진다', async () => {
  const { window, showToast } = await buildWindow({ hud: true });
  showToast('이미지 업로드 중...', 'info', { key: 'paste-image' });
  assert.strictEqual(hudMsg(window).hidden, false);

  showToast('이미지 업로드 실패 (500)', 'error', { key: 'paste-image' });
  assert.strictEqual(hudMsg(window).hidden, true,
    '한 동작이 두 자리에서 서로 다른 말을 하면 안 된다');
  assert.deepStrictEqual(toasts(window).map((e) => e.textContent), ['이미지 업로드 실패 (500)']);
});
