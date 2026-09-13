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
import { vtFetch, vtEsc } from '../../core/api.js';
import { showToast } from '../../ui/toast.js';

// ⚠ ADR-26 — 이 파일은 지연 청크(panels.js)에 들어가므로 core/store.js를
// **정적 import 하지 않는다**. 하면 Vite가 세션 싱글톤을 청크 안에 복제해
// 앱 본체와 다른 객체가 된다(build-output.test.js가 실제로 잡아냈다).
// 그 파일이 이미 노출해 둔 window 브리지로만 읽는다.
const activeSession = () => (typeof window.activeSession === 'function' ? window.activeSession() : null);

const FILTERS = [
  { id: 'all', label: '전체' },
  { id: 'shared', label: '공유 중' },
  { id: 'expiring', label: '만료 임박' },
];

// §3 「만료 · 필수」 — 서버의 VALID_TTLS와 같은 값이어야 한다(다르면 400).
const TTLS = [
  { v: 3600, label: '1시간' },
  { v: 86400, label: '24시간' },
  { v: 604800, label: '7일' },
  { v: 2592000, label: '30일' },
];

let _filter = 'all';
let _lastItems = [];
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

// "남은 기간" 열. 공유 중이거나 pin된 파일은 TTL 정리에서 제외되므로(§1)
// 서버가 expires_at을 null로 내려준다 — 그 경우 기간이 아니라 이유를 쓴다.
export function fmtRemaining(item, nowSec = Date.now() / 1000) {
  if (item.pin) return '고정됨';
  if (!item.expires_at) return item.shares && item.shares.length ? '공유 중' : '—';
  const sec = item.expires_at - nowSec;
  if (sec <= 0) return '만료됨';
  const days = Math.floor(sec / 86400);
  if (days >= 1) return `${days}일`;
  const hours = Math.floor(sec / 3600);
  if (hours >= 1) return `${hours}시간`;
  return `${Math.max(1, Math.floor(sec / 60))}분`;
}

// 공유 열. 모드가 섞여 있으면(device + pin) 둘 다 보여준다 — "공유 중"
// 한 단어로 뭉개면 PIN이 걸렸는지 아닌지가 화면에서 사라진다.
export function shareLabel(item) {
  const shares = item.shares || [];
  if (!shares.length) return '';
  const modes = [...new Set(shares.map((s) => (typeof s === 'string' ? '공유 중' : s.mode === 'pin' ? 'PIN' : '내 기기')))];
  return modes.join(' · ');
}

function closeFiles() { closePanel('vt-files'); }

