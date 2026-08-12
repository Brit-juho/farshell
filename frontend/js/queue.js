// 프롬프트 큐 패널 (P4) — 에이전트가 작업 중일 때 지시를 쌓아두는 곳.
// 음성 모드와 짝이다: 걸어가면서 3개 던져놓고 순서대로 실행시킨다.
//
// 패널 껍데기(.vt-viewer-backdrop / .vt-viewer-card)는 viewer.js와 공유한다.

    let _queueTimer = null;

    function _qEl() { return document.getElementById('vt-queue'); }

    function closeQueue() {
      const el = _qEl();
      if (!el) return;
      el.remove();
      document.removeEventListener('keydown', _qKey);
      if (_queueTimer) { clearInterval(_queueTimer); _queueTimer = null; }
      try { setTimeout(() => fitAndResize(activeId), 60); } catch (_) {}
    }

    function _qKey(ev) {
      // 입력창에서 Esc를 누르면 패널이 닫히는 게 자연스럽지만,
      // 작성 중이던 내용이 날아가므로 입력이 비어 있을 때만 닫는다.
      if (ev.key !== 'Escape') return;
      const inp = document.getElementById('vt-q-input');
      if (inp && document.activeElement === inp && inp.value.trim()) { inp.value = ''; return; }
      ev.stopPropagation();
      closeQueue();
    }

    async function _qApi(path, opts) {
      const sep = path.includes('?') ? '&' : '?';
      const res = await fetch(`${API_BASE}${path}${sep}${_tokenQuery.replace(/^[?&]/, '')}`, opts);
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
        e.data = data;
        throw e;
      }
      return data;
    }

    function showQueue() {
      if (_qEl()) { closeQueue(); return; }

      const el = document.createElement('div');
      el.id = 'vt-queue';
      el.className = 'vt-viewer-backdrop';
      el.innerHTML = `
        <div class="vt-viewer-card" role="dialog" aria-modal="true" aria-label="프롬프트 큐">
          <div class="vt-viewer-head">
            <div class="vt-vw-title">프롬프트 큐</div>
            <button class="vt-vw-diff" id="vt-q-run" title="한 건 지금 투입">지금 실행</button>
            <button class="vt-vw-x" aria-label="닫기">✕</button>
          </div>
          <div class="vt-q-compose">
            <textarea id="vt-q-input" rows="2" placeholder="작업이 끝나면 실행할 지시… (Ctrl+Enter로 추가)"></textarea>
            <button class="vt-pt-btn" id="vt-q-add">추가</button>
          </div>
          <div class="vt-vw-body" id="vt-q-body">
            <div class="vt-vw-loading">불러오는 중…</div>
          </div>
        </div>
      `;
      el.querySelector('.vt-vw-x').addEventListener('click', closeQueue);
      el.addEventListener('click', (ev) => { if (ev.target === el) closeQueue(); });
      el.querySelector('#vt-q-add').addEventListener('click', addQueueItem);
      el.querySelector('#vt-q-run').addEventListener('click', runQueueOnce);
      el.querySelector('#vt-q-input').addEventListener('keydown', (ev) => {
        // Enter는 줄바꿈(멀티라인 지시가 흔하다), Ctrl/Cmd+Enter가 추가.
        if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); addQueueItem(); }
      });
      document.addEventListener('keydown', _qKey);
      document.body.appendChild(el);

      refreshQueue();
      _queueTimer = setInterval(() => { if (_qEl()) refreshQueue(); else closeQueue(); }, 5000);
    }

    async function refreshQueue() {
      const body = document.getElementById('vt-q-body');
      if (!body) return;
      let d;
      try {
        d = await _qApi('/api/queue');
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${e.message}</div>`;
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
        await _qApi('/api/queue', {
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
      try { await _qApi(`/api/queue/${id}`, { method: 'DELETE' }); }
      catch (e) { showToast(`삭제 실패: ${e.message}`); }
      refreshQueue();
    }

    async function unblockQueueItem(id) {
      try { await _qApi(`/api/queue/${id}/unblock`, { method: 'POST' }); }
      catch (e) { showToast(`재개 실패: ${e.message}`); }
      refreshQueue();
    }

    async function runQueueOnce() {
      try {
        const r = await _qApi('/api/queue/run', { method: 'POST' });
        showToast(r.drained ? `투입됨 (남은 ${r.remaining}건)` : '큐가 비었습니다');
      } catch (e) {
        showToast(e.message);
      }
      refreshQueue();
    }
