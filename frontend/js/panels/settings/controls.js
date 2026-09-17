// 설정 화면의 공유 컨트롤·포맷 헬퍼 (S4).
//
// 원래 panels/settings.js 한 파일에 섹션 아홉 개가 870줄로 나열돼 있었다.
// 섹션을 파일로 쪼개면서, 여러 섹션이 같이 쓰는 것만 여기로 모은다.
//
// **컨트롤은 스키마에서 그린다** — 항목을 하드코딩하면 core/settings.js 스키마와
// 두 벌이 되어 어긋난다. 값·범위·기본값은 전부 스토어에서 읽는다.
import { get as setting, set as setSetting, SCHEMA } from '../../core/settings.js';
import { icon } from '../../ui/icons.js';

export function row(label, controlEl, help) {
  const el = document.createElement('div');
  el.className = 'vt-set-row';
  const left = document.createElement('div');
  left.className = 'vt-set-label';
  left.textContent = label;
  if (help) {
    const h = document.createElement('div');
    h.className = 'vt-set-help';
    h.textContent = help;
    left.appendChild(h);
  }
  el.append(left, controlEl);
  return el;
}

// 2.1.6 — 네이티브 체크박스에서 [ON][OFF] 세그먼트로 바꿨다
// (20-design-system.md §3: "ON/OFF가 글자로 먼저 읽히는 세그먼트, 스위치 아님").
// 체크박스는 "켜짐"만 표시하고 꺼짐은 빈 네모라, 이 화면처럼 항목이 세로로
// 늘어선 곳에서는 무엇이 꺼져 있는지 훑어서 알기 어려웠다.
//
// role="switch" + aria-checked를 쓰는 이유: <button>이라 스페이스·엔터가 브라우저
// 기본으로 동작하고, 스크린리더가 "켜짐/꺼짐"을 그대로 읽는다. div로 만들었다면
// 키보드 처리와 포커스 관리를 전부 직접 짜야 하고 대개 빠뜨린다.
export function boolControl(key) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'vt-toggle';
  btn.setAttribute('role', 'switch');
  const on = document.createElement('span');
  on.textContent = 'ON';
  const off = document.createElement('span');
  off.textContent = 'OFF';
  btn.append(on, off);
  const paint = (v) => { btn.setAttribute('aria-checked', v ? 'true' : 'false'); };
  paint(!!setting(key));
  btn.addEventListener('click', () => {
    const next = btn.getAttribute('aria-checked') !== 'true';
    paint(next);
    setSetting(key, next);
  });
  return btn;
}

export function rangeControl(key, step) {
  const spec = SCHEMA[key] || {};
  const wrap = document.createElement('div');
  wrap.className = 'vt-range';
  const input = document.createElement('input');
  input.type = 'range';
  input.min = spec.min ?? 0;
  input.max = spec.max ?? 100;
  input.step = step || 1;
  input.value = setting(key);
  const out = document.createElement('span');
  out.className = 'vt-range-value';
  out.textContent = input.value;
  input.addEventListener('input', () => { out.textContent = input.value; });
  // 드래그 중에는 화면만 갱신하고, 놓을 때 저장한다 — 안 그러면 슬라이더 한 번에
  // PUT이 수십 번 나간다(설정 스토어에는 디바운스가 없다. 있어야 할 곳은
  // 여기가 아니라 이 컨트롤이다).
  input.addEventListener('change', () => setSetting(key, input.value));
  wrap.append(input, out);
  return wrap;
}

// <select>는 그대로 두고 OS 화살표만 지운다(appearance:none) — 직접 만든
// 드롭다운으로 바꾸면 키보드 탐색·타이핑 점프·모바일 네이티브 피커를 전부
// 다시 짜야 하고, 그렇게 만든 것이 네이티브보다 나은 경우는 드물다.
// 화살표는 아이콘 레지스트리의 chevron-down 하나를 쓴다(라이브러리 혼용 금지).
export function selectControl(key, labels) {
  const spec = SCHEMA[key] || {};
  const wrap = document.createElement('div');
  wrap.className = 'vt-select-wrap';
  const sel = document.createElement('select');
  sel.className = 'vt-select';
  for (const v of spec.values || []) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = (labels && labels[v]) || v;
    sel.appendChild(opt);
  }
  sel.value = setting(key);
  sel.addEventListener('change', () => setSetting(key, sel.value));
  wrap.appendChild(sel);
  wrap.insertAdjacentHTML('beforeend', icon('chevron-down', 12, 2));
  return wrap;
}

export function renderItems(section) {
  const frag = document.createDocumentFragment();
  for (const item of section.items) {
    let control;
    if (item.kind === 'bool') control = boolControl(item.key);
    else if (item.kind === 'range') control = rangeControl(item.key, item.step);
    else control = selectControl(item.key, item.labels);
    frag.appendChild(row(item.label, control, item.help));
  }
  return frag;
}


export function toast(msg, type) {
  if (typeof window.showToast === 'function') window.showToast(msg, type);
}

export function statusLine(r) {
  const el = document.createElement('div');
  el.className = 'vt-set-help';
  el.textContent = r;
  return el;
}

// 세션 패널의 「연결된 화면」(C3, clients.js)과 같은 mountClients()를 그대로
// 재사용한다 — 컴포넌트를 두 벌 만들지 않는다(작업 지시). 이 섹션을 떠날 때

export function secRow(label, value, state, help) {
  const el = document.createElement('div');
  el.className = 'vt-set-secrow';
  const left = document.createElement('div');
  left.className = 'vt-set-label';
  left.textContent = label;
  if (help) {
    const h = document.createElement('div');
    h.className = 'vt-set-help';
    h.textContent = help;
    left.appendChild(h);
  }
  const badge = document.createElement('span');
  badge.className = 'vt-set-secstate';
  badge.dataset.state = state;
  badge.textContent = value;
  el.append(left, badge);
  return el;
}

export function fmtWhen(ts) {
  if (!ts) return '기록 없음';
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return '기록 없음';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtBytes(n) {
  if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + 'GB';
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + 'MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + 'KB';
  return n + 'B';
}
