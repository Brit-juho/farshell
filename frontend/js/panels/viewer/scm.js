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
import { emptyState } from '../../ui/empty.js';
import { icon } from '../../ui/icons.js';
import { vtFetch } from '../../core/api.js';
import { renderGitStatus, renderCommit, renderCommitFileDiff, _gitStatusLetter, _gitFileLabel } from './git.js';
import { renderFileDiff } from './diff.js';
import { _viewerState } from './state.js';
import { t } from '../../core/i18n.js';
import { watchPath, coalesce } from '../../shell/fs-watch.js';

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

// 머리의 맥락 한 줄: `저장소 / 브랜치`(시안 P01 머리 ctx). 브랜치·upstream·↑↓·±는 본문 첫 줄(git.js)이 그린다.
// 값이 없는 조각은 **그리지 않는다**(2.0 규칙 — 빈 칸을 만들지 않는다).
function _renderHead(headEl, repo, d) {
  if (!repo) { headEl.textContent = t('열린 세션 없음'); headEl.title = ''; return; }
  const name = repo.split('/').pop();
  headEl.textContent = d && d.repo && d.branch ? `${name} / ${d.branch}` : name;
  headEl.title = repo;
  // 계정 칩 없음(ADR-27) — 40 §1의 계정 저장소는 구현만 되고 dock에 연결하지 않는다.
}

// N4 — `opts.paneHost`가 있으면 dock/모달이 아니라 그 pane 칸에 그린다.
// 이 패널은 이식 비용이 거의 없었다: 본문 조회를 이미 `panel.body`·
// `panel.el.querySelector`로 하고 있어서 전역 id에 기대는 곳이 헤더 둘뿐이다.
export async function showScm(opts = {}) {
  const panel = openPanel({
    paneHost: opts.paneHost || null,
    paneId: opts.paneId || null,
    id: PANEL_ID,
    ariaLabel: t('소스컨트롤'),
    headHTML: `<div class="vt-vw-title">${t('소스컨트롤')}</div>`
      + '<span class="vt-ph-ctx vt-scm-head" id="vt-scm-head"></span>'
      + `<button type="button" class="vt-icon-btn sm" id="vt-scm-refresh" aria-label="${t('새로고침')}" data-tip="${t('새로고침')}">${icon('refresh-cw', 14)}</button>`,
    bodyId: 'vt-scm-body',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

  const body = panel.body;
  // pane 인스턴스는 안쪽 id에 접미사가 붙는다 — ctx가 그걸 안다.
  const headEl = panel.ctx.byId('vt-scm-head');

  // 파일을 연다(ADR-44 표면 설정을 따른다). entry.file은 저장소 상대경로라
  // repo(절대경로)와 합쳐야 /api/fs/*(fsguard)가 받는 절대경로가 된다.
  // window.openFile은 viewer-lazy.js가 다는 전역 브리지다.
  const openInViewer = (repo, file) => {
    if (typeof window.openFile !== 'function' || !repo || !file) return;
    const abs = `${repo.replace(/\/+$/, '')}/${file}`;
    window.openFile(abs);
  };

  // 자동 갱신 — 보고 있는 저장소의 작업 디렉토리를 재귀로 구독한다. 변경 목록을
  // 보고 있을 때만 다시 그린다: diff·커밋 상세를 읽는 중에 목록으로 튕기면 안 된다.
  let listing = false;
  let watched = null;
  let unwatch = null;
  const refreshIfListing = coalesce(async () => {
    if (!body.isConnected) { stopWatch(); return; }
    if (listing) await paint({ quiet: true });
  });
  const stopWatch = () => { if (unwatch) unwatch(); unwatch = null; watched = null; };
  const watchRepo = (repo) => {
    if (repo === watched) return;
    stopWatch();
    if (!repo) return;
    watched = repo;
    unwatch = watchPath('repo', repo, refreshIfListing);
  };
  const detail = (fn) => (...args) => { listing = false; return fn(...args); };

  const paint = async ({ quiet = false } = {}) => {
    listing = true;
    const repo = await _currentRepo();
    watchRepo(repo);
    if (!repo) {
      _renderHead(headEl, null, null);
      body.innerHTML = '';
      body.appendChild(emptyState({
        icon: 'folder',
        title: t('볼 저장소가 없습니다'),
        desc: t('세션을 열면 그 작업 디렉터리의 변경 사항을 여기서 봅니다.'),
      }));
      return;
    }
    const back = () => paint();
    const d = await renderGitStatus(body, repo, {
      quiet,
      isCurrent: () => listing,
      readOnly: false,
      onFile: detail((file, staged, entry) => renderFileDiff(body, repo, file, staged, {
        onBack: back,
        onOpenFile: (f) => openInViewer(repo, f),
        status: entry ? _gitStatusLetter(entry, staged) : '',
        statusLabel: entry ? _gitFileLabel(entry) : '',
      })),
      onOpenFile: (file) => openInViewer(repo, file),
      onCommit: detail((sha) => renderCommit(body, repo, sha, {
        onBack: back,
        onFile: (file) => renderCommitFileDiff(body, repo, sha, file, {
          onBack: () => renderCommit(body, repo, sha, { onBack: back, onFile: (f) => renderCommitFileDiff(body, repo, sha, f, { onBack: back }) }),
        }),
      })),
    });
    // 조용한 갱신이 실패하면(null) 머리말도 지금 것을 둔다.
    if (d || !quiet) _renderHead(headEl, repo, d);
  };

  panel.ctx.byId('vt-scm-refresh').addEventListener('click', () => paint());
  // 세션을 바꾸면 보는 저장소도 바뀐다 — dock은 "지금 보고 있는 페인"을 따라간다.
  const unsub = window.storeSubscribe ? window.storeSubscribe(() => paint()) : null;
  panel.el._vtOnClose = () => { if (unsub) unsub(); stopWatch(); };

  await paint();
}