function showFiles() {
  const panel = openPanel({
    id: 'vt-files',
    ariaLabel: '파일',
    headHTML: `
      <div class="vt-vw-title" id="vt-fl-title">파일</div>
      <button class="vt-vw-diff" id="vt-fl-upload" title="파일 올리기">올리기</button>
    `,
    extraHTML: `
      <div class="vt-fl-chips" id="vt-fl-chips" role="tablist"></div>
    `,
    bodyId: 'vt-fl-body',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

  // 계약(파일 상단 1번): 새 input을 만들지 않고 기존 하나를 누른다.
  panel.el.querySelector('#vt-fl-upload').addEventListener('click', () => {
    document.getElementById('file-input')?.click();
  });

  renderChips();
  refreshFiles();
  // 목록은 자주 안 바뀌지만(업로드는 사용자가 직접 한다) 다른 기기에서 올린
  // 파일·TTL 정리 결과가 반영돼야 해서 느리게 돈다. 패널이 닫히면 자동 정리.
  setPanelPoll('vt-files', 10000, refreshFiles);
}

function renderChips() {
  const host = document.getElementById('vt-fl-chips');
  if (!host) return;
  host.innerHTML = '';
  for (const f of FILTERS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-fl-chip' + (_filter === f.id ? ' active' : '');
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(_filter === f.id));
    const n = f.id === 'shared' ? _lastItems.filter((x) => (x.shares || []).length).length
      : f.id === 'expiring' ? _lastItems.filter((x) => x.expiring_soon).length
        : 0;
    b.textContent = n ? `${f.label} ${n}` : f.label;
    b.addEventListener('click', () => { _filter = f.id; renderChips(); refreshFiles(); });
    host.appendChild(b);
  }
}

async function refreshFiles() {
  const body = document.getElementById('vt-fl-body');
  if (!body) return;
  let data;
  try {
    data = await vtFetch(`/api/files?filter=${encodeURIComponent(_filter)}`);
  } catch (e) {
    body.innerHTML = `<div class="vt-vw-loading">목록을 불러오지 못했습니다: ${vtEsc(e.message)}</div>`;
    return;
  }
  const items = data?.items || [];
  // 칩의 개수는 **전체 기준**이라 필터가 'all'일 때만 갱신한다 — 'shared'로
  // 걸러진 응답으로 개수를 세면 "공유 중 2"가 필터를 누르는 순간 자기 자신을
  // 세어 항상 전체 개수와 같아진다.
  if (_filter === 'all') { _lastItems = items; renderChips(); }

  const quota = data?.quota || {};
  const title = document.getElementById('vt-fl-title');
  if (title) {
    title.textContent = `파일 · ${items.length}개${quota.used != null ? ` · ${fmtBytes(quota.used)}` : ''}`;
  }

  body.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'vt-vw-loading';
    empty.textContent = _filter === 'all'
      ? '저장된 파일이 없습니다. 「올리기」 또는 터미널에 이미지 붙여넣기로 추가됩니다.'
      : '해당하는 파일이 없습니다.';
    body.appendChild(empty);
  } else {
    for (const item of items) body.appendChild(renderRow(item));
  }
  body.appendChild(renderFooter(quota));
}

function iconBtn(label, title, cls, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'vt-fl-act ' + cls;
  b.textContent = label;
  b.title = title;
  b.setAttribute('aria-label', title);
  b.addEventListener('click', onClick);
  return b;
}

function renderRow(item) {
  const row = document.createElement('div');
  row.className = 'vt-fl-row';
  row.dataset.id = item.id;

  const main = document.createElement('div');
  main.className = 'vt-fl-row-main';
  const name = document.createElement('div');
  name.className = 'vt-fl-name';
  name.textContent = item.name;
  name.title = item.name;
  const sub = document.createElement('div');
  sub.className = 'vt-fl-sub';
  const share = shareLabel(item);
  sub.textContent = [share, fmtBytes(item.size), fmtRemaining(item)].filter(Boolean).join(' · ');
  if (item.expiring_soon) sub.classList.add('warn');
  main.appendChild(name);
  main.appendChild(sub);

  const acts = document.createElement('div');
  acts.className = 'vt-fl-acts';
  acts.appendChild(iconBtn('⬇', '다운로드', 'dl', () => downloadFile(item)));
  acts.appendChild(iconBtn('📋', '경로 복사', 'copy', () => copyPath(item)));
  acts.appendChild(iconBtn('⌨', '터미널에 삽입', 'ins', () => insertFile(item)));
  acts.appendChild(iconBtn('🔗', '링크 발급', 'share', () => openShareDialog(item)));
  acts.appendChild(iconBtn('🗑', '삭제', 'rm', () => deleteFile(item)));

  row.appendChild(main);
  row.appendChild(acts);
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
    ? `${fmtBytes(used)} / ${fmtBytes(max)}${quota.ttl_days ? ` · ${quota.ttl_days}일 후 자동 삭제` : ''}`
    : `${fmtBytes(used)}`;
  foot.appendChild(bar);
  foot.appendChild(text);
  return foot;
}

// ── 행 동작 5개 ─────────────────────────────────────────────────────────────

function downloadFile(item) {
  // 서버가 attachment + nosniff로 내려주므로 같은 탭 내비게이션으로 충분하다
  // (새 창을 열면 모바일에서 빈 탭이 남는다).
  window.location.href = `/api/files/${encodeURIComponent(item.id)}/download`;
}

