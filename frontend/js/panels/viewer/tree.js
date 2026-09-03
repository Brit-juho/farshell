// 코드 뷰어 계층 트리 — F4에서 viewer.js에서 분리. 루트 네비게이션(주소창 입력,
// 현재 터미널 위치로 열기, 경로를 터미널에 삽입) + 폴더 펼침/접기 트리 + "최근
// 연 파일" 섹션을 담당한다. shell.js와 순환 import 관계다(shell.js 헤더 주석 참고).
import { vtFetch } from '../../core/api.js';
import { activeSession, activeSessionId } from '../../core/store.js';
import { sendToPty } from '../../term/clipboard.js';
import { _viewerState, _setMsg, _ICON_CHEVRON, _ICON_INSERT } from './state.js';
import { _setPath, _setActivePane } from './shell.js';
import { openFile } from './file.js';

// 주소창에 경로를 직접 입력해 트리 최상단을 그 경로로 바꾼다.
// 서버(fsguard.resolve_under_roots)가 VT_BROWSE_ROOTS 경계 안인지 다시 검증하므로
// 여기서는 별도 화이트리스트 검사 없이 그대로 요청한다 — 거부되면 토스트만 띄운다.
export async function _navigateRoot(path) {
  const treeEl = document.getElementById('vt-vw-tree');
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
  _setPath(path);
  if (_viewerState.displayMode === 'sheet') _setActivePane('tree');
  _renderTopLevel(treeEl, data);
  return true;
}

export function _wirePathInput(el) {
  const input = el.querySelector('#vt-vw-path');
  if (!input) return;
  input.addEventListener('focus', () => input.select());
  input.addEventListener('keydown', async (ev) => {
    if (ev.key === 'Escape') { input.value = _viewerState.root || ''; input.blur(); return; }
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    const v = input.value.trim();
    if (!v) return;
    const prevRoot = _viewerState.root;
    const ok = await _navigateRoot(v);
    if (!ok) input.value = prevRoot || '';
    input.blur();
  });
}

// L2: Termius SFTP→프롬프트 패턴 — 뷰어에서 보던 경로를 활성 터미널에 그대로
// 타이핑해 넣는다(엔터는 안 침 — 뒤에 명령을 이어 쓸 수 있게). 이미지 붙여넣기
// 업로드 후 경로 삽입(pasteImageUpload)과 같은 sendToPty 패턴 재사용.
export function _insertPathToTerminal(path) {
  if (!activeSession()) {
    showToast('열려 있는 터미널 세션이 없습니다');
    return;
  }
  sendToPty(activeSessionId(), path + ' ');
  showToast('경로 삽입됨: ' + path.split('/').pop());
}

// 현재 활성 터미널(tmux) 세션의 cwd를 트리 최상단으로 연다.
export async function _openAtTerminalCwd() {
  const _s = activeSession();
  if (!_s) {
    showToast('열려 있는 터미널 세션이 없습니다');
    return;
  }
  const tmuxName = _s.tmuxName || _s.tmux_name;
  if (!tmuxName) {
    showToast('현재 세션은 tmux 세션이 아니라 위치를 알 수 없습니다');
    return;
  }
  let list;
  try {
    list = await vtFetch('/api/tmux/sessions');
  } catch (e) {
    showToast(`위치 확인 실패: ${e.message}`);
    return;
  }
  const info = (list || []).find(s => s.name === tmuxName);
  if (!info || !info.cwd) {
    showToast('현재 터미널 위치를 확인할 수 없습니다');
    return;
  }
  await _navigateRoot(info.cwd);
}

// --- 계층 트리 ----------------------------------------------------------------

export function _fmtSize(n) {
  if (n < 1024) return n + 'B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K';
  return (n / 1024 / 1024).toFixed(1) + 'M';
}

function _isDescendant(path, of) {
  return path !== of && path.startsWith(of.replace(/\/$/, '') + '/');
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
  name.textContent = entry.name;                      // textContent — XSS 방어

  row.appendChild(chev);
  row.appendChild(name);

  if (!entry.dir) {
    const size = document.createElement('span');
    size.className = 'vt-vw-size';
    size.textContent = _fmtSize(entry.size);
    row.appendChild(size);
    // L2: 파일을 열지 않고도 경로만 터미널에 바로 꽂을 수 있는 행별 버튼.
    const insert = document.createElement('button');
    insert.className = 'vt-vw-row-insert';
    insert.type = 'button';
    insert.title = '터미널에 경로 삽입';
    insert.innerHTML = _ICON_INSERT;
    insert.addEventListener('click', (e) => {
      e.stopPropagation(); // row 클릭(파일 열기)으로 안 번지게
      _insertPathToTerminal(path);
    });
    row.appendChild(insert);
  }
  return row;
}

function _wireTreeRow(row, entry, path, depth) {
  row.addEventListener('click', () => {
    if (entry.dir) _toggleDir(row, path, depth);
    else _selectFile(path, row);
  });
}

export async function _renderRootTree() {
  const treeEl = document.getElementById('vt-vw-tree');
  let data;
  try {
    data = await vtFetch(`/api/fs/tree?path=${encodeURIComponent(_viewerState.root)}`);
  } catch (e) {
    _setMsg(treeEl, 'vt-vw-empty', [e.message]);
    return;
  }
  _renderTopLevel(treeEl, data);
}

