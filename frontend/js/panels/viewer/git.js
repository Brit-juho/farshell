// 코드 뷰어 git status/stage/commit + log/show — F4에서 viewer.js에서 분리.
//
// 코드 뷰어의 유일한 쓰기 경로. push·브랜치 조작은 절대 추가하지 않는다.
// 스코프를 stage/unstage/commit 으로만 좁게 유지한다 — TODOS.md D16 참고.
import { vtFetch } from '../../core/api.js';
import { _setMsg } from './state.js';
import { _renderDiffDOM } from './diff.js';

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

// N35 §6 — 이 아래 렌더러들은 **어디에 그릴지를 인자로 받는다**. 2.1.0부터
// 같은 화면이 두 자리에 뜨기 때문이다: 코드 뷰어 패널(2.0, 쓰기 가능)과 dock
// 소스컨트롤 탭(40 §5, 2.1.0은 읽기 전용). 컴포넌트를 두 벌 만들면 반드시
// 어긋나므로 목록·커밋 상자·로그가 전부 "그릴 곳"을 인자로 받는다. (2.0의
// 모달 코드 뷰어는 §6에서 제거됐다 — 그때 크롬에 묶여 있던 래퍼들도 같이
// 사라졌고, 지금 유일한 소비처는 dock 소스컨트롤 탭이다.)
//
// opts:
//   readOnly  — +/−·커밋 버튼을 렌더는 하되 disabled + 사유 툴팁(40 §5)
//   onFile(file, staged)  — 파일 행 클릭(인라인 diff)
//   onCommit(sha)         — 커밋 행 클릭
//   onOpenFile(file)       — 파일 행의 「뷰어」 버튼 클릭 — 뷰어 페인(N35 §6 리프
//                            타입 viewer)으로 그 파일을 연다. 인라인 diff와 별개
//                            동작이라 버튼을 따로 둔다(행 클릭은 diff를 덮지 않는다).

const _RO_HINT = '읽기 전용입니다';

function _gitRowEl(repo, entry, staged, opts) {
  const row = document.createElement('div');
  row.className = 'vt-vw-grow';

  const btn = document.createElement('button');
  btn.className = 'vt-vw-gact';
  btn.textContent = staged ? '－' : '＋';
  btn.title = opts.readOnly ? _RO_HINT : (staged ? '스테이지 해제' : '스테이지');
  btn.setAttribute('aria-label', btn.title);
  if (opts.readOnly) {
    btn.disabled = true;
  } else {
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      btn.disabled = true;
      try {
        await _gitAction(repo, staged ? '/api/git/unstage' : '/api/git/stage', [entry.file]);
        await opts.reload();
      } catch (e) {
        showToast(`${btn.title} 실패: ${e.message}`);
        btn.disabled = false;
      }
    });
  }

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

  if (opts.onOpenFile) {
    const openBtn = document.createElement('button');
    openBtn.className = 'vt-vw-gopen';
    openBtn.type = 'button';
    openBtn.textContent = '뷰어';
    openBtn.title = '뷰어 페인에서 파일 열기';
    openBtn.setAttribute('aria-label', openBtn.title);
    openBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      opts.onOpenFile(entry.file);
    });
    row.appendChild(openBtn);
  }

  row.addEventListener('click', () => opts.onFile(entry.file, staged));
  return row;
}

function _gitSectionEl(title, entries, repo, staged, opts) {
  const sec = document.createElement('div');
  sec.className = 'vt-vw-gsec';
  const head = document.createElement('div');
  head.className = 'vt-vw-ghead';
  head.textContent = `${title} (${entries.length})`;
  sec.appendChild(head);
  entries.forEach(e => sec.appendChild(_gitRowEl(repo, e, staged, opts)));
  return sec;
}

async function _doCommit(repo, wrap, reload) {
  const ta = wrap.querySelector('.vt-vw-gmsg');
  const btn = wrap.querySelector('.vt-vw-gcommit-btn');
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
    await reload();
  } catch (e) {
    showToast(`커밋 실패: ${e.message}`);
    btn.disabled = false;
  }
}