async function copyPath(item) {
  // 경로는 목록 응답에 없다(API는 id만 다룬다는 §1 원칙) — 사용자가 실제로
  // 원하는 건 "터미널에 붙일 수 있는 값"이라 다운로드 URL이 아니라 서버가
  // 삽입에 쓰는 경로와 같은 것을 줘야 한다. 그래서 삽입 API와 같은 모양의
  // 경로 문자열을 서버에서 받아온다.
  try {
    const r = await vtFetch(`/api/files/${encodeURIComponent(item.id)}/path`);
    await navigator.clipboard.writeText(r.path);
    showToast('경로 복사됨', 'success');
  } catch (e) {
    showToast('복사 실패: ' + e.message, 'error');
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
    showToast(`${host}(으)로 전송 중…`);
    try {
      const r = await vtFetch(`/api/files/${encodeURIComponent(item.id)}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, session: s.remote.tmux }),
      });
      showToast(r.typed
        ? `${host}에 전송 후 경로 삽입됨${r.reused ? ' (이미 있던 파일 재사용)' : ''}`
        : `${host}에 전송됨 — pane을 못 찾아 경로는 못 넣었습니다`, r.typed ? 'success' : 'error');
    } catch (e) {
      showToast(`전송 실패: ${e.message}`, 'error');
    }
    return;
  }
  if (!tmuxName) {
    showToast('tmux 세션에 연결된 터미널을 먼저 선택하세요', 'error');
    return;
  }
  try {
    await vtFetch(`/api/files/${encodeURIComponent(item.id)}/insert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: tmuxName }),
    });
    showToast(`${tmuxName}에 경로 삽입됨`, 'success');
  } catch (e) {
    showToast('삽입 실패: ' + e.message, 'error');
  }
}

async function deleteFile(item) {
  const shared = (item.shares || []).length;
  const msg = shared
    ? `${item.name} 을(를) 삭제합니다.\n\n공유 링크 ${shared}개도 함께 무효가 됩니다. 계속할까요?`
    : `${item.name} 을(를) 삭제할까요?`;
  if (!confirm(msg)) return;
  try {
    await vtFetch(`/api/files/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
    showToast('삭제됨', 'success');
  } catch (e) {
    showToast('삭제 실패: ' + e.message, 'error');
  }
  refreshFiles();
}

// ── 링크 발급 다이얼로그 (§3 화면 3b) ────────────────────────────────────────

function openShareDialog(item) {
  const existing = document.getElementById('vt-fl-share');
  if (existing) existing.remove();

  const wrap = document.createElement('div');
  wrap.id = 'vt-fl-share';
  wrap.className = 'vt-viewer-backdrop vt-fl-share-backdrop';
  wrap.innerHTML = `
    <div class="vt-viewer-card vt-fl-share-card" role="dialog" aria-modal="true" aria-label="공유 링크 발급">
      <div class="vt-viewer-head">
        <div class="vt-vw-title">링크 발급 — ${vtEsc(item.name)}</div>
        <button class="vt-vw-x" aria-label="닫기">✕</button>
      </div>
      <div class="vt-vw-body">
        <div class="vt-fl-field">
          <div class="vt-fl-field-label">누가 열 수 있나</div>
          <label class="vt-fl-radio"><input type="radio" name="vt-fl-mode" value="device" checked> 내 기기만 <span class="vt-fl-hint">등록된 기기에서 로그인 상태로만 열립니다</span></label>
          <label class="vt-fl-radio"><input type="radio" name="vt-fl-mode" value="pin"> PIN 보호 <span class="vt-fl-hint">코드를 아는 사람이면 누구나 (5회 틀리면 링크 소멸)</span></label>
          <label class="vt-fl-radio disabled"><input type="radio" name="vt-fl-mode" value="public" disabled> 누구나 열기 <span class="vt-fl-hint">제공하지 않습니다</span></label>
        </div>
        <div class="vt-fl-field" id="vt-fl-pin-field" hidden>
          <div class="vt-fl-field-label">PIN · 4~20자</div>
          <input type="text" id="vt-fl-pin" class="vt-fl-input" autocomplete="off" inputmode="numeric" placeholder="예: 4821" />
        </div>
        <div class="vt-fl-field">
          <div class="vt-fl-field-label">만료 · 필수</div>
          <select id="vt-fl-ttl" class="vt-fl-input">
            ${TTLS.map((t) => `<option value="${t.v}"${t.v === 86400 ? ' selected' : ''}>${t.label}</option>`).join('')}
          </select>
        </div>
        <label class="vt-fl-check"><input type="checkbox" id="vt-fl-once"> 다운로드 1회 후 자동 취소</label>
        <div class="vt-fl-share-actions">
          <button class="vt-pt-btn" id="vt-fl-issue">발급</button>
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
    showToast('PIN은 4~20자여야 합니다', 'error');
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
      showToast('발급 실패: ' + e.message, 'error');
      return;
    }
    if (!(await elevate())) return;
    try { res = await post(); } catch (e2) { showToast('발급 실패: ' + e2.message, 'error'); return; }
  }

  if (res?.share?.shareId && res?.token) _issuedTokens.set(res.share.shareId, res.token);
  showToast('링크 발급됨', 'success');
  try { await navigator.clipboard.writeText(absoluteShareUrl(res.url)); showToast('링크가 복사되었습니다', 'success'); } catch (_) {}
  await renderShareList(item, wrap);
  refreshFiles();
}

