// 코드 뷰어 파일 렌더 — F4에서 viewer.js에서 분리.
// 파일 본문은 CodeMirror 6(읽기 전용, `editor` 지연 청크 — editor.js)로 그린다(파일 개편 단계 2).
// hljs(`ensureHljs`·`_hl`)는 diff 화면(diff.js)만 쓴다 — diff를 옮기는 단계 9까지 남는다.
import { vtEsc, vtFetch, vtUrl } from '../../core/api.js';
import { _setMsg } from './state.js';
import { t } from '../../core/i18n.js';
// UMD 전역 VTDiffLex — 뷰어 전용이라 panels 청크에서 평가한다. ⚠ 마지막 import여야 한다:
// tests/helpers/vm-esm.js의 IMPORT_RE가 뒤에 `from '…'`이 더 있으면 부작용 import를 삼킨다.
import '../../lib/difflex.js';

// 파일 크기 표기 — 구 viewer/tree.js에서 이관(그 파일은 모달 뷰어와 함께 사라졌다).
export function _fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

// P1: highlight.min.js(127KB)는 diff를 실제로 열 때만 불러온다. fetch와 겹쳐
// 돌므로 체감 지연이 거의 없고, _hl()이 `!window.hljs`를 이스케이프 텍스트로
// 안전하게 폴백하므로 로드 전에 렌더링이 일어나도 깨지지 않는다.
let _hljsLoading = null;
export function ensureHljs() {
  if (window.hljs) return Promise.resolve();
  if (!_hljsLoading) {
    _hljsLoading = new Promise((resolve) => {
      const el = document.createElement('script');
      el.src = '/static/vendor/highlight.min.js';
      el.onload = resolve;
      el.onerror = resolve;   // 실패해도 이스케이프 폴백으로 계속 동작
      document.head.appendChild(el);
    });
  }
  return _hljsLoading;
}

// 하이라이팅. 실패하면 반드시 이스케이프된 원문으로 폴백한다 —
// 여기서 예외가 새면 뷰어 전체가 빈 화면이 된다.
export function _hl(text, lang) {
  if (!lang || !window.hljs) return vtEsc(text);
  try {
    return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
  } catch (_) {
    return vtEsc(text);
  }
}

// ── 파일 본문: CodeMirror 6 ─────────────────────────────────────────────────────────
// 편집기 청크는 파일을 열 때 요청과 겹쳐 받는다. 못 받으면(오프라인, 배포 직후 등) 줄 번호 없는 일반
// 텍스트로라도 보여 준다 — 빈 화면이나 오류만 남기지 않는다.
let _editorLoading = null;
export function loadEditor() {
  if (!_editorLoading) {
    _editorLoading = import('./editor.js').catch((e) => {
      _editorLoading = null;   // 다음 열기 때 다시 시도한다
      throw e;
    });
  }
  return _editorLoading;
}

// 붙여 둔 편집기들. CM은 창(window)과 스크롤 조상에 수신기를 걸어 두므로, 소비처가 호스트를 그냥 떼어 내면
// (다른 파일 열기, 페인 닫기) 그 편집기가 메모리에 남는다. 새 편집기를 붙일 때 DOM에서 빠진 것을 거둔다.
const _editors = new Set();
function _sweepEditors() {
  for (const h of _editors) {
    if (!h.view.dom.isConnected) {
      _editors.delete(h);
      try { h.destroy(); } catch (_) { /* 이미 정리됨 */ }
    }
  }
}

function _plainText(container, text) {
  const pre = document.createElement('pre');
  pre.className = 'vt-vw-plain';
  pre.textContent = text;
  container.appendChild(pre);
}

function _truncNote(d) {
  const note = document.createElement('div');
  note.className = 'vt-vw-note warn vt-vw-trunc';
  note.textContent = t('파일이 커서 앞부분만 표시했습니다 (전체 {size})', { size: _fmtSize(d.size) });
  return note;
}

function _fetchFile(path) {
  return vtFetch(`/api/fs/file?path=${encodeURIComponent(path)}`);
}

