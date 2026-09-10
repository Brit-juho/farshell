// 코드 뷰어 파일 렌더 — F4에서 viewer.js에서 분리. hljs 지연 로드 결과(있으면
// 하이라이트, 없으면 이스케이프 폴백)로 파일 내용을 줄 번호와 함께 그린다.
import { vtEsc, vtFetch } from '../../core/api.js';
import { _setMsg } from './state.js';

// 파일 크기 표기 — 구 viewer/tree.js에서 이관(그 파일은 모달 뷰어와 함께 사라졌다).
export function _fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

// P1: highlight.min.js(127KB)는 파일을 실제로 열 때만 불러온다. fetch와 겹쳐
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

function _renderFileDOM(container, content, lang) {
  const lines = VTDiffLex.normalize(content).split('\n');
  const wrap = document.createElement('div');
  wrap.className = 'vt-vw-code';
  lines.forEach((ln, i) => {
    const row = document.createElement('div');
    row.className = 'vt-vw-cl';
    const no = document.createElement('span');
    no.className = 'vt-vw-no';
    no.textContent = i + 1;
    const tx = document.createElement('span');
    tx.className = 'vt-vw-tx';
    tx.innerHTML = _hl(ln, lang);   // hljs.highlight()/vtEsc 결과만 innerHTML 예외
    row.appendChild(no);
    row.appendChild(tx);
    wrap.appendChild(row);
  });
  container.appendChild(wrap);
}

/** 파일 하나를 container에 그린다. N35 §6부터 유일한 소비처는 뷰어 **페인**이다. */
export async function renderFile(container, path) {
  ensureHljs();   // fire-and-forget — 아래 fetch와 겹쳐 돈다
  container.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
  let d;
  try {
    d = await vtFetch(`/api/fs/file?path=${encodeURIComponent(path)}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }
  if (d.binary) {
    _setMsg(container, 'vt-vw-empty', [`바이너리 파일 (${_fmtSize(d.size)})`, '미리보기를 지원하지 않습니다.']);
    return;
  }
  container.innerHTML = '';
  const lang = window.VTDiffLex ? VTDiffLex.langForPath(path) : null;
  _renderFileDOM(container, d.content, lang);
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = `파일이 커서 앞부분만 표시했습니다 (전체 ${_fmtSize(d.size)})`;
    container.appendChild(note);
  }
}
