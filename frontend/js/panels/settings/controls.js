// 설정 화면의 공유 컨트롤·포맷 헬퍼 (S4).
//
// 원래 panels/settings.js 한 파일에 섹션 아홉 개가 870줄로 나열돼 있었다.
// 섹션을 파일로 쪼개면서, 여러 섹션이 같이 쓰는 것만 여기로 모은다.
//
// **컨트롤은 스키마에서 그린다** — 항목을 하드코딩하면 core/settings.js 스키마와
// 두 벌이 되어 어긋난다. 값·범위·기본값은 전부 스토어에서 읽는다.
import { get as setting, set as setSetting, SCHEMA } from '../../core/settings.js';

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

export function boolControl(key) {
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.className = 'vt-set-check';
  cb.checked = !!setting(key);
  cb.addEventListener('change', () => setSetting(key, cb.checked));
  return cb;
}

export function rangeControl(key, step) {
  const spec = SCHEMA[key] || {};
  const wrap = document.createElement('div');
  wrap.className = 'vt-set-range';
  const input = document.createElement('input');
  input.type = 'range';
  input.min = spec.min ?? 0;
  input.max = spec.max ?? 100;
  input.step = step || 1;
  input.value = setting(key);
  const out = document.createElement('span');
  out.className = 'vt-set-value';
  out.textContent = input.value;
  input.addEventListener('input', () => { out.textContent = input.value; });
  // 드래그 중에는 화면만 갱신하고, 놓을 때 저장한다 — 안 그러면 슬라이더 한 번에
  // PUT이 수십 번 나간다(설정 스토어에는 디바운스가 없다. 있어야 할 곳은
  // 여기가 아니라 이 컨트롤이다).
  input.addEventListener('change', () => setSetting(key, input.value));
  wrap.append(input, out);
  return wrap;
}

export function selectControl(key, labels) {
  const spec = SCHEMA[key] || {};
  const sel = document.createElement('select');
  sel.className = 'vt-set-select';
  for (const v of spec.values || []) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = (labels && labels[v]) || v;
    sel.appendChild(opt);
  }
  sel.value = setting(key);
  sel.addEventListener('change', () => setSetting(key, sel.value));
  return sel;
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
