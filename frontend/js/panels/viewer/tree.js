// 사이드바 「파일」 활동(`vt-dir` 패널, `dir.show`) — 디렉토리 트리.
//
// 파일 개편 단계 5(2026-11)부터 트리 본문은 Solid + TypeScript(`frontend/js/files/`, `files` 지연 청크)다:
// 평탄화 목록 + 가상 스크롤 + 행 이벤트 위임 + 경로 키 비교 갱신, 키보드·aria-tree, 이름 필터, 페이지(1000개씩),
// 트리 루트 하나의 `tree` 감시(바뀐 폴더만 다시 받는다). 이 파일은 **패널 뼈대와 계약**만 남는다:
//   - 패널 id `vt-dir`·`showTree(opts)` — 활동 막대(shell/Dock.tsx)·팔레트·pane 리프(panels/registry.js)가 부른다.
//     `opts.paneHost`가 있으면 그 칸 안에 그린다(활동을 칸으로 끌어다 놓는 경로).
//   - 「경로 삽입」의 인용 규칙(`pathInsertText`·`insertPathToTerminal`) — 시험이 이 파일을 직접 불러 본다.
//   - `_viewerState.root/cwd` — 소스컨트롤 탭이 저장소 위치의 폴백으로 읽는다.
// 파일을 고르면 이 패널 안에서 열지 않고 파일 열기 브리지(window.openFile — ADR-44 표면 설정)로 넘긴다.
import { openPanel } from '../panel.js';
import { icon } from '../../ui/icons.js';
import { vtFetch } from '../../core/api.js';
import { get as getSetting, subscribe as subscribeSettings } from '../../core/settings.js';
import { relayHost } from '../../core/host-route.js';
import { copyToClipboard } from '../../term/clipboard.js';
import { _viewerState, _setMsg, _shareFile } from './state.js';
import { _loadRecent, _pushRecent, _clearRecent } from './recent.js';
import { watchPath } from '../../shell/fs-watch.js';
import { t } from '../../core/i18n.js';
import { shellQuotePath, hasControlChars } from '../../lib/shell-quote.js';

const PANEL_ID = 'vt-dir';

// 「경로 삽입」이 터미널에 타이핑하는 문자열. 파일 이름은 열람 경계 안의 누구나(클론한 저장소, 받은 압축 파일) 정할 수
// 있어서 `a$(touch x).txt` 같은 이름이 그대로 들어가면 Enter를 누르는 순간 실행된다. 업로드 삽입과 같은 인용
// 규칙(lib/shell-quote.js)을 쓴다 — 평범한 경로는 그대로 나간다. 제어문자가 든 경로는 인용으로 못 막으므로
// (hasControlChars 주석) null — 넣지 않는다.
export function pathInsertText(path) {
  if (hasControlChars(path)) return null;
  return shellQuotePath(path) + ' ';
}

// 활성 세션에 경로를 타이핑한다. 넣었으면 true. 거절한 이유는 토스트로 말한다.
export function insertPathToTerminal(path) {
  if (typeof window.activeSession !== 'function' || !window.activeSession()) {
    showToast(t('열려 있는 터미널 세션이 없습니다'));
    return false;
  }
  const text = pathInsertText(path);
  if (text == null) {
    showToast(t('경로에 제어문자가 있어 터미널에 넣지 않았습니다 — 줄바꿈 같은 문자는 셸이 바로 실행할 수 있습니다'), 'error');
    return false;
  }
  window.sendToPty(window.activeSessionId(), text);
  // 성공을 말하지 않는다 — 터미널에 경로가 실제로 타이핑된 것이 증거다.
  return true;
}

// 경로 복사 — 제어문자가 든 경로도 그대로 복사한다(붙여 넣는 곳이 셸이라면 사용자가 본다). 성공 여부만 돌려준다.
async function copyPath(path) {
  try { return !!(await copyToClipboard(path)); } catch (_) { return false; }
}

async function terminalCwd() {
  const s = typeof window.activeSession === 'function' ? window.activeSession() : null;
  if (!s) { showToast(t('열려 있는 터미널 세션이 없습니다')); return null; }
  const tmuxName = s.tmuxName || s.tmux_name;
  if (!tmuxName) { showToast(t('현재 세션은 tmux 세션이 아니라 위치를 알 수 없습니다')); return null; }
  let list;
  try { list = await vtFetch('/api/tmux/sessions'); }
  catch (e) { showToast(t('위치 확인 실패: {msg}', { msg: e.message })); return null; }
  const info = (list || []).find((x) => x.name === tmuxName);
  if (!info || !info.cwd) { showToast(t('현재 터미널 위치를 확인할 수 없습니다')); return null; }
  return info.cwd;
}

