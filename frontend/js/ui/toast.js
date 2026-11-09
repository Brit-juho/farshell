// 통합 토스트 — picker.js(타입 지원·스택)와 grid.js(싱글톤·교체) 두 벌을 하나로.
// F5에서 classic script(IIFE)에서 ES 모듈로 전환.
//
// showToast는 window 브리지를 계속 유지한다 — clearWorkspace·VTPush·VTFavicon과
// 같은 이유(F5 계획서 예외 목록)가 아니라, voice.js가 app.js와 완전히 분리된
// 별도 번들이라 실제 import가 불가능하기 때문이다(자세한 사고 경위는
// frontend/js/voice/recording.js 상단 주석 참고 — 상태를 가진 모듈을 두 번째
// 번들에서 import하면 안 되는 이유와 같은 클래스의 문제. showToast 자체는
// mutable state가 없어 두 번 인라인돼도 안전하지만, 어차피 cross-bundle 호출을
// 위해 window 전역이 필요하므로 여기 있는 25곳 넘는 bare 호출부(대부분 이미
// ESM인 term/*.js·agent/*.js 등)를 전부 import로 바꾸는 건 이득 없는 손질이라
// 하지 않았다.
//
// 왜 통합했나 (실버그, F0에서 이미 정리됨):
//   같은 이름의 showToast 가 picker.js:71 과 grid.js:377 에 각각 있었고,
//   bootstrap manifest 순서상 grid.js(8번째)가 picker.js(6번째)를 덮어썼다.
//   grid.js 판은 두 번째 인자(type)를 아예 받지 않아서, picker/terminal/snippets
//   11곳이 넘기던 'error'/'success' 가 조용히 버려졌다 — 에러 토스트가 중립색으로
//   떴다. 게다가 grid.js 판은 #agent-toast 싱글톤이라 연속 토스트가 서로를 덮어썼다.
//
// key 옵션이 있는 이유:
//   잦게 갱신되는 진행 상황(이미지 업로드 "올리는 중…" → "경로 삽입됨")을 그대로
//   쌓으면 화면이 토스트로 덮인다. 같은 key 를 가진 토스트는 새 것이 옛 것을
//   제자리에서 교체하고(= 옛 싱글톤 동작), key 가 없으면 세로로 쌓인다(= 옛
//   picker 동작). 두 동작이 다 필요했던 것이지 한쪽이 틀렸던 게 아니다.
//
// ── 2.2.1: 어디에 띄우는가 ────────────────────────────────────────────────
// 2.2.0까지 모든 토스트는 화면 하단 **중앙**에 고정(bottom:96px)된 카드였다.
// 터미널에서 하단 중앙은 프롬프트 줄이고, 폰에서 그 자리는 keybar 대역이라
// (keybar는 bottom:52px + --kb-height) 카드가 z-index 200으로 키들 위에 앉았다.
// 게다가 96px은 keybar.js가 실측 변수(--kb-height/--kb-overlap)로 이미 걷어낸
// 바로 그 매직 넘버였다 — 토스트만 옛 상수를 들고 남아 있었다.
//
// 그래서 **등급에 따라 목적지를 나눈다**:
//   info/success  → 상태 막대(#vt-hud, 맨 아래 24px)의 메시지 칸에 잠시 뜬다(맨 왼쪽 에이전트 칩 바로
//                   오른쪽). body가 그 높이만큼 비워 둔 자리라 터미널을 1px도 가리지 않는다. 터미널 메시지가
//                   원래 있어야 할 자리다(tmux status line·vim echo area).
//   warn/error    → 상태 막대 위 오른쪽에 뜨는 카드. 읽어야 하는 것만 터미널을 덮는다.
//   compact(<720) → HUD가 아예 없는 폭이므로(DESIGN.md §7) 전부 카드.
//
// 투명도로 "덜 가리게" 하는 길은 택하지 않았다 — 뒤가 모노스페이스 글자라
// 반투명이면 양쪽 다 안 읽히고, backdrop-filter는 상시 리페인트되는 xterm
// 캔버스 위에서 매 프레임 다시 계산된다(그리고 DESIGN.md가 금지한다).
//
// 배치는 #vt-toasts 컨테이너(components.css)가 **한 곳에서만** 맡는다. 예전엔
// .vt-toast 자체에도 position:fixed 배치가 남아 있었는데(voice.js가 body에
// 직접 붙이던 시절의 잔재, D6에서 그 경로가 사라졌다) 정의가 두 벌이라 한쪽만
// 고치면 어긋났다 — 지웠다.
import { COMPACT_MAX } from '../layout/breakpoints.js';

