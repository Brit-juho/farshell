// 코드 뷰어 파일 렌더 — F4에서 viewer.js에서 분리. hljs 지연 로드 결과(있으면
// 하이라이트, 없으면 이스케이프 폴백)로 파일 내용을 줄 번호와 함께 그린다.
import { vtEsc, vtFetch } from '../../core/api.js';
import { _viewerState, _setMsg } from './state.js';
import { _setPath, _setTitle } from './shell.js';
import { _fmtSize } from './tree.js';

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

export async function openFile(path) {
  _viewerState.mode = 'file';
  _viewerState.selectedPath = path;
  _viewerState.cwd = path.replace(/\/[^/]+$/, '') || _viewerState.root;
  _setPath(path);
  _setTitle(path.split('/').pop());
  const pane = document.getElementById('vt-vw-code-pane');
  pane.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
  let d;
  try {
    d = await vtFetch(`/api/fs/file?path=${encodeURIComponent(path)}`);
  } catch (e) {
    _setMsg(pane, 'vt-vw-empty', [e.message]);
    return;
  }
  if (d.binary) {
    _setMsg(pane, 'vt-vw-empty', [`바이너리 파일 (${_fmtSize(d.size)})`, '미리보기를 지원하지 않습니다.']);
    return;
  }
  pane.innerHTML = '';
  const lang = window.VTDiffLex ? VTDiffLex.langForPath(path) : null;
  _renderFileDOM(pane, d.content, lang);
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = `파일이 커서 앞부분만 표시했습니다 (전체 ${_fmtSize(d.size)})`;
    pane.appendChild(note);
  }
}
