// dock 파일 탭 (N19~N21 · 50-files-share.md §3·§4) — 저장소에 올라온 파일을
// 보고, 내려받고, 터미널에 꽂고, 공유 링크를 발급/취소하는 화면.
//
// 백엔드는 2.1.2 앞 커밋에서 전부 끝났다(file_store.py·routes/share.py).
// 여기는 그 API의 **유일한 UI**다. queue.js·ports.js와 같은 관용구를 그대로
// 따른다: panels/panel.js의 껍데기 + core/api.js의 vtFetch + registerAction.
//
// 설계 메모 3가지(왜 이렇게 생겼는지):
//  1. 올리기는 새 input을 만들지 않는다 — index.html의 `#file-input` 하나를
//     세 트리거(키바 📎 · 팔레트 · 여기)가 공유한다는 기존 계약을 지킨다.
//     두 번째 input을 만들면 onchange 핸들러가 갈라져 업로드 경로가 둘이 된다.
//  2. 링크 발급은 **승격(require_elevated)**이 필요하다. 그래서 401
//     elevation_required를 받으면 비밀번호를 한 번 더 묻고 재시도한다 —
//     지금까지 승격을 요구하는 UI 경로가 하나도 없어서(ADR-27) 이 파일이
//     그 첫 소비자다.
//  3. QR은 그리지 않는다. 프런트에 QR 생성기가 없고(의존성 0개 유지),
//     링크는 복사해서 보내면 되는 값이다 — 모바일 QR은 `fsh mobile`이 이미
//     서버 쪽에서 만든다.
import { openPanel, closePanel, setPanelPoll } from '../panel.js';
import { errorState } from '../../ui/empty.js';
import { vtFetch, vtEsc, vtUrl } from '../../core/api.js';
import { showToast } from '../../ui/toast.js';
import { icon } from '../../ui/icons.js';
import { okFlash } from '../../ui/okflash.js';
import { confirmDialog, promptDialog } from '../../core/dialog.js';
import { t } from '../../core/i18n.js';

// ⚠ ADR-26 — 이 파일은 지연 청크(panels.js)에 들어가므로 core/store.js를
// **정적 import 하지 않는다**. 하면 Vite가 세션 싱글톤을 청크 안에 복제해
// 앱 본체와 다른 객체가 된다(build-output.test.js가 실제로 잡아냈다).
// 그 파일이 이미 노출해 둔 window 브리지로만 읽는다.
const activeSession = () => (typeof window.activeSession === 'function' ? window.activeSession() : null);

const FILTERS = [
  { id: 'all', label: t('전체') },
  { id: 'shared', label: t('공유 중') },
  { id: 'expiring', label: t('만료 임박') },
];

// §3 「만료 · 필수」 — 서버의 VALID_TTLS와 같은 값이어야 한다(다르면 400).
const TTLS = [
  { v: 3600, label: t('1시간') },
  { v: 86400, label: t('24시간') },
  { v: 604800, label: t('7일') },
  { v: 2592000, label: t('30일') },
];

// N4 — 필터·마지막 목록은 **인스턴스별** 상태다. 모듈 레벨 변수 한 벌로
// 두면 dock과 pane에 동시에 띄웠을 때 한쪽에서 「공유 중」을 고르는 순간
// 다른 쪽 칩도 같이 바뀐다(그리고 목록은 안 바뀐다 — 그리는 쪽이 하나뿐이라).
// 열려 있는 인스턴스를 모아 두고 각자의 상태를 그 안에 담는다.
//
// `isConnected`로 걸러 읽는 이유: pane 칸이 다른 것으로 바뀌면 렌더러가
// `replaceChildren()`으로 통째로 떼어내므로 `closePanel`이 안 불린다 —
// 그때 `onClose`에 기대면 죽은 인스턴스가 집합에 남는다.
// shareId → 토큰. **서버는 목록 API로 토큰을 내려주지 않는다** — 목록은 승격
// 없이도 읽을 수 있는데 토큰이 거기 실리면 그게 곧 파일을 받아갈 수 있는
// 자격이라, 승격으로 막아 둔 발급을 우회하는 셈이 된다. 그래서 이 세션에서
// **내가 방금 발급한** 것만 기억해 두고 [복사]를 열어 준다.
const _issuedTokens = new Map();

export function fmtBytes(n) {
  if (!n) return '0B';
  if (n < 1024) return n + 'B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + 'KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(0) + 'MB';
  return (n / 1024 / 1024 / 1024).toFixed(1) + 'GB';
}

