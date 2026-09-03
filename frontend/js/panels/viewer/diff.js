// 코드 뷰어 diff 렌더 + 줄 주석→프롬프트 큐 — F4에서 viewer.js에서 분리.
// git.js가 상태/커밋 목록에서 특정 파일을 눌렀을 때 _showFileDiff를 부른다.
import { vtFetch } from '../../core/api.js';
import { isMac } from '../../core/env.js';
import { _viewerState, _setMsg } from './state.js';
import { _setPath, _setTitle, _setActivePane } from './shell.js';
import { _hl } from './file.js';

export function _renderDiffDOM(container, diffText) {
  const files = VTDiffLex.parse(diffText);
  files.forEach(f => {
    const st = VTDiffLex.stats(f);
    const lang = VTDiffLex.langForPath(f.newPath || f.oldPath);

    const fileEl = document.createElement('div');
    fileEl.className = 'vt-vw-dfile';

    const head = document.createElement('div');
    head.className = 'vt-vw-dhead';
    const pathEl = document.createElement('span');
    pathEl.className = 'vt-vw-dpath';
    pathEl.textContent = f.newPath || f.oldPath;
    const statEl = document.createElement('span');
    statEl.className = 'vt-vw-dstat';
    const addB = document.createElement('b'); addB.className = 'add'; addB.textContent = `+${st.add}`;
    const delB = document.createElement('b'); delB.className = 'del'; delB.textContent = `-${st.del}`;
    statEl.appendChild(addB);
    statEl.appendChild(document.createTextNode(' '));
    statEl.appendChild(delB);
    head.appendChild(pathEl);
    head.appendChild(statEl);
    fileEl.appendChild(head);

    if (f.binary) {
      const note = document.createElement('div');
      note.className = 'vt-vw-note';
      note.textContent = '바이너리 파일';
      fileEl.appendChild(note);
    } else {
      f.hunks.forEach(h => {
        const hunk = document.createElement('div');
        hunk.className = 'vt-vw-hunk';
        hunk.textContent = h.header;                    // textContent — XSS 방어
        fileEl.appendChild(hunk);

        h.lines.forEach(l => {
          const cls = l.type === 'add' ? 'add' : l.type === 'del' ? 'del' : l.type === 'meta' ? 'meta' : '';
          const row = document.createElement('div');
          row.className = 'vt-vw-dl' + (cls ? ' ' + cls : '');

          const oldNo = document.createElement('span');
          oldNo.className = 'vt-vw-no';
          oldNo.textContent = l.oldNo == null ? '' : l.oldNo;
          const newNo = document.createElement('span');
          newNo.className = 'vt-vw-no';
          newNo.textContent = l.newNo == null ? '' : l.newNo;
          const sign = document.createElement('span');
          sign.className = 'vt-vw-sign';
          sign.textContent = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' ';
          const tx = document.createElement('span');
          tx.className = 'vt-vw-tx';
          if (l.type === 'meta') tx.textContent = l.text;
          else tx.innerHTML = _hl(l.text, lang);         // hljs 결과만 innerHTML 예외

          row.appendChild(oldNo);
          row.appendChild(newNo);
          row.appendChild(sign);
          row.appendChild(tx);
          _wireDiffLineAnnotate(row, f.newPath || f.oldPath, l.newNo ?? l.oldNo ?? null);
          fileEl.appendChild(row);
        });
      });
    }
    container.appendChild(fileEl);
  });
}

// --- diff 줄 주석 → 프롬프트 큐 --------------------------------------------
// 폰에서 diff를 보다가 그 줄에 바로 지시를 남기면 프롬프트 큐로 들어간다.
// 새 서버 엔드포인트 없이 기존 POST /api/queue를 그대로 쓴다.