/** git status/커밋상자/로그를 container에 그린다. 성공하면 status 응답을 돌려준다. */
export async function renderGitStatus(container, repo, opts = {}) {
  const o = {
    readOnly: false,
    onFile: () => {},
    onCommit: () => {},
    onOpenFile: null,
    reload: () => renderGitStatus(container, repo, opts),
    ...opts,
  };
  container.innerHTML = '<div class="vt-vw-loading">git status 확인 중…</div>';

  let d;
  try {
    d = await vtFetch(`/api/git/status?repo=${encodeURIComponent(repo)}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return null;
  }
  if (!d.repo) { _setMsg(container, 'vt-vw-empty', ['git 저장소가 아닙니다.']); return d; }

  // 미추적 파일("??")은 index_status/worktree_status 둘 다 '?'로 채워지는데,
  // 실제 인덱스에는 없으므로 스테이지됨으로 분류하면 안 된다.
  const staged = d.files.filter(f => f.index_status && f.status !== '??');
  const unstaged = d.files.filter(f => f.status === '??' || (!f.index_status && f.worktree_status));

  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'vt-vw-git';

  if (!d.files.length) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = '변경된 내용이 없습니다.';
    wrap.appendChild(empty);
  } else {
    if (staged.length) wrap.appendChild(_gitSectionEl('스테이지됨', staged, repo, true, o));
    if (unstaged.length) wrap.appendChild(_gitSectionEl('변경사항', unstaged, repo, false, o));
  }

  // 읽기 전용(dock, 2.1.0)에서는 커밋 상자를 아예 안 그린다 — 비활성 입력창은
  // 자리만 먹고, "여기서 커밋한다"는 자리 표시는 dock 아래 버튼 줄이 이미 한다.
  const canCommit = staged.length && !o.readOnly;
  if (!o.readOnly) {
  const commitBox = document.createElement('div');
  commitBox.className = 'vt-vw-gcommit';
  const ta = document.createElement('textarea');
  ta.className = 'vt-vw-gmsg';
  ta.rows = 2;
  ta.placeholder = '커밋 메시지';
  ta.disabled = !canCommit;
  const cbtn = document.createElement('button');
  cbtn.className = 'vt-vw-gcommit-btn';
  cbtn.textContent = '커밋';
  cbtn.disabled = !canCommit;
  commitBox.appendChild(ta);
  commitBox.appendChild(cbtn);
  wrap.appendChild(commitBox);
  if (canCommit) cbtn.addEventListener('click', () => _doCommit(repo, wrap, o.reload));
  }

  const logSec = document.createElement('div');
  logSec.className = 'vt-vw-glog';
  wrap.appendChild(logSec);

  container.appendChild(wrap);

  _renderCommitLog(repo, logSec, 0, o);
  return d;
}

// --- git log / show (커밋 기록 · 커밋 간 diff, 읽기 전용) -----------------------

function _commitRowEl(repo, c, opts) {
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
  row.addEventListener('click', () => opts.onCommit(c.hash));
  return row;
}

// skip=0이면 헤더부터 새로 그린다. "더 보기"는 같은 container에 이어 붙인다.
async function _renderCommitLog(repo, container, skip, opts) {
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
  d.commits.forEach(c => container.appendChild(_commitRowEl(repo, c, opts)));
  if (d.has_more) {
    const btn = document.createElement('button');
    btn.className = 'vt-pt-btn vt-vw-glog-more';
    btn.textContent = '더 보기';
    btn.addEventListener('click', () => _renderCommitLog(repo, container, skip + d.commits.length, opts));
    container.appendChild(btn);
  }
}

/** 커밋 하나(메타 + 변경 파일 목록)를 container에 그린다. */
export async function renderCommit(container, repo, sha, opts = {}) {
  const o = { onBack: null, onFile: () => {}, ...opts };
  container.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';

  let d;
  try {
    d = await vtFetch(`/api/git/show?repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }

  container.innerHTML = '';
  if (o.onBack) container.appendChild(_backBtn('‹ 상태로', o.onBack));

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
    row.addEventListener('click', () => o.onFile(f.file));
    sec.appendChild(row);
  });
  wrap.appendChild(sec);
  container.appendChild(wrap);
}

/** 커밋 안의 파일 하나의 diff를 container에 그린다. */
export async function renderCommitFileDiff(container, repo, sha, file, opts = {}) {
  const o = { onBack: null, ...opts };
  container.innerHTML = '<div class="vt-vw-loading">git show 실행 중…</div>';

  let d;
  try {
    const q = `repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}&file=${encodeURIComponent(file)}`;
    d = await vtFetch(`/api/git/show?${q}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }

  container.innerHTML = '';
  if (o.onBack) container.appendChild(_backBtn('‹ 커밋으로', o.onBack));

  if (!d.diff || !d.diff.trim()) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = '변경된 내용이 없습니다.';
    container.appendChild(empty);
    return;
  }
  _renderDiffDOM(container, d.diff);
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = 'diff가 커서 일부만 표시했습니다.';
    container.appendChild(note);
  }
}

function _backBtn(label, onClick) {
  const back = document.createElement('button');
  back.className = 'vt-pt-btn vt-vw-cback';
  back.textContent = label;
  back.addEventListener('click', onClick);
  return back;
}