// 업로드 일자·시각. 분까지 보여달라는 요구(2026-09-22)라 연도가 같아도
// "MM-DD HH:mm"까지 나간다 — 연도가 다르면 연도까지 앞에 붙는다. 전체 시각은
// 그래도 title로 한 번 더(초 단위까지, 로케일 표기).
export function fmtUploadDate(item, now = new Date()) {
  if (!item.created) return '';
  const d = new Date(item.created * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  const date = d.getFullYear() === now.getFullYear() ? `${mm}-${dd}` : `${d.getFullYear()}-${mm}-${dd}`;
  return `${date} ${hh}:${mi}`;
}

// "남은 기간" 열. 공유 중이거나 pin된 파일은 TTL 정리에서 제외되므로(§1)
// 서버가 expires_at을 null로 내려준다 — 그 경우 기간이 아니라 이유를 쓴다.
export function fmtRemaining(item, nowSec = Date.now() / 1000) {
  if (item.pin) return t('고정됨');
  if (!item.expires_at) return item.shares && item.shares.length ? t('공유 중') : '—';
  const sec = item.expires_at - nowSec;
  if (sec <= 0) return t('만료됨');
  const days = Math.floor(sec / 86400);
  if (days >= 1) return t('{n}일', { n: days });
  const hours = Math.floor(sec / 3600);
  if (hours >= 1) return t('{n}시간', { n: hours });
  return t('{n}분', { n: Math.max(1, Math.floor(sec / 60)) });
}

// 공유 열. 모드가 섞여 있으면(device + pin) 둘 다 보여준다 — "공유 중"
// 한 단어로 뭉개면 PIN이 걸렸는지 아닌지가 화면에서 사라진다.
export function shareLabel(item) {
  const shares = item.shares || [];
  if (!shares.length) return '';
  const modes = [...new Set(shares.map((s) => (typeof s === 'string' ? t('공유 중') : s.mode === 'pin' ? 'PIN' : t('내 기기'))))];
  return modes.join(' · ');
}

function closeFiles() { closePanel('vt-files'); }

const _instances = new Set();

function _liveInstances() {
  for (const inst of [...(_instances)]) if (!inst.el.isConnected) _instances.delete(inst);
  return [..._instances];
}

/** 목록을 바꾸는 동작(삭제·링크 발급/취소)은 **열려 있는 모든** 파일 패널을
 * 새로 고친다. 한쪽에서 지운 파일이 다른 쪽에 남아 있으면 그게 더 헷갈린다. */
function refreshAllFiles() {
  for (const inst of _liveInstances()) refreshFiles(inst);
}

// 업로드 진행 표시 — term/upload.js가 진행 상태의 유일한 주인이다(2026-09-23,
// 설계 2번). 그쪽은 이 모듈을 정적 import 못 하므로(ADR-26, 파일 상단 경고)
// window 브리지(vtUploadJobs/vtCancelUpload)와 "다시 그려라" 신호뿐인 전역
// CustomEvent로만 연결된다 — 이벤트엔 페이로드가 없다, 상태는 항상
// vtUploadJobs()에서 읽는다. 그래서 패널을 업로드 도중에 열어도(예전엔
// 다음 progress 이벤트가 올 때까지 비어 있었다) 그 즉시 대기열 전체가 보인다.
function _liveJobs() {
  return (typeof window.vtUploadJobs === 'function') ? window.vtUploadJobs() : [];
}

let _lastJobIds = new Set();
document.addEventListener('vt:upload-progress', () => {
  const ids = new Set(_liveJobs().map((j) => j.clientId));
  // 있던 작업이 이번엔 없다 = 끝났다(done/error/cancelled 중 하나) — 완료라면
  // 실제 서버 레코드가 새로 생겼을 수 있으니 다시 받아온다. 진행 중 틱은
  // 재요청 없이 진행 바만 다시 그린다.
  const finished = [..._lastJobIds].some((id) => !ids.has(id));
  _lastJobIds = ids;
  if (finished) { refreshAllFiles(); return; }   // refreshFiles가 _paintBody까지 한다
  for (const inst of _liveInstances()) _paintBody(inst);
});

// N4 — `opts.paneHost`가 있으면 그 pane 칸에 그린다.
function showFiles(opts = {}) {
  const panel = openPanel({
    paneHost: opts.paneHost || null,
    paneId: opts.paneId || null,
    id: 'vt-files',
    ariaLabel: t('파일'),
    headHTML: `
      <div class="vt-vw-title" id="vt-fl-title">${t('파일')}</div>
      <button class="vt-btn sm quiet" id="vt-fl-select-toggle" title="${t('여러 개 골라 한 번에 정리')}" aria-pressed="false">${t('선택')}</button>
      <button class="vt-btn sm vt-vw-diff" id="vt-fl-upload" title="${t('파일 올리기')}">${t('올리기')}</button>
    `,
    extraHTML: `
      <div class="vt-fl-chips" id="vt-fl-chips" role="tablist"></div>
      <div class="vt-fl-selectbar" id="vt-fl-selectbar" hidden></div>
    `,
    bodyId: 'vt-fl-body',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

  const inst = {
    ctx: panel.ctx, el: panel.el, filter: 'all', lastItems: [],
    _items: [], _quota: {}, // 마지막으로 받아온 목록 — 진행률만 바뀔 때 재요청 없이 다시 그린다
    selectMode: false, selected: new Set(),   // 2026-09-23 — 일괄 정리(설계 4번)
  };
  _instances.add(inst);

  // 계약(파일 상단 1번): 새 input을 만들지 않고 기존 하나를 누른다.
  // `#file-input`은 인스턴스가 아니라 **문서에 하나뿐인** 요소다(AGENTS.md
  // 계약 4) — 여기서만 전역 조회가 맞다.
  panel.ctx.byId('vt-fl-upload').addEventListener('click', () => {
    document.getElementById('file-input')?.click();
  });
  panel.ctx.byId('vt-fl-select-toggle').addEventListener('click', () => _toggleSelectMode(inst));

  wireFileDrop(panel.el);

  renderChips(inst);
  refreshFiles(inst);
  // 목록은 자주 안 바뀌지만(업로드는 사용자가 직접 한다) 다른 기기에서 올린
  // 파일·TTL 정리 결과가 반영돼야 해서 느리게 돈다. 패널이 닫히면 자동 정리.
  setPanelPoll(panel.el, 10000, () => refreshFiles(inst));
}

// R6 — 패널 전체가 드롭존이다(50-files-share.md §4 「드롭존: dock 탭 전체」).
// dock 인스턴스와 pane 인스턴스 양쪽에 그대로 붙는다 — 배선이 `panel.el`에
// 달리므로 어디에 떠 있든 같다.
//
// 업로드 구현은 `term/upload.js` 하나뿐인데 여기서 **정적 import 하지 않는다**:
// 그쪽은 core/store.js를 쓰고 이 파일은 지연 청크(panels.js)라, 끌어오면 세션
// 싱글톤이 청크 안에 복제된다(ADR-26 — 파일 상단 경고와 같은 이유).
//
// 경로는 터미널에 **안 넣는다**(사용자 확정): 파일을 정리하는 중에 터미널로
// 글자가 끼어들면 방해가 된다. 터미널 칸에 놓았을 때만 꽂힌다(layout/dnd.js).
function wireFileDrop(rootEl) {
  const hasFiles = (dt) => !!dt && Array.prototype.includes.call(dt.types || [], 'Files');
  rootEl.addEventListener('dragover', (e) => {
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault();
    // pane 안에 떠 있으면 바깥 `.vt-pane`도 파일 드롭을 받는다(그쪽은 세션에
    // 경로를 꽂는다) — 더 안쪽인 우리가 이긴다.
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
    rootEl.dataset.dropfile = '1';
  });
  rootEl.addEventListener('dragleave', (e) => {
    if (!rootEl.contains(e.relatedTarget)) delete rootEl.dataset.dropfile;
  });
  rootEl.addEventListener('drop', async (e) => {
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    delete rootEl.dataset.dropfile;
    const up = window.vtUploadFiles;
    if (typeof up !== 'function') return;   // core가 아직 안 올라온 창(방어)
    await up(e.dataTransfer, { insertPath: false });
    // 올린 것이 바로 보여야 한다. 열려 있는 **모든** 파일 패널을 새로 고친다 —
    // 삭제·링크 발급과 같은 규칙(refreshAllFiles 주석).
    refreshAllFiles();
  });
}

function renderChips(inst) {
  const host = inst.ctx.byId('vt-fl-chips');
  if (!host) return;
  host.innerHTML = '';
  for (const f of FILTERS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-chip' + (inst.filter === f.id ? ' active' : '');
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(inst.filter === f.id));
    const n = f.id === 'shared' ? inst.lastItems.filter((x) => (x.shares || []).length).length
      : f.id === 'expiring' ? inst.lastItems.filter((x) => x.expiring_soon).length
        : 0;
    b.textContent = n ? `${f.label} ${n}` : f.label;
    b.addEventListener('click', () => { inst.filter = f.id; renderChips(inst); refreshFiles(inst); });
    host.appendChild(b);
  }
}

async function refreshFiles(inst) {
  const body = inst.ctx.byId('vt-fl-body');
  if (!body) return;
  let data;
  try {
    data = await vtFetch(`/api/files?filter=${encodeURIComponent(inst.filter)}`);
  } catch (e) {
    // 2.1.6 — 서버 원문(예: "unauthorized")을 제목 자리에 그대로 놓으면 그게
    // 이 화면의 이름처럼 읽힌다. 제목은 사람 말로, 원문은 detail로, 그리고
    // 다시 해볼 수 있는 일이므로 [다시 시도]를 단다.
    body.innerHTML = '';
    body.appendChild(errorState(t('파일 목록을 불러오지 못했습니다'), e, () => refreshFiles(inst)));
    return;
  }
  const items = data?.items || [];
  // 칩의 개수는 **전체 기준**이라 필터가 'all'일 때만 갱신한다 — 'shared'로
  // 걸러진 응답으로 개수를 세면 "공유 중 2"가 필터를 누르는 순간 자기 자신을
  // 세어 항상 전체 개수와 같아진다.
  if (inst.filter === 'all') { inst.lastItems = items; renderChips(inst); }

  const quota = data?.quota || {};
  const title = inst.ctx.byId('vt-fl-title');
  if (title) {
    title.textContent = t('파일 · {n}개', { n: items.length }) + (quota.used != null ? ` · ${fmtBytes(quota.used)}` : '');
  }

  inst._items = items;
  inst._quota = quota;
  // 목록이 새로 왔으니, 그 사이 사라진 파일(다른 기기가 지웠거나 TTL 정리로
  // 없어진)의 선택은 버린다 — 유령 id를 들고 있다가 일괄 삭제에 실려 보내면
  // 서버가 그냥 missing으로 무시하긴 하지만, 화면의 "N개 선택" 숫자가
  // 실제로 보이는 행 수와 안 맞게 된다.
  if (inst.selected.size) {
    const live = new Set(items.map((x) => x.id));
    for (const id of [...inst.selected]) if (!live.has(id)) inst.selected.delete(id);
  }
  _paintBody(inst);
}

// refreshFiles(네트워크 재요청)와 진행률 이벤트(재요청 없음) 양쪽이 공유하는
// 그리기 단계. inst._items/_quota는 마지막으로 받아온 서버 목록 — 업로드 중
// 파일이 늘거나 줄 때마다 그걸 다시 fetch할 필요는 없다.
function _paintBody(inst) {
  const body = inst.ctx.byId('vt-fl-body');
  if (!body) return;
  const items = inst._items || [];
  const uploadingRows = _liveJobs().map((job) => renderUploadingRow(job));

  body.innerHTML = '';
  for (const row of uploadingRows) body.appendChild(row);
  if (!items.length && !uploadingRows.length) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-loading';
    empty.textContent = inst.filter === 'all'
      ? t('저장된 파일이 없습니다. 「올리기」 또는 터미널에 이미지 붙여넣기로 추가됩니다.')
      : t('해당하는 파일이 없습니다.');
    body.appendChild(empty);
  } else {
    for (const item of items) body.appendChild(renderRow(item, inst));
  }
  body.appendChild(renderFooter(inst._quota || {}));
  _renderSelectBar(inst);
}

// 업로드 중인 행 — 아직 서버 레코드가 없으니 삭제·공유 같은 동작은 없고
// 진행률 바 + 취소뿐이다. 기존 용량 게이지(.vt-fl-gauge)와 같은 클래스를
// 재사용한다(새 토큰 없음). 2026-09-23 — 상태의 주인이 term/upload.js로
// 옮겨가며(설계 2번) job.phase를 세 단계로 그대로 보여준다: 대기 중 · 업로드
// 중 · 서버 저장 중(이 단계는 취소를 끈다 — 바이트가 이미 다 넘어가 서버가
// 파일을 만들고 있을 수 있는 구간이라, 취소를 받아 주면 화면·서버가 어긋난다).
function renderUploadingRow(job) {
  const row = document.createElement('div');
  row.className = 'vt-fl-row vt-fl-row-uploading';

  const main = document.createElement('div');
  main.className = 'vt-fl-row-main';
  const name = document.createElement('div');
  name.className = 'vt-fl-name';
  name.textContent = job.name;
  name.title = job.name;
  const pct = job.size ? Math.min(100, Math.round((job.loaded / job.size) * 100)) : 0;
  const label = job.phase === 'queued' ? t('대기 중')
    : job.phase === 'saving' ? t('서버 저장 중')
      : t('업로드 중 · {loaded} / {size}', { loaded: fmtBytes(job.loaded), size: fmtBytes(job.size) });
  const sub = document.createElement('div');
  sub.className = 'vt-fl-sub';
  sub.textContent = label;
  const bar = document.createElement('div');
  bar.className = 'vt-fl-gauge vt-fl-row-progress';
  const fill = document.createElement('div');
  fill.className = 'vt-fl-gauge-fill';
  fill.style.width = (job.phase === 'queued' ? 0 : pct) + '%';
  bar.appendChild(fill);

  main.appendChild(name);
  main.appendChild(sub);
  main.appendChild(bar);
  row.appendChild(main);

  const acts = document.createElement('div');
  acts.className = 'vt-fl-acts';
  const cancelBtn = iconBtn('x', t('취소'), 'rm', () => window.vtCancelUpload?.(job.clientId));
  if (job.phase === 'saving') {
    cancelBtn.disabled = true;
    cancelBtn.title = t('서버가 저장하는 중이라 취소할 수 없습니다');
  }
  acts.appendChild(cancelBtn);
  row.appendChild(acts);
  return row;
}

// ── 일괄 선택/삭제 (2026-09-23, 설계 4번 — 첨부 파일 정리. 30일 자동 정리와
// 별개로 사용자가 필터 칩으로 조건을 고르고 그 결과 전체를 한 번에 지운다.
// 프로젝트 파일 트리(코드 뷰어)는 이 범위가 아니다 — files.md의 읽기 전용
// 계약은 그대로다.) ────────────────────────────────────────────────────────

function _toggleSelectMode(inst) {
  inst.selectMode = !inst.selectMode;
  if (!inst.selectMode) inst.selected.clear();
  const btn = inst.ctx.byId('vt-fl-select-toggle');
  if (btn) { btn.setAttribute('aria-pressed', String(inst.selectMode)); btn.classList.toggle('active', inst.selectMode); }
  _paintBody(inst);
}

/** 지금 필터로 보이는 항목(업로드 중 행 제외) 기준 — "만료 임박"을 고르고
 * 전체 선택하면 그 조건에 맞는 것만 골라진다(설계 4번의 핵심). */
function _visibleSelectableIds(inst) {
  return (inst._items || []).map((x) => x.id);
}

function _renderSelectBar(inst) {
  const bar = inst.ctx.byId('vt-fl-selectbar');
  if (!bar) return;
  if (!inst.selectMode) { bar.hidden = true; bar.innerHTML = ''; return; }
  bar.hidden = false;
  const visible = _visibleSelectableIds(inst);
  const allSelected = visible.length > 0 && visible.every((id) => inst.selected.has(id));
  bar.innerHTML = '';

  const count = document.createElement('span');
  count.className = 'vt-fl-selectbar-count';
  count.textContent = t('{n}개 선택', { n: inst.selected.size });
  bar.appendChild(count);

  // 전체 선택은 체크박스로 — 일부만 골랐을 때는 가운데 줄(indeterminate)로 보인다.
  const selectAll = document.createElement('label');
  selectAll.className = 'vt-fl-selectall';
  const allCb = document.createElement('input');
  allCb.type = 'checkbox';
  allCb.className = 'vt-fl-select-cb';
  const someSelected = visible.some((id) => inst.selected.has(id));
  allCb.checked = allSelected;
  allCb.indeterminate = someSelected && !allSelected;
  allCb.disabled = visible.length === 0;
  allCb.addEventListener('change', () => {
    if (allSelected) for (const id of visible) inst.selected.delete(id);
    else for (const id of visible) inst.selected.add(id);
    _paintBody(inst);
  });
  const allLabel = document.createElement('span');
  allLabel.textContent = t('전체 선택');
  selectAll.append(allCb, allLabel);
  bar.insertBefore(selectAll, count);

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'vt-btn sm danger';
  deleteBtn.textContent = t('삭제');
  deleteBtn.disabled = inst.selected.size === 0;
  deleteBtn.addEventListener('click', () => bulkDeleteSelected(inst));
  bar.appendChild(deleteBtn);

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'vt-btn sm quiet';
  cancelBtn.textContent = t('취소');
  cancelBtn.addEventListener('click', () => _toggleSelectMode(inst));
  bar.appendChild(cancelBtn);
}

function _toggleRowSelected(inst, id) {
  if (inst.selected.has(id)) inst.selected.delete(id); else inst.selected.add(id);
  _paintBody(inst);
}

async function bulkDeleteSelected(inst) {
  const ids = [...inst.selected];
  if (!ids.length) return;
  const items = (inst._items || []).filter((x) => ids.includes(x.id));
  const totalSize = items.reduce((n, x) => n + (x.size || 0), 0);
  const sharedCount = items.filter((x) => (x.shares || []).length).length;
  const lines = [t('{n}개({size})를 삭제합니다.', { n: ids.length, size: fmtBytes(totalSize) })];
  if (sharedCount) lines.push(t('공유 링크가 걸린 파일 {n}개도 함께 무효가 됩니다.', { n: sharedCount }));
  lines.push(t('계속할까요?'));
  if (!await confirmDialog(lines.join('\n\n'), { title: t('파일 일괄 삭제'), okLabel: t('삭제'), danger: true })) return;
  try {
    const r = await vtFetch('/api/files/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
    });
    if (r.missing && r.missing.length) {
      // 화면에 있던 항목이 서버엔 없었다 — 다른 기기가 먼저 지웠거나 TTL
      // 정리와 겹쳤다. 나머지는 성공했으니 조용히 넘기지 않고 한 줄만 남긴다.
      showToast(t('{n}개는 이미 지워져 있었습니다', { n: r.missing.length }), 'info');
    }
  } catch (e) {
    showToast(t('일괄 삭제 실패: {msg}', { msg: e.message }), 'error');
  }
  inst.selectMode = false;
  inst.selected.clear();
  const btn = inst.ctx.byId('vt-fl-select-toggle');
  if (btn) { btn.setAttribute('aria-pressed', 'false'); btn.classList.remove('active'); }
  refreshAllFiles();
}