function sessionName() {
  const s = typeof window.activeSession === 'function' ? window.activeSession() : null;
  return (s && (s.name || s.tmuxName || s.tmux_name)) || '';
}

// 호스트 표시 이름 — `/api/hosts`(로컬 경로, 중계하지 않는다)의 label. 못 받으면 id 그대로.
async function hostLabel(host) {
  try {
    const list = await vtFetch('/api/hosts');
    const hosts = Array.isArray(list) ? list : (list && Array.isArray(list.hosts) ? list.hosts : []);
    const h = hosts.find((x) => x && x.id === host);
    return (h && h.label) || (host === 'local' ? t('이 컴퓨터') : host);
  } catch (_) {
    return host === 'local' ? t('이 컴퓨터') : host;
  }
}

/** 트리 본문 모듈(files 청크). 측정 페이지도 같은 진입점을 부른다. */
export function loadTreeModule() {
  return import('../../files/mount.tsx');
}

export async function showTree(opts = {}) {
  const panel = openPanel({
    paneHost: opts.paneHost || null,
    paneId: opts.paneId || null,
    id: PANEL_ID,
    ariaLabel: t('파일'),
    headHTML: `<div class="vt-vw-title">${t('파일')}</div>`
      + `<button type="button" class="vt-icon-btn" id="vt-dir-refresh" title="${t('새로고침')}" aria-label="${t('새로고침')}">${icon('refresh-cw', 14)}</button>`,
    bodyId: 'vt-dir-body',
  });
  if (!panel) return null;   // 토글 — 이미 열려 있어서 닫기만 했다

  const body = panel.body;
  body.classList.add('vt-ft-host-body');
  let startRoot = opts.root || _viewerState.root;
  if (!startRoot) {
    try {
      const d = await vtFetch('/api/fs/roots');
      startRoot = (d && d.roots && d.roots[0]) || null;
    } catch (_) { startRoot = null; }
  }
  if (!startRoot) {
    _setMsg(body, 'vt-vw-empty', [t('열람 가능한 위치를 찾을 수 없습니다')]);
    return null;
  }

  let mod;
  try { mod = await loadTreeModule(); } catch (e) {
    _setMsg(body, 'vt-vw-empty', [t('파일 트리를 불러오지 못했습니다'), String(e && e.message || e)]);
    return null;
  }
  if (!body.isConnected) return null;   // 받는 사이 패널이 닫혔다

  const host = relayHost();
  body.innerHTML = '';
  const mounted = mod.mountFileTree(body, {
    host,
    remote: host !== 'local',
    fetchJSON: (p) => vtFetch(p),
    // pane에 붙었던 인스턴스는 칸이 바뀌면 닫기 훅 없이 떨어져 나간다 — 알림이 올 때 거둔다.
    watch: (kind, path, fn) => watchPath(kind, path, (detail) => {
      if (!body.isConnected) { dispose(); return; }
      fn(detail);
    }),
    fetchHostLabel: () => hostLabel(host),
    openFile: (path) => {
      if (typeof window.openFile !== 'function' || !path) return;
      window.openFile(path);
      _pushRecent(path);
    },
    insertPath: insertPathToTerminal,
    copyPath,
    sharePath: _shareFile,
    terminalCwd,
    sessionName,
    recent: _loadRecent,
    clearRecent: _clearRecent,
    toast: (msg, kind) => showToast(msg, kind),
    getDense: () => !!getSetting('files.tree.dense'),
    subscribeSettings,
    onRoot: (root) => { _viewerState.root = root; _viewerState.cwd = root; },
  });
  let disposed = false;
  function dispose() {
    if (disposed) return;
    disposed = true;
    mounted.dispose();
  }
  panel.el._vtOnClose = dispose;
  panel.ctx.byId('vt-dir-refresh')?.addEventListener('click', () => { void mounted.store.refreshVisible(); });
  try {
    await mounted.store.open(startRoot);
    // 처음 열기가 끝나기 전에 사용자가 경로를 입력해 다른 곳을 열었을 수 있다 — 그때는 이 결과가 버려졌고
    // 루트는 사용자가 연 곳이다(onRoot가 이미 기록했다). 실제로 열린 루트만 기록한다.
    const root = mounted.store.root;
    if (root) { _viewerState.root = root; _viewerState.cwd = root; }
  } catch (e) {
    showToast(t('이동할 수 없습니다: {msg}', { msg: e.message }));
  }
  return mounted;
}
