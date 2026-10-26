import { icon } from '../../ui/icons.js';
import { t } from '../../core/i18n.js';

/** Numeric input with one shared validation/commit path for typing and step buttons. */
export function numberSpinner({ value, min, max, step = 1, onCommit, onError }) {
  if (![value, min, max, step].every(Number.isFinite) || min > max || step <= 0 || typeof onCommit !== 'function') {
    throw new RangeError('Invalid number spinner bounds');
  }
  const precision = (n) => {
    const [fraction, exponent = '0'] = String(n).split('e');
    return Math.max(0, (fraction.split('.')[1]?.length || 0) - Number(exponent));
  };
  const digits = Math.max(precision(min), precision(step));
  if (digits > 12 || !Number.isFinite((max - min) / step)) throw new RangeError('Unsupported number spinner precision');
  const limit = Math.floor((max - min) / step + 1e-9);
  const normalize = (n) => Number((min + Math.max(0, Math.min(limit, Math.round((n - min) / step))) * step).toFixed(digits));
  let current = normalize(value);
  let busy = false;
  let disabled = false;
  let revision = 0;
  const element = document.createElement('div');
  element.className = 'vt-number-spinner';
  const input = document.createElement('input');
  input.type = 'number'; input.className = 'vt-range-number';
  input.min = String(min); input.max = String(max); input.step = String(step);
  const buttons = document.createElement('div');
  buttons.className = 'vt-number-steps';
  const up = document.createElement('button');
  const down = document.createElement('button');
  for (const [button, direction] of [[up, 'up'], [down, 'down']]) {
    button.type = 'button'; button.className = `vt-number-step ${direction}`;
    button.innerHTML = icon('chevron-down', 12, 2);
    // Keep a mouse/touch click from blurring the draft and submitting twice.
    button.addEventListener('pointerdown', (e) => { if (e.button === 0) e.preventDefault(); });
  }
  buttons.append(up, down); element.append(input, buttons);
  const draft = () => Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : current;
  const paintButtons = () => {
    input.disabled = disabled && !busy;
    input.readOnly = busy;
    const n = draft();
    down.disabled = disabled || busy || n <= min;
    up.disabled = disabled || busy || n >= normalize(max);
    element.setAttribute('aria-busy', String(busy));
  };
  const paint = () => { input.value = String(current); paintButtons(); };
  const setValue = (n) => {
    if (!Number.isFinite(Number(n)) || n === '' || n === null) return;
    revision++; current = normalize(Number(n)); paint();
  };
  async function commit(n) {
    if (busy || disabled) return;
    n = normalize(n);
    if (n === current) { paint(); return; }
    if (element.contains(document.activeElement)) input.focus({ preventScroll: true });
    const previous = current;
    const mine = ++revision;
    current = n; busy = true; paint();
    try {
      const result = await onCommit(n);
      if (mine !== revision) return;
      if (result === false) current = previous;
      else if (typeof result === 'number' && Number.isFinite(result)) current = normalize(result);
    } catch (e) {
      if (mine === revision) {
        current = previous;
        if (onError) onError(e);
        else window.showToast?.(t('값을 저장하지 못했습니다'), 'error');
      }
    } finally {
      busy = false; paint();
    }
  }
  const change = () => {
    if (input.value === '' || !Number.isFinite(input.valueAsNumber)) { paint(); return; }
    void commit(input.valueAsNumber);
  };
  const move = (direction) => {
    const position = (draft() - min) / step;
    const tick = direction > 0 ? Math.floor(position + 1e-9) + 1 : Math.ceil(position - 1e-9) - 1;
    void commit(min + tick * step);
  };
  input.addEventListener('input', paintButtons);
  input.addEventListener('change', change);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); move(e.key === 'ArrowUp' ? 1 : -1); }
    else if (e.key === 'Enter') { e.preventDefault(); change(); }
    else if (e.key === 'Escape') { e.preventDefault(); paint(); }
  });
  up.addEventListener('click', () => move(1));
  down.addEventListener('click', () => move(-1));
  element.vtSetLabel = (label, helpId) => {
    input.setAttribute('aria-label', t('{label} 직접 입력', { label }));
    up.setAttribute('aria-label', t('{label} 늘리기', { label }));
    down.setAttribute('aria-label', t('{label} 줄이기', { label }));
    if (helpId) input.setAttribute('aria-describedby', helpId);
  };
  element.vtSetValue = setValue;
  element.vtSetDisabled = (next) => { disabled = !!next; paintButtons(); };
  paint();
  return { element, input, setValue, setDisabled: element.vtSetDisabled };
}
