// 프롬프트 스니펫 라이브러리 (L3) — iTerm2 Snippets와 같은 개념.
// 프롬프트 큐(queue.js)와 다르다: 큐는 "순서를 기다렸다가" 나가지만, 스니펫은
// 대기 없이 지금 보고 있는 세션에 바로 주입한다. 그래서 상태 기계 없이 순수
// CRUD + "실행" 뿐이다.
//
// N6(60 §4)에서 두 가지가 늘었다:
//   1. 스코프(전체/프로젝트) — 저장할 때 「이 프로젝트에만」을 고를 수 있고,
//      목록은 두 탭(프로젝트/전체)으로 필터한다. project 키(저장소 top 경로)
//      판정은 서버(snippet_store.resolve_project_key)가 한다 — 클라이언트가
//      git 판정을 중복 구현하지 않는다.
//   2. 실행 방식(mode, 실사용 요청 2026-09-11) — 스니펫마다 저장할 때 하나를
//      고른다: `paste`(지금 세션에 텍스트만 넣고 Enter는 안 누름 — 기본값,
//      값 채우고 확인 후 실행해야 하는 것들) / `new_section`(활성 페인을
//      오른쪽으로 분할해 같은 cwd의 새 tmux 세션을 만들고 바로 Enter까지).
//      행마다 버튼 두 개를 매번 고르던 걸, 저장 시 한 번 고르고 그 뒤로는
//      버튼 하나로 고정한다 — 나중에 바꾸고 싶으면 행의 모드 전환 버튼으로.
//
// 패널 껍데기 · fetch · 닫기 뼈대는 panels/panel.js·core/api.js가 공유한다.
// F5에서 classic script에서 ES 모듈로 전환.
import { openPanel, closePanel } from './panels/panel.js';
import { vtFetch, vtEsc } from './core/api.js';
import { activeSession, activeSessionId, getSession } from './core/store.js';
import { sendToPty, sendPaste } from './term/clipboard.js';
import { addSession } from './term/session.js';

// '전체' 탭 기본 — 기존 동작(스코프 구분 없이 전부 표시)과 최대한 같게 시작한다.
let _scopeTab = 'global';

function closeSnippets() { closePanel('vt-snippets'); }

