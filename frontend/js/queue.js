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
import { openPanel, setPanelPoll } from './panels/panel.js';
import { icon } from './ui/icons.js';
import { vtFetch, vtEsc } from './core/api.js';
import { isMac } from './core/env.js';
import { t } from './core/i18n.js';


/** U8 — 「지금 실행」은 **큐에 투입할 게 있을 때만** 누를 수 있다.
 *
 * 빈 큐에서도 눌렸고, 눌러봐야 서버가 할 일이 없다. 누를 수 없는 이유를
 * `title`로 말해 준다 — 비활성 버튼이 이유 없이 회색이면 "고장났나"로 읽힌다.
 * `refreshQueue`가 목록을 그릴 때마다 부른다(폴링 5초). export하는 이유는
 * 단위 테스트가 DOM 없이도 규칙을 고정할 수 있게 하기 위해서다. */
export function syncRunButton(root, count) {
  // `root`는 인스턴스 ctx(`byId`를 가진 것)이거나 평범한 DOM 루트다. pane
  // 인스턴스는 안쪽 id가 `vt-q-run--pane-p3`처럼 옮겨져 있어서 `'#vt-q-run'`
  // 셀렉터로는 못 찾는다 — ctx가 그 접미사를 안다.
  const btn = root?.byId ? root.byId('vt-q-run') : root?.querySelector?.('#vt-q-run');
  if (!btn) return null;
  btn.disabled = count === 0;
  btn.title = count === 0 ? t('큐가 비어 있습니다') : t('한 건 지금 투입');
  return btn;
}

// N4 — `opts.paneHost`가 있으면 dock/모달이 아니라 **그 pane 칸**에 그린다.
// 같은 패널이 dock에도 동시에 떠 있을 수 있으므로(사용자 결정, 2026-09-20)
// 이 함수 안의 모든 DOM 조회는 전역 id가 아니라 `ctx`(자기 인스턴스)를 거친다.
// 그 전환이 이 패널 이식의 전부다 — 나머지 로직은 손대지 않았다.
/** 막힌 항목의 한 줄. 권한 재확인으로 막힌 항목은 서버가 사람이 읽는 문구(blocked_text)를 함께 주므로 코드 대신 그것을 보인다.
 * retry_at이 있으면 서버 점검(queue_store.sweep)이 알아서 다시 시도한다. */
export function blockedLine(it) {
  const reason = it.blocked_text || it.blocked_reason;
  return it.retry_at
    ? t('차단됨: {reason} — 자동 재시도 ({n}회째)', { reason, n: it.attempts || 0 })
    : t('차단됨: {reason}', { reason });
}

