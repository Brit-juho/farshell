// dock "파일" 탭 — 디렉토리 트리 브라우저. N35 §6이 모달 코드 뷰어와 함께 지운
// 폴더 트리(구 viewer/tree.js)를 dock 탭으로 되살린다(2026-09-22, 사용자 확인 —
// N35의 "코드뷰어는 dock에 안 들어간다" 결정을 번복). git diff/커밋은 여기
// 없다 — 그건 소스컨트롤 탭(scm.js) 몫이라 겹치지 않는다.
//
// 구버전과 다른 점: 모달 셸(shell.js, 1230줄) 전체가 아니라 트리 화면만 가져온다.
// 파일을 고르면 이 패널 안에서 열지 않고 파일 열기 브리지(window.openFile —
// ADR-44 표면 설정을 따른다)로 넘긴다.
import { openPanel } from '../panel.js';
import { icon } from '../../ui/icons.js';
import { vtFetch } from '../../core/api.js';
import { _viewerState, _setMsg, _ICON_CHEVRON, _ICON_INSERT } from './state.js';
import { _loadRecent, _pushRecent, _clearRecent } from './recent.js';
import { _fmtSize } from './file.js';
import { watchPath, coalesce } from './watch.js';

const PANEL_ID = 'vt-dir';

// OS가 만드는 메타데이터 파일만 목록에서 뺀다 — 구 tree.js와 같은 이유
// (사용자가 만든 dot 디렉토리는 실제로 열어볼 일이 있어 남긴다).
const OS_NOISE = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini', '.localized']);
const _denoise = (entries) => (entries || []).filter((e) => !OS_NOISE.has(e.name));
const _isDescendant = (path, of) => path !== of && path.startsWith(of.replace(/\/$/, '') + '/');