export function showSnippets() {
      const panel = openPanel({
        id: 'vt-snippets',
        ariaLabel: '프롬프트 스니펫',
        headHTML: `<div class="vt-vw-title">프롬프트 스니펫</div>`,
        extraHTML: `
          <div class="vt-sn-scope-tabs" role="tablist" aria-label="스니펫 스코프">
            <button type="button" class="vt-segment vt-sn-scope-tab" data-scope="project" id="vt-sn-tab-project" role="tab">프로젝트</button>
            <button type="button" class="vt-segment vt-sn-scope-tab" data-scope="global" id="vt-sn-tab-global" role="tab">전체</button>
          </div>
          <div class="vt-q-compose">
            <input id="vt-sn-label" class="vt-input lg" type="text" placeholder="이름 (선택)" maxlength="60" />
            <textarea id="vt-sn-input" rows="3" placeholder="저장할 지시문/명령…"></textarea>
            <div class="vt-sn-mode-choice" role="radiogroup" aria-label="실행 방식">
              <label class="vt-sn-mode-opt">
                <input type="radio" name="vt-sn-mode" value="paste" checked />
                붙여넣기만 <span class="vt-q-sub">Enter는 안 누름 — 값 채우고 확인 후 직접 실행</span>
              </label>
              <label class="vt-sn-mode-opt">
                <input type="radio" name="vt-sn-mode" value="new_section" />
                새 섹션 자동 실행 <span class="vt-q-sub">오른쪽에 새 세션을 열고 바로 Enter까지</span>
              </label>
            </div>
            <div class="vt-q-compose-row">
              <label class="vt-sn-save-scope" id="vt-sn-save-scope-label" title="현재 활성 세션의 저장소에만 저장">
                <input type="checkbox" id="vt-sn-save-project" /> 이 프로젝트에만
              </label>
              <button class="vt-btn" id="vt-sn-add">저장</button>
            </div>
          </div>
        `,
        bodyId: 'vt-sn-body',
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      panel.el.querySelector('#vt-sn-add').addEventListener('click', addSnippet);
      panel.el.querySelector('#vt-sn-tab-project').addEventListener('click', () => _switchScopeTab('project'));
      panel.el.querySelector('#vt-sn-tab-global').addEventListener('click', () => _switchScopeTab('global'));
      _switchScopeTab(_scopeTab);
    }

    // 활성 세션의 tmux cwd — scm.js _currentRepo()/term/links.js _getSessionCwd()와
    // 같은 방식(tmux 세션 목록에서 이름으로 찾기). 여기서 다시 만드는 이유는
    // 두 모듈 다 이 파일과 같은 동기 로드 그래프에 없어(하나는 지연 청크) 직접
    // import하면 청크 경계를 또 하나 만들게 되기 때문 — 10줄짜리 조회라 중복
    // 비용이 낮다.
    async function _activeCwd() {
      const s = activeSession();
      const tmuxName = s && (s.tmuxName || s.tmux_name);
      if (!tmuxName) return null;
      try {
        const list = await vtFetch('/api/tmux/sessions');
        const info = (list || []).find((x) => x.name === tmuxName);
        return (info && info.cwd) || null;
      } catch (_) { return null; }
    }

    async function _currentProjectKey() {
      const cwd = await _activeCwd();
      if (!cwd) return null;
      try {
        const d = await vtFetch(`/api/snippets/project?cwd=${encodeURIComponent(cwd)}`);
        return (d && d.project) || null;
      } catch (_) { return null; }
    }

    async function _switchScopeTab(scope) {
      _scopeTab = scope;
      const panel = document.getElementById('vt-snippets');
      if (!panel) return;
      const pTab = panel.querySelector('#vt-sn-tab-project');
      const gTab = panel.querySelector('#vt-sn-tab-global');
      if (pTab) { pTab.classList.toggle('active', scope === 'project'); pTab.setAttribute('aria-selected', String(scope === 'project')); }
      if (gTab) { gTab.classList.toggle('active', scope === 'global'); gTab.setAttribute('aria-selected', String(scope === 'global')); }
      await refreshSnippets();
    }

    async function refreshSnippets() {
      const body = document.getElementById('vt-sn-body');
      if (!body) return;
      let d;
      try {
        d = await vtFetch('/api/snippets');
      } catch (e) {
        body.innerHTML = `<div class="vt-vw-empty">${vtEsc(e.message)}</div>`;
        return;
      }

      let items = d.items;
      if (_scopeTab === 'project') {
        const projectKey = await _currentProjectKey();
        if (!projectKey) {
          body.innerHTML = '<div class="vt-vw-empty">현재 세션이 git 저장소 안에 있어야 프로젝트 스니펫을 볼 수 있습니다.</div>';
          return;
        }
        items = items.filter((it) => it.scope === 'project' && it.project === projectKey);
      }

      if (!items.length) {
        body.innerHTML = _scopeTab === 'project'
          ? '<div class="vt-vw-empty">이 프로젝트에 저장된 스니펫이 없습니다.</div>'
          : '<div class="vt-vw-empty">저장된 스니펫이 없습니다.<br>자주 쓰는 지시문을 위에 저장해두세요.</div>';
        return;
      }

      const list = document.createElement('div');
      list.className = 'vt-vw-list';
      items.forEach((it) => {
        const row = document.createElement('div');
        row.className = 'vt-q-row';

        const meta = document.createElement('div');
        meta.className = 'vt-q-meta';
        const txt = document.createElement('div');
        txt.className = 'vt-q-text';
        txt.textContent = it.label || it.text;             // textContent — XSS 방어
        meta.appendChild(txt);
        if (it.label) {
          const sub = document.createElement('div');
          sub.className = 'vt-q-sub';
          sub.textContent = it.text.replace(/\n/g, ' ⏎ ').slice(0, 80);
          meta.appendChild(sub);
        }
        if (it.scope === 'project') {
          const chip = document.createElement('span');
          chip.className = 'vt-tag vt-q-chip';
          chip.textContent = '프로젝트';
          chip.title = it.project || '';
          meta.appendChild(chip);
        }

        const act = document.createElement('span');
        act.className = 'vt-pt-actions';
        const isPaste = it.mode !== 'new_section';   // 구형(모드 없는) 항목도 paste로 안전하게 처리

        const run = document.createElement('button');
        run.className = 'vt-btn';
        if (isPaste) {
          run.textContent = '붙여넣기';
          run.title = '지금 보고 있는 세션에 텍스트만 입력 — Enter는 직접';
          run.onclick = () => pasteSnippet(it);
        } else {
          run.textContent = '새 섹션 실행';
          run.title = '오른쪽으로 분할해 같은 위치의 새 세션에서 바로 실행';
          run.onclick = () => runSnippetNewSection(it);
        }

        // 저장할 때 고른 모드를 나중에 바꿀 수 있어야 "관리"다 — 매번 지우고
        // 다시 만들게 하지 않는다.
        const toggle = document.createElement('button');
        toggle.className = 'vt-btn vt-sn-mode-toggle';
        toggle.title = isPaste ? '새 섹션 자동 실행으로 바꾸기' : '붙여넣기만으로 바꾸기';
        toggle.textContent = isPaste ? '⇥ 자동으로' : '⇤ 붙여넣기로';
        toggle.onclick = () => toggleSnippetMode(it, isPaste ? 'new_section' : 'paste');

        const rm = document.createElement('button');
        rm.className = 'vt-btn danger';
        rm.textContent = '삭제';
        rm.onclick = () => removeSnippet(it.id);
        act.appendChild(run); act.appendChild(toggle); act.appendChild(rm);

        row.appendChild(meta); row.appendChild(act);
        list.appendChild(row);
      });
      body.innerHTML = '';
      body.appendChild(list);
    }

    async function addSnippet() {
      const labelEl = document.getElementById('vt-sn-label');
      const inputEl = document.getElementById('vt-sn-input');
      const projectCk = document.getElementById('vt-sn-save-project');
      const modeEl = document.querySelector('input[name="vt-sn-mode"]:checked');
      const text = (inputEl.value || '');
      if (!text.trim()) return;
      const wantProject = !!(projectCk && projectCk.checked);
      const cwd = wantProject ? await _activeCwd() : null;
      try {
        await vtFetch('/api/snippets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text, label: labelEl.value,
            scope: wantProject ? 'project' : 'global',
            cwd,
            mode: (modeEl && modeEl.value) || 'paste',
          }),
        });
        labelEl.value = '';
        inputEl.value = '';
      } catch (e) {
        showToast(`저장 실패: ${e.message}`);
      }
      refreshSnippets();
    }

    async function removeSnippet(id) {
      try { await vtFetch(`/api/snippets/${id}`, { method: 'DELETE' }); }
      catch (e) { showToast(`삭제 실패: ${e.message}`); }
      refreshSnippets();
    }

    async function toggleSnippetMode(it, newMode) {
      try {
        await vtFetch(`/api/snippets/${it.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: newMode }),
        });
      } catch (e) {
        showToast(`변경 실패: ${e.message}`);
        return;
      }
      refreshSnippets();
    }

    // 붙여넣기만 — 지금 보고 있는 세션에 텍스트를 넣기만 하고 Enter는 안
    // 누른다. 값을 끼워 넣거나 실행 전에 확인해야 하는 스니펫용(경로·플래그가
    // 매번 달라지는 것 등) — 자동 실행되면 오히려 사고가 난다. N24: 이건
    // 의미 그대로 "붙여넣기"라 sendPaste를 쓴다 — 아래 runSnippetNewSection과
    // 달리 줄마다 실행되면 안 되므로(그게 사고), bracketed paste로 감싸지는
    // 경로가 정확히 맞는다.
    function pasteSnippet(it) {
      if (!activeSession()) { showToast('열려 있는 세션이 없습니다', 'error'); return; }
      sendPaste(activeSessionId(), it.text);
      closeSnippets();
    }

    // wsHandle이 OPEN이 될 때까지 짧게 폴링 — sendToPty는 큐잉을 하지 않고
    // OPEN이 아니면 조용히 버리므로, 새로 만든 세션에 곧바로 텍스트를 밀어
    // 넣으면 연결이 뜨기 전에 유실될 수 있다.
    function _waitForPtyOpen(id, timeoutMs = 5000) {
      return new Promise((resolve) => {
        const start = Date.now();
        (function poll() {
          const h = getSession(id)?.wsHandle;
          if (h && h.readyState === WebSocket.OPEN) { resolve(true); return; }
          if (Date.now() - start > timeoutMs) { resolve(false); return; }
          setTimeout(poll, 100);
        })();
      });
    }

    // N6(60 §4) 「새 섹션」 — 활성 페인을 오른쪽으로 분할하고, 원 세션과 같은
    // cwd로 새 tmux 세션을 만들어 그 자리에 배정한 뒤 스니펫을 주입한다.
    // splitActivePane은 layout/store.js가 window로만 노출한다(직접 import하면
    // 청크 경계에 따라 트리 상태가 복제될 수 있다는 그 모듈 자신의 경고 — N36
    // 주석 참고) — 그 규칙을 그대로 따른다.
    async function runSnippetNewSection(it) {
      if (!activeSession()) { showToast('열려 있는 세션이 없습니다', 'error'); return; }
      if (typeof window.splitActivePane !== 'function') {
        showToast('분할을 사용할 수 없습니다', 'error');
        return;
      }
      const cwd = await _activeCwd();
      const newPaneId = window.splitActivePane('right');
      if (!newPaneId) {
        showToast('분할 실패 — 페인 개수 상한에 도달했을 수 있습니다', 'error');
        return;
      }
      let data;
      try {
        data = await vtFetch('/api/tmux/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cwd ? { cwd } : {}),
        });
      } catch (e) {
        showToast(`새 섹션 생성 실패: ${e.message}`, 'error');
        return;
      }
      // addSession()이 탭·xterm·WS를 만들고 switchTo()로 활성 pane(방금 분할한
      // 자리, splitPane이 이미 그쪽으로 activePaneId를 옮겨뒀다)에 배정한다 —
      // layout/dnd.js가 드롭으로 세션을 꽂을 때 쓰는 것과 같은 경로.
      addSession(data.id, data.name || data.id);
      const s = getSession(data.id);
      if (s) s.tmuxName = data.tmux_session;

      const ready = await _waitForPtyOpen(data.id);
      if (!ready) {
        showToast('새 세션 연결이 늦어 스니펫을 아직 보내지 못했습니다 — 연결되면 직접 붙여넣으세요', 'error');
        return;
      }
      // N24: 이 경로는 sendPaste로 옮기지 않는다 — 멀티라인 스니펫은 줄마다
      // trailing \n이 붙어 **순차 실행**되는 게 의도다(각 줄이 진짜 Enter를
      // 친 것처럼). bracketed paste로 감싸면 그 전체가 한 덩어리 텍스트로
      // 삽입될 뿐 줄마다 실행되지 않는다 — 그게 bracketed paste의 존재
      // 이유이기도 하다. 그래서 이건 의미상 "붙여넣기"가 아니라 "타이핑"이다.
      let text = it.text;
      if (!text.endsWith('\n')) text += '\n';
      sendToPty(data.id, text);
      closeSnippets();
    }

// F3(c): data-action 위임용 등록은 snippets-lazy.js에 있다 — 부팅 시점 등록이
// 지연 청크 안으로 들어가면 안 된다.