// 2026-09-16 — 이모지(⬇📋⌨🔗🗑)를 SVG로 바꿨다. DESIGN.md §0의 "No emoji as a
// UI asset"이 이미 규칙이었는데 이 다섯 자리가 예외로 남아 있었다. 이모지는
// OS·폰트마다 모양이 갈리고 currentColor를 안 따라서, `.vt-fl-act.rm:hover`가
// 색을 --err로 바꿔도 🗑는 그대로였다 — 이제 stroke가 따라온다.
function iconBtn(iconName, title, cls, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'vt-icon-btn vt-fl-act ' + cls;
  b.innerHTML = icon(iconName, 15);
  b.title = title;
  b.setAttribute('aria-label', title);
  b.addEventListener('click', onClick);
  return b;
}

function renderRow(item, inst) {
  const row = document.createElement('div');
  row.className = 'vt-fl-row';
  row.dataset.id = item.id;

  // 선택 모드(설계 4번)에서는 행 앞에 체크박스가 붙고, 동작 버튼(다운로드·
  // 삭제 등)은 지운다 — 두 삭제 경로(행별 즉시 삭제 · 일괄 삭제)가 같은
  // 화면에 동시에 있으면 "지금 누르면 뭐가 지워지나"가 애매해진다.
  if (inst && inst.selectMode) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'vt-fl-select-cb';
    cb.checked = inst.selected.has(item.id);
    cb.setAttribute('aria-label', t('{name} 선택', { name: item.name }));
    cb.addEventListener('change', () => _toggleRowSelected(inst, item.id));
    row.appendChild(cb);
    row.classList.add('vt-fl-row-selectable');
    if (cb.checked) row.classList.add('selected');
  }

  const main = document.createElement('div');
  main.className = 'vt-fl-row-main';
  const name = document.createElement('div');
  name.className = 'vt-fl-name';
  name.textContent = item.name;
  name.title = item.name;

  // 2026-09-22 — 항상 2줄 구조로 고정한다(사용자 확정). 예전엔 한 줄에 공유
  // 라벨·용량·날짜시각·남은기간을 전부 점으로 이어붙여서, 공유 없는 파일은
  // 1줄인데 공유 중인 파일만 줄바꿈되어 행 높이가 들쑥날쑥했다. "지금 상태"
  // (용량·남은기간)와 "상세 기록"(공유 여부·업로드 시각)을 애초에 다른 줄로
  // 나누면 내용과 무관하게 항상 2줄이고, 위계도 굵기/색으로 또렷해진다.
  const meta = document.createElement('div');
  meta.className = 'vt-fl-meta';
  meta.textContent = [fmtBytes(item.size), fmtRemaining(item)].filter(Boolean).join(' · ');
  if (item.expiring_soon) meta.classList.add('warn');

  const sub = document.createElement('div');
  sub.className = 'vt-fl-sub';
  sub.textContent = [shareLabel(item), fmtUploadDate(item)].filter(Boolean).join(' · ');
  if (item.created) sub.title = new Date(item.created * 1000).toLocaleString();

  main.appendChild(name);
  main.appendChild(meta);
  main.appendChild(sub);
  row.appendChild(main);

  if (!inst || !inst.selectMode) {
    const acts = document.createElement('div');
    acts.className = 'vt-fl-acts';
    acts.appendChild(iconBtn('download', t('다운로드'), 'dl', () => downloadFile(item)));
    acts.appendChild(iconBtn('clipboard-copy', t('경로 복사'), 'copy', (e) => copyPath(item, e.currentTarget)));
    acts.appendChild(iconBtn('keyboard', t('터미널에 삽입'), 'ins', () => insertFile(item)));
    acts.appendChild(iconBtn('link', t('링크 발급'), 'share', () => openShareDialog(item)));
    acts.appendChild(iconBtn('trash-2', t('삭제'), 'rm', () => deleteFile(item)));
    row.appendChild(acts);
  }
  return row;
}

