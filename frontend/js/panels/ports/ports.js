// 포트 대시보드 (P3) — Ports.app이 메뉴바에서 하는 일을 원격에서.
// "지금 뭐가 떠 있지 / 3000번 죽여줘"를 폰에서 처리한다.
//
// 패널 껍데기 · fetch · 닫기/폴링 뼈대는 panels/panel.js·core/api.js가 공유한다.
// 리스트 행만 .vt-pt-* 로 따로 둔다. F5에서 classic script에서 ES 모듈로 전환.
import { openPanel, closePanel, setPanelPoll } from '../panel.js';
import { vtFetch, vtEsc } from '../../core/api.js';
import { _isCoarsePointer } from '../../core/env.js';
import { confirmDialog, alertDialog, promptDialog, openDialog, nextId } from '../../core/dialog.js';
import { relayPath } from '../../core/host-route.js';
import { t } from '../../core/i18n.js';

// 2026-10 — session("내 기기만") 노출의 공개 주소를 쿠키 없이 열면 안내 화면만 뜬다
// (메인 FarShell 주소를 드러내지 않으려고 — server/routes/port_proxy_app.py
// _session_only_page). 그래서 이 패널에서 여는 링크는 FarShell 쪽 `/p/authorize`에서
// 시작한다: 로그인·기기를 확인하고 1회용 티켓을 붙여 공개 주소로 보내 준다.
// `base`는 그 맥의 FarShell 주소 — 이 맥이면 빈 문자열(같은 origin의 상대 경로).
export function shareOpenUrl(tunnelUrl, share, base = '') {
  if (!share || share.mode !== 'session' || !share.shareId) return tunnelUrl;
  return `${base.replace(/\/$/, '')}/p/authorize?share=${encodeURIComponent(share.shareId)}&next=%2F`;
}

// 다른 맥으로 전환해 그 맥의 포트를 보고 있으면 로그인 확인도 **그 맥**에서 받아야 한다
// — 이 맥의 /p/authorize는 그 share를 모른다. 그 맥의 메인 주소를 물어 와서 쓴다.
async function _openUrlFor(tunnelUrl, share) {
  if (!share || share.mode !== 'session') return tunnelUrl;
  if (relayPath('/api/ports') === '/api/ports') return shareOpenUrl(tunnelUrl, share);
  try {
    const d = await vtFetch('/api/tunnel/list');
    if (d && d.main && d.main.url) return shareOpenUrl(tunnelUrl, share, d.main.url);
  } catch { /* 아래로 */ }
  showToast(t('그 맥의 FarShell 주소를 찾지 못했습니다 — 공개 주소로 엽니다(안내 화면이 뜰 수 있습니다)'));
  return tunnelUrl;
}

// 2.2 — 노출 모드 3가지(plan-2.2/00-port-access.md §1). files.js의 공유 링크
// 다이얼로그와 같은 화면 언어(`.vt-fl-*`)를 재사용한다 — "파일을 공유"와
// "포트를 공개"는 사용자에게 같은 종류의 결정이라 같은 모양이어야 한다.
const MODE_LABEL = { public: t('공개'), session: t('내 기기만'), pin: t('코드') };
const TTLS = [
  { v: 3600, label: t('1시간') },
  { v: 86400, label: t('24시간') },
  { v: 604800, label: t('7일') },
  { v: 2592000, label: t('30일') },
];


