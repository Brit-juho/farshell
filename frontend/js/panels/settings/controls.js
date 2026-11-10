// 설정 화면의 공유 컨트롤·포맷 헬퍼 (S4).
//
// 원래 panels/settings.js 한 파일에 섹션 아홉 개가 870줄로 나열돼 있었다.
// 섹션을 파일로 쪼개면서, 여러 섹션이 같이 쓰는 것만 여기로 모은다.
//
// **컨트롤은 스키마에서 그린다** — 항목을 하드코딩하면 core/settings.js 스키마와
// 두 벌이 되어 어긋난다. 값·범위·기본값은 전부 스토어에서 읽는다.
import { get as setting, set as setSetting, scopeOf, SCHEMA, DEVICE_PROFILE } from '../../core/settings.js';
import { numberSpinner } from '../../ui/number-spinner.js';
import { icon } from '../../ui/icons.js';
import { t } from '../../core/i18n.js';

let _rowId = 0;

export function row(label, controlEl, help, options = {}) {
  const el = document.createElement('div');
  el.className = 'vt-set-row';
  const meta = document.createElement('div');
  meta.className = 'vt-set-meta';
  const left = document.createElement('div');
  left.className = 'vt-set-label';
  const base = `vt-set-row-${++_rowId}`;
  left.id = `${base}-label`;
  left.appendChild(document.createTextNode(label));
  if (options.scope || options.scopeText) {
    const badge = document.createElement('span');
    badge.className = 'vt-tag vt-set-scope';
    badge.textContent = options.scopeText || (options.scope === 'device'
      ? ({ mobile: t('모바일'), mac: 'Mac', windows: 'Windows', linux: 'Linux' })[DEVICE_PROFILE]
      : t('모든 기기'));
    left.appendChild(badge);
  }
  let helpId = null;
  if (help) {
    const h = document.createElement('div');
    h.className = 'vt-set-help';
    h.id = `${base}-help`;
    helpId = h.id;
    h.textContent = help;
    meta.appendChild(h);
  }
  if (options.key) {
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'vt-set-default';
    reset.textContent = t('기본값으로');
    const updateReset = () => { reset.hidden = setting(options.key) === SCHEMA[options.key]?.def; };
    reset.addEventListener('click', () => {
      setSetting(options.key, SCHEMA[options.key].def);
      controlEl.vtSetValue?.(setting(options.key));
      updateReset();
    });
    controlEl.addEventListener('vt-setting-value', updateReset);
    updateReset();
    meta.appendChild(reset);
  }
  // 행 안의 주 설정 컨트롤만 라벨에 연결한다. 실행 버튼 여러 개가 든 행은 각
  // 버튼의 동사 자체가 이름이므로 덮어쓰지 않는다.
  let primary = null;
  if (controlEl.matches?.('input, select, textarea, [role="switch"], [role="radiogroup"]')) primary = controlEl;
  else if (controlEl.classList?.contains('vt-range')) primary = controlEl.querySelector('input[type="range"]');
  else if (controlEl.classList?.contains('vt-select-wrap')) primary = controlEl.querySelector('select');
  else if (controlEl.classList?.contains('vt-segmented')) primary = controlEl;
  if (primary) {
    primary.setAttribute('aria-labelledby', left.id);
    if (helpId) primary.setAttribute('aria-describedby', helpId);
  }
  const directNumber = controlEl.querySelector?.('.vt-range-number');
  if (directNumber) {
    directNumber.setAttribute('aria-label', t('{label} 직접 입력', { label }));
    if (helpId) directNumber.setAttribute('aria-describedby', helpId);
  }
  const spinner = controlEl.matches?.('.vt-number-spinner') ? controlEl : controlEl.querySelector?.('.vt-number-spinner');
  spinner?.vtSetLabel?.(label, helpId);
  meta.prepend(left);
  el.append(meta, controlEl);
  return el;
}