function renderFooter(quota) {
  const foot = document.createElement('div');
  foot.className = 'vt-fl-footer';
  const used = Number(quota.used || 0), max = Number(quota.max || 0);
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const bar = document.createElement('div');
  bar.className = 'vt-fl-gauge';
  const fill = document.createElement('div');
  fill.className = 'vt-fl-gauge-fill' + (pct >= 90 ? ' warn' : '');
  fill.style.width = pct + '%';
  bar.appendChild(fill);
  const text = document.createElement('div');
  text.className = 'vt-fl-footer-text';
  text.textContent = max
    ? `${fmtBytes(used)} / ${fmtBytes(max)}${quota.ttl_days ? ` · ${t('{n}일 후 자동 삭제', { n: quota.ttl_days })}` : ''}`
    : `${fmtBytes(used)}`;
  foot.appendChild(bar);
  foot.appendChild(text);
  return foot;
}

// ── 행 동작 5개 ─────────────────────────────────────────────────────────────

function downloadFile(item) {
  // 서버가 attachment + nosniff로 내려주므로 같은 탭 내비게이션으로 충분하다
  // (새 창을 열면 모바일에서 빈 탭이 남는다).
  window.location.href = vtUrl(`/api/files/${encodeURIComponent(item.id)}/download`);
}

