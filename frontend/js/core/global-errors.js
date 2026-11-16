// 전역 오류 포착 — 지금까지는 어디서도 잡지 않아서, 폰에서 난 예외와 배포 직후의 청크 로드 실패가 그냥 사라졌다.
import { logEvent } from './event-log.js';
import { t } from './i18n.js';

// 브라우저가 레이아웃 반복을 경고하는 무해한 오류. 기록하면 잡음만 된다.
const NOISE = /ResizeObserver loop/;

function isAbort(reason) {
  return Boolean(reason && (reason.name === 'AbortError' || reason.code === 20));
}

export function installGlobalErrors() {
  if (typeof window === 'undefined' || window.__vtGlobalErrors) return;
  window.__vtGlobalErrors = true;

  // capture=true: <script>·<link>·<img> 의 로드 실패는 버블링하지 않아서 창에서 캡처로만 잡힌다.
  window.addEventListener('error', (ev) => {
    const el = ev.target;
    if (el && el !== window && el.nodeType === 1) {
      const url = el.src || el.href || '';
      const tag = String(el.tagName || '').toLowerCase();
      // 이미지는 폴백이 흔해 잡음이다. 스크립트·스타일 실패만 기록한다.
      if (tag !== 'script' && tag !== 'link') return;
      logEvent({ level: 'error', source: 'js', msg: t('리소스를 불러오지 못했습니다: {url}', { url }) });
      return;
    }
    if (NOISE.test(String(ev.message || ''))) return;
    const where = ev.filename ? `${ev.filename}:${ev.lineno || 0}:${ev.colno || 0}` : '';
    const err = ev.error;
    logEvent({
      level: 'error',
      source: 'js',
      msg: String(ev.message || (err && err.message) || t('알 수 없는 스크립트 오류')),
      detail: (err && err.stack) || where,
    });
  }, true);

  window.addEventListener('unhandledrejection', (ev) => {
    const r = ev.reason;
    if (isAbort(r)) return;
    const msg = r && r.message ? r.message : typeof r === 'string' ? r : t('처리되지 않은 비동기 오류');
    logEvent({ level: 'error', source: 'js', msg, error: r && typeof r === 'object' ? r : undefined, detail: r && r.stack ? undefined : String(r) });
  });

  document.addEventListener('securitypolicyviolation', (ev) => {
    logEvent({
      level: 'error',
      source: 'js',
      msg: t('CSP가 막았습니다: {directive} {uri}', { directive: ev.violatedDirective || '', uri: ev.blockedURI || '' }),
      detail: `${ev.effectiveDirective || ''} ${ev.sourceFile || ''}:${ev.lineNumber || 0}`.trim(),
    });
  });
}
