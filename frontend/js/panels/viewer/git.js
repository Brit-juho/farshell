// 코드 뷰어 git status/stage/commit + log/show — F4에서 viewer.js에서 분리.
//
// 코드 뷰어의 유일한 쓰기 경로. push·브랜치 조작은 절대 추가하지 않는다.
// 스코프를 stage/unstage/commit 으로만 좁게 유지한다 — TODOS.md D16 참고.
import { vtFetch } from '../../core/api.js';
import { _viewerState, _setMsg } from './state.js';
import { _setPath, _setTitle, _setActivePane } from './shell.js';
import { _renderDiffDOM, _showFileDiff } from './diff.js';

function _gitFileLabel(entry) {
  if (entry.index_status === '?' || entry.worktree_status === '?') return '추가되지 않음';
  const code = entry.index_status || entry.worktree_status;
  return { M: '수정됨', A: '추가됨', D: '삭제됨', R: '이름변경', C: '복사됨', U: '충돌' }[code] || code;
}

async function _gitAction(repo, path, files) {
  return vtFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, files }),
  });
}

function _gitRowEl(repo, entry, staged) {
  const row = document.createElement('div');
  row.className = 'vt-vw-grow';

  const btn = document.createElement('button');
  btn.className = 'vt-vw-gact';
  btn.textContent = staged ? '－' : '＋';
  btn.title = staged ? '스테이지 해제' : '스테이지';
  btn.setAttribute('aria-label', btn.title);
  btn.addEventListener('click', async (ev) => {
    ev.stopPropagation();
    btn.disabled = true;
    try {
      await _gitAction(repo, staged ? '/api/git/unstage' : '/api/git/stage', [entry.file]);
      await showGit(repo);
    } catch (e) {
      showToast(`${btn.title} 실패: ${e.message}`);
      btn.disabled = false;
    }
  });

  const badge = document.createElement('span');
  badge.className = 'vt-vw-gstat';
  badge.textContent = entry.index_status === '?' ? '??' : (staged ? entry.index_status : entry.worktree_status) || '';

  const name = document.createElement('span');
  name.className = 'vt-vw-name';
  name.textContent = entry.orig_file ? `${entry.orig_file} → ${entry.file}` : entry.file;
  name.title = _gitFileLabel(entry);

  row.appendChild(btn);
  row.appendChild(badge);
  row.appendChild(name);
  row.addEventListener('click', () => _showFileDiff(repo, entry.file, staged));
  return row;
}

function _gitSectionEl(title, entries, repo, staged) {
  const sec = document.createElement('div');
  sec.className = 'vt-vw-gsec';
  const head = document.createElement('div');
  head.className = 'vt-vw-ghead';
  head.textContent = `${title} (${entries.length})`;
  sec.appendChild(head);
  entries.forEach(e => sec.appendChild(_gitRowEl(repo, e, staged)));
  return sec;
}

async function _doCommit(repo, pane) {
  const ta = pane.querySelector('#vt-vw-commit-msg');
  const btn = pane.querySelector('#vt-vw-commit-btn');
  const message = (ta.value || '').trim();
  if (!message) return;
  btn.disabled = true;
  try {
    await vtFetch('/api/git/commit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo, message }),
    });
    showToast('커밋했습니다.');
    await showGit(repo);
  } catch (e) {
    showToast(`커밋 실패: ${e.message}`);
    btn.disabled = false;
  }
}

export async function showGit(repo) {
  const target = repo || _viewerState.cwd || _viewerState.root;
  if (!target) return;
  _viewerState.mode = 'diff';
  _viewerState.cwd = target;
  _setTitle('Git');
  _setPath(target);
  const pane = document.getElementById('vt-vw-code-pane');
  pane.innerHTML = '<div class="vt-vw-loading">git status 확인 중…</div>';
  if (_viewerState.displayMode === 'sheet') _setActivePane('code');

  let d;
  try {
    d = await vtFetch(`/api/git/status?repo=${encodeURIComponent(target)}`);
  } catch (e) {
    _setMsg(pane, 'vt-vw-empty', [e.message]);
    return;
  }
  if (!d.repo) { _setMsg(pane, 'vt-vw-empty', ['git 저장소가 아닙니다.']); return; }

  // 미추적 파일("??")은 index_status/worktree_status 둘 다 '?'로 채워지는데,
  // 실제 인덱스에는 없으므로 스테이지됨으로 분류하면 안 된다.
  const staged = d.files.filter(f => f.index_status && f.status !== '??');
  const unstaged = d.files.filter(f => f.status === '??' || (!f.index_status && f.worktree_status));

  pane.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'vt-vw-git';

  if (!d.files.length) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = '변경된 내용이 없습니다.';
    wrap.appendChild(empty);
  } else {
    if (staged.length) wrap.appendChild(_gitSectionEl('스테이지됨', staged, target, true));
    if (unstaged.length) wrap.appendChild(_gitSectionEl('변경사항', unstaged, target, false));
  }

  const commitBox = document.createElement('div');
  commitBox.className = 'vt-vw-gcommit';
  commitBox.innerHTML = `
    <textarea id="vt-vw-commit-msg" class="vt-vw-gmsg" placeholder="커밋 메시지" rows="2"
      ${staged.length ? '' : 'disabled'}></textarea>
    <button id="vt-vw-commit-btn" class="vt-vw-gcommit-btn" ${staged.length ? '' : 'disabled'}>커밋</button>
  `;
  wrap.appendChild(commitBox);

  const logSec = document.createElement('div');
  logSec.className = 'vt-vw-glog';
  wrap.appendChild(logSec);

  pane.appendChild(wrap);

  pane.querySelector('#vt-vw-commit-btn').addEventListener('click', () => _doCommit(target, pane));
  _renderCommitLog(target, logSec, 0);
}