const TYPE_CLASS = { info: 'info', error: 'err', success: 'ok', warn: 'warn' };
// 등급별 기본 지속. 성공은 이미 눈으로 본 걸 확인해 주는 말이라 빨리 비켜야
// 하고, 실패 문구는 길어서 읽을 시간이 필요하다(2.2.0까지는 전부 4000ms).
const DURATION = { info: 3000, success: 3000, warn: 6000, error: 6000 };
const EXIT_MS = 120;        // 퇴장 트랜지션(--dur-fast)과 같은 값
const MAX_CARDS = 3;        // 동시에 떠 있는 카드 상한
const HUD_TYPES = { info: 1, success: 1 };

const keyed = new Map();   // key -> element (제자리 교체용)
// 2.1.6 — 지금 화면에 떠 있는 토스트를 "타입+문구"로 찾는다. key를 안 넘긴
// 호출부가 같은 문구를 연달아 띄우면 예전에는 그대로 쌓였다(실브라우저 검증
// 중 "설정을 서버에 저장하지 못했습니다"가 셋까지 쌓인 걸 재현했다). 같은 말을
// 세 번 하는 건 정보가 아니라 잡음이라, 한 줄로 합치고 횟수를 붙인다.
// key가 있는 호출은 예전대로 제자리 교체 — 그쪽은 "최신 값으로 갱신"이 의도다.
const live = new Map();    // "type\u0000msg" -> element

function container() {
  let c = document.getElementById('vt-toasts');
  if (!c) {
    c = document.createElement('div');
    c.id = 'vt-toasts';
    // 터치 기기는 screenReaderMode 가 기본 ON 인데(M5) 정작 알림이 안 읽히던
    // 문제가 있었다. polite 로 두고, 에러만 개별 요소에서 alert 로 승격한다.
    c.setAttribute('aria-live', 'polite');
    document.body.appendChild(c);
  }
  return c;
}

// HUD 줄의 메시지 칸. index.html에 static으로 두지 않고 여기서 만든다 —
// #vt-hud는 Solid의 render() 루트라 mountHud가 컨테이너의 기존 자식을 비운다.
// 마운트 뒤에 덧붙이는 노드는 <For>가 관리하는 범위 밖이라 안전하고, 혹시
// 지워지더라도 다음 호출에서 다시 만들어진다.
//
// 칩을 DOM에서 빼지 않고 #vt-hud에 클래스만 얹어 숨기는 것도 같은 이유다 —
// Solid <For>의 노드를 뒤에서 지우면 실제 DOM과 어긋난다(Hud.tsx에 기록된 사고).
// fitChips가 쓰는 .overflow-hidden 토글과 같은 관용구.
function hudLine() {
  // HUD는 regular 티어부터만 존재한다(components.css의 min-width:720px).
  // 폭 경계는 layout/breakpoints.js 한 곳에만 있어야 한다(DESIGN.md §3).
  if (typeof window !== 'undefined' && window.innerWidth < COMPACT_MAX) return null;
  const bar = document.getElementById('vt-hud');
  if (!bar) return null;
  let el = document.getElementById('vt-hud-msg');
  if (!el || el.parentElement !== bar) {
    el = document.createElement('div');
    el.id = 'vt-hud-msg';
    el.hidden = true;
    // 상태 막대에서 낭독되는 곳은 이 칸 하나다(막대 전체는 role=region) — 칩이 바뀔 때마다 읽히면 소음이다.
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.addEventListener('click', () => retireHud(el));
    bindHoverPause(el);
    bar.appendChild(el);
  }
  return el;
}

// 손가락/마우스가 올라와 있는 동안은 타이머를 세운다 — 긴 실패 문구를 읽는
// 도중에 사라지면 다시 띄울 방법이 없다. 떠난 뒤에는 남은 시간이 아니라 처음
// 시간으로 다시 잰다(읽다 만 자리부터가 아니라 처음부터 읽을 것이므로).
function bindHoverPause(el) {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
  let fine = false;
  try { fine = window.matchMedia('(pointer: fine)').matches; } catch (_) { return; }
  if (!fine) return;
  el.addEventListener('mouseenter', () => clearTimeout(el._vtTimer));
  el.addEventListener('mouseleave', () => arm(el, el._vtMs));
}

