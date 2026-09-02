// 포트 대시보드 (P3) — Ports.app이 메뉴바에서 하는 일을 원격에서.
// "지금 뭐가 떠 있지 / 3000번 죽여줘"를 폰에서 처리한다.
//
// 패널 껍데기 · fetch · 닫기/폴링 뼈대는 panel.js/vtapi.js가 공유한다.
// 리스트 행만 .vt-pt-* 로 따로 둔다.

    function closePorts() { closePanel('vt-ports'); }

    function showPorts() {
      const panel = openPanel({
        id: 'vt-ports',
        ariaLabel: '포트 대시보드',
        headHTML: `
          <div class="vt-vw-title">포트 — 실행 중인 개발 서버</div>
          <button class="vt-vw-diff" id="vt-pt-refresh" title="새로고침">새로고침</button>
        `,
        bodyId: 'vt-pt-body',
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      panel.el.querySelector('#vt-pt-refresh').addEventListener('click', () => refreshPorts(true));

      refreshPorts(true);
      // 5초 폴링. 패널이 열려 있을 때만 돈다 — 닫으면 setPanelPoll이 정리한다.
      setPanelPoll('vt-ports', 5000, () => refreshPorts(false));
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
      if (dm) return parseInt(dm[1], 10) + '일';       // "05-01:26" → "5일"
      const parts = s.split(':');
      if (parts.length === 3) return parseInt(parts[0], 10) + '시간';
      if (parts.length === 2) return parseInt(parts[0], 10) + '분';
      return s;
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

    async function refreshPorts(fresh) {
      const body = document.getElementById('vt-pt-body');
      if (!body) return;
      let d;
      try {
        d = await vtFetch(`/api/ports${fresh ? '?fresh=true' : ''}`);
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${vtEsc(e.message)}</div>`;
        return;
      }
      if (!d.ports.length) {
        body.innerHTML = '<div class="vt-vw-empty">리스닝 중인 포트가 없습니다.</div>';
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
      d.ports.forEach(p => {
        if (hasBothGroups && !p.protected && !sawMine) {
          sawMine = true;
          const head = document.createElement('div');
          head.className = 'vt-pt-section';
          head.textContent = '내 서버';
          list.appendChild(head);
        }
        if (hasBothGroups && p.protected && !sawProtected) {
          sawProtected = true;
          const head = document.createElement('div');
          head.className = 'vt-pt-section';
          head.textContent = '보호됨 / 시스템';
          list.appendChild(head);
        }
        const row = document.createElement('div');
        row.className = 'vt-pt-row';

        const port = document.createElement('span');
        port.className = 'vt-pt-port';
        port.textContent = p.port;

        const meta = document.createElement('div');
        meta.className = 'vt-pt-meta';
        const name = document.createElement('div');
        name.className = 'vt-pt-cmd';
        name.textContent = p.cmd;                       // textContent — XSS 방어
        const sub = document.createElement('div');
        sub.className = 'vt-pt-sub';
        const bits = [`pid ${p.pid}`];
        if (p.uptime) bits.push(_fmtUptime(p.uptime));
        if (p.rss_kb) bits.push(_fmtMem(p.rss_kb));
        if (typeof p.cpu === 'number' && p.cpu > 0) bits.push(p.cpu.toFixed(1) + '%');
        sub.textContent = bits.join(' · ');
        meta.appendChild(name); meta.appendChild(sub);

        const tags = document.createElement('span');
        tags.className = 'vt-pt-tags';
        if (p.public) {
          const t = document.createElement('b');
          t.className = 'vt-pt-tag pub';
          t.textContent = '외부';
          t.title = '모든 인터페이스에 열려 있습니다 (*)';
          tags.appendChild(t);
        }

        const actions = document.createElement('span');
        actions.className = 'vt-pt-actions';
        // U5/L6: 터치 기기는 종료 버튼을 왼쪽 스와이프로 드러낸다(Mail/Linear 패턴) —
        // 목록을 스크롤하다 손가락이 스치는 것만으로 프로세스가 죽는 오탭을 막기 위함.
        // 마우스는 오탭 위험이 없으니 기존처럼 버튼이 항상 보인다.
        const swipeKillOnTouch = _isCoarsePointer() && !p.protected;
        if (p.protected) {
          const lock = document.createElement('span');
          lock.className = 'vt-pt-lock';
          lock.textContent = '보호됨';
          lock.title = p.protected_reason;
          actions.appendChild(lock);
        } else {
          const ex = document.createElement('button');
          ex.className = 'vt-pt-btn';
          ex.textContent = '공개';
          ex.title = '이 포트를 Cloudflare 터널로 인터넷에 공개합니다';
          ex.onclick = () => exposePort(p.port);
          actions.appendChild(ex);
          if (!swipeKillOnTouch) {
            const kb = document.createElement('button');
            kb.className = 'vt-pt-btn danger';
            kb.textContent = '종료';
            kb.onclick = () => killPort(p.port, p.pid, p.cmd);
            actions.appendChild(kb);
          }
        }

        if (!swipeKillOnTouch) {
          row.appendChild(port); row.appendChild(meta);
          row.appendChild(tags); row.appendChild(actions);
          list.appendChild(row);
          return;
        }

        // 스와이프 레이어: row는 뷰포트(overflow:hidden), inner가 실제로 좌우로
        // 밀리고, 그 아래 깔린 kill 버튼이 밀린 만큼 드러난다.
        row.classList.add('swipeable');
        const inner = document.createElement('div');
        inner.className = 'vt-pt-row-inner';
        inner.appendChild(port); inner.appendChild(meta);
        inner.appendChild(tags); inner.appendChild(actions);

        const kill = document.createElement('button');
        kill.className = 'vt-pt-swipe-kill';
        kill.textContent = '종료';
        kill.onclick = () => { _closeSwipe(row); killPort(p.port, p.pid, p.cmd); };

        row.appendChild(kill);
        row.appendChild(inner);
        _wireSwipe(row, inner, kill);
        list.appendChild(row);
      });
      body.innerHTML = '';
      body.appendChild(list);
      if (d.truncated) {
        const n = document.createElement('div');
        n.className = 'vt-vw-note warn';
        n.textContent = '포트가 많아 일부만 표시했습니다.';
        body.appendChild(n);
      }
    }

    async function killPort(port, pid, cmd) {
      if (!confirm(`포트 ${port} (${cmd}, pid ${pid}) 를 종료할까요?`)) return;
      try {
        // pid를 함께 보낸다 — 조회 후 프로세스가 바뀌었으면 서버가 409로 거부한다.
        const r = await vtFetch(`/api/ports/${port}?pid=${pid}`, { method: 'DELETE' });
        showToast(`포트 ${port} 종료됨 (${r.signal})`);
      } catch (e) {
        showToast(`종료 실패: ${e.message}`);
      }
      refreshPorts(true);
    }

    async function exposePort(port) {
      // 2단계 확인. 서버도 confirm 없으면 428로 거부하지만, 오탭을 UI에서 먼저 막는다.
      if (!confirm(`포트 ${port} 를 공개 인터넷에 노출합니다.\n\n누구나 URL만 알면 접근할 수 있습니다. 계속할까요?`)) return;
      showToast(`포트 ${port} 터널 여는 중…`);
      try {
        const r = await vtFetch(`/api/ports/${port}/expose`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true }),
        });
        showToast(r.url ? `공개됨: ${r.url}` : '공개됨');
      } catch (e) {
        showToast(`공개 실패: ${e.message}`);
      }
    }