// --- git log / show (커밋 기록 · 커밋 간 diff, 읽기 전용) -----------------------

function _commitRowEl(repo, c) {
  const row = document.createElement('div');
  row.className = 'vt-vw-grow vt-vw-crow';
  const sha = document.createElement('span');
  sha.className = 'vt-vw-gstat';
  sha.textContent = c.short;
  const name = document.createElement('span');
  name.className = 'vt-vw-name';
  name.textContent = c.subject;
  name.title = `${c.author} · ${c.date}`;
  row.appendChild(sha);
  row.appendChild(name);
  row.addEventListener('click', () => _showCommit(repo, c.hash));
  return row;
}

// skip=0이면 헤더부터 새로 그린다. "더 보기"는 같은 container에 이어 붙인다.
async function _renderCommitLog(repo, container, skip) {
  if (skip === 0) {
    container.innerHTML = '';
    const head = document.createElement('div');
    head.className = 'vt-vw-ghead';
    head.textContent = '커밋 기록';
    container.appendChild(head);
  }
  const more = container.querySelector('.vt-vw-glog-more');
  if (more) more.remove();

  let d;
  try {
    d = await vtFetch(`/api/git/log?repo=${encodeURIComponent(repo)}&skip=${skip}&limit=20`);
  } catch (e) {
    const err = document.createElement('div');
    err.className = 'vt-vw-empty';
    err.textContent = e.message;
    container.appendChild(err);
    return;
  }
  if (!d.commits.length) {
    if (skip === 0) {
      const empty = document.createElement('div');
      empty.className = 'vt-vw-empty';
      empty.textContent = '커밋이 없습니다.';
      container.appendChild(empty);
    }
    return;
  }
  d.commits.forEach(c => container.appendChild(_commitRowEl(repo, c)));
  if (d.has_more) {
    const btn = document.createElement('button');
    btn.className = 'vt-pt-btn vt-vw-glog-more';
    btn.textContent = '더 보기';
    btn.addEventListener('click', () => _renderCommitLog(repo, container, skip + d.commits.length));
    container.appendChild(btn);
  }
}

async function _showCommit(repo, sha) {
  _viewerState.mode = 'diff';
  _setTitle(sha.slice(0, 7));
  _setPath(repo);
  const pane = document.getElementById('vt-vw-code-pane');
  pane.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
  if (_viewerState.displayMode === 'sheet') _setActivePane('code');

  let d;
  try {
    d = await vtFetch(`/api/git/show?repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}`);
  } catch (e) {
    _setMsg(pane, 'vt-vw-empty', [e.message]);
    return;
  }

  pane.innerHTML = '';
  const back = document.createElement('button');
  back.className = 'vt-pt-btn vt-vw-cback';
  back.textContent = '‹ 상태로';
  back.addEventListener('click', () => showGit(repo));
  pane.appendChild(back);

  const wrap = document.createElement('div');
  wrap.className = 'vt-vw-git';

  const meta = document.createElement('div');
  meta.className = 'vt-vw-cmeta';
  const subj = document.createElement('div');
  subj.className = 'vt-vw-cmeta-subject';
  subj.textContent = d.commit.subject;
  meta.appendChild(subj);
  if (d.commit.body) {
    const body = document.createElement('div');
    body.className = 'vt-vw-cmeta-body';
    body.textContent = d.commit.body;
    meta.appendChild(body);
  }
  const info = document.createElement('div');
  info.className = 'vt-vw-cmeta-info';
  info.textContent = `${d.commit.short} · ${d.commit.author} · ${d.commit.date}`;
  meta.appendChild(info);
  wrap.appendChild(meta);

  const sec = document.createElement('div');
  sec.className = 'vt-vw-gsec';
  const head = document.createElement('div');
  head.className = 'vt-vw-ghead';
  head.textContent = `변경된 파일 (${d.files.length})`;
  sec.appendChild(head);
  d.files.forEach(f => {
    const row = document.createElement('div');
    row.className = 'vt-vw-grow';
    const badge = document.createElement('span');
    badge.className = 'vt-vw-gstat';
    badge.textContent = f.status;
    const name = document.createElement('span');
    name.className = 'vt-vw-name';
    name.textContent = f.orig_file ? `${f.orig_file} → ${f.file}` : f.file;
    row.appendChild(badge);
    row.appendChild(name);
    row.addEventListener('click', () => _showCommitFileDiff(repo, sha, f.file));
    sec.appendChild(row);
  });
  wrap.appendChild(sec);
  pane.appendChild(wrap);
}

async function _showCommitFileDiff(repo, sha, file) {
  _viewerState.mode = 'diff';
  _setTitle(file.split('/').pop());
  _setPath(file);
  const pane = document.getElementById('vt-vw-code-pane');
  pane.innerHTML = '<div class="vt-vw-loading">git show 실행 중…</div>';
  if (_viewerState.displayMode === 'sheet') _setActivePane('code');

  let d;
  try {
    const q = `repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}&file=${encodeURIComponent(file)}`;
    d = await vtFetch(`/api/git/show?${q}`);
  } catch (e) {
    _setMsg(pane, 'vt-vw-empty', [e.message]);
    return;
  }

  pane.innerHTML = '';
  const back = document.createElement('button');
  back.className = 'vt-pt-btn vt-vw-cback';
  back.textContent = '‹ 커밋으로';
  back.addEventListener('click', () => _showCommit(repo, sha));
  pane.appendChild(back);

  if (!d.diff || !d.diff.trim()) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = '변경된 내용이 없습니다.';
    pane.appendChild(empty);
    return;
  }
  _renderDiffDOM(pane, d.diff);
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = 'diff가 커서 일부만 표시했습니다.';
    pane.appendChild(note);
  }
}
