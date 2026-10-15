// 코드 뷰어 git status/stage/commit + log/show — F4에서 viewer.js에서 분리.
//
// 코드 뷰어의 유일한 쓰기 경로. push·브랜치 조작은 절대 추가하지 않는다.
// 스코프를 stage/unstage/commit 으로만 좁게 유지한다.
import { vtFetch } from '../../core/api.js';
import { emptyState, errorState } from '../../ui/empty.js';
import { _setMsg } from './state.js';
import { _renderDiffDOM } from './diff.js';
import { t } from '../../core/i18n.js';

function _gitFileLabel(entry) {
  if (entry.index_status === '?' || entry.worktree_status === '?') return t('추가되지 않음');
  const code = entry.index_status || entry.worktree_status;
  return { M: t('수정됨'), A: t('추가됨'), D: t('삭제됨'), R: t('이름변경'), C: t('복사됨'), U: t('충돌') }[code] || code;
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

const _RO_HINT = t('읽기 전용입니다');

function _gitRowEl(repo, entry, staged, opts) {
  const row = document.createElement('div');
  row.className = 'vt-vw-grow';

  const btn = document.createElement('button');
  // 모양은 components.css의 .vt-btn(sm · quiet) 이 정한다 — legacy.css에 또 하나
  // 만들지 않는다(20-design-system.md §3). vt-vw-gact는 이제 치수 보정과
  // hover 드러남을 거는 훅이다. quiet(테두리 없음)인 이유: 행 안의 부차
  // 액션이라 행마다 테두리가 쌓이면 목록이 격자처럼 보인다.
  btn.type = 'button';
  btn.className = 'vt-btn sm quiet vt-vw-gact';
  // 전각 ±(＋－)을 쓰던 자리. 버튼 폭을 만들려고 전각을 빌려 쓴 것이었는데
  // 폭은 이제 CSS가 24px로 잡는다 — 글자는 주위(↑↓ · +142 −38 머릿말)와 같은
  // 반각 부호로 맞춘다. 이모지가 아니고 icons.js에도 minus가 없어 글자로 남긴다.
  btn.textContent = staged ? '−' : '+';
  btn.title = opts.readOnly ? _RO_HINT : (staged ? t('스테이지 해제') : t('스테이지'));
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
        showToast(staged ? t('스테이지 해제 실패: {msg}', { msg: e.message }) : t('스테이지 실패: {msg}', { msg: e.message }));
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
    openBtn.className = 'vt-btn sm quiet vt-vw-gopen';
    openBtn.type = 'button';
    openBtn.textContent = t('뷰어');
    openBtn.title = t('뷰어 페인에서 파일 열기');
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
    showToast(t('커밋했습니다.'));
    await reload();
  } catch (e) {
    showToast(t('커밋 실패: {msg}', { msg: e.message }));
    btn.disabled = false;
  }
}

/** git status/커밋상자/로그를 container에 그린다. 성공하면 status 응답을 돌려준다.
 * `opts.quiet`(자동 갱신): 로딩 문구로 비우지 않고, 실패하면 지금 화면을 그대로 두며,
 * 쓰던 커밋 메시지·커서·스크롤을 새 화면으로 옮긴다. */