async function copyPath(item, btn) {
  // 경로는 목록 응답에 없다(API는 id만 다룬다는 §1 원칙) — 사용자가 실제로
  // 원하는 건 "터미널에 붙일 수 있는 값"이라 다운로드 URL이 아니라 서버가
  // 삽입에 쓰는 경로와 같은 것을 줘야 한다. 그래서 삽입 API와 같은 모양의
  // 경로 문자열을 서버에서 받아온다.
  try {
    const r = await vtFetch(`/api/files/${encodeURIComponent(item.id)}/path`);
    await navigator.clipboard.writeText(r.path);
    // 성공은 누른 버튼에서 끝낸다(ui/okflash.js) — 클립보드는 화면에 증거가
    // 남지 않지만, 알려야 할 범위도 이 버튼 하나다.
    okFlash(btn);
  } catch (e) {
    showToast(t('복사 실패: {msg}', { msg: e.message }), 'error');
  }
}

async function insertFile(item) {
  const s = activeSession();
  const tmuxName = s && (s.tmuxName || s.tmux_name);
  if (s && s.remote) {
    // A2 — 파일 바이트는 이 맥에만 있다. 경로만 타이핑하면 그쪽에는 그 파일이
    // 없고, 같은 경로에 **다른** 파일이 있으면 더 나쁘다. 그래서 바이트를 먼저
    // 옮기고(상대가 저장한 뒤 자기 경로를 타이핑한다) 그 결과만 알린다.
    const host = s.remote.host;
    showToast(t('{host}(으)로 전송 중…', { host }));
    try {
      const r = await vtFetch(`/api/files/${encodeURIComponent(item.id)}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, session: s.remote.tmux }),
      });
      showToast(r.typed
        ? (r.reused ? t('{host}에 전송 후 경로 삽입됨 (이미 있던 파일 재사용)', { host }) : t('{host}에 전송 후 경로 삽입됨', { host }))
        : t('{host}에 전송됨 — pane을 못 찾아 경로는 못 넣었습니다', { host }), r.typed ? 'success' : 'error');
    } catch (e) {
      showToast(t('전송 실패: {msg}', { msg: e.message }), 'error');
    }
    return;
  }
  if (!tmuxName) {
    showToast(t('tmux 세션에 연결된 터미널을 먼저 선택하세요'), 'error');
    return;
  }
  try {
    await vtFetch(`/api/files/${encodeURIComponent(item.id)}/insert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: tmuxName }),
    });
    // 성공을 말하지 않는다 — 터미널에 경로가 실제로 타이핑된 것이 증거다.
  } catch (e) {
    showToast(t('삽입 실패: {msg}', { msg: e.message }), 'error');
  }
}