function _wireDiffLineAnnotate(row, filePath, lineNo) {
  row.addEventListener('click', () => {
    // 텍스트를 드래그로 복사-선택한 직후의 클릭이면 무시한다 — 안 그러면
    // diff를 긁어 복사할 때마다 주석 박스가 열린다. sign 컬럼이 14px라
    // 모바일 터치 타깃으로 쓰기엔 좁아서, 줄 전체를 눌러도 열리게 한다.
    if (window.getSelection && String(window.getSelection())) return;
    _toggleDiffAnnotate(row, filePath, lineNo);
  });
}

function _toggleDiffAnnotate(row, filePath, lineNo) {
  const existing = row.nextElementSibling;
  if (existing && existing.classList.contains('vt-vw-annotate')) { existing.remove(); return; }
  // 한 번에 하나만 — 다른 줄에서 이미 열려 있던 박스는 닫는다.
  document.querySelectorAll('.vt-vw-annotate').forEach(el => el.remove());

  const box = document.createElement('div');
  box.className = 'vt-vw-annotate';
  const ta = document.createElement('textarea');
  ta.className = 'vt-vw-annotate-input';
  ta.rows = 2;
  ta.placeholder = `이 줄에 지시… (${isMac ? 'Cmd' : 'Ctrl'}+Enter로 추가)`;
  const actions = document.createElement('div');
  actions.className = 'vt-vw-annotate-row';
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'vt-pt-btn';
  cancelBtn.type = 'button';
  cancelBtn.textContent = '취소';
  const addBtn = document.createElement('button');
  addBtn.className = 'vt-pt-btn';
  addBtn.type = 'button';
  addBtn.textContent = '큐에 추가';
  actions.appendChild(cancelBtn);
  actions.appendChild(addBtn);
  box.appendChild(ta);
  box.appendChild(actions);
  row.after(box);

  cancelBtn.addEventListener('click', () => box.remove());
  const submit = () => _submitDiffAnnotate(box, ta, filePath, lineNo);
  addBtn.addEventListener('click', submit);
  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); submit(); }
  });
  // 클릭이 곧바로 버블링돼 document의 다른 리스너가 이 박스를 즉시 지우지
  // 않도록 막는다(현재 문서 전역에 그런 리스너는 없지만, 있었을 때를 대비).
  box.addEventListener('click', (ev) => ev.stopPropagation());
  requestAnimationFrame(() => ta.focus());
}

async function _submitDiffAnnotate(box, ta, filePath, lineNo) {
  const comment = (ta.value || '').trim();
  if (!comment) return;
  const text = lineNo != null ? `${filePath}:${lineNo} — ${comment}` : `${filePath} — ${comment}`;
  const addBtn = box.querySelector('.vt-pt-btn:last-child');
  if (addBtn) addBtn.disabled = true;
  try {
    await vtFetch('/api/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (typeof showToast === 'function') showToast('큐에 추가됨');
    box.remove();
  } catch (e) {
    if (addBtn) addBtn.disabled = false;
    if (typeof showToast === 'function') showToast(`추가 실패: ${e.message}`);
  }
}

// 파일 하나의 diff. status 목록에서 특정 파일을 눌렀을 때 쓴다.
export async function _showFileDiff(repo, file, staged) {
  _viewerState.mode = 'diff';
  _setTitle(file.split('/').pop());
  _setPath(file);
  const pane = document.getElementById('vt-vw-code-pane');
  pane.innerHTML = '<div class="vt-vw-loading">git diff 실행 중…</div>';
  if (_viewerState.displayMode === 'sheet') _setActivePane('code');

  let d;
  try {
    const q = `repo=${encodeURIComponent(repo)}&file=${encodeURIComponent(file)}&staged=${staged ? 'true' : 'false'}`;
    d = await vtFetch(`/api/git/diff?${q}`);
  } catch (e) {
    _setMsg(pane, 'vt-vw-empty', [e.message]);
    return;
  }
  if (!d.diff || !d.diff.trim()) { _setMsg(pane, 'vt-vw-empty', ['변경된 내용이 없습니다.']); return; }

  pane.innerHTML = '';
  _renderDiffDOM(pane, d.diff);
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = 'diff가 커서 일부만 표시했습니다.';
    pane.appendChild(note);
  }
}