// ── 스위치 · 세그먼트 — 설정 컨트롤의 단일 빌더 ──────────────────────────────
// 설정 화면의 켜기/끄기와 3개 이하 선택은 **이 두 함수로만** 만든다(섹션마다 따로 그리지 않는다).
// 모양은 styles/layers/components.css 의 .vt-switch · .vt-segmented 가 정한다. 되돌리려면
// 여기의 클래스 이름만 바꾸면 된다(옛 ON | OFF 알약 = .vt-toggle 규칙은 스니펫 스코프용으로 남아 있다).
//
// 스위치는 <button role="switch" aria-checked> 라 스페이스·엔터가 브라우저 기본으로 동작하고
// 스크린리더가 켜짐/꺼짐을 그대로 읽는다. 세그먼트는 role="radiogroup" + 한 칸에 하나의
// role="radio" 버튼(roving tabindex, 방향키로 이동 = 선택).
export function switchButton({ checked = false, onChange, label, disabled = false } = {}) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'vt-switch';
  btn.setAttribute('role', 'switch');
  if (label) btn.setAttribute('aria-label', label);
  btn.disabled = !!disabled;
  const paint = (v) => { btn.setAttribute('aria-checked', v ? 'true' : 'false'); };
  paint(!!checked);
  btn.addEventListener('click', () => {
    const next = btn.getAttribute('aria-checked') !== 'true';
    paint(next);
    onChange?.(next);
    btn.dispatchEvent(new Event('vt-setting-value'));
  });
  btn.vtSetValue = (v) => paint(!!v);
  btn.vtSetDisabled = (v) => { btn.disabled = !!v; };
  return btn;
}

// options: [[value, label], …]. 값은 문자열로 비교한다(스키마 값이 숫자여도 된다).
export function segmentedControl({ options, value, onChange, label, disabled = false } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'vt-segmented';
  wrap.setAttribute('role', 'radiogroup');
  if (label) wrap.setAttribute('aria-label', label);
  const opts = options.map(([v, text]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-segmented-opt';
    b.setAttribute('role', 'radio');
    b.dataset.value = String(v);
    b.textContent = text;
    return b;
  });
  let current = String(value);
  const paint = (v) => {
    current = String(v);
    let tab = opts.find((o) => o.dataset.value === current) || opts[0];
    for (const o of opts) {
      o.setAttribute('aria-checked', o === tab && o.dataset.value === current ? 'true' : 'false');
      o.tabIndex = o === tab ? 0 : -1;
    }
  };
  const choose = (o, focus) => {
    if (o.dataset.value === current) { if (focus) o.focus(); return; }
    paint(o.dataset.value);
    if (focus) o.focus();
    onChange?.(options.find(([v]) => String(v) === o.dataset.value)[0]);
    wrap.dispatchEvent(new Event('vt-setting-value'));
  };
  for (const o of opts) {
    o.addEventListener('click', () => choose(o, false));
    o.addEventListener('keydown', (e) => {
      const i = opts.indexOf(o);
      const to = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: opts.length - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      choose(opts[(to + opts.length) % opts.length], true);
    });
  }
  wrap.append(...opts);
  paint(value);
  wrap.vtSetValue = paint;
  wrap.vtSetDisabled = (v) => {
    if (v) wrap.setAttribute('aria-disabled', 'true'); else wrap.removeAttribute('aria-disabled');
    for (const o of opts) o.disabled = !!v;
  };
  if (disabled) wrap.vtSetDisabled(true);
  return wrap;
}

// 선택지가 3개 이하이고 글자가 짧으면 세그먼트, 아니면 select. 한 곳에서 정한다.
export const SEGMENT_MAX_OPTIONS = 3;
export const SEGMENT_MAX_LABEL = 10;
export const SEGMENT_MAX_TOTAL = 24;
export function prefersSegment(texts) {
  return texts.length >= 2 && texts.length <= SEGMENT_MAX_OPTIONS
    && texts.every((x) => x.length <= SEGMENT_MAX_LABEL) && texts.join('').length <= SEGMENT_MAX_TOTAL;
}