export async function renderGitStatus(container, repo, opts = {}) {
  const o = {
    readOnly: false,
    onFile: () => {},
    onCommit: () => {},
    onOpenFile: null,
    ...opts,
    // 스테이지·커밋 뒤의 다시 그리기는 조용한 갱신이 아니다(커밋한 메시지는 비워야 한다).
    reload: opts.reload || (() => renderGitStatus(container, repo, { ...opts, quiet: false })),
  };
  let carry = null;
  if (o.quiet) {
    const prev = container.querySelector('.vt-vw-gmsg');
    if (prev) {
      carry = {
        value: prev.value,
        focused: document.activeElement === prev,
        start: prev.selectionStart,
        end: prev.selectionEnd,
      };
    }
  } else {
    container.innerHTML = `<div class="vt-vw-loading">${t('git status 확인 중…')}</div>`;
  }

  let d;
  try {
    d = await vtFetch(`/api/git/status?repo=${encodeURIComponent(repo)}`);
  } catch (e) {
    if (o.quiet) return null;
    // 2.1.6 — 서버 예외 문자열을 제목 자리에 그대로 두면 그게 이 화면의 이름처럼
    // 읽힌다. 제목은 사람 말, 원문은 detail, 그리고 다시 해볼 수 있는 일이다.
    container.innerHTML = '';
    container.appendChild(errorState(t('변경 사항을 불러오지 못했습니다'), e, () => renderGitStatus(container, repo, opts)));
    return null;
  }
  // 기다리는 사이 사용자가 diff·커밋 상세로 들어갔으면 그 화면을 덮지 않는다.
  if (o.quiet && typeof o.isCurrent === 'function' && !o.isCurrent()) return null;
  if (!d.repo) {
    // dock의 3분의 1을 차지하면서 "git 저장소가 아닙니다." 한 문장만 있던 자리다
    // (2.1.6 디자인 리뷰 Blocker 1). 빈 상태는 비었다는 사실이 아니라 **다음에
    // 뭘 할 수 있는지**를 말해야 한다.
    container.innerHTML = '';
    container.appendChild(emptyState({
      icon: 'git-branch',
      title: t('git 저장소가 아닙니다'),
      desc: t('이 워크트리에는 .git이 없습니다. 저장소 안의 세션을 열면 그 변경 사항이 여기 나옵니다.'),
    }));
    return d;
  }

  // 미추적 파일("??")은 index_status/worktree_status 둘 다 '?'로 채워지는데,
  // 실제 인덱스에는 없으므로 스테이지됨으로 분류하면 안 된다.
  const staged = d.files.filter(f => f.index_status && f.status !== '??');
  const unstaged = d.files.filter(f => f.status === '??' || (!f.index_status && f.worktree_status));

  const scrollTop = container.scrollTop;
  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'vt-vw-git';

  if (!d.files.length) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = t('변경된 내용이 없습니다.');
    wrap.appendChild(empty);
  } else {
    if (staged.length) wrap.appendChild(_gitSectionEl(t('스테이지됨'), staged, repo, true, o));
    if (unstaged.length) wrap.appendChild(_gitSectionEl(t('변경사항'), unstaged, repo, false, o));
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
  ta.placeholder = t('커밋 메시지');
  ta.disabled = !canCommit;
  const cbtn = document.createElement('button');
  cbtn.className = 'vt-btn solid vt-vw-gcommit-btn';
  cbtn.textContent = t('커밋');
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
  if (o.quiet) {
    const ta = wrap.querySelector('.vt-vw-gmsg');
    // 스테이지된 게 없어져 입력창이 잠겨도 쓰던 글은 남긴다 — 다시 스테이지하면 이어 쓴다.
    if (ta && carry) {
      ta.value = carry.value;
      if (carry.focused && !ta.disabled) {
        ta.focus({ preventScroll: true });
        try { ta.setSelectionRange(carry.start, carry.end); } catch (_) { /* 선택 불가 — 무시 */ }
      }
    }
    container.scrollTop = scrollTop;
  }

  _renderCommitLog(repo, logSec, 0, { ...o, branch: d.branch });
  return d;
}

// --- git log / show (커밋 기록 · 커밋 간 diff, 읽기 전용) -----------------------

const _LANE_W = 12;
const _ROW_H = 24;
const _LANE_COLORS = ['var(--acc)', 'var(--ok)', 'var(--warn)', '#a371f7', '#f778ba', '#39c5cf'];