// N4 — `opts.paneHost`가 있으면 그 pane 칸에 그린다. dock에 떠 있는 것과
// 공존하므로 이 파일의 DOM 조회·폴링은 전부 인스턴스(`ctx`·`panel.el`)를
// 거친다. 큐와 같은 전환이고 같은 이유다(panels/panel.js 머리말).
function showPorts(opts = {}) {
      const panel = openPanel({
        paneHost: opts.paneHost || null,
        paneId: opts.paneId || null,
        id: 'vt-ports',
        ariaLabel: t('포트 대시보드'),
        headHTML: `
          <div class="vt-vw-title">${t('포트 — 실행 중인 개발 서버')}</div>
          <button class="vt-btn sm vt-vw-diff" id="vt-pt-refresh" title="${t('새로고침')}">${t('새로고침')}</button>
        `,
        bodyId: 'vt-pt-body',
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
      const ctx = panel.ctx;

      ctx.byId('vt-pt-refresh').addEventListener('click', () => refreshPorts(ctx, true));

      refreshPorts(ctx, true);
      // U4/D4(60-settings-palette.md §6): 2초 폴링. 패널이 열려 있을 때만 돈다 —
      // 닫으면 setPanelPoll이 정리한다.
      setPanelPoll(panel.el, 2000, () => refreshPorts(ctx, false));
    }

    function _fmtMem(kb) {
      if (!kb) return '';
      if (kb < 1024) return kb + 'K';
      return (kb / 1024).toFixed(0) + 'M';
    }

    // "20-04:05:48" → "20일", "07:10:55" → "7시간"
    function _fmtUptime(s) {
      if (!s) return '';
      const dm = /^(\d+)-/.exec(s);
      if (dm) return t('{n}일', { n: parseInt(dm[1], 10) });       // "05-01:26" → "5일"
      const parts = s.split(':');
      if (parts.length === 3) return t('{n}시간', { n: parseInt(parts[0], 10) });
      if (parts.length === 2) return t('{n}분', { n: parseInt(parts[0], 10) });
      return s;
    }

    // U4/D4(60-settings-palette.md §6): 같은 pid가 여러 포트를 리스닝하면
    // (예: vite 5173 + hmr 소켓 5174) 대표 행 하나 아래로 나머지를 묶는다.
    // 서버(portscan.py:scan)가 이미 (protected, port) 오름차순으로 내려주므로,
    // 같은 pid가 처음 나오는 자리 = 그 pid의 가장 낮은 포트다 — 그 항목을
    // 대표로 삼으면 별도 정렬 없이 "가장 낮은 포트" 기준이 자연히 성립한다.
    // protected 그룹과 non-protected 그룹은 애초에 섞이지 않으므로(서버 정렬)
    // 대표 선정이 그 경계를 넘어갈 일도 없다.
    function _groupByPid(ports) {
      const groups = [];
      const byPid = new Map();   // pid → groups 배열 인덱스
      for (const p of ports) {
        const idx = byPid.get(p.pid);
        if (idx !== undefined) {
          groups[idx].subs.push(p);
          continue;
        }
        byPid.set(p.pid, groups.length);
        groups.push({ rep: p, subs: [] });
      }
      return groups;
    }

    // U5/L6: 포트 종료 버튼 스와이프 액션 (터치 전용). 한 번에 하나만 열려 있게 관리.
    const SWIPE_OPEN_PX = 76;   // kill 버튼 폭 — 열렸을 때 inner가 이만큼 왼쪽으로 밀린다.
    const SWIPE_THRESHOLD_PX = 36;
    let _openSwipeRow = null;

    function _closeSwipe(row) {
      if (!row) return;
      row.classList.remove('open');
      const inner = row.querySelector('.vt-pt-row-inner');
      if (inner) inner.style.transform = '';
      if (_openSwipeRow === row) _openSwipeRow = null;
    }

    function _wireSwipe(row, inner, killBtn) {
      let startX = 0, startY = 0, dx = 0, dragging = false, decided = false, horizontal = false;

      row.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) return;
        if (_openSwipeRow && _openSwipeRow !== row) _closeSwipe(_openSwipeRow);
        startX = e.touches[0].clientX; startY = e.touches[0].clientY;
        dx = 0; dragging = true; decided = false; horizontal = false;
        inner.style.transition = 'none';
      }, { passive: true });

      row.addEventListener('touchmove', (e) => {
        if (!dragging) return;
        const x = e.touches[0].clientX, y = e.touches[0].clientY;
        const rawDx = x - startX;
        if (!decided) {
          // 세로 스크롤과 헷갈리지 않게 — 수평 이동이 확실히 더 클 때만 스와이프로 확정.
          if (Math.abs(rawDx) < 6 && Math.abs(y - startY) < 6) return;
          horizontal = Math.abs(rawDx) > Math.abs(y - startY);
          decided = true;
        }
        if (!horizontal) return;
        e.preventDefault();
        const base = row.classList.contains('open') ? -SWIPE_OPEN_PX : 0;
        dx = Math.max(-SWIPE_OPEN_PX, Math.min(0, base + rawDx));
        inner.style.transform = `translateX(${dx}px)`;
      }, { passive: false });

      const finish = () => {
        if (!dragging) return;
        dragging = false;
        inner.style.transition = '';
        if (!horizontal) { inner.style.transform = row.classList.contains('open') ? `translateX(${-SWIPE_OPEN_PX}px)` : ''; return; }
        if (dx <= -SWIPE_THRESHOLD_PX) {
          row.classList.add('open');
          inner.style.transform = `translateX(${-SWIPE_OPEN_PX}px)`;
          _openSwipeRow = row;
        } else {
          _closeSwipe(row);
        }
      };
      row.addEventListener('touchend', finish);
      row.addEventListener('touchcancel', finish);

      // 열려 있을 때 inner(카드 본문)를 탭하면 닫기만 하고 그 탭이 공개/종료
      // 버튼 클릭으로 새지 않게 한다.
      inner.addEventListener('click', (e) => {
        if (row.classList.contains('open')) { e.stopPropagation(); e.preventDefault(); _closeSwipe(row); }
      }, true);
    }

    // isSub: 같은 pid 그룹 안에서 대표 포트가 아닌 나머지 — 들여써서(↳) 보여준다.
    function _buildRow(ctx, p, isSub) {
      const row = document.createElement('div');
      row.className = isSub ? 'vt-pt-row vt-pt-row-sub' : 'vt-pt-row';

      const port = document.createElement('span');
      port.className = 'vt-pt-port';
      if (isSub) {
        const arrow = document.createElement('span');
        arrow.className = 'vt-pt-sub-arrow';
        arrow.textContent = '↳';
        arrow.setAttribute('aria-hidden', 'true');
        port.appendChild(arrow);
      }
      port.appendChild(document.createTextNode(String(p.port)));

      const meta = document.createElement('div');
      meta.className = 'vt-pt-meta';
      const name = document.createElement('span');
      name.className = 'vt-pt-cmd';
      name.textContent = p.cmd;                       // textContent — XSS 방어
      name.title = p.cmd;                              // 좁은 dock에서 말줄임돼도 hover로 복구
      const sub = document.createElement('span');
      sub.className = 'vt-pt-sub';
      const bits = [`pid ${p.pid}`];
      if (p.uptime) bits.push(_fmtUptime(p.uptime));
      if (p.rss_kb) bits.push(_fmtMem(p.rss_kb));
      if (typeof p.cpu === 'number' && p.cpu > 0) bits.push(p.cpu.toFixed(1) + '%');
      sub.textContent = bits.join(' · ');
      meta.appendChild(name); meta.appendChild(sub);

      const actions = document.createElement('span');
      actions.className = 'vt-pt-actions';
      // U5/L6: 터치 기기는 종료 버튼을 왼쪽 스와이프로 드러낸다(Mail/Linear 패턴) —
      // 목록을 스크롤하다 손가락이 스치는 것만으로 프로세스가 죽는 오탭을 막기 위함.
      // 마우스는 오탭 위험이 없으니 기존처럼 버튼이 항상 보인다.
      const swipeKillOnTouch = _isCoarsePointer() && !p.protected;
      if (p.protected) {
        const lock = document.createElement('span');
        lock.className = 'vt-pt-lock';
        lock.textContent = t('보호됨');
        lock.title = p.protected_reason;
        actions.appendChild(lock);
      } else {
        // L5: 이미 공개 터널이 열려 있으면(server가 GET /api/ports에 tunnel_url을
        // 얹어 알려준다) "공개" 대신 "미리보기"로 바꿔 앱 안에서 바로 확인하게 한다 —
        // 매번 "공개→URL 복사→새 탭"을 거칠 필요가 없다(Termius 포트포워딩+브라우저 워크플로).
        const ex = document.createElement('button');
        // sm = 행 높이(24px)와 같은 단. 기본 .vt-btn은 28px이라 24px 행보다 컸다.
        // quiet = 테두리 상자를 뗀다 — 목록 행 안의 동작이지 주 액션이 아니다.
        ex.className = 'vt-btn sm quiet';
        if (p.tunnel_url) {
          ex.textContent = t('미리보기');
          ex.title = p.tunnel_url;
          ex.onclick = async () => showPortPreview(p.port, await _openUrlFor(p.tunnel_url, p.share));
        } else {
          ex.textContent = t('공개');
          ex.title = t('이 포트를 Cloudflare 터널로 인터넷에 공개합니다');
          ex.onclick = () => exposePort(ctx, p.port);
        }
        actions.appendChild(ex);
        if (!swipeKillOnTouch) {
          const kb = document.createElement('button');
          kb.className = 'vt-btn sm quiet danger';
          kb.textContent = t('종료');
          kb.onclick = () => killPort(ctx, p.port, p.pid, p.cmd);
          actions.appendChild(kb);
        }
      }

      if (!swipeKillOnTouch) {
        row.appendChild(port); row.appendChild(meta);
        row.appendChild(actions);
        return row;
      }

      // 스와이프 레이어: row는 뷰포트(overflow:hidden), inner가 실제로 좌우로
      // 밀리고, 그 아래 깔린 kill 버튼이 밀린 만큼 드러난다.
      row.classList.add('swipeable');
      const inner = document.createElement('div');
      inner.className = 'vt-pt-row-inner';
      inner.appendChild(port); inner.appendChild(meta);
      inner.appendChild(actions);

      const kill = document.createElement('button');
      kill.className = 'vt-pt-swipe-kill';
      kill.textContent = t('종료');
      kill.onclick = () => { _closeSwipe(row); killPort(ctx, p.port, p.pid, p.cmd); };

      row.appendChild(kill);
      row.appendChild(inner);
      _wireSwipe(row, inner, kill);
      return row;
    }