// 응답 하나를 비어 있는 container에 그린다.
async function _paint(container, d, path) {
  container._vtEditor = null;
  // [T3] 이미지는 그려 준다 — 스크린샷·다이어그램은 원격에서 코드만큼 자주
  // 열어본다. 바이트는 /api/fs/raw가 주고, 그 엔드포인트는 이미지 타입만
  // 내보낸다(SVG·HTML은 같은 오리진에서 열면 XSS라 제외 — server/fsguard.py).
  if (d.image) {
    if (d.too_large) {
      _setMsg(container, 'vt-vw-empty', [t('이미지가 너무 큽니다 ({size})', { size: _fmtSize(d.size) }),
        t('미리보기 상한을 넘어 표시하지 않습니다.')]);
      return;
    }
    const wrap = document.createElement('div');
    wrap.className = 'vt-vw-image';
    const img = document.createElement('img');
    img.src = vtUrl(`/api/fs/raw?path=${encodeURIComponent(path)}`);
    img.alt = path.split('/').pop() || t('이미지');
    img.loading = 'lazy';
    // 못 불러오면 빈 칸을 남기지 않는다 — 깨진 아이콘만 뜨면 "왜 안 되는지"를 모른다.
    img.addEventListener('error', () => {
      _setMsg(container, 'vt-vw-empty', [t('이미지를 불러오지 못했습니다.')]);
    });
    wrap.appendChild(img);
    const note = document.createElement('div');
    note.className = 'vt-vw-note';
    note.textContent = `${d.mime} · ${_fmtSize(d.size)}`;
    wrap.appendChild(note);
    container.appendChild(wrap);
    return;
  }
  if (d.binary) {
    _setMsg(container, 'vt-vw-empty', [t('바이너리 파일 ({size})', { size: _fmtSize(d.size) }), t('미리보기를 지원하지 않습니다.')]);
    return;
  }
  const text = VTDiffLex.normalize(d.content);
  let handle = null;
  try {
    const mod = await loadEditor();
    handle = await mod.mountEditor(container, text, path, VTDiffLex.langForPath(path));
  } catch (e) {
    console.warn('[FarShell viewer] editor unavailable, showing plain text', e);
  }
  if (handle) {
    _sweepEditors();
    _editors.add(handle);
    container._vtEditor = handle;
  } else {
    _plainText(container, text);
  }
  if (d.truncated) container.appendChild(_truncNote(d));
}

function _currentHost(scrollEl) {
  return Array.from(scrollEl.children).find((c) => c.classList.contains('vt-vw-code-pane')) || null;
}

function _swapHost(scrollEl, host) {
  const top = scrollEl.scrollTop;
  const left = scrollEl.scrollLeft;
  const old = _currentHost(scrollEl);
  if (old) old.replaceWith(host);
  else scrollEl.appendChild(host);
  scrollEl.scrollTop = top;
  scrollEl.scrollLeft = left;
}

/**
 * 이미 열린 파일을 다시 그린다(새로고침 버튼·자동 갱신).
 * - 편집기가 떠 있고 새 응답도 텍스트면 **그 편집기의 바뀐 부분만** 바꾼다. 스크롤·선택이 그대로 남고,
 *   내용이 같으면 아무것도 그리지 않는다.
 * - 그 밖(이미지와 텍스트가 바뀜, 앞선 실패 등)은 떨어진 새 호스트에 다 그린 뒤 한 번에 바꾼다 —
 *   깜빡이지 않고 스크롤 위치를 되돌린다.
 * `scrollEl`은 호스트(.vt-vw-code-pane)를 담은 스크롤 상자다. 호스트만 바꾸고 곁에 있는 것(페인 뷰어의
 * 새로고침 버튼)은 둔다. `isCurrent()`는 그 사이 다른 파일로 바뀌지 않았는지 확인한다.
 */
export async function refreshFile(scrollEl, path, isCurrent) {
  const seq = (scrollEl._vtRefreshSeq || 0) + 1;
  scrollEl._vtRefreshSeq = seq;
  // 늦게 도착한 이전 새로고침이 최신 것을 덮으면 안 된다.
  const stale = () => scrollEl._vtRefreshSeq !== seq || !isCurrent();
  let d;
  try {
    d = await _fetchFile(path);
  } catch (e) {
    if (stale()) return;
    // 실패도 화면에 보여 준다 — 옛 내용을 조용히 남기면 끊긴 것을 모른다.
    const host = document.createElement('div');
    host.className = 'vt-vw-code-pane';
    _setMsg(host, 'vt-vw-empty', [e.message]);
    _swapHost(scrollEl, host);
    return;
  }
  if (stale()) return;
  const old = _currentHost(scrollEl);
  const handle = old && old._vtEditor;
  if (handle && handle.view.dom.isConnected && !d.image && !d.binary) {
    handle.setText(VTDiffLex.normalize(d.content));
    const note = old.querySelector(':scope > .vt-vw-trunc');
    if (note) note.remove();
    if (d.truncated) old.appendChild(_truncNote(d));
    return;
  }
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  await _paint(host, d, path);
  if (stale()) return;
  _swapHost(scrollEl, host);
}

/** 파일 하나를 container에 그린다. 소비처는 뷰어 페인(layout/panes.js)과 파일 오버레이(overlay.js)다. */
export async function renderFile(container, path) {
  loadEditor().catch(() => {});   // fire-and-forget — 아래 요청과 겹쳐 받는다. 실패는 _paint가 처리한다
  container.innerHTML = `<div class="vt-vw-loading">${t('불러오는 중…')}</div>`;
  let d;
  try {
    d = await _fetchFile(path);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }
  container.innerHTML = '';
  await _paint(container, d, path);
}
