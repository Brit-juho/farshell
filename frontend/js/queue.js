// 프롬프트 큐 패널 (P4) — 에이전트가 작업 중일 때 지시를 쌓아두는 곳.
// 음성 모드와 짝이다: 걸어가면서 3개 던져놓고 순서대로 실행시킨다.
//
// 패널 껍데기 · fetch · 닫기/폴링 뼈대는 panels/panel.js·core/api.js가 공유한다.
// F5에서 classic script에서 ES 모듈로 전환.
//
// N6(60 §4) 문서의 "상태 … `새 섹션에서 실행`(아래)" 표기에 대한 판단: "아래"가
// 가리키는 실제 정의는 스니펫 섹션의 [새 섹션] 실행처뿐이고, 큐의 target 스키마는
// 문서상 `{worktree?|session?}` 두 키뿐이라 "새 섹션"이라는 세 번째 타깃 종류는
// 없다. 그래서 큐에는 새 상태/타깃 모드를 추가하지 않았고, target 칩(대상
// 워크트리/세션)만 구현했다 — 스니펫 쪽 "새 섹션"과 용어를 공유할 뿐 큐 자체의
// 드레인 동작을 바꾸는 지시는 아니라고 판단(불명확한 지점, 90-verification.md
// §4 12항 취지상 문서화하고 진행).
import { openPanel, closePanel, setPanelPoll } from './panels/panel.js';
import { vtFetch, vtEsc } from './core/api.js';
import { isMac } from './core/env.js';
import { registerAction } from './core/dom.js';

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
            <textarea id="vt-q-input" rows="2" placeholder="작업이 끝나면 실행할 지시… (${isMac ? 'Cmd' : 'Ctrl'}+Enter로 추가)"></textarea>
            <div class="vt-q-compose-row">
              <select id="vt-q-target" class="vt-q-target" title="대상 세션 — 비워두면 음성과 같은 자동 타깃 규칙을 따릅니다">
                <option value="">자동 (음성 타깃 규칙)</option>
              </select>
              <button class="vt-pt-btn" id="vt-q-add">추가</button>
            </div>
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

      _loadQueueTargets();
      refreshQueue();
      // 5초 폴링. 패널이 열려 있을 때만 돈다 — 닫으면 setPanelPoll이 정리한다.
      setPanelPoll('vt-queue', 5000, refreshQueue);
    }

    // 대상 세션 드롭다운 — tmux 세션 목록으로 채운다. 실패해도 "자동"만 남기고 조용히 넘어간다
    // (큐 자체를 쓰는 데는 지장이 없어야 한다).
    async function _loadQueueTargets() {
      const sel = document.getElementById('vt-q-target');
      if (!sel) return;
      let sessions;
      try {
        sessions = await vtFetch('/api/tmux/sessions');
      } catch (_) { return; }
      const cur = sel.value;
      (sessions || []).forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.name;
        opt.textContent = s.name;
        sel.appendChild(opt);
      });
      sel.value = cur;
    }

    // U9: 드롭다운 첫 옵션("자동")의 문구를 실제 타깃으로 갱신.
    // lock:<세션> 모드면 "그 세션으로 고정돼 있다"까지 드러내야 음성 타깃(fsh voice-target)과
    // 헷갈리지 않는다 — auto가 항상 "현재 보고 있는 세션"은 아니기 때문.
    function _updateAutoTargetLabel(label, mode) {
      const opt = document.querySelector('#vt-q-target option[value=""]');
      if (!opt) return;
      if (!label) { opt.textContent = '자동 (대상 없음)'; return; }
      opt.textContent = mode && mode.startsWith('lock:')
        ? `자동 (→ ${label}, 고정됨)`
        : `자동 (→ ${label})`;
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
      // U9: "자동" 옵션이 실제로 가리키는 세션을 드롭다운 라벨에 바로 반영.
      _updateAutoTargetLabel(d.auto_target, d.auto_target_mode);

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
        if (it.blocked_reason) {
          const sub = document.createElement('div');
          sub.className = 'vt-q-sub';
          sub.textContent = `차단됨: ${it.blocked_reason}`;
          meta.appendChild(sub);
        }

        // [대상 워크트리 칩](60 §4) — target.session만 실제로 채워진다(2.1.0엔
        // 워크트리 모델이 없다, ADR-20). target.worktree는 2.1.1에서 레일이
        // 실제 워크트리를 그리기 시작하면 여기 표시가 늘어날 자리다.
        const targetSession = it.target && it.target.session;
        if (targetSession) {
          const chip = document.createElement('span');
          chip.className = 'vt-q-chip';
          chip.textContent = targetSession;
          chip.title = `대상 세션: ${targetSession}`;
          meta.appendChild(chip);
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
      const sel = document.getElementById('vt-q-target');
      const text = (inp.value || '').trim();
      if (!text) return;
      // N6(60 §4): target 스키마가 `{worktree?|session?}`로 바뀌었다 — 2.1.0엔
      // 워크트리 모델이 없어 session 키만 채운다(server/queue_store.py 참고).
      const target = sel && sel.value ? { session: sel.value } : undefined;
      try {
        await vtFetch('/api/queue', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, target }),
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

// F3(c): data-action 위임용 등록.
registerAction('queue.show', () => showQueue());