// N22(50-files-share.md §5) — 노출 중인 터널(메인 + fsh tunnel expose한 포트) 요약을
// 포트 목록 아래에 얹는다. 해제는 이미 있는 DELETE /api/ports/{port}/expose를 그대로 쓴다
// — 메인 터널은 fsh 쪽 명령이 아니라 여기서 해제 대상이 아니므로 행에 해제 버튼이 없다.
async function _renderTunnelSection(ctx, body) {
  let d;
  try {
    d = await vtFetch('/api/tunnel/list');
  } catch {
    return;   // 조회 실패는 조용히 생략 — 포트 목록 자체는 이미 떴다
  }
  if (!d.count) return;

  const section = document.createElement('div');
  section.className = 'vt-pt-tunnel-section';
  const head = document.createElement('div');
  head.className = 'vt-pt-section';
  head.textContent = t('노출 중 · {n}', { n: d.count });
  section.appendChild(head);

  const rows = d.main ? [d.main, ...d.extra] : d.extra;
  rows.forEach((tn) => {
    const row = document.createElement('div');
    row.className = 'vt-pt-tunnel-row';
    // 2.2 — 모드 라벨(누가 열 수 있나) + 리스너가 못 뜬 경우의 경고(§8, ADR-31
    // 바인드 실패 시 broken). 메인 터널(d.main)은 노출 정책이 없어 mode가 없다.
    const modeLabel = tn.mode ? (MODE_LABEL[tn.mode] || tn.mode) : '';
    row.innerHTML = `
      <span class="vt-pt-port">:${vtEsc(String(tn.port))}</span>
      <span class="vt-pt-tunnel-label">${vtEsc(tn.label)}</span>
      ${modeLabel ? `<span class="vt-pt-tunnel-mode">${vtEsc(modeLabel)}</span>` : ''}
      ${tn.share && tn.share.autoRenew ? `<span class="vt-pt-tunnel-mode" title="${vtEsc(t('만료되거나 터널이 사라지면 자동으로 다시 엽니다'))}">${vtEsc(t('자동'))}</span>` : ''}
      ${tn.broken ? `<span class="vt-pt-tunnel-broken" title="${vtEsc(t('내부 프록시가 뜨지 못했습니다 — 지금은 접속이 안 됩니다'))}">⚠ ${vtEsc(t('고장'))}</span>` : ''}
      <a class="vt-pt-tunnel-url" href="${vtEsc(tn.url)}" target="_blank" rel="noopener noreferrer">${vtEsc(tn.url.replace(/^https:\/\//, ''))}</a>
    `;
    // session 노출은 링크를 누를 때 /p/authorize부터 시작한다(shareOpenUrl). 표시·복사는
    // 공개 주소 그대로 — 남에게 보내면 안내 화면만 보이고 FarShell 주소는 안 드러난다.
    if (tn.mode === 'session' && tn.share) {
      const a = row.querySelector('.vt-pt-tunnel-url');
      a.addEventListener('click', async (ev) => {
        ev.preventDefault();
        window.open(await _openUrlFor(tn.url, tn.share), '_blank', 'noopener,noreferrer');
      });
    }
    const actions = document.createElement('span');
    actions.className = 'vt-pt-actions';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'vt-btn sm quiet';
    copyBtn.textContent = t('복사');
    copyBtn.addEventListener('click', () => _copyText(tn.url));
    actions.appendChild(copyBtn);
    if (tn.mode === 'pin') {
      const pinBtn = document.createElement('button');
      pinBtn.className = 'vt-btn sm quiet';
      pinBtn.textContent = t('코드 재발급');
      pinBtn.title = t('터널·URL은 그대로 두고 접속 코드만 새로 만듭니다');
      pinBtn.addEventListener('click', () => reissuePin(ctx, tn.port));
      actions.appendChild(pinBtn);
    }
    if (tn !== d.main && tn.share) {
      const autoOn = !!tn.share.autoRenew;
      const autoBtn = document.createElement('button');
      autoBtn.className = 'vt-btn sm quiet';
      autoBtn.textContent = autoOn ? t('자동 끄기') : t('자동 켜기');
      autoBtn.title = t('기간이 끝나거나 재부팅해도 자동으로 다시 열기');
      autoBtn.addEventListener('click', () => setAutoRenew(ctx, tn.port, !autoOn));
      actions.appendChild(autoBtn);
    }
    if (tn !== d.main) {
      const closeBtn = document.createElement('button');
      closeBtn.className = 'vt-btn sm quiet danger';
      closeBtn.textContent = t('해제');
      closeBtn.addEventListener('click', () => _unexposeTunnel(ctx, tn.port));
      actions.appendChild(closeBtn);
    }
    row.appendChild(actions);
    section.appendChild(row);
  });
  body.appendChild(section);
}

