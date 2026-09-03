// 프롬프트 스니펫 라이브러리 (L3) — iTerm2 Snippets와 같은 개념.
// 프롬프트 큐(queue.js)와 다르다: 큐는 "순서를 기다렸다가" 나가지만, 스니펫은
// 대기 없이 지금 보고 있는 세션에 바로 주입한다. 그래서 상태 기계 없이 순수
// CRUD + "실행"(=sendToPty) 하나뿐이다.
//
// 패널 껍데기 · fetch · 닫기 뼈대는 panel.js/vtapi.js가 공유한다.

    function closeSnippets() { closePanel('vt-snippets'); }

    function showSnippets() {
      const panel = openPanel({
        id: 'vt-snippets',
        ariaLabel: '프롬프트 스니펫',
        headHTML: `<div class="vt-vw-title">프롬프트 스니펫</div>`,
        extraHTML: `
          <div class="vt-q-compose">
            <input id="vt-sn-label" type="text" placeholder="이름 (선택)" maxlength="60" />
            <textarea id="vt-sn-input" rows="3" placeholder="저장할 지시문/명령… (여러 줄이면 줄마다 Enter로 실행됩니다)"></textarea>
            <div class="vt-q-compose-row">
              <button class="vt-pt-btn" id="vt-sn-add">저장</button>
            </div>
          </div>
        `,
        bodyId: 'vt-sn-body',
      });
      if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

      panel.el.querySelector('#vt-sn-add').addEventListener('click', addSnippet);
      refreshSnippets();
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
      if (!d.items.length) {
        body.innerHTML = '<div class="vt-vw-empty">저장된 스니펫이 없습니다.<br>자주 쓰는 지시문을 위에 저장해두세요.</div>';
        return;
      }

      const list = document.createElement('div');
      list.className = 'vt-vw-list';
      d.items.forEach((it) => {
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

        const act = document.createElement('span');
        act.className = 'vt-pt-actions';
        const run = document.createElement('button');
        run.className = 'vt-pt-btn';
        run.textContent = '실행';
        run.title = '지금 보고 있는 세션에 바로 입력';
        run.onclick = () => runSnippet(it);
        const rm = document.createElement('button');
        rm.className = 'vt-pt-btn danger';
        rm.textContent = '삭제';
        rm.onclick = () => removeSnippet(it.id);
        act.appendChild(run); act.appendChild(rm);

        row.appendChild(meta); row.appendChild(act);
        list.appendChild(row);
      });
      body.innerHTML = '';
      body.appendChild(list);
    }

    async function addSnippet() {
      const labelEl = document.getElementById('vt-sn-label');
      const inputEl = document.getElementById('vt-sn-input');
      const text = (inputEl.value || '');
      if (!text.trim()) return;
      try {
        await vtFetch('/api/snippets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, label: labelEl.value }),
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

    // 큐에 넣지 않고 지금 활성 세션에 바로 주입 — iTerm2 Snippets를 클릭하는 것과 동일.
    function runSnippet(it) {
      if (!activeSession()) { showToast('열려 있는 세션이 없습니다', 'error'); return; }
      let text = it.text;
      if (!text.endsWith('\n')) text += '\n';   // 마지막 줄도 Enter로 실행되게.
      sendToPty(activeId, text);
      closeSnippets();
    }

// F3(c): data-action 위임용 등록.
registerAction('snippets.show', () => showSnippets());