export function showQueue(opts = {}) {
      const panel = openPanel({
        paneHost: opts.paneHost || null,
        paneId: opts.paneId || null,
        id: 'vt-queue',
        ariaLabel: t('프롬프트 큐'),
        headHTML: `
          <div class="vt-vw-title">${t('프롬프트 큐')}</div>
          <button class="vt-btn sm vt-vw-diff" id="vt-q-run" title="${t('한 건 지금 투입')}">${t('지금 실행')}</button>
        `,
        extraHTML: `
          <div class="vt-q-compose">
            <textarea id="vt-q-input" class="vt-input lg" rows="2" placeholder="${t('작업이 끝나면 실행할 지시… ({key}+Enter로 추가)', { key: isMac ? 'Cmd' : 'Ctrl' })}"></textarea>
            <div class="vt-q-compose-row">
              <select id="vt-q-target" class="vt-input vt-q-target" title="${t('대상 세션 — 비워두면 음성과 같은 자동 타깃 규칙을 따릅니다')}">
                <option value="">${t('자동 (음성 타깃 규칙)')}</option>
              </select>
              <button class="vt-btn" id="vt-q-add">${t('추가')}</button>
            </div>
          </div>
        `,
        bodyId: 'vt-q-body',
        onKey: (ev) => {
          // 입력창에서 Esc를 누르면 패널이 닫히는 게 자연스럽지만,
          // 작성 중이던 내용이 날아가므로 입력이 비어 있을 때만 기본 닫기로 넘긴다.
          const inp = ctx.byId('vt-q-input');
          if (inp && document.activeElement === inp && inp.value.trim()) { inp.value = ''; return true; }
        },
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
      // 이 인스턴스 안에서만 찾는다. dock/모달에서는 전역 조회와 같다.
      const ctx = panel.ctx;

      ctx.byId('vt-q-add').addEventListener('click', () => addQueueItem(ctx));
      ctx.byId('vt-q-run').addEventListener('click', () => runQueueOnce(ctx));
      ctx.byId('vt-q-input').addEventListener('keydown', (ev) => {
        // Enter는 줄바꿈(멀티라인 지시가 흔하다), Ctrl/Cmd+Enter가 추가.
        if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); addQueueItem(ctx); }
      });

      // 첫 응답이 오기 전까지는 개수를 모른다 — 모르는 동안은 막아둔다.
      // 열자마자 누를 수 있으면 그 찰나에 빈 큐로 요청이 나간다.
      syncRunButton(ctx, 0);
      _loadQueueTargets(ctx);
      refreshQueue(ctx);
      // 5초 폴링. 패널이 열려 있을 때만 돈다 — 닫으면 setPanelPoll이 정리한다.
      setPanelPoll(panel.el, 5000, () => refreshQueue(ctx));
    }

    // 대상 세션 드롭다운 — tmux 세션 목록으로 채운다. 실패해도 "자동"만 남기고 조용히 넘어간다
    // (큐 자체를 쓰는 데는 지장이 없어야 한다).
    async function _loadQueueTargets(ctx) {
      const sel = ctx.byId('vt-q-target');
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
      // A3(80 §1 3단계) — 원격 호스트 세션. 값은 `host::세션` 합성 키다:
      // 세션 이름만으로는 어느 기계인지 못 정한다(`dev`는 맥에도 gpu-box에도 있다).
      try {
        const hosts = (await vtFetch('/api/hosts'))?.hosts || [];
        for (const h of hosts) {
          if (h.id === 'local' || !h.online) continue;
          for (const rs of h.sessions || []) {
            const opt = document.createElement('option');
            opt.value = `${h.id}::${rs.name}`;
            opt.textContent = `${rs.name} · ${h.label || h.id}`;
            sel.appendChild(opt);
          }
        }
      } catch (_) { /* 멀티호스트를 안 쓰는 환경 — 로컬 목록만 남는다 */ }
      sel.value = cur;
    }

    // U9: 드롭다운 첫 옵션("자동")의 문구를 실제 타깃으로 갱신.
    // lock:<세션> 모드면 "그 세션으로 고정돼 있다"까지 드러내야 음성 타깃(fsh voice-target)과
    // 헷갈리지 않는다 — auto가 항상 "현재 보고 있는 세션"은 아니기 때문.
    function _updateAutoTargetLabel(ctx, label, mode) {
      const opt = document.querySelector('#vt-q-target option[value=""]');
      if (!opt) return;
      if (!label) { opt.textContent = t('자동 (대상 없음)'); return; }
      opt.textContent = mode && mode.startsWith('lock:')
        ? t('자동 (→ {label}, 고정됨)', { label })
        : t('자동 (→ {label})', { label });
    }

    async function refreshQueue(ctx) {
      const body = ctx.byId('vt-q-body');
      if (!body) return;
      let d;
      try {
        d = await vtFetch('/api/queue');
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${vtEsc(e.message)}</div>`;
        return;
      }
      // U9: "자동" 옵션이 실제로 가리키는 세션을 드롭다운 라벨에 바로 반영.
      _updateAutoTargetLabel(ctx, d.auto_target, d.auto_target_mode);

      syncRunButton(ctx, d.items.length);

      if (!d.items.length) {
        // 「지금 실행」이 방금 비활성이 됐으므로 그쪽을 가리키지 않는다 —
        // 못 누르는 버튼을 쓰라고 안내하면 그게 더 헷갈린다.
        body.innerHTML = `<div class="vt-vw-empty">${t('큐가 비어 있습니다.')}<br>`
          + `${d.autodrain ? t('작업이 끝나면 자동으로 투입됩니다.') : t('자동 투입이 꺼져 있습니다 — 항목을 추가한 뒤 「지금 실행」으로 투입하세요.')}</div>`;
        return;
      }

      const list = document.createElement('div');
      list.className = 'vt-q-list';
      d.items.forEach((it, i) => {
        const row = document.createElement('div');
        row.className = 'vt-q-row' + (it.status === 'blocked' ? ' blocked' : '');

        // 머리줄: 순번(또는 멈춤 아이콘) · 상태 한 줄 · [대상 세션 칩]
        const head = document.createElement('div');
        head.className = 'vt-q-head';
        const idx = document.createElement('span');
        idx.className = 'vt-q-idx';
        // blocked는 숫자 자리에 '멈춤' 아이콘을 넣는다. 이모지(⏸)는 플랫폼마다
        // 모양이 갈리고 currentColor를 안 따라 상태색과 어긋났다.
        if (it.status === 'blocked') idx.innerHTML = icon('pause', 12);
        else idx.textContent = `#${i + 1}`;
        head.appendChild(idx);

        const sub = document.createElement('span');
        sub.className = 'vt-q-sub';
        if (it.blocked_reason) sub.textContent = blockedLine(it);
        else if (it.status === 'sending') sub.textContent = t('전송 중');
        head.appendChild(sub);

        // [대상 워크트리 칩](60 §4) — target.session만 실제로 채워진다(2.1.0엔
        // 워크트리 모델이 없다, ADR-20). target.worktree는 2.1.1에서 레일이
        // 실제 워크트리를 그리기 시작하면 여기 표시가 늘어날 자리다.
        const targetSession = it.target && it.target.session;
        const targetHost = it.target && it.target.host;
        if (targetSession) {
          const chip = document.createElement('span');
          chip.className = 'vt-tag';
          chip.textContent = targetHost ? `${targetSession} · ${targetHost}` : targetSession;
          chip.title = targetHost
            ? t('대상: {host} 호스트의 {session} 세션', { host: targetHost, session: targetSession })
            : t('대상 세션: {session}', { session: targetSession });
          head.appendChild(chip);
        }
        row.appendChild(head);

        const txt = document.createElement('div');
        txt.className = 'vt-q-text';
        txt.textContent = it.text;                    // textContent — XSS 방어
        row.appendChild(txt);

        const act = document.createElement('div');
        act.className = 'vt-q-actions';
        if (it.status === 'blocked') {
          const ub = document.createElement('button');
          ub.className = 'vt-btn sm';
          ub.textContent = t('재개');
          ub.onclick = () => unblockQueueItem(ctx, it.id);
          act.appendChild(ub);
        }
        // 전송 중인 항목은 서버가 삭제를 거부한다(409 item_sending) — 버튼도 안 그린다.
        if (it.status !== 'sending') {
          const rm = document.createElement('button');
          rm.className = 'vt-btn sm danger';
          rm.textContent = t('삭제');
          rm.onclick = () => removeQueueItem(ctx, it.id);
          act.appendChild(rm);
        }
        if (act.childElementCount) row.appendChild(act);
        list.appendChild(row);
      });
      body.innerHTML = '';
      body.appendChild(list);

      const foot = document.createElement('div');
      foot.className = 'vt-vw-note';
      foot.textContent = d.autodrain
        ? t('{n}건 대기 — 에이전트 작업이 끝나면 순서대로 투입됩니다', { n: d.pending })
        : t('{n}건 대기 — 자동 투입 꺼짐(VT_QUEUE_AUTODRAIN=0)', { n: d.pending });
      body.appendChild(foot);
    }

    async function addQueueItem(ctx) {
      const inp = ctx.byId('vt-q-input');
      const sel = ctx.byId('vt-q-target');
      const text = (inp.value || '').trim();
      if (!text) return;
      // N6(60 §4): target 스키마가 `{worktree?|session?}`로 바뀌었다 — 2.1.0엔
      // 워크트리 모델이 없어 session 키만 채운다(server/queue_store.py 참고).
      // `host::세션`이면 host를 분리해 보낸다(A3). 구분자가 없으면 로컬이다.
      const raw = sel && sel.value ? sel.value : '';
      const target = raw
        ? (raw.includes('::')
            ? { host: raw.slice(0, raw.indexOf('::')), session: raw.slice(raw.indexOf('::') + 2) }
            : { session: raw })
        : undefined;
      try {
        await vtFetch('/api/queue', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, target }),
        });
        inp.value = '';
      } catch (e) {
        showToast(t('추가 실패: {msg}', { msg: e.message }));
      }
      refreshQueue(ctx);
    }

    async function removeQueueItem(ctx, id) {
      try { await vtFetch(`/api/queue/${id}`, { method: 'DELETE' }); }
      catch (e) { showToast(t('삭제 실패: {msg}', { msg: e.message })); }
      refreshQueue(ctx);
    }

    async function unblockQueueItem(ctx, id) {
      try { await vtFetch(`/api/queue/${id}/unblock`, { method: 'POST' }); }
      catch (e) { showToast(t('재개 실패: {msg}', { msg: e.message })); }
      refreshQueue(ctx);
    }

    async function runQueueOnce(ctx) {
      try {
        const r = await vtFetch('/api/queue/run', { method: 'POST' });
        showToast(r.drained ? t('투입됨 (남은 {n}건)', { n: r.remaining }) : t('큐가 비었습니다'));
      } catch (e) {
        showToast(e.message);
      }
      refreshQueue(ctx);
    }

// F3(c): data-action 위임용 등록.
// 액션 등록은 queue-lazy.js(app.js에 남는 스텁)가 맡는다.