async function _copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(t('복사됨'));
  } catch {
    showToast(t('복사 실패 — 직접 선택해 복사하세요'));
  }
}

async function _unexposeTunnel(ctx, port) {
  if (!await confirmDialog(t('포트 {port} 터널을 해제할까요?', { port }), { title: t('터널 해제'), okLabel: t('해제'), danger: true })) return;
  try {
    await vtFetch(`/api/ports/${port}/expose`, { method: 'DELETE' });
    showToast(t('포트 {port} 터널 해제됨', { port }));
  } catch (e) {
    showToast(t('해제 실패: {msg}', { msg: e.message }));
  }
  refreshPorts(ctx, true);
}

    async function refreshPorts(ctx, fresh) {
      const body = ctx.byId('vt-pt-body');
      if (!body) return;
      let d;
      try {
        d = await vtFetch(`/api/ports${fresh ? '?fresh=true' : ''}`);
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${vtEsc(e.message)}</div>`;
        return;
      }
      if (!d.ports.length) {
        body.innerHTML = `<div class="vt-vw-empty">${vtEsc(t('리스닝 중인 포트가 없습니다.'))}</div>`;
        await _renderTunnelSection(ctx, body);
        return;
      }

      const list = document.createElement('div');
      list.className = 'vt-vw-list';
      // U3: 서버가 이미 protected(보호됨/시스템) 기준으로 뒤로 정렬해서 보내준다.
      // 그 경계가 바뀌는 지점에 구분 헤더만 얹는다 — 정렬 로직 자체는 서버가 단일 소스.
      // 두 그룹이 실제로 섞여 있을 때만 헤더를 보여준다 — 전부 한쪽뿐이면 굳이 안 나눔.
      const hasBothGroups = d.ports.some(p => !p.protected) && d.ports.some(p => p.protected);
      let sawProtected = false;
      let sawMine = false;
      // U4/D4: 같은 pid 행 병합 — 대표(가장 낮은 포트) 아래 나머지를 들여쓴
      // 하위 행으로 그린다. 그룹핑 자체는 _groupByPid, 헤더·행 렌더링은 여기서.
      _groupByPid(d.ports).forEach(({ rep, subs }) => {
        if (hasBothGroups && !rep.protected && !sawMine) {
          sawMine = true;
          const head = document.createElement('div');
          head.className = 'vt-pt-section';
          head.textContent = t('내 서버');
          list.appendChild(head);
        }
        if (hasBothGroups && rep.protected && !sawProtected) {
          sawProtected = true;
          const head = document.createElement('div');
          head.className = 'vt-pt-section';
          head.textContent = t('보호됨 / 시스템');
          list.appendChild(head);
        }
        list.appendChild(_buildRow(ctx, rep, false));
        subs.forEach(p => list.appendChild(_buildRow(ctx, p, true)));
      });
      body.innerHTML = '';
      body.appendChild(list);
      if (d.truncated) {
        const n = document.createElement('div');
        n.className = 'vt-vw-note warn';
        n.textContent = t('포트가 많아 일부만 표시했습니다.');
        body.appendChild(n);
      }
      await _renderTunnelSection(ctx, body);
    }

    async function killPort(ctx, port, pid, cmd) {
      if (!await confirmDialog(t('포트 {port} ({cmd}, pid {pid}) 를 종료할까요?', { port, cmd, pid }), { title: t('프로세스 종료'), okLabel: t('종료'), danger: true })) return;
      try {
        // pid를 함께 보낸다 — 조회 후 프로세스가 바뀌었으면 서버가 409로 거부한다.
        const r = await vtFetch(`/api/ports/${port}?pid=${pid}`, { method: 'DELETE' });
        showToast(t('포트 {port} 종료됨 ({signal})', { port, signal: r.signal }));
      } catch (e) {
        showToast(t('종료 실패: {msg}', { msg: e.message }));
      }
      refreshPorts(ctx, true);
    }

    // 2.2 — 「누가 열 수 있나」 3지 선택 + 만료. files.js openShareDialog와 같은
    // 뼈대(core/dialog.js의 openDialog)를 쓴다. 취소/배경클릭/Esc는 null.
    function openExposeDialog(port) {
      const id = nextId();
      return new Promise((resolve) => {
        let result = null;
        let settled = false;
        const finish = () => { if (!settled) { settled = true; resolve(result); } };
        const { body } = openDialog({
          id, title: t('포트 {port} 공개', { port }), onClose: finish,
          bodyHTML: `
            <div class="vt-fl-field">
              <div class="vt-fl-field-label">${t('누가 열 수 있나')}</div>
              <label class="vt-fl-radio"><input type="radio" name="vt-pt-mode" value="session" checked> ${t('내 기기만')} <span class="vt-fl-hint">${t('이 FarShell에 로그인한 기기에서만 열립니다')}</span></label>
              <label class="vt-fl-radio"><input type="radio" name="vt-pt-mode" value="pin"> ${t('접속 코드')} <span class="vt-fl-hint">${t('서버가 코드를 만들어 보여줍니다 — 코드를 아는 사람이면 누구나')}</span></label>
              <label class="vt-fl-radio"><input type="radio" name="vt-pt-mode" value="public"> ${t('누구나')} <span class="vt-fl-hint">${t('URL을 아는 사람 모두 — 인증 없음')}</span></label>
            </div>
            <div class="vt-fl-field">
              <div class="vt-fl-field-label">${t('만료 · 필수')}</div>
              <select id="vt-pt-ttl" class="vt-input lg">
                ${TTLS.map((o) => `<option value="${o.v}"${o.v === 3600 ? ' selected' : ''}>${vtEsc(o.label)}</option>`).join('')}
              </select>
            </div>
            <div class="vt-fl-field">
              <div class="vt-fl-field-label">${t('자동 재노출')}</div>
              <label class="vt-fl-check"><input type="checkbox" id="vt-pt-auto"> ${t('기간이 끝나거나 재부팅해도 자동으로 다시 열기')}</label>
              <span class="vt-fl-hint">${t('만료되면 같은 기간만큼 연장하고, 터널이 사라지면 서버가 다시 엽니다. 다시 열면 주소가 바뀔 수 있습니다.')}</span>
            </div>
            <div class="vt-dialog-actions">
              <button type="button" class="vt-btn vt-dialog-cancel">${t('취소')}</button>
              <button type="button" class="vt-btn solid vt-dialog-ok">${t('공개')}</button>
            </div>`,
        });
        if (!body) { resolve(null); return; }
        body.querySelector('.vt-dialog-ok').addEventListener('click', () => {
          const checked = body.querySelector('input[name="vt-pt-mode"]:checked');
          result = {
            mode: checked ? checked.value : 'session',
            ttl: Number(body.querySelector('#vt-pt-ttl').value),
            autoRenew: body.querySelector('#vt-pt-auto').checked,
          };
          closePanel(id);
        });
        body.querySelector('.vt-dialog-cancel').addEventListener('click', () => closePanel(id));
      });
    }

    // 승격(require_elevated) — 포트를 여는 것도 공유 링크 발급과 같은 등급의
    // 쓰기 액션이다. files.js의 elevate()와 같은 패턴(비밀번호 확인 후 재시도).
    async function elevate() {
      const pw = await promptDialog(t('이 동작에는 비밀번호 확인이 필요합니다.'), { title: t('비밀번호 확인'), password: true });
      if (pw === null) return false;
      try {
        await vtFetch('/api/auth/elevate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: pw }),
        });
        return true;
      } catch (e) {
        showToast(t('비밀번호 확인 실패'));
        return false;
      }
    }

    async function _elevatedFetch(url, opts) {
      try {
        return await vtFetch(url, opts);
      } catch (e) {
        if (!/elevation_required|401/.test(e.message || '')) throw e;
        if (!(await elevate())) return null;
        return vtFetch(url, opts);
      }
    }

    async function _showPinOnce(pin) {
      // 저장은 해시뿐이라 이 응답이 원문을 보는 유일한 기회다.
      await alertDialog(t('접속 코드: {pin}\n\n다시 표시되지 않습니다 — 잊으면 재발급하세요.', { pin }), { title: t('접속 코드') });
    }

    async function exposePort(ctx, port) {
      const choice = await openExposeDialog(port);
      if (!choice) return;
      showToast(t('포트 {port} 터널 여는 중…', { port }));
      const send = (extra) => _elevatedFetch(`/api/ports/${port}/expose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true, mode: choice.mode, ttl: choice.ttl, auto_renew: choice.autoRenew, ...extra }),
      });
      const r = await send({}).catch(async (e) => {
        // 위험 포트(Chrome 원격 디버깅 등) — 서버가 이유를 알려 준다. 한 번 더 확인받는다.
        if (e.status === 409 && e.data && e.data.error === 'dangerous_port') {
          const ok = await confirmDialog(
            t('{reason}\n\n이 포트를 공개 인터넷에 열면 접근 제어가 있어도 위험합니다. 그래도 열까요?', { reason: e.data.reason }),
            { title: t('포트 {port}는 위험한 포트입니다', { port }), okLabel: t('그래도 공개'), danger: true });
          if (!ok) { showToast(t('공개를 취소했습니다')); return null; }
          return send({ force_danger: true }).catch((e2) => { showToast(t('공개 실패: {msg}', { msg: e2.message })); return null; });
        }
        showToast(t('공개 실패: {msg}', { msg: e.message }));
        return null;
      });
      if (r) {
        if (r.pin) await _showPinOnce(r.pin);
        else showToast(r.url ? t('공개됨: {url}', { url: r.url }) : t('공개됨'));
      }
      // L5: 성공하면 그 행의 버튼이 "공개"→"미리보기"로 바뀌어야 하니 목록을 새로 받는다.
      refreshPorts(ctx, true);
    }

    async function setAutoRenew(ctx, port, on) {
      const r = await _elevatedFetch(`/api/ports/${port}/share`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_renew: on }),
      }).catch((e) => { showToast(t('변경 실패: {error}', { error: e.message })); return null; });
      if (r) showToast(on ? t('포트 {port} 자동 재노출 켜짐', { port }) : t('포트 {port} 자동 재노출 꺼짐', { port }));
      refreshPorts(ctx, true);
    }

    async function reissuePin(ctx, port) {
      const r = await _elevatedFetch(`/api/ports/${port}/share/pin`, { method: 'POST' })
        .catch((e) => { showToast(t('재발급 실패: {msg}', { msg: e.message })); return null; });
      if (r?.pin) await _showPinOnce(r.pin);
      refreshPorts(ctx, true);
    }


    // L5: 이미 열려 있는 터널을 앱 안 iframe으로 바로 확인. X-Frame-Options/CSP로
    // 대상 서버가 프레임을 거부하는 건 우리가 감지도 우회도 할 수 없는 영역이라
    // (그런 실패는 iframe 안에서 브라우저가 자체적으로 보여줄 뿐 JS 이벤트로 알 수 없다),
    // "새 탭에서 열기"를 항상 같이 둬 안전망으로 삼는다.
    function showPortPreview(port, url) {
      const panel = openPanel({
        id: 'vt-pt-preview',
        ariaLabel: t('포트 {port} 미리보기', { port }),
        extraClass: 'mode-preview',
        headHTML: `
          <div class="vt-vw-title">${vtEsc(t('포트 {port} 미리보기', { port }))}</div>
          <a class="vt-btn sm vt-vw-diff" href="${vtEsc(url)}" target="_blank" rel="noopener noreferrer">${vtEsc(t('새 탭에서 열기'))}</a>
        `,
        bodyId: 'vt-pt-preview-body',
        bodyHTML: `<iframe src="${vtEsc(url)}" title="${vtEsc(t('포트 {port} 미리보기', { port }))}" sandbox="allow-scripts allow-forms allow-same-origin allow-popups"></iframe>`,
      });
      if (!panel) return;
    }

// 액션 등록은 지연 진입점(panels/ports-lazy.js)이 맡는다 — 이 파일 자체가
// `panels` 청크에 있어 여기서 등록하면 청크를 받기 전엔 액션이 없다.
// 테스트 전용 export — DOM 부수효과 없는 순수 함수만.
export { _groupByPid, showPorts };