export async function showTree(opts = {}) {
  const panel = openPanel({
    paneHost: opts.paneHost || null,
    paneId: opts.paneId || null,
    id: PANEL_ID,
    ariaLabel: '파일',
    headHTML: '<div class="vt-vw-title">파일</div>'
      + `<button type="button" class="vt-icon-btn" id="vt-dir-here" title="현재 터미널 위치로 열기">${icon('terminal', 14)}</button>`
      + '<button type="button" class="vt-btn sm vt-vw-diff" id="vt-dir-refresh" title="새로고침">새로고침</button>',
    extraHTML: '<input class="vt-vw-path" id="vt-dir-path" type="text" spellcheck="false" autocapitalize="off" autocomplete="off" '
      + 'title="경로를 입력하고 Enter — 이 위치를 최상단으로 엽니다">',
    bodyId: 'vt-dir-body',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

  const body = panel.body;
  const pathInput = panel.ctx.byId('vt-dir-path');
  let treeEl = null;   // navigateRoot가 처음 채운 뒤로는 body를 통째로 안 갈아엎는다
  // 자동 갱신 — 최상단과 펼친 디렉토리마다 비재귀 구독 하나. path -> 해제 함수.
  const watches = new Map();

  const openInPane = (path) => {
    if (typeof window.openFile !== 'function' || !path) return;
    window.openFile(path);
    _pushRecent(path);
  };

  const insertToTerminal = (path) => {
    if (typeof window.activeSession !== 'function' || !window.activeSession()) {
      showToast('열려 있는 터미널 세션이 없습니다');
      return;
    }
    window.sendToPty(window.activeSessionId(), path + ' ');
    // 성공을 말하지 않는다 — 터미널에 경로가 실제로 타이핑된 것이 증거다.
  };

  const openAtTerminalCwd = async () => {
    const s = typeof window.activeSession === 'function' ? window.activeSession() : null;
    if (!s) { showToast('열려 있는 터미널 세션이 없습니다'); return; }
    const tmuxName = s.tmuxName || s.tmux_name;
    if (!tmuxName) { showToast('현재 세션은 tmux 세션이 아니라 위치를 알 수 없습니다'); return; }
    let list;
    try { list = await vtFetch('/api/tmux/sessions'); }
    catch (e) { showToast(`위치 확인 실패: ${e.message}`); return; }
    const info = (list || []).find((t) => t.name === tmuxName);
    if (!info || !info.cwd) { showToast('현재 터미널 위치를 확인할 수 없습니다'); return; }
    await navigateRoot(info.cwd);
  };

  function _selectFile(path, row) {
    body.querySelectorAll('.vt-vw-trow.active').forEach((r) => r.classList.remove('active'));
    if (row) row.classList.add('active');
    openInPane(path);
  }

  function _recentSectionEl() {
    const list = _loadRecent();
    if (!list.length) return null;
    const sec = document.createElement('div');
    sec.className = 'vt-vw-recent';
    const head = document.createElement('div');
    head.className = 'vt-vw-recent-head';
    const label = document.createElement('span');
    label.textContent = '최근';
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'vt-vw-recent-clear';
    clear.textContent = '지우기';
    clear.addEventListener('click', (e) => { e.stopPropagation(); _clearRecent(); sec.remove(); });
    head.appendChild(label);
    head.appendChild(clear);
    sec.appendChild(head);
    list.forEach((p) => {
      const row = document.createElement('div');
      row.className = 'vt-vw-row vt-vw-recent-row';
      const name = document.createElement('div');
      name.className = 'vt-vw-name';
      name.textContent = p.split('/').pop() || p;
      const dir = document.createElement('div');
      dir.className = 'vt-vw-recent-dir';
      // 루트 밑 상대경로만 보여준다 — 전체 경로는 폰 폭에서 앞부분이 다 잘린다.
      const rootPrefix = (_viewerState.root || '').replace(/\/$/, '') + '/';
      const rel = p.startsWith(rootPrefix) ? p.slice(rootPrefix.length) : p;
      dir.textContent = rel.split('/').slice(0, -1).join('/') || '.';
      row.appendChild(name);
      row.appendChild(dir);
      row.addEventListener('click', () => _selectFile(p, null));
      sec.appendChild(row);
    });
    return sec;
  }

  function _treeRowEl(entry, path, depth) {
    const row = document.createElement('div');
    row.className = 'vt-vw-trow' + (entry.dir ? ' dir' : '');
    row.style.setProperty('--d', depth);
    row.dataset.path = path;

    const chev = document.createElement('span');
    chev.className = 'vt-vw-chev';
    chev.innerHTML = _ICON_CHEVRON;
    const name = document.createElement('span');
    name.className = 'vt-vw-name';
    name.textContent = entry.name;   // textContent — XSS 방어
    row.appendChild(chev);
    row.appendChild(name);

    if (!entry.dir) {
      const size = document.createElement('span');
      size.className = 'vt-vw-size';
      size.textContent = _fmtSize(entry.size);
      row.appendChild(size);
      // L2: 파일을 열지 않고도 경로만 터미널에 바로 꽂을 수 있는 행별 버튼.
      const insert = document.createElement('button');
      insert.type = 'button';
      insert.className = 'vt-icon-btn xs vt-vw-row-insert';
      insert.title = '터미널에 경로 삽입';
      insert.innerHTML = _ICON_INSERT;
      insert.addEventListener('click', (e) => { e.stopPropagation(); insertToTerminal(path); });
      row.appendChild(insert);
    }

    row.addEventListener('click', () => {
      if (entry.dir) _toggleDir(row, path, depth);
      else _selectFile(path, row);
    });
    return row;
  }

  function _upRowEl() {
    const row = document.createElement('div');
    row.className = 'vt-vw-trow vt-vw-up';
    row.style.setProperty('--d', 0);
    const chev = document.createElement('span');
    chev.className = 'vt-vw-chev';
    chev.innerHTML = _ICON_CHEVRON;
    const name = document.createElement('span');
    name.className = 'vt-vw-name';
    name.textContent = '..';
    row.appendChild(chev);
    row.appendChild(name);
    row.addEventListener('click', _goUpRoot);
    return row;
  }

  async function _goUpRoot() {
    const cur = _viewerState.root;
    if (!cur || cur === '/') return;
    const parent = cur.replace(/\/[^/]+\/?$/, '') || '/';
    await navigateRoot(parent);
  }

  function _renderTopLevel(data) {
    treeEl.innerHTML = '';
    const frag = document.createDocumentFragment();
    if (_viewerState.root !== '/') frag.appendChild(_upRowEl());
    const recent = _recentSectionEl();
    if (recent) frag.appendChild(recent);

    const entries = _denoise(data.entries);
    if (!entries.length) {
      treeEl.appendChild(frag);
      const empty = document.createElement('div');
      empty.className = 'vt-vw-empty';
      empty.textContent = '빈 디렉토리';
      treeEl.appendChild(empty);
      return;
    }
    entries.forEach((entry) => {
      const childPath = _viewerState.root.replace(/\/$/, '') + '/' + entry.name;
      frag.appendChild(_treeRowEl(entry, childPath, 0));
    });
    if (data.truncated) {
      const note = document.createElement('div');
      note.className = 'vt-vw-note warn';
      note.style.setProperty('--d', 0);
      note.textContent = `항목이 많아 일부만 표시했습니다 (최대 ${data.entries.length}개)`;
      frag.appendChild(note);
    }
    treeEl.appendChild(frag);
  }

  async function _toggleDir(row, path, depth) {
    if (_viewerState.expanded.has(path)) { _collapseDir(row, path); return; }

    row.classList.add('open');
    _viewerState.expanded.add(path);
    const chev = row.querySelector('.vt-vw-chev');
    const prevIcon = chev.innerHTML;
    chev.innerHTML = '<span class="vt-vw-trow-spin"></span>';

    let data;
    try {
      data = await vtFetch(`/api/fs/tree?path=${encodeURIComponent(path)}`);
    } catch (e) {
      chev.innerHTML = prevIcon;
      row.classList.remove('open');
      _viewerState.expanded.delete(path);
      showToast(`목록을 불러오지 못했습니다: ${e.message}`);
      return;
    }
    chev.innerHTML = prevIcon;   // 펼쳐진 화살표 방향은 .open의 CSS 회전이 담당한다
    row.after(_childrenFrag(path, depth, data));
    _syncWatches();
  }

  function _childrenFrag(path, depth, data) {
    const frag = document.createDocumentFragment();
    const entries = _denoise(data.entries);
    entries.forEach((entry) => {
      const childPath = path.replace(/\/$/, '') + '/' + entry.name;
      frag.appendChild(_treeRowEl(entry, childPath, depth + 1));
    });
    if (data.truncated) {
      const note = document.createElement('div');
      note.className = 'vt-vw-note warn';
      note.style.setProperty('--d', depth + 1);
      note.textContent = `일부만 표시했습니다 (최대 ${data.entries.length}개)`;
      frag.appendChild(note);
    }
    if (!entries.length && !data.truncated) {
      const empty = document.createElement('div');
      empty.className = 'vt-vw-empty';
      empty.style.setProperty('--d', depth + 1);
      empty.textContent = '빈 디렉토리';
      frag.appendChild(empty);
    }
    return frag;
  }

  function _collapseDir(row, path) {
    row.classList.remove('open');
    _viewerState.expanded.delete(path);
    // 이 행 바로 다음부터, path 하위였던 행(과 그 사이의 안내문)을 전부 제거한다.
    let next = row.nextElementSibling;
    while (next && (!next.dataset.path || _isDescendant(next.dataset.path, path))) {
      const rm = next;
      next = next.nextElementSibling;
      rm.remove();
    }
    for (const p of Array.from(_viewerState.expanded)) {
      if (_isDescendant(p, path)) _viewerState.expanded.delete(p);
    }
    _syncWatches();
  }

  // 구독을 "최상단 + 펼친 디렉토리"에 맞춘다. 접으면 풀고, 펼치면 건다.
  function _syncWatches() {
    const want = new Set(_viewerState.root ? [_viewerState.root, ..._viewerState.expanded] : []);
    for (const [p, off] of watches) {
      if (!want.has(p)) { off(); watches.delete(p); }
    }
    for (const p of want) {
      if (!watches.has(p)) watches.set(p, watchPath('dir', p, reloadTree));
    }
  }

  function _unwatchAll() {
    for (const off of watches.values()) off();
    watches.clear();
  }

  // 디스크가 바뀌면 트리를 다시 그린다. 펼친 디렉토리·선택한 행·스크롤은 지킨다
  // (새로고침 버튼은 최상단으로 다시 여는 것이라 펼침을 접는다 — 둘은 다른 동작이다).
  const reloadTree = coalesce(async () => {
    // pane에 붙었던 인스턴스는 칸이 바뀌면 닫기 훅 없이 떨어져 나간다 — 여기서 거둔다.
    if (!treeEl || !treeEl.isConnected) { _unwatchAll(); return; }
    const root = _viewerState.root;
    if (!root) return;
    const snap = Array.from(_viewerState.expanded);
    const fetchTree = (p) => vtFetch(`/api/fs/tree?path=${encodeURIComponent(p)}`);
    let top;
    try { top = await fetchTree(root); } catch (_) { return; }
    const listings = new Map();
    await Promise.all(snap.map(async (p) => {
      try { listings.set(p, await fetchTree(p)); } catch (_) { /* 지워진 디렉토리 — 접힌다 */ }
    }));
    // 기다리는 사이 사용자가 다른 곳으로 갔거나 펼침을 바꿨으면 이번 결과는 버린다.
    // 펼침만 바뀐 경우는 구독이 그대로라 다음 변경 때 다시 맞춰진다.
    if (_viewerState.root !== root || !treeEl.isConnected) return;
    if (Array.from(_viewerState.expanded).join('\n') !== snap.join('\n')) return;

    const scrollTop = body.scrollTop;
    const activePath = treeEl.querySelector('.vt-vw-trow.active')?.dataset.path || null;
    _viewerState.expanded = new Set();
    _renderTopLevel(top);
    const expand = (row, path, depth) => {
      const data = listings.get(path);
      if (!data) return;
      row.classList.add('open');
      _viewerState.expanded.add(path);
      const frag = _childrenFrag(path, depth, data);
      const kids = Array.from(frag.children).filter((c) => c.classList.contains('dir'));
      row.after(frag);
      for (const k of kids) expand(k, k.dataset.path, depth + 1);
    };
    for (const row of Array.from(treeEl.querySelectorAll(':scope > .vt-vw-trow.dir'))) {
      expand(row, row.dataset.path, 0);
    }
    if (activePath) {
      for (const row of treeEl.querySelectorAll('.vt-vw-trow')) {
        if (row.dataset.path === activePath) { row.classList.add('active'); break; }
      }
    }
    body.scrollTop = scrollTop;
    _syncWatches();
  });

  async function navigateRoot(path) {
    let data;
    try {
      data = await vtFetch(`/api/fs/tree?path=${encodeURIComponent(path)}`);
    } catch (e) {
      showToast(`이동할 수 없습니다: ${e.message}`);
      return false;
    }
    _viewerState.root = path;
    _viewerState.cwd = path;
    _viewerState.expanded = new Set();
    pathInput.value = path;
    _renderTopLevel(data);
    _syncWatches();
    return true;
  }

  // 주소창에 경로를 직접 입력해 트리 최상단을 그 경로로 바꾼다. 서버
  // (fsguard.resolve_under_roots)가 경계 안인지 다시 검증하므로 여기서는
  // 별도 화이트리스트 검사 없이 그대로 요청한다 — 거부되면 토스트만 띄운다.
  pathInput.addEventListener('focus', () => pathInput.select());
  pathInput.addEventListener('keydown', async (ev) => {
    if (ev.key === 'Escape') { pathInput.value = _viewerState.root || ''; pathInput.blur(); return; }
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    const v = pathInput.value.trim();
    if (!v) return;
    const prevRoot = _viewerState.root;
    const ok = await navigateRoot(v);
    if (!ok) pathInput.value = prevRoot || '';
    pathInput.blur();
  });
  panel.ctx.byId('vt-dir-here').addEventListener('click', openAtTerminalCwd);
  panel.ctx.byId('vt-dir-refresh').addEventListener('click', () => { if (_viewerState.root) navigateRoot(_viewerState.root); });

  panel.el._vtOnClose = _unwatchAll;

  body.innerHTML = '<div class="vt-vw-loading">불러오는 중…</div>';
  let startRoot = _viewerState.root;
  if (!startRoot) {
    try {
      const d = await vtFetch('/api/fs/roots');
      startRoot = (d && d.roots && d.roots[0]) || null;
    } catch (_) { startRoot = null; }
  }
  if (!startRoot) {
    _setMsg(body, 'vt-vw-empty', ['열람 가능한 위치를 찾을 수 없습니다']);
    return;
  }

  body.innerHTML = '';
  treeEl = document.createElement('div');
  body.appendChild(treeEl);
  await navigateRoot(startRoot);
}