/** 상대 경로(`/s/<token>`)를 지금 보고 있는 출처 기준 절대 URL로. 공유 링크는
 * 다른 사람에게 보내는 값이라 반드시 절대 URL이어야 한다. */
export function absoluteShareUrl(url) {
  try { return new URL(url, window.location.origin).toString(); } catch (_) { return url; }
}

async function elevate() {
  const pw = prompt('공유 링크 발급에는 비밀번호 확인이 필요합니다.');
  if (pw === null) return false;
  try {
    await vtFetch('/api/auth/elevate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    return true;
  } catch (e) {
    showToast('비밀번호 확인 실패', 'error');
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
    none.textContent = '발급된 링크가 없습니다.';
    host.appendChild(none);
    return;
  }
  for (const s of shares) {
    const row = document.createElement('div');
    row.className = 'vt-fl-share-row';
    const label = document.createElement('div');
    label.className = 'vt-fl-share-label';
    const left = Math.max(0, Math.round((s.exp - Date.now() / 1000) / 3600));
    label.textContent = `${s.mode === 'pin' ? 'PIN' : '내 기기'} · ${left}시간 남음${s.once ? ' · 1회용' : ''}`;
    const acts = document.createElement('div');
    acts.className = 'vt-fl-acts';
    const token = _issuedTokens.get(s.shareId);
    const copyBtn = iconBtn('복사', token ? '링크 복사' : '이 링크의 주소는 발급한 그 순간에만 알 수 있습니다(서버가 보관하지 않음) — 필요하면 취소 후 새로 발급하세요', 'copy', async () => {
      if (!token) return;
      try { await navigator.clipboard.writeText(absoluteShareUrl(`/s/${token}`)); showToast('복사됨', 'success'); }
      catch (e) { showToast('복사 실패: ' + e.message, 'error'); }
    });
    copyBtn.disabled = !token;
    acts.appendChild(copyBtn);
    acts.appendChild(iconBtn('지금 취소', '공유 취소', 'rm', async () => {
      try {
        await vtFetch(`/api/files/${encodeURIComponent(item.id)}/share/${encodeURIComponent(s.shareId)}`, { method: 'DELETE' });
        showToast('취소됨 — 링크가 즉시 404가 됩니다', 'success');
      } catch (e) {
        if (/elevation_required|401/.test(e.message || '') && await elevate()) {
          try { await vtFetch(`/api/files/${encodeURIComponent(item.id)}/share/${encodeURIComponent(s.shareId)}`, { method: 'DELETE' }); }
          catch (e2) { showToast('취소 실패: ' + e2.message, 'error'); }
        } else { showToast('취소 실패: ' + e.message, 'error'); }
      }
      await renderShareList(item, wrap);
      refreshFiles();
    }));
    row.appendChild(label);
    row.appendChild(acts);
    host.appendChild(row);
  }
}

export { showFiles, closeFiles };