// 레인 배정: lanes[i] = i번 줄이 다음에 기다리는 커밋 해시. 페이지를 이어 불러와도 상태를 넘긴다.
function _layoutGraph(state, c) {
  const lanes = state.lanes;
  const before = lanes.slice();
  let col = lanes.indexOf(c.hash);
  if (col < 0) { col = lanes.indexOf(null); if (col < 0) col = lanes.length; }
  const merging = [];
  lanes.forEach((h, i) => { if (h === c.hash && i !== col) merging.push(i); });
  lanes[col] = null;
  merging.forEach(i => { lanes[i] = null; });
  c.parents.forEach((ph, k) => {
    if (lanes.indexOf(ph) >= 0) return;
    let at = k === 0 ? col : lanes.indexOf(null);
    if (at < 0 || (k > 0 && at === col)) at = lanes.length;
    lanes[at] = ph;
  });
  while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
  return { before, after: lanes.slice(), col, merging, width: Math.max(before.length, lanes.length, col + 1) };
}

function _graphSvg(g) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', g.width * _LANE_W + 4);
  svg.setAttribute('height', _ROW_H);
  svg.setAttribute('class', 'vt-vw-graph');
  const x = i => i * _LANE_W + _LANE_W / 2 + 2;
  const mid = _ROW_H / 2;
  const color = i => _LANE_COLORS[i % _LANE_COLORS.length];
  const line = (x1, y1, x2, y2, i) => {
    const l = document.createElementNS(ns, 'path');
    const m = (y1 + y2) / 2;
    l.setAttribute('d', x1 === x2 ? `M${x1} ${y1}V${y2}` : `M${x1} ${y1}C${x1} ${m} ${x2} ${m} ${x2} ${y2}`);
    l.setAttribute('stroke', color(i));
    l.setAttribute('stroke-width', '1.5');
    l.setAttribute('fill', 'none');
    svg.appendChild(l);
  };
  g.before.forEach((h, i) => {
    if (h === null || i === g.col) return;
    if (g.merging.includes(i)) line(x(i), 0, x(g.col), mid, i);
    else line(x(i), 0, x(i), _ROW_H, i);
  });
  if (g.before[g.col] != null) line(x(g.col), 0, x(g.col), mid, g.col);
  g.after.forEach((h, i) => {
    if (h === null || i === g.col) return;
    if (g.before[i] === h) return; // 위에서 이어진 줄은 이미 그렸다
    line(x(g.col), mid, x(i), _ROW_H, i);
  });
  if (g.after[g.col] != null) line(x(g.col), mid, x(g.col), _ROW_H, g.col);
  const dot = document.createElementNS(ns, 'circle');
  dot.setAttribute('cx', x(g.col));
  dot.setAttribute('cy', mid);
  dot.setAttribute('r', '3.5');
  dot.setAttribute('fill', color(g.col));
  svg.appendChild(dot);
  return svg;
}

function _commitRowEl(repo, c, opts, graph) {
  const row = document.createElement('div');
  row.className = 'vt-vw-grow vt-vw-crow';
  if (graph) row.appendChild(_graphSvg(graph));
  const sha = document.createElement('span');
  sha.className = 'vt-vw-gstat';
  sha.textContent = c.short;
  const name = document.createElement('span');
  name.className = 'vt-vw-name';
  name.textContent = c.subject;
  name.title = `${c.author} · ${c.date}`;
  row.appendChild(sha);
  (c.refs || []).forEach(r => {
    const tag = document.createElement('span');
    tag.className = 'vt-vw-gref';
    tag.textContent = r.replace('HEAD -> ', '');
    row.appendChild(tag);
  });
  row.appendChild(name);
  row.addEventListener('click', () => opts.onCommit(c.hash));
  return row;
}

async function _buildBranchPicker(repo, container, current, opts) {
  let list = [];
  try {
    list = (await vtFetch(`/api/git/branches?repo=${encodeURIComponent(repo)}`)).branches || [];
  } catch (_) { return null; }
  if (!list.length) return null;
  const sel = document.createElement('select');
  sel.className = 'vt-vw-gbranch';
  sel.title = t('브랜치');
  list.forEach(b => {
    const o = document.createElement('option');
    o.value = b;
    o.textContent = b;
    sel.appendChild(o);
  });
  if (current && list.includes(current)) sel.value = current;
  sel.addEventListener('change', () => {
    container._ref = sel.value;
    _renderCommitLog(repo, container, 0, opts);
  });
  return sel;
}