async function deleteFile(item) {
  const shared = (item.shares || []).length;
  const msg = shared
    ? t('{name} 을(를) 삭제합니다.\n\n공유 링크 {n}개도 함께 무효가 됩니다. 계속할까요?', { name: item.name, n: shared })
    : t('{name} 을(를) 삭제할까요?', { name: item.name });
  if (!await confirmDialog(msg, { title: t('파일 삭제'), okLabel: t('삭제'), danger: true })) return;
  try {
    await vtFetch(`/api/files/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
    // 성공을 말하지 않는다 — 아래 refreshAllFiles()로 행이 사라지는 것이 증거다.
  } catch (e) {
    showToast(t('삭제 실패: {msg}', { msg: e.message }), 'error');
  }
  refreshAllFiles();
}

// ── 링크 발급 다이얼로그 (§3 화면 3b) ────────────────────────────────────────

function openShareDialog(item, { snapshot = false } = {}) {
  const existing = document.getElementById('vt-fl-share');
  if (existing) existing.remove();

  const wrap = document.createElement('div');
  wrap.id = 'vt-fl-share';
  wrap.className = 'vt-viewer-backdrop vt-fl-share-backdrop';
  wrap.innerHTML = `
    <div class="vt-viewer-card vt-fl-share-card" role="dialog" aria-modal="true" aria-label="${t('공유 링크 발급')}">
      <div class="vt-viewer-head">
        <div class="vt-vw-title">${vtEsc(t('링크 발급 — {name}', { name: item.name }))}</div>
        <button class="vt-icon-btn vt-vw-x" aria-label="${t('닫기')}">${icon('x', 16)}</button>
      </div>
      <div class="vt-vw-body">
        ${snapshot ? `<div class="vt-fl-hint">${t('지금 시점의 복사본(스냅샷)을 공유합니다. 원본이 바뀌어도 링크의 내용은 그대로입니다.')}</div>` : ''}
        <div class="vt-fl-field">
          <div class="vt-fl-field-label">${t('누가 열 수 있나')}</div>
          <label class="vt-fl-radio"><input type="radio" name="vt-fl-mode" value="device" checked> ${t('내 기기만')} <span class="vt-fl-hint">${t('등록된 기기에서 로그인 상태로만 열립니다')}</span></label>
          <label class="vt-fl-radio"><input type="radio" name="vt-fl-mode" value="pin"> ${t('PIN 보호')} <span class="vt-fl-hint">${t('코드를 아는 사람이면 누구나 (5회 틀리면 링크 소멸)')}</span></label>
          <label class="vt-fl-radio disabled"><input type="radio" name="vt-fl-mode" value="public" disabled> ${t('누구나 열기')} <span class="vt-fl-hint">${t('제공하지 않습니다')}</span></label>
        </div>
        <div class="vt-fl-field" id="vt-fl-pin-field" hidden>
          <div class="vt-fl-field-label">${t('PIN · 4~20자')}</div>
          <input type="text" id="vt-fl-pin" class="vt-input lg vt-fl-input" autocomplete="off" inputmode="numeric" placeholder="${t('예: 4821')}" />
        </div>
        <div class="vt-fl-field">
          <div class="vt-fl-field-label">${t('만료 · 필수')}</div>
          <select id="vt-fl-ttl" class="vt-input lg vt-fl-input">
            ${TTLS.map((o) => `<option value="${o.v}"${o.v === 86400 ? ' selected' : ''}>${o.label}</option>`).join('')}
          </select>
        </div>
        <label class="vt-fl-check"><input type="checkbox" id="vt-fl-once"> ${t('다운로드 1회 후 자동 취소')}</label>
        <div class="vt-fl-share-actions">
          <button class="vt-btn" id="vt-fl-issue">${t('발급')}</button>
        </div>
        <div class="vt-fl-share-list" id="vt-fl-share-list"></div>
      </div>
    </div>
  `;
  document.body.appendChild(wrap);

  const close = () => wrap.remove();
  wrap.querySelector('.vt-vw-x').addEventListener('click', close);
  wrap.addEventListener('click', (ev) => { if (ev.target === wrap) close(); });

  const pinField = wrap.querySelector('#vt-fl-pin-field');
  for (const r of wrap.querySelectorAll('input[name="vt-fl-mode"]')) {
    r.addEventListener('change', () => { pinField.hidden = r.value !== 'pin' || !r.checked; });
  }
  wrap.querySelector('#vt-fl-issue').addEventListener('click', () => issueShare(item, wrap));

  renderShareList(item, wrap);
}

function selectedMode(wrap) {
  const r = wrap.querySelector('input[name="vt-fl-mode"]:checked');
  return r ? r.value : 'device';
}

async function issueShare(item, wrap) {
  const mode = selectedMode(wrap);
  const ttl = Number(wrap.querySelector('#vt-fl-ttl').value);
  const once = wrap.querySelector('#vt-fl-once').checked;
  const pin = wrap.querySelector('#vt-fl-pin').value.trim();
  if (mode === 'pin' && (pin.length < 4 || pin.length > 20)) {
    showToast(t('PIN은 4~20자여야 합니다'), 'error');
    return;
  }
  const body = { mode, ttl, once };
  if (mode === 'pin') body.pin = pin;

  const post = () => vtFetch(`/api/files/${encodeURIComponent(item.id)}/share`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  let res;
  try {
    res = await post();
  } catch (e) {
    // 파일 상단 2번: 승격이 필요한 첫 UI 경로. 비밀번호를 한 번 더 받고 재시도.
    if (!/elevation_required|401/.test(e.message || '')) {
      showToast(t('발급 실패: {msg}', { msg: e.message }), 'error');
      return;
    }
    if (!(await elevate())) return;
    try { res = await post(); } catch (e2) { showToast(t('발급 실패: {msg}', { msg: e2.message }), 'error'); return; }
  }

  if (res?.share?.shareId && res?.token) _issuedTokens.set(res.share.shareId, res.token);
  // 한 동작이 토스트를 둘 연달아 띄우지 않는다 — 발급과 복사는 한 번의 조작이라
  // 결과도 한 줄이어야 한다. 복사까지 됐는지가 유일하게 다른 정보다.
  let copied = false;
  try { await navigator.clipboard.writeText(absoluteShareUrl(res.url)); copied = true; } catch (_) {}
  showToast(copied ? t('링크 발급됨 — 클립보드에 복사했습니다') : t('링크 발급됨'), 'success');
  await renderShareList(item, wrap);
  refreshAllFiles();
}

/** 파일 탭·뷰어의 경로를 공유한다 — 저장소로 스냅샷 복사한 뒤 그 파일로 링크 발급 다이얼로그를 연다.
 * 공유 링크는 저장소(id) 파일에만 걸리므로 복사본이 필요하다. 승격이 필요하면 한 번 묻고 재시도. */
export async function shareFsPath(path) {
  if (!path) return;
  const post = () => vtFetch('/api/fs/share-import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  let res;
  try {
    res = await post();
  } catch (e) {
    if (!/elevation_required|401/.test(e.message || '')) {
      showToast(t('공유 준비 실패: {msg}', { msg: e.message }), 'error');
      return;
    }
    if (!(await elevate())) return;
    try { res = await post(); } catch (e2) { showToast(t('공유 준비 실패: {msg}', { msg: e2.message }), 'error'); return; }
  }
  if (!res || !res.item) { showToast(t('공유 준비 실패: {msg}', { msg: '?' }), 'error'); return; }
  openShareDialog(res.item, { snapshot: true });
  refreshAllFiles();
}

/** 상대 경로(`/s/<token>`)를 지금 보고 있는 출처 기준 절대 URL로. 공유 링크는
 * 다른 사람에게 보내는 값이라 반드시 절대 URL이어야 한다. */
export function absoluteShareUrl(url) {
  try { return new URL(url, window.location.origin).toString(); } catch (_) { return url; }
}

async function elevate() {
  const pw = await promptDialog(t('공유 링크 발급에는 비밀번호 확인이 필요합니다.'), { title: t('비밀번호 확인'), password: true });
  if (pw === null) return false;
  try {
    await vtFetch('/api/auth/elevate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    return true;
  } catch (e) {
    showToast(t('비밀번호 확인 실패'), 'error');
    return false;
  }
}

async function renderShareList(item, wrap) {
  const host = wrap.querySelector('#vt-fl-share-list');
  if (!host) return;
  let items;
  try {
    items = (await vtFetch('/api/files?filter=all'))?.items || [];
  } catch (_) { return; }
  const fresh = items.find((x) => x.id === item.id);
  const shares = (fresh?.shares || []).filter((s) => typeof s === 'object');
  host.innerHTML = '';
  if (!shares.length) {
    const none = document.createElement('div');
    none.className = 'vt-fl-hint';
    none.textContent = t('발급된 링크가 없습니다.');
    host.appendChild(none);
    return;
  }
  for (const s of shares) {
    const row = document.createElement('div');
    row.className = 'vt-fl-share-row';
    const label = document.createElement('div');
    label.className = 'vt-fl-share-label';
    const left = Math.max(0, Math.round((s.exp - Date.now() / 1000) / 3600));
    label.textContent = `${s.mode === 'pin' ? 'PIN' : t('내 기기')} · ${t('{n}시간 남음', { n: left })}${s.once ? ` · ${t('1회용')}` : ''}`;
    const acts = document.createElement('div');
    acts.className = 'vt-fl-acts';
    const token = _issuedTokens.get(s.shareId);
    // 2.2.1 — 첫 인자는 **아이콘 이름**인데(iconBtn 시그니처) 이 두 자리만
    // 사람이 읽는 낱말('복사'·'지금 취소')을 넘기고 있었다. icons.js에 그런
    // 이름이 없으니 icon()이 빈 문자열을 돌려줘서, 공유 목록의 두 버튼은
    // 아이콘 없이 빈 상자로 그려지고 있었다(console.warn만 남았다).
    const copyBtn = iconBtn('clipboard-copy', token ? t('링크 복사') : t('이 링크의 주소는 발급한 그 순간에만 알 수 있습니다(서버가 보관하지 않음) — 필요하면 취소 후 새로 발급하세요'), 'copy', async (e) => {
      if (!token) return;
      try { await navigator.clipboard.writeText(absoluteShareUrl(`/s/${token}`)); okFlash(e.currentTarget); }
      catch (err) { showToast(t('복사 실패: {msg}', { msg: err.message }), 'error'); }
    });
    copyBtn.disabled = !token;
    acts.appendChild(copyBtn);
    acts.appendChild(iconBtn('trash-2', t('공유 취소'), 'rm', async () => {
      try {
        await vtFetch(`/api/files/${encodeURIComponent(item.id)}/share/${encodeURIComponent(s.shareId)}`, { method: 'DELETE' });
        showToast(t('취소됨 — 링크가 즉시 404가 됩니다'), 'success');
      } catch (e) {
        if (/elevation_required|401/.test(e.message || '') && await elevate()) {
          try { await vtFetch(`/api/files/${encodeURIComponent(item.id)}/share/${encodeURIComponent(s.shareId)}`, { method: 'DELETE' }); }
          catch (e2) { showToast(t('취소 실패: {msg}', { msg: e2.message }), 'error'); }
        } else { showToast(t('취소 실패: {msg}', { msg: e.message }), 'error'); }
      }
      await renderShareList(item, wrap);
      refreshAllFiles();
    }));
    row.appendChild(label);
    row.appendChild(acts);
    host.appendChild(row);
  }
}

export { showFiles, closeFiles };