// 상위 이동(".." 행) — 서버(fsguard)가 정한 경계까지만 실제로 올라간다.
// 경계는 프론트가 미리 알지 못하므로 항상 시도해보고, 막히면(403) 토스트만 띄운다.
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
  await _navigateRoot(parent);
}

// OS가 만드는 메타데이터 파일만 목록에서 뺀다. 사용자가 만든 dot 디렉토리
// (.claude, .vscode, .github 등)는 실제로 열어볼 일이 있으므로 남긴다 —
// "숨김 파일 전부 숨기기"로 잡으면 그쪽까지 사라져 오히려 불편해진다.
const _OS_NOISE = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini', '.localized']);
function _denoise(entries) {
  return (entries || []).filter(e => !_OS_NOISE.has(e.name));
}

// 트리 최상단(현재 root의 자식들) 렌더링 — 초기 로드와 "위로 이동" 양쪽에서 쓴다.
function _renderTopLevel(treeEl, data) {
  treeEl.innerHTML = '';
  const frag = document.createDocumentFragment();
  if (_viewerState.root !== '/') frag.appendChild(_upRowEl());
  const recent = _recentSectionEl();
  if (recent) frag.appendChild(recent);

  if (!_denoise(data.entries).length) {
    treeEl.appendChild(frag);
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = '빈 디렉토리';
    treeEl.appendChild(empty);
    return;
  }
  _denoise(data.entries).forEach(entry => {
    const childPath = _viewerState.root.replace(/\/$/, '') + '/' + entry.name;
    const row = _treeRowEl(entry, childPath, 0);
    _wireTreeRow(row, entry, childPath, 0);
    frag.appendChild(row);
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
  _viewerState.cwd = path;
  const chev = row.querySelector('.vt-vw-chev');
  const prevIcon = chev.innerHTML;
  chev.innerHTML = '';
  const spin = document.createElement('span');
  spin.className = 'vt-vw-trow-spin';
  chev.appendChild(spin);

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

  const frag = document.createDocumentFragment();
  _denoise(data.entries).forEach(entry => {
    const childPath = path.replace(/\/$/, '') + '/' + entry.name;
    const childRow = _treeRowEl(entry, childPath, depth + 1);
    _wireTreeRow(childRow, entry, childPath, depth + 1);
    frag.appendChild(childRow);
  });
  if (data.truncated) {
    const note = document.createElement('div');
    note.className = 'vt-vw-note warn';
    note.style.setProperty('--d', depth + 1);
    note.textContent = `일부만 표시했습니다 (최대 ${data.entries.length}개)`;
    frag.appendChild(note);
  }
  if (!_denoise(data.entries).length && !data.truncated) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.style.setProperty('--d', depth + 1);
    empty.textContent = '빈 디렉토리';
    frag.appendChild(empty);
  }
  row.after(frag);
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
}

export function _selectFile(path, row) {
  document.querySelectorAll('.vt-vw-trow.active').forEach(r => r.classList.remove('active'));
  if (row) row.classList.add('active');
  _pushRecent(path);
  openFile(path);
  if (_viewerState.displayMode === 'sheet') _setActivePane('code');
}

// --- 최근 연 파일 -----------------------------------------------------------
// 루트가 ~/GitHub 이라 실제로 보는 파일까지 매번 네다섯 단계를 눌러 내려가야 했다.
// 폰에서는 그 자체가 뷰어를 안 쓰게 되는 이유가 된다. 최근 목록을 트리 맨 위에
// 얹어 한 번에 도달하게 한다. 경로만 저장하므로 파일 내용은 남지 않는다.
const VT_RECENT_KEY = 'vt_viewer_recent';
const VT_RECENT_MAX = 8;

// quickopen.js가 뷰어를 안 연 상태에서도 "최근 파일" 항목을 채우려고 이 함수를
// 부른다(공개 API로 승격은 백로그 — 10-frontend-restructure.md F3 TODO 참고).
export function _loadRecent() {
  try {
    const v = JSON.parse(localStorage.getItem(VT_RECENT_KEY) || '[]');
    return Array.isArray(v) ? v.filter(p => typeof p === 'string') : [];
  } catch (_) { return []; }
}
function _pushRecent(path) {
  if (!path) return;
  try {
    const list = _loadRecent().filter(p => p !== path);
    list.unshift(path);
    localStorage.setItem(VT_RECENT_KEY, JSON.stringify(list.slice(0, VT_RECENT_MAX)));
  } catch (_) { /* 사생활 보호 모드 등 — 최근 목록 없이 그냥 동작한다 */ }
}

// 최근 목록 섹션을 트리 상단에 만들어 반환한다. 없으면 null.
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
  clear.addEventListener('click', (e) => {
    e.stopPropagation();
    try { localStorage.removeItem(VT_RECENT_KEY); } catch (_) {}
    sec.remove();
  });
  head.appendChild(label);
  head.appendChild(clear);
  sec.appendChild(head);

  list.forEach(p => {
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