/** 값이 하나씩 갈리는 선택 컨트롤(스키마 없이 값·콜백만). 세그먼트 또는 select. */
export function choiceControl({ options, value, onChange, label, disabled = false }) {
  if (prefersSegment(options.map(([, text]) => text))) return segmentedControl({ options, value, onChange, label, disabled });
  const wrap = document.createElement('div');
  wrap.className = 'vt-select-wrap';
  const sel = document.createElement('select');
  sel.className = 'vt-select';
  if (label) sel.setAttribute('aria-label', label);
  for (const [v, text] of options) {
    const opt = document.createElement('option');
    opt.value = String(v);
    opt.textContent = text;
    sel.appendChild(opt);
  }
  sel.value = String(value);
  sel.disabled = !!disabled;
  sel.addEventListener('change', () => {
    onChange?.(options.find(([v]) => String(v) === sel.value)[0]);
    wrap.dispatchEvent(new Event('vt-setting-value'));
  });
  wrap.appendChild(sel);
  wrap.insertAdjacentHTML('beforeend', icon('chevron-down', 12, 2));
  wrap.vtSetValue = (v) => { sel.value = String(v); };
  wrap.vtSetDisabled = (v) => { sel.disabled = !!v; };
  return wrap;
}

export function boolControl(key) {
  return switchButton({ checked: !!setting(key), onChange: (next) => setSetting(key, next) });
}

export function rangeControl(key, step, unit = '', presets = []) {
  const spec = SCHEMA[key] || {};
  const wrap = document.createElement('div');
  wrap.className = 'vt-range';
  const input = document.createElement('input');
  input.type = 'range';
  input.min = spec.min ?? 0;
  input.max = spec.max ?? 100;
  input.step = step || 1;
  input.value = setting(key);
  const spinner = numberSpinner({ value: Number(input.value), min: Number(input.min), max: Number(input.max), step: Number(input.step),
    onCommit: async (value) => {
      input.value = String(value);
      input.setAttribute('aria-valuetext', unit === '줄' ? t('{n}줄', { n: value.toLocaleString() }) : `${value}${unit}`);
      input.disabled = true;
      try {
        const saved = setSetting(key, value);
        wrap.dispatchEvent(new Event('vt-setting-value'));
        await saved;
        return setting(key);
      } finally { input.disabled = false; }
    },
  });
  const suffix = document.createElement('span');
  suffix.className = 'vt-range-unit';
  suffix.textContent = unit === '줄' ? t('줄') : unit;
  let quickSeg = null;
  const paint = (v) => {
    input.value = v; spinner.setValue(Number(v)); quickSeg?.vtSetValue(Number(v));
    input.setAttribute('aria-valuetext', unit === '줄' ? t('{n}줄', { n: Number(v).toLocaleString() }) : `${v}${unit}`);
  };
  paint(input.value);
  input.addEventListener('input', () => paint(input.value));
  // 드래그 중에는 화면만 갱신하고, 놓을 때 저장한다 — 안 그러면 슬라이더 한 번에
  // PUT이 수십 번 나간다(설정 스토어에는 디바운스가 없다. 있어야 할 곳은
  // 여기가 아니라 이 컨트롤이다).
  input.addEventListener('change', () => setSetting(key, input.value));
  wrap.append(input, spinner.element, suffix);
  if (presets.length) {
    // 빠른 선택은 세그먼트 한 벌(2,000줄 · 5,000줄 · 10,000줄). 현재 값이 목록에 없으면 아무 칸도 선택되지 않는다.
    wrap.classList.add('has-presets');
    const quick = document.createElement('div');
    quick.className = 'vt-range-presets';
    quickSeg = segmentedControl({
      label: t('빠른 선택'),
      options: presets.map((preset) => [preset, unit === '줄' ? t('{n}줄', { n: Number(preset).toLocaleString() }) : `${preset}${unit}`]),
      value: Number(setting(key)),
      onChange: (preset) => {
        setSetting(key, preset); paint(setting(key));
        wrap.dispatchEvent(new Event('vt-setting-value'));
      },
    });
    quick.appendChild(quickSeg);
    wrap.appendChild(quick);
  }
  input.addEventListener('change', () => wrap.dispatchEvent(new Event('vt-setting-value')));
  wrap.vtSetValue = paint;
  return wrap;
}