function arm(el, ms) {
  clearTimeout(el._vtTimer);
  if (el._vtLeaving || !(ms > 0)) return;
  el._vtTimer = setTimeout(
    () => (el.id === 'vt-hud-msg' ? retireHud(el) : retire(el)),
    ms,
  );
}

function unregister(el) {
  if (el._vtKey && keyed.get(el._vtKey) === el) keyed.delete(el._vtKey);
  if (el._vtDedupKey && live.get(el._vtDedupKey) === el) live.delete(el._vtDedupKey);
}

// 자동 퇴장 — 120ms 페이드 뒤에 지운다. 사라지는 중인 토스트는 합치기·교체
// 대상에서 즉시 빠진다(맵에서 먼저 뺀다). 안 그러면 새 호출이 죽어 가는
// 엘리먼트에 문구를 써 넣고 그대로 같이 사라진다.
function retire(el) {
  if (!el || el._vtLeaving) return;
  el._vtLeaving = true;
  clearTimeout(el._vtTimer);
  unregister(el);
  el.classList.add('leaving');
  el._vtExit = setTimeout(() => { if (el.isConnected) el.remove(); }, EXIT_MS);
}

function retireHud(el) {
  if (!el || el.hidden || el._vtLeaving) return;
  el._vtLeaving = true;
  clearTimeout(el._vtTimer);
  el.classList.add('leaving');
  el._vtExit = setTimeout(() => clearHud(el), EXIT_MS);
}

function clearHud(el) {
  el = el || document.getElementById('vt-hud-msg');
  if (!el) return;
  clearTimeout(el._vtTimer);
  clearTimeout(el._vtExit);
  el._vtLeaving = false;
  el.classList.remove('leaving');
  el.hidden = true;
  el.textContent = '';
  el._vtDedupKey = null;
  el._vtKey = null;
  el._vtCount = 1;
  el.removeAttribute('data-tip');
  if (el.parentElement) el.parentElement.classList.remove('vt-hud-busy');
}

// 카드 상한. 넘치면 **가장 오래된 것**을 비우되, 실패(err/warn)는 정보(info/ok)
// 보다 나중에 비운다 — 정보가 에러를 밀어내면 등급이 뒤집힌다.
function enforceCap() {
  const alive = [...container().children].filter((n) => !n._vtLeaving);
  while (alive.length > MAX_CARDS) {
    const i = alive.findIndex(
      (n) => !n.classList.contains('err') && !n.classList.contains('warn'),
    );
    const victim = alive[i >= 0 ? i : 0];
    alive.splice(alive.indexOf(victim), 1);
    retire(victim);
  }
}

// 문구와 횟수 배지. 문구를 전용 span에 담는 이유는 HUD 줄에서 **문구만**
// 말줄임하고 배지는 남겨야 하기 때문이다(el.textContent에 직접 쓰면 배지가
// 지워지고, 말줄임은 배지까지 같이 먹는다).
function paintBody(el, text, count) {
  let t = el.querySelector(':scope > .vt-toast-text');
  if (!t) {
    t = document.createElement('span');
    t.className = 'vt-toast-text';
    el.prepend(t);
  }
  t.textContent = text;

  let badge = el.querySelector(':scope > .vt-toast-count');
  if (count > 1) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'vt-toast-count';
      el.appendChild(badge);
    }
    // 문구 자체는 건드리지 않는다 — "…(3회)"처럼 문장에 끼워 넣으면
    // 스크린리더가 매번 문장 전체를 다시 읽는다.
    badge.textContent = '×' + count;
  } else if (badge) {
    badge.remove();
  }
}

