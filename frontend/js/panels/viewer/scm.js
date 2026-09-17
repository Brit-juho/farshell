// N35 §6 / 40 §3·§5 — dock 「소스컨트롤」 탭.
//
// 2.1.1부터 stage/unstage/커밋이 실제로 동작한다(ambient git, D16) — 승격
// 세션·계정 바인딩은 붙이지 않는다(ADR-27, 40 §1·§2는 구현만 되고 미사용).
// push·PR/MR 버튼은 **아예 렌더하지 않는다**(ADR-27) — disabled로도 남기지
// 않는다. 계정 칩(`[계정: gh-work ▾]`)도 마찬가지로 없다.
//
// 화면 자체는 새로 만들지 않는다: 2.0 코드 뷰어의 git 탭 렌더러(git.js·diff.js)를
// "어디에 그릴지"만 인자로 받게 바꿔서 그대로 쓴다. 같은 화면을 두 벌 만들면
// 반드시 어긋난다.
//
// 이 파일은 viewer 지연 청크(shell.js)에 속한다 — app.js 번들 상한(ADR-26) 때문에
// 정적 import는 금지. 진입점은 panels/viewer-lazy.js의 `scm.show` 액션이다.
import { openPanel } from '../panel.js';
import { vtFetch } from '../../core/api.js';
import { renderGitStatus, renderCommit, renderCommitFileDiff } from './git.js';
import { renderFileDiff } from './diff.js';
import { _viewerState } from './state.js';

const PANEL_ID = 'vt-dock-scm';

/** 대상 저장소 = 활성 페인의 워크트리(30) 또는 세션 cwd. 둘 다 없으면 뷰어 루트. */
async function _currentRepo() {
  const w = window;
  const sid = w.activeSessionId ? w.activeSessionId() : null;
  const s = sid && w.allSessions ? w.allSessions()[sid] : null;
  const tmuxName = s && (s.tmuxName || s.tmux_name);
  if (tmuxName) {
    try {
      const list = await vtFetch('/api/tmux/sessions');
      const hit = (list || []).find((t) => t.name === tmuxName);
      if (hit && hit.cwd) return hit.cwd;
    } catch (_) { /* 아래 폴백 */ }
  }
  if (_viewerState.cwd || _viewerState.root) return _viewerState.cwd || _viewerState.root;
  // 코드 뷰어를 아직 한 번도 안 연 상태(뷰어 상태가 비어 있다)에서도 dock은
  // 뭔가를 보여줘야 한다 — 서버가 첫 화면 기준으로 쓰는 시작 루트를 그대로
  // 쓴다(열람 경계 자체가 아니라 시작 지점: /api/fs/roots).
  try {
    const d = await vtFetch('/api/fs/roots');
    return (d && d.roots && d.roots[0]) || null;
  } catch (_) { return null; }
}

function _el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// 머리말 한 줄: `feat/x ← origin/feat/x  ↑3 ↓0  +142 −38  파일 7`.
// 값이 없는 조각은 **그리지 않는다**(2.0 규칙 — 빈 칸을 만들지 않는다).
function _renderHead(headEl, repo, d) {
  headEl.innerHTML = '';
  const left = _el('div', 'vt-scm-head-left');
  if (!repo) {
    left.appendChild(_el('span', 'vt-scm-repo', '열린 세션 없음'));
    headEl.appendChild(left);
    return;
  }
  left.appendChild(_el('span', 'vt-scm-repo', repo.split('/').pop()));
  if (d && d.repo) {
    if (d.branch) left.appendChild(_el('span', 'vt-scm-branch', d.branch));
    if (d.upstream) left.appendChild(_el('span', 'vt-scm-upstream', `← ${d.upstream}`));
    if (d.ahead) left.appendChild(_el('span', 'vt-scm-track', `↑${d.ahead}`));
    if (d.behind) left.appendChild(_el('span', 'vt-scm-track', `↓${d.behind}`));
    if (d.insertions) left.appendChild(_el('span', 'vt-scm-add', `+${d.insertions}`));
    if (d.deletions) left.appendChild(_el('span', 'vt-scm-del', `−${d.deletions}`));
    if (d.files && d.files.length) left.appendChild(_el('span', 'vt-scm-count', `파일 ${d.files.length}`));
  }
  headEl.appendChild(left);
  // 계정 칩 없음(ADR-27) — 40 §1의 계정 저장소는 구현만 되고 dock에 연결하지 않는다.
}

export async function showScm() {
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: '소스컨트롤',
    headHTML: '<div class="vt-scm-head" id="vt-scm-head"></div>'
      + '<button class="vt-btn sm vt-vw-diff" id="vt-scm-refresh" title="새로고침">새로고침</button>',
    bodyId: 'vt-scm-body',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

  const body = panel.body;
  const headEl = panel.el.querySelector('#vt-scm-head');

  // 파일을 뷰어 페인(N35 §6 리프 타입 viewer)으로 연다. entry.file은 저장소
  // 상대경로라 repo(절대경로)와 합쳐야 /api/fs/*(fsguard)가 받는 절대경로가
  // 된다. window.openFileInPane은 viewer-lazy.js가 다는 전역 브리지 —
  // dock 청크(panels.js)에서 app.js의 store.js를 정적 import할 수 없어서
  // (ADR-26) 이 저장소 관행대로 브리지를 쓴다.
  const openInViewer = (repo, file) => {
    if (typeof window.openFileInPane !== 'function' || !repo || !file) return;
    const abs = `${repo.replace(/\/+$/, '')}/${file}`;
    window.openFileInPane(abs);
  };

  const paint = async () => {
    const repo = await _currentRepo();
    if (!repo) {
      _renderHead(headEl, null, null);
      body.innerHTML = '<div class="vt-vw-empty">저장소를 찾을 수 없습니다. 세션을 열면 그 작업 디렉터리를 봅니다.</div>';
      return;
    }
    const back = () => paint();
    const d = await renderGitStatus(body, repo, {
      readOnly: false,
      onFile: (file, staged) => renderFileDiff(body, repo, file, staged, { onBack: back }),
      onOpenFile: (file) => openInViewer(repo, file),
      onCommit: (sha) => renderCommit(body, repo, sha, {
        onBack: back,
        onFile: (file) => renderCommitFileDiff(body, repo, sha, file, {
          onBack: () => renderCommit(body, repo, sha, { onBack: back, onFile: (f) => renderCommitFileDiff(body, repo, sha, f, { onBack: back }) }),
        }),
      }),
    });
    _renderHead(headEl, repo, d);
  };

  panel.el.querySelector('#vt-scm-refresh').addEventListener('click', paint);
  // 세션을 바꾸면 보는 저장소도 바뀐다 — dock은 "지금 보고 있는 페인"을 따라간다.
  const unsub = window.storeSubscribe ? window.storeSubscribe(() => paint()) : null;
  panel.el._vtOnClose = () => { if (unsub) unsub(); };

  await paint();
}