// 값 목록은 스키마(spec.values)에서 온다. 글자가 짧은 3개 이하는 세그먼트(choiceControl이 정한다).
// 그 밖에는 <select>를 그대로 두고 OS 화살표만 지운다(appearance:none) — 직접 만든
// 드롭다운은 키보드 탐색·타이핑 점프·모바일 네이티브 피커를 다시 짜야 한다.
export function selectControl(key, labels) {
  const spec = SCHEMA[key] || {};
  const options = (spec.values || []).map((v) => [v, (labels && labels[v]) || String(v)]);
  const cur = setting(key);
  const el = choiceControl({ options, value: cur, onChange: (v) => setSetting(key, v) });
  // 스키마 값이 숫자여도(가장자리 반응 시간) 저장은 원래 타입으로 한다 — choiceControl이 options의 원본 값을 돌려준다.
  return el;
}

export function renderItems(section) {
  const frag = document.createDocumentFragment();
  for (const item of section.items) {
    let control;
    if (item.kind === 'bool') control = boolControl(item.key);
    else if (item.kind === 'range') control = rangeControl(item.key, item.step, item.unit, item.presets);
    else control = selectControl(item.key, item.labels);
    frag.appendChild(row(item.label, control, item.help, { scope: scopeOf(item.key), key: item.key }));
  }
  return frag;
}

// 구역 머리 28(12/600 + 아래 선). aside 는 머리 오른쪽에 붙는 칩·버튼·상태 글자(선택).
export function group(title, content, aside) {
  const el = document.createElement('section');
  el.className = 'vt-set-group';
  const h = document.createElement('h3');
  h.className = 'vt-set-group-title';
  h.textContent = title;
  if (aside) h.append(aside);
  el.append(h, content);
  return el;
}

// 상태 한 줄 — state: 'ok'(조용한 회색 체크) | 'warn'(호박색) | 'err'(빨강) | 없으면 글자만.
export function statLine(text, state) {
  const el = document.createElement('div');
  el.className = 'vt-set-stat';
  if (state) el.dataset.state = state;
  const ic = { ok: 'check', warn: 'alert-triangle', err: 'alert-triangle' }[state];
  if (ic) el.insertAdjacentHTML('beforeend', icon(ic, 12, 2));
  const span = document.createElement('span');
  span.textContent = text;
  el.appendChild(span);
  el.vtSetText = (v) => { span.textContent = v; };
  return el;
}

// 명령 한 줄(코드 상자) + 복사 버튼. 클립보드를 못 쓰면 알려 주고 직접 선택하게 한다.
export function codeLine(command, ariaLabel) {
  const el = document.createElement('div');
  el.className = 'vt-set-code';
  const p = document.createElement('span'); p.className = 'vt-set-code-p'; p.textContent = '$'; p.setAttribute('aria-hidden', 'true');
  const code = document.createElement('code'); code.textContent = command;
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'vt-btn sm quiet vt-set-code-copy';
  b.innerHTML = icon('clipboard-copy', 14, 2);
  b.append(document.createTextNode(t('복사')));
  b.setAttribute('aria-label', ariaLabel || t('{command} 명령 복사', { command }));
  b.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(command); toast(t('복사했습니다')); }
    catch (_) { toast(t('복사하지 못했습니다. 직접 선택해 복사해 주세요'), 'error'); }
  });
  el.append(p, code, b);
  return el;
}

/** 컨트롤 + 뒤에 붙는 단위 글자(예: 「초」). */
export function withUnit(controlEl, unit) {
  const wrap = document.createElement('div');
  wrap.className = 'vt-set-unitwrap';
  const u = document.createElement('span');
  u.className = 'vt-range-unit';
  u.textContent = unit;
  wrap.append(controlEl, u);
  return wrap;
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
  badge.className = 'vt-tag vt-set-secstate';
  badge.dataset.state = state;
  badge.textContent = value;
  el.append(left, badge);
  return el;
}

export function fmtWhen(ts) {
  if (!ts) return t('기록 없음');
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return t('기록 없음');
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtBytes(n) {
  if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + 'GB';
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + 'MB';
  if (n >= 1024) return (n / 1024).toFixed(0) + 'KB';
  return n + 'B';
}