// showToast(메시지, 'info'|'success'|'warn'|'error', { key, duration })
//   key      — 같은 key 의 기존 토스트를 제자리에서 교체 (없으면 새로 쌓음)
//   duration — ms. 0 이하면 자동으로 사라지지 않음. 생략하면 등급별 기본값
export function showToast(msg, type, opts) {
  type = TYPE_CLASS[type] ? type : 'info';
  opts = opts || {};
  const ms = (typeof opts.duration === 'number' && isFinite(opts.duration))
    ? opts.duration : DURATION[type];
  const text = msg == null ? '' : String(msg);

  const key = opts.key || null;
  if (HUD_TYPES[type]) {
    const hud = hudLine();
    if (hud) return paintHud(hud, text, type, ms, key);
  }
  // 같은 key는 등급이 달라도 한 마디다. "이미지 업로드 중…"(info → HUD 줄)이
  // 실패로 끝나면 카드가 뜨는데, HUD 줄에 옛 진행 문구가 남아 있으면 한 동작이
  // 두 자리에서 서로 다른 말을 하게 된다.
  if (key) {
    const hud = document.getElementById('vt-hud-msg');
    if (hud && !hud.hidden && hud._vtKey === key) clearHud(hud);
  }
  return paintCard(text, type, ms, key);
}

function paintHud(el, text, type, ms, key) {
  // 사라지는 중에 새 메시지가 오면 퇴장을 취소한다.
  clearTimeout(el._vtExit);
  el._vtLeaving = false;
  el.classList.remove('leaving');

  const dedupKey = type + '\u0000' + text;
  const same = !el.hidden && el._vtDedupKey === dedupKey;
  el._vtCount = same ? (el._vtCount || 1) + 1 : 1;
  el._vtDedupKey = dedupKey;
  el._vtKey = key || null;

  paintBody(el, text, el._vtCount);
  el.dataset.tone = TYPE_CLASS[type];
  // 띠가 좁아 말줄임될 때를 위해 전문을 툴팁에 남긴다(DESIGN.md §10 .vt-tip).
  el.setAttribute('data-tip', text);
  el.hidden = false;
  if (el.parentElement) el.parentElement.classList.add('vt-hud-busy');

  el._vtMs = ms;
  arm(el, ms);
  return el;
}

function paintCard(text, type, ms, key) {
  const dedupKey = type + '\u0000' + text;

  let el = key ? keyed.get(key) : null;
  if (el && (!el.isConnected || el._vtLeaving)) { keyed.delete(key); el = null; }

  // key가 없을 때만 문구로 합친다. 이미 떠 있는 같은 토스트를 다시 띄우면
  // 새로 쌓지 않고 횟수만 올리고 타이머를 되감는다.
  if (!el && !key) {
    const same = live.get(dedupKey);
    if (same && same.isConnected && !same._vtLeaving) {
      same._vtCount = (same._vtCount || 1) + 1;
      paintBody(same, text, same._vtCount);
      same._vtMs = ms;
      arm(same, ms);
      return same;
    }
    if (same) live.delete(dedupKey);
  }

  if (!el) {
    el = document.createElement('div');
    el.className = 'vt-toast entering';
    el.addEventListener('click', () => retire(el));
    bindHoverPause(el);
    if (key) { el._vtKey = key; keyed.set(key, el); }
    container().appendChild(el);
    // 등장 애니메이션은 "처음 뜰 때" 한 번만이다 — 제자리 교체·횟수 갱신마다
    // 다시 튀면 DESIGN.md §0의 "화면에서 움직이는 건 하나"가 깨진다.
    setTimeout(() => el.classList.remove('entering'), EXIT_MS);
  }
  if (!key) { el._vtDedupKey = dedupKey; live.set(dedupKey, el); }

  // className을 통째로 덮지 않는다 — 'entering'이 지워져 등장 애니메이션이
  // 첫 프레임에서 잘린다.
  el.classList.add('vt-toast');
  el.classList.remove('info', 'err', 'ok', 'warn');
  el.classList.add(TYPE_CLASS[type]);
  el._vtCount = 1;
  paintBody(el, text, 1);
  el.setAttribute('role', type === 'error' || type === 'warn' ? 'alert' : 'status');

  el._vtMs = ms;
  arm(el, ms);
  enforceCap();
  return el;
}

// 프로그램에서 즉시 없앨 때 — 페이드 없이 그 자리에서 사라진다.
export function dismissToast(el) {
  if (!el) return;
  if (el.id === 'vt-hud-msg') { clearHud(el); return; }
  clearTimeout(el._vtTimer);
  clearTimeout(el._vtExit);
  unregister(el);
  if (el.isConnected) el.remove();
}

window.showToast = showToast;
window.dismissToast = dismissToast;
