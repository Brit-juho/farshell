// 프롬프트 큐 패널 (P4) — 에이전트가 작업 중일 때 지시를 쌓아두는 곳.
// 음성 모드와 짝이다: 걸어가면서 3개 던져놓고 순서대로 실행시킨다.
//
// 패널 껍데기 · fetch · 닫기/폴링 뼈대는 panel.js/vtapi.js가 공유한다.

    function closeQueue() { closePanel('vt-queue'); }

    function showQueue() {
      const panel = openPanel({
        id: 'vt-queue',
        ariaLabel: '프롬프트 큐',
        headHTML: `
          <div class="vt-vw-title">프롬프트 큐</div>
          <button class="vt-vw-diff" id="vt-q-run" title="한 건 지금 투입">지금 실행</button>
        `,
        extraHTML: `
          <div class="vt-q-compose">
            <textarea id="vt-q-input" rows="2" placeholder="작업이 끝나면 실행할 지시… (Ctrl+Enter로 추가)"></textarea>
            <button class="vt-pt-btn" id="vt-q-add">추가</button>
          </div>
        `,
        bodyId: 'vt-q-body',
        onKey: (ev) => {
          // 입력창에서 Esc를 누르면 패널이 닫히는 게 자연스럽지만,
          // 작성 중이던 내용이 날아가므로 입력이 비어 있을 때만 기본 닫기로 넘긴다.
          const inp = document.getElementById('vt-q-input');
          if (inp && document.activeElement === inp && inp.value.trim()) { inp.value = ''; return true; }
        },
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      panel.el.querySelector('#vt-q-add').addEventListener('click', addQueueItem);
      panel.el.querySelector('#vt-q-run').addEventListener('click', runQueueOnce);
      panel.el.querySelector('#vt-q-input').addEventListener('keydown', (ev) => {
        // Enter는 줄바꿈(멀티라인 지시가 흔하다), Ctrl/Cmd+Enter가 추가.
        if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); addQueueItem(); }
      });

      refreshQueue();
      // 5초 폴링. 패널이 열려 있을 때만 돈다 — 닫으면 setPanelPoll이 정리한다.
      setPanelPoll('vt-queue', 5000, refreshQueue);
    }

    async function refreshQueue() {
      const body = document.getElementById('vt-q-body');
      if (!body) return;
      let d;
      try {
        d = await vtFetch('/api/queue');
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${vtEsc(e.message)}</div>`;
        return;
      }
      if (!d.items.length) {
        body.innerHTML = `<div class="vt-vw-empty">큐가 비어 있습니다.<br>`
          + `${d.autodrain ? '작업이 끝나면 자동으로 투입됩니다.' : '자동 투입이 꺼져 있습니다 — "지금 실행"을 쓰세요.'}</div>`;
        return;
      }

      const list = document.createElement('div');
      list.className = 'vt-vw-list';
      d.items.forEach((it, i) => {
        const row = document.createElement('div');
        row.className = 'vt-q-row' + (it.status === 'blocked' ? ' blocked' : '');

        const idx = document.createElement('span');
        idx.className = 'vt-q-idx';
        idx.textContent = it.status === 'blocked' ? '⏸' : (i + 1);

        const meta = document.createElement('div');
        meta.className = 'vt-q-meta';
        const txt = document.createElement('div');
        txt.className = 'vt-q-text';
        txt.textContent = it.text;                    // textContent — XSS 방어
        meta.appendChild(txt);
        if (it.target || it.blocked_reason) {
          const sub = document.createElement('div');
          sub.className = 'vt-q-sub';
          sub.textContent = it.blocked_reason
            ? `차단됨: ${it.blocked_reason}`
            : `→ ${it.target}`;
          meta.appendChild(sub);
        }

        const act = document.createElement('span');
        act.className = 'vt-pt-actions';
        if (it.status === 'blocked') {
          const ub = document.createElement('button');
          ub.className = 'vt-pt-btn';
          ub.textContent = '재개';
          ub.onclick = () => unblockQueueItem(it.id);
          act.appendChild(ub);
        }
        const rm = document.createElement('button');
        rm.className = 'vt-pt-btn danger';
        rm.textContent = '삭제';
        rm.onclick = () => removeQueueItem(it.id);
        act.appendChild(rm);

        row.appendChild(idx); row.appendChild(meta); row.appendChild(act);
        list.appendChild(row);
      });
      body.innerHTML = '';
      body.appendChild(list);

      const foot = document.createElement('div');
      foot.className = 'vt-vw-note';
      foot.textContent = d.autodrain
        ? `${d.pending}건 대기 — 에이전트 작업이 끝나면 순서대로 투입됩니다`
        : `${d.pending}건 대기 — 자동 투입 꺼짐(VT_QUEUE_AUTODRAIN=0)`;
      body.appendChild(foot);
    }

    async function addQueueItem() {
      const inp = document.getElementById('vt-q-input');
      const text = (inp.value || '').trim();
      if (!text) return;
      try {
        await vtFetch('/api/queue', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        });
        inp.value = '';
      } catch (e) {
        showToast(`추가 실패: ${e.message}`);
      }
      refreshQueue();
    }

    async function removeQueueItem(id) {
      try { await vtFetch(`/api/queue/${id}`, { method: 'DELETE' }); }
      catch (e) { showToast(`삭제 실패: ${e.message}`); }
      refreshQueue();
    }

    async function unblockQueueItem(id) {
      try { await vtFetch(`/api/queue/${id}/unblock`, { method: 'POST' }); }
      catch (e) { showToast(`재개 실패: ${e.message}`); }
      refreshQueue();
    }

    async function runQueueOnce() {
      try {
        const r = await vtFetch('/api/queue/run', { method: 'POST' });
        showToast(r.drained ? `투입됨 (남은 ${r.remaining}건)` : '큐가 비었습니다');
      } catch (e) {
        showToast(e.message);
      }
      refreshQueue();
    }