// skip=0이면 헤더부터 새로 그린다. "더 보기"는 같은 container에 이어 붙인다.
async function _renderCommitLog(repo, container, skip, opts) {
  if (skip === 0) {
    container.innerHTML = '';
    container._graph = { lanes: [] };
    const head = document.createElement('div');
    head.className = 'vt-vw-ghead';
    head.textContent = t('커밋 기록');
    container.appendChild(head);
    const picker = await _buildBranchPicker(repo, container, container._ref || opts.branch, opts);
    if (picker) {
      head.appendChild(picker);
      container._ref = picker.value;
    }
  }
  const more = container.querySelector('.vt-vw-glog-more');
  if (more) more.remove();

  let d;
  try {
    const ref = container._ref ? `&ref=${encodeURIComponent(container._ref)}` : '';
    d = await vtFetch(`/api/git/log?repo=${encodeURIComponent(repo)}&skip=${skip}&limit=20${ref}`);
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
      empty.textContent = d.error || t('커밋이 없습니다.');
      container.appendChild(empty);
    }
    return;
  }
  d.commits.forEach(c => {
    const g = _layoutGraph(container._graph, c);
    container._graph.maxW = Math.max(container._graph.maxW || 1, g.width);
    container.appendChild(_commitRowEl(repo, c, opts, g));
  });
  // 모든 행의 그래프 칸을 가장 넓은 줄 수에 맞춰, 해시·제목 열이 행마다 흔들리지 않게 한다.
  container.style.setProperty('--vt-graph-w', `${container._graph.maxW * _LANE_W + 4}px`);
  if (d.has_more) {
    const btn = document.createElement('button');
    btn.className = 'vt-btn vt-vw-glog-more';
    btn.textContent = t('더 보기');
    btn.addEventListener('click', () => _renderCommitLog(repo, container, skip + d.commits.length, opts));
    container.appendChild(btn);
  }
}

/** 커밋 하나(메타 + 변경 파일 목록)를 container에 그린다. */
export async function renderCommit(container, repo, sha, opts = {}) {
  const o = { onBack: null, onFile: () => {}, ...opts };
  container.innerHTML = `<div class="vt-vw-loading">${t('불러오는 중…')}</div>`;

  let d;
  try {
    d = await vtFetch(`/api/git/show?repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }

  container.innerHTML = '';
  if (o.onBack) container.appendChild(_backBtn(t('‹ 상태로'), o.onBack));

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
  head.textContent = t('변경된 파일 ({n})', { n: d.files.length });
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
  container.innerHTML = `<div class="vt-vw-loading">${t('git show 실행 중…')}</div>`;

  let d;
  try {
    const q = `repo=${encodeURIComponent(repo)}&sha=${encodeURIComponent(sha)}&file=${encodeURIComponent(file)}`;
    d = await vtFetch(`/api/git/show?${q}`);
  } catch (e) {
    _setMsg(container, 'vt-vw-empty', [e.message]);
    return;
  }

  container.innerHTML = '';
  if (o.onBack) container.appendChild(_backBtn(t('‹ 커밋으로'), o.onBack));

  if (!d.diff || !d.diff.trim()) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = t('변경된 내용이 없습니다.');
    container.appendChild(empty);
    return;
  }
  _renderDiffDOM(container, d.diff);
  if (d.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.textContent = t('diff가 커서 일부만 표시했습니다.');
    container.appendChild(note);
  }
}

function _backBtn(label, onClick) {
  // 상단 고정 줄: 파일 경로 헤더(sticky)가 이 줄 바로 아래에 붙도록 CSS가 top 을 맞춘다.
  const bar = document.createElement('div');
  bar.className = 'vt-vw-cbar';
  const back = document.createElement('button');
  back.className = 'vt-btn vt-vw-cback';
  back.textContent = label;
  back.addEventListener('click', onClick);
  bar.appendChild(back);
  return bar;
}
