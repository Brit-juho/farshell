// 자주 쓰는 명령·지시문 보관함 (L3, 2026-10-23 개편) — iTerm2 Snippets와 같은 개념.
//
// 프롬프트 큐(queue.js)와 다르다: 큐는 "순서를 기다렸다가" 나가지만, 스니펫은 대기
// 없이 지금 바로 들어간다. 그래서 상태 기계 없이 순수 CRUD + 실행뿐이다.
//
// 개편에서 바뀐 것:
//   1. 실행 방식을 **저장할 때 고르지 않는다.** 행마다 「이 세션에 입력」과 「새 세션」
//      두 버튼이 있고 실행하는 순간 고른다. 같은 문구를 두 방식으로 쓰려고 두 번
//      저장하던 일이 없어진다.
//   2. 「마지막 줄까지 Enter를 칠지」는 **본문이 정한다** — 본문 끝의 줄바꿈이 곧 마지막
//      Enter다(snippets-util.js). 편집 화면에는 「끝에 Enter 포함」 스위치로 드러난다.
//   3. 새 세션은 이름을 정할 수 있다. 이미 있으면 `이름-1`, `이름-2`(서버가 번호를 건다).
//   4. 수정 가능, 줄바꿈이 있는 본문은 줄 그대로 미리 보인다, 검색.
//   5. dock의 「스니펫」 탭에 산다(이전에는 레일 ⋯ 메뉴의 팝업). compact에서는 예전처럼
//      시트로 뜬다(panels/panel.js의 호스트 규칙).
//
// 스코프(전체/프로젝트)의 project 키 판정은 서버(snippet_store.resolve_project_key)가
// 한다 — 클라이언트가 git 판정을 중복 구현하지 않는다.
//
// 패널 껍데기 · fetch · 닫기 뼈대는 panels/panel.js·core/api.js가 공유한다.
import { openPanel, closePanel } from './panels/panel.js';
import { vtFetch, vtEsc } from './core/api.js';
import { activeSession, activeSessionId, getSession } from './core/store.js';
import { screenId } from './core/screen-id.js';
import { sendToPty, sendPaste } from './term/clipboard.js';
import { addSession } from './term/session.js';
import { icon } from './ui/icons.js';
import { isMac } from './core/env.js';
import { t } from './core/i18n.js';
import {
  splitText, composeText, displayName, matchesQuery, sessionNameFor, sessionNameAdjusted, planInput,
} from './snippets-util.js';

// '전체' 탭 기본 — 스코프 구분 없이 쓰던 예전 동작과 가장 가깝다.
let _scopeTab = 'global';
let _query = '';
let _items = [];
let _projectKey = null;
let _editing = null;        // null = 닫힘, { id: string|null } = 열림(null이면 새 스니펫)
let _namingId = null;       // 「새 세션」 이름 입력이 열린 행
let _confirmDeleteId = null;
const _expanded = new Set();
let _panelEl = null;

const $ = (sel) => _panelEl && _panelEl.querySelector(sel);

function closeSnippets() { closePanel('vt-snippets'); }

// dock 탭 안에서는 쓰고 나서 패널을 닫지 않는다(탭이 사라지는 게 아니라 내용이 비는 셈이다).
// 시트(compact)에서만 닫아 터미널을 보여 준다.
function _afterRun(sessionId) {
  if (_panelEl && !_panelEl.classList.contains('vt-dock-panel')) closeSnippets();
  try { getSession(sessionId)?.term?.focus(); } catch (_) { /* 포커스 실패는 무시 */ }
}

export function showSnippets() {
  const key = isMac ? 'Cmd' : 'Ctrl';
  const panel = openPanel({
    id: 'vt-snippets',
    ariaLabel: t('프롬프트 스니펫'),
    headHTML: `
      <div class="vt-vw-title">${t('프롬프트 스니펫')}</div>
      <button type="button" class="vt-btn sm solid" id="vt-sn-new">${icon('plus', 14)}${t('새 스니펫')}</button>`,
    extraHTML: `
      <div class="vt-sn-tools">
        <label class="vt-sn-search">
          ${icon('search', 14)}
          <input id="vt-sn-q" class="vt-input" type="search" autocomplete="off" spellcheck="false"
                 placeholder="${t('이름·내용 검색')}" aria-label="${t('스니펫 검색')}" />
        </label>
        <div class="vt-sn-seg" role="tablist" aria-label="${t('스니펫 스코프')}">
          <button type="button" class="vt-segment" role="tab" id="vt-sn-tab-project" data-scope="project">${t('프로젝트')}</button>
          <button type="button" class="vt-segment" role="tab" id="vt-sn-tab-global" data-scope="global">${t('전체')}</button>
        </div>
      </div>
      <form class="vt-sn-editor" id="vt-sn-editor" hidden autocomplete="off">
        <input id="vt-sn-label" class="vt-input lg" type="text" maxlength="60"
               placeholder="${t('이름 (비우면 첫 줄)')}" aria-label="${t('이름')}" />
        <textarea id="vt-sn-text" class="vt-input mono lg" rows="6" spellcheck="false"
                  placeholder="${t('명령이나 지시문. 줄바꿈은 Enter로 입력됩니다.')}" aria-label="${t('내용')}"></textarea>
        <div class="vt-sn-opts">
          <div class="vt-sn-opt">
            <div class="vt-sn-opt-text"><b>${t('끝에 Enter 포함')}</b>
              <span>${t('켜면 마지막 줄까지 실행됩니다. 끄면 입력만 하고 멈춥니다.')}</span></div>
            <button type="button" class="vt-toggle" role="switch" aria-checked="false" id="vt-sn-enter" aria-label="${t('끝에 Enter 포함')}"><span>ON</span><span>OFF</span></button>
          </div>
          <div class="vt-sn-opt" id="vt-sn-proj-opt">
            <div class="vt-sn-opt-text"><b>${t('이 프로젝트에만')}</b>
              <span>${t('지금 세션의 git 저장소에서만 보입니다.')}</span></div>
            <button type="button" class="vt-toggle" role="switch" aria-checked="false" id="vt-sn-proj" aria-label="${t('이 프로젝트에만')}"><span>ON</span><span>OFF</span></button>
          </div>
        </div>
        <div class="vt-sn-editor-foot">
          <span class="vt-sn-hint">${t('{key}+Enter로 저장', { key })}</span>
          <button type="button" class="vt-btn quiet" id="vt-sn-cancel">${t('취소')}</button>
          <button type="submit" class="vt-btn solid" id="vt-sn-save">${t('저장')}</button>
        </div>
      </form>`,
    bodyId: 'vt-sn-body',
    onClose: () => { _panelEl = null; _editing = null; _namingId = null; _confirmDeleteId = null; },
    onKey: () => {
      // 편집 중 Esc는 편집만 닫는다 — 쓰던 내용 때문에 패널째 닫히면 안 된다.
      if (_editing) { _closeEditor(); return true; }
      if (_namingId) { _namingId = null; _render(); return true; }
    },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  _panelEl = panel.el;

  $('#vt-sn-new').addEventListener('click', () => (_editing ? _closeEditor() : _openEditor(null)));
  $('#vt-sn-q').addEventListener('input', (ev) => { _query = ev.target.value; _render(); });
  $('#vt-sn-tab-project').addEventListener('click', () => _switchScopeTab('project'));
  $('#vt-sn-tab-global').addEventListener('click', () => _switchScopeTab('global'));
  $('#vt-sn-cancel').addEventListener('click', _closeEditor);
  _bindToggle($('#vt-sn-enter'));
  _bindToggle($('#vt-sn-proj'));

  const editor = $('#vt-sn-editor');
  editor.addEventListener('submit', (ev) => { ev.preventDefault(); _save(); });
  editor.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.stopPropagation(); _closeEditor(); }
    else if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); _save(); }
  });

  // 목록 쪽 동작은 위임 하나로 받는다 — 행을 다시 그려도 리스너가 쌓이지 않는다.
  $('#vt-sn-body').addEventListener('click', _onBodyClick);
  $('#vt-sn-body').addEventListener('submit', _onBodySubmit);
  $('#vt-sn-body').addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && _namingId) { ev.stopPropagation(); _namingId = null; _render(); }
  });

  _paintTabs();
  _refresh();
}

function _bindToggle(btn) {
  btn.addEventListener('click', () => {
    btn.setAttribute('aria-checked', btn.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
  });
}
const _toggleOn = (btn) => btn.getAttribute('aria-checked') === 'true';
const _setToggle = (btn, on) => btn.setAttribute('aria-checked', on ? 'true' : 'false');

// ── 데이터 ────────────────────────────────────────────────────────────────
// 활성 세션의 tmux cwd — scm.js _currentRepo()/term/links.js _getSessionCwd()와 같은 방식
// (tmux 세션 목록에서 이름으로 찾기). 두 모듈 다 이 파일과 같은 동기 로드 그래프에 없어
// 직접 import하면 청크 경계를 하나 더 만들게 되므로 10줄 조회를 여기서 다시 한다.
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

async function _refresh() {
  if (!_panelEl) return;
  try {
    _items = (await vtFetch('/api/snippets')).items || [];
  } catch (e) {
    const body = $('#vt-sn-body');
    if (body) body.innerHTML = `<div class="vt-sn-empty">${vtEsc(e.message)}</div>`;
    return;
  }
  _projectKey = await _currentProjectKey();
  _render();
}

function _paintTabs() {
  for (const scope of ['project', 'global']) {
    const tab = $(`#vt-sn-tab-${scope}`);
    if (!tab) continue;
    tab.classList.toggle('active', _scopeTab === scope);
    tab.setAttribute('aria-selected', String(_scopeTab === scope));
  }
}

function _switchScopeTab(scope) {
  _scopeTab = scope;
  _paintTabs();
  _render();
}

// ── 목록 ──────────────────────────────────────────────────────────────────
function _visibleItems() {
  // 「전체」 탭은 전부 보인다 — 프로젝트 스니펫은 태그로 구분한다.
  const scoped = _scopeTab === 'project'
    ? _items.filter((it) => it.scope === 'project' && _projectKey && it.project === _projectKey)
    : _items;
  return scoped.filter((it) => matchesQuery(it, _query));
}

function _emptyHTML() {
  if (_query.trim()) return `<div class="vt-sn-empty"><p>${t('일치하는 스니펫이 없습니다.')}</p></div>`;
  if (_scopeTab === 'project') {
    return `<div class="vt-sn-empty"><p>${_projectKey
      ? t('이 프로젝트에 저장된 스니펫이 없습니다.')
      : t('현재 세션이 git 저장소 안에 있어야 프로젝트 스니펫을 볼 수 있습니다.')}</p></div>`;
  }
  return `<div class="vt-sn-empty">${icon('braces', 22)}
    <p>${t('저장된 스니펫이 없습니다.')}</p>
    <p class="vt-sn-empty-sub">${t('자주 쓰는 명령이나 지시문을 저장해 두면 한 번에 입력합니다.')}</p></div>`;
}

function _rowHTML(it) {
  const { body, enter } = splitText(it.text);
  const open = _expanded.has(it.id);
  const multi = body.includes('\n') || body.length > 90;
  const flags = [
    it.scope === 'project' ? `<span class="vt-tag" title="${vtEsc(it.project || '')}">${t('프로젝트')}</span>` : '',
    enter ? `<span class="vt-sn-enter" title="${t('끝에 Enter 포함')}">${icon('corner-down-left', 13)}Enter</span>` : '',
    multi ? `<span class="vt-sn-fold${open ? ' open' : ''}" aria-hidden="true">${icon('chevron-down', 14)}</span>` : '',
  ].join('');

  // 길거나 여러 줄이면 눌러서 펼친다(접힘 3줄). 짧으면 그냥 보여 준다.
  // 한글이 섞인 본문은 지시문(문장)이라 UI 글꼴로, 그 밖에는 명령이라 고정폭으로 보인다.
  const prose = /[가-힣]/.test(body);
  const cls = `vt-sn-text${open ? ' open' : ''}${multi ? ' foldable' : ''}${prose ? ' prose' : ''}`;
  const pre = `<pre>${vtEsc(body)}</pre>`;
  const preview = multi
    ? `<button type="button" class="${cls}" data-act="toggle" data-id="${vtEsc(it.id)}" aria-expanded="${open}">${pre}</button>`
    : `<div class="${cls}">${pre}</div>`;

  let actions;
  if (_namingId === it.id) {
    actions = `
      <form class="vt-sn-name-form" data-id="${vtEsc(it.id)}">
        <input class="vt-input" name="name" maxlength="40" autocomplete="off" spellcheck="false"
               placeholder="${vtEsc(it.label ? it.label : t('세션 이름 (비우면 폴더 이름)'))}" aria-label="${t('새 세션 이름')}" />
        <button type="submit" class="vt-btn sm solid">${t('실행')}</button>
        <button type="button" class="vt-btn sm quiet" data-act="cancel-name">${t('취소')}</button>
      </form>`;
  } else if (_confirmDeleteId === it.id) {
    actions = `
      <div class="vt-sn-actions">
        <span class="vt-sn-confirm">${t('삭제할까요?')}</span>
        <span class="vt-sn-spacer"></span>
        <button type="button" class="vt-btn sm quiet" data-act="cancel-delete" data-id="${vtEsc(it.id)}">${t('취소')}</button>
        <button type="button" class="vt-btn sm danger" data-act="delete" data-id="${vtEsc(it.id)}">${t('삭제')}</button>
      </div>`;
  } else {
    actions = `
      <div class="vt-sn-actions">
        <button type="button" class="vt-btn sm" data-act="paste" data-id="${vtEsc(it.id)}"
                title="${t('지금 보고 있는 세션에 입력')}">${icon('terminal', 14)}${t('이 세션에 입력')}</button>
        <button type="button" class="vt-btn sm quiet" data-act="new-session" data-id="${vtEsc(it.id)}"
                title="${t('오른쪽으로 분할해 새 세션에서 실행')}">${icon('square-terminal', 14)}${t('새 세션')}</button>
        <span class="vt-sn-spacer"></span>
        <button type="button" class="vt-icon-btn" data-act="edit" data-id="${vtEsc(it.id)}"
                aria-label="${t('수정')}" title="${t('수정')}">${icon('pencil', 14)}</button>
        <button type="button" class="vt-icon-btn" data-act="ask-delete" data-id="${vtEsc(it.id)}"
                aria-label="${t('삭제')}" title="${t('삭제')}">${icon('trash-2', 14)}</button>
      </div>`;
  }

  return `
    <article class="vt-sn-row${_editing && _editing.id === it.id ? ' editing' : ''}" data-id="${vtEsc(it.id)}">
      <div class="vt-sn-head">
        <span class="vt-sn-name">${vtEsc(displayName(it))}</span>
        <span class="vt-sn-flags">${flags}</span>
      </div>
      ${preview}
      ${actions}
    </article>`;
}

function _render() {
  const body = $('#vt-sn-body');
  if (!body) return;
  const items = _visibleItems();
  body.innerHTML = items.length
    ? `<div class="vt-sn-list">${items.map(_rowHTML).join('')}</div>`
    : _emptyHTML();
  const nameInput = body.querySelector('.vt-sn-name-form input');
  if (nameInput) nameInput.focus();
}

const _find = (id) => _items.find((x) => x.id === id);

function _onBodyClick(ev) {
  const el = ev.target.closest('[data-act]');
  if (!el) return;
  const it = _find(el.dataset.id);
  switch (el.dataset.act) {
    case 'toggle':
      if (_expanded.has(el.dataset.id)) _expanded.delete(el.dataset.id); else _expanded.add(el.dataset.id);
      _render();
      break;
    case 'paste': if (it) _pasteSnippet(it); break;
    case 'new-session': _namingId = el.dataset.id; _confirmDeleteId = null; _render(); break;
    case 'cancel-name': _namingId = null; _render(); break;
    case 'edit': if (it) _openEditor(it); break;
    case 'ask-delete': _confirmDeleteId = el.dataset.id; _namingId = null; _render(); break;
    case 'cancel-delete': _confirmDeleteId = null; _render(); break;
    case 'delete': _remove(el.dataset.id); break;
    default: break;
  }
}

function _onBodySubmit(ev) {
  const form = ev.target.closest('.vt-sn-name-form');
  if (!form) return;
  ev.preventDefault();
  const it = _find(form.dataset.id);
  if (!it) return;
  const typed = form.elements.name.value;
  _namingId = null;
  _runInNewSession(it, sessionNameFor(it, typed));
}

// ── 편집 ──────────────────────────────────────────────────────────────────
function _openEditor(it) {
  _editing = { id: it ? it.id : null };
  _namingId = null;
  const { body, enter } = it ? splitText(it.text) : { body: '', enter: false };
  $('#vt-sn-label').value = it ? it.label || '' : '';
  $('#vt-sn-text').value = body;
  _setToggle($('#vt-sn-enter'), enter);
  _setToggle($('#vt-sn-proj'), !!it && it.scope === 'project');
  $('#vt-sn-save').textContent = it ? t('수정 저장') : t('저장');
  $('#vt-sn-editor').hidden = false;
  $('#vt-sn-new').setAttribute('aria-pressed', 'true');
  _render();                    // 수정 중인 행을 표시한다
  if (it) _panelEl.querySelector('.vt-sn-row.editing')?.scrollIntoView({ block: 'nearest' });
  (it ? $('#vt-sn-text') : $('#vt-sn-label')).focus();
}

function _closeEditor() {
  _editing = null;
  const ed = $('#vt-sn-editor');
  if (ed) ed.hidden = true;
  const btn = $('#vt-sn-new');
  if (btn) btn.removeAttribute('aria-pressed');
  _render();
}

async function _save() {
  if (!_editing) return;
  const text = composeText($('#vt-sn-text').value, _toggleOn($('#vt-sn-enter')));
  if (!text) { showToast(t('내용을 입력하세요'), 'error'); $('#vt-sn-text').focus(); return; }
  const label = $('#vt-sn-label').value;
  const wantProject = _toggleOn($('#vt-sn-proj'));
  const existing = _editing.id ? _find(_editing.id) : null;
  const scopeChanged = !existing || (existing.scope === 'project') !== wantProject;
  const cwd = wantProject && scopeChanged ? await _activeCwd() : null;
  const payload = { text, label };
  if (scopeChanged) { payload.scope = wantProject ? 'project' : 'global'; payload.cwd = cwd; }
  try {
    await vtFetch(existing ? `/api/snippets/${existing.id}` : '/api/snippets', {
      method: existing ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    showToast(t('저장 실패: {error}', { error: e.message }), 'error');
    return;                       // 폼은 그대로 둔다 — 쓰던 내용을 잃지 않는다
  }
  _closeEditor();
  await _refresh();
}

async function _remove(id) {
  _confirmDeleteId = null;
  try { await vtFetch(`/api/snippets/${id}`, { method: 'DELETE' }); }
  catch (e) { showToast(t('삭제 실패: {error}', { error: e.message }), 'error'); }
  _expanded.delete(id);
  await _refresh();
}

// ── 실행 ──────────────────────────────────────────────────────────────────
// 이 세션에 입력 — 본문 전체를 붙여넣기로 넣고, 끝에 줄바꿈이 있을 때만 Enter 한 번.
// 붙여넣기 경로(sendPaste)는 서버가 마커·개행을 처리한다(N24). 줄마다 실행되면 사고가
// 나는 본문(셸 여러 줄)이 한 덩어리로 들어간 뒤 Enter 한 번으로 나가는 게 의도다.
function _pasteSnippet(it) {
  const id = activeSessionId();
  if (!activeSession() || !id) { showToast(t('열려 있는 세션이 없습니다'), 'error'); return; }
  const plan = planInput(it.text, 'paste');
  sendPaste(id, plan.paste);
  // 붙여넣기는 같은 WS에서 순서대로 처리되지만 tmux paste-buffer가 끝난 뒤에 Enter가
  // 가야 하므로 아주 짧게 띄운다.
  if (plan.enter) setTimeout(() => sendToPty(id, '\r'), 80);
  _afterRun(id);
}

// wsHandle이 OPEN이 될 때까지 짧게 폴링 — sendToPty는 큐잉을 하지 않고 OPEN이 아니면
// 조용히 버리므로, 새로 만든 세션에 곧바로 밀어 넣으면 연결이 뜨기 전에 유실될 수 있다.
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

// 새 세션에서 실행 — 활성 페인을 오른쪽으로 분할하고, 원 세션과 같은 cwd로 새 tmux
// 세션을 만들어 그 자리에 배정한 뒤 본문을 줄마다 Enter로 입력한다(끝 줄바꿈이 없으면
// 마지막 줄은 입력만). splitActivePane은 layout/store.js가 window로만 노출한다(직접
// import하면 청크 경계에 따라 트리 상태가 복제될 수 있다는 그 모듈 자신의 경고).
async function _runInNewSession(it, name) {
  if (!activeSession()) { showToast(t('열려 있는 세션이 없습니다'), 'error'); _render(); return; }
  if (typeof window.splitActivePane !== 'function') {
    showToast(t('분할을 사용할 수 없습니다'), 'error');
    return;
  }
  const cwd = await _activeCwd();
  const newPaneId = window.splitActivePane('right');
  if (!newPaneId) {
    showToast(t('분할 실패 — 페인 개수 상한에 도달했을 수 있습니다'), 'error');
    return;
  }
  let data;
  try {
    data = await vtFetch('/api/tmux/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...(cwd ? { cwd } : {}),
        ...(name ? { name, unique_name: true } : {}),
        screen: screenId(),
      }),
    });
  } catch (e) {
    showToast(t('새 세션 생성 실패: {error}', { error: e.message }), 'error');
    return;
  }
  if (name && sessionNameAdjusted(name, data.tmux_session)) {
    showToast(t('세션 이름은 영문·숫자·-·_만 쓸 수 있어 {name}(으)로 만들었습니다', { name: data.tmux_session }), 'info');
  }
  // addSession()이 탭·xterm·WS를 만들고 활성 pane(방금 분할한 자리)에 배정한다 —
  // layout/dnd.js가 드롭으로 세션을 꽂을 때 쓰는 것과 같은 경로.
  addSession(data.id, data.name || data.id, { place: 'pane', tmuxName: data.tmux_session });
  const s = getSession(data.id);
  if (s) s.tmuxName = data.tmux_session;

  if (!(await _waitForPtyOpen(data.id))) {
    showToast(t('새 세션 연결이 늦어 스니펫을 아직 보내지 못했습니다 — 연결되면 직접 입력하세요'), 'error');
    return;
  }
  // N24: 이 경로는 sendPaste로 옮기지 않는다 — 줄마다 Enter가 쳐져 **순차 실행**되는 게
  // 의도다. bracketed paste로 감싸면 한 덩어리 텍스트로 들어갈 뿐 줄마다 실행되지 않는다.
  // 그래서 의미상 「붙여넣기」가 아니라 「타이핑」이다.
  sendToPty(data.id, planInput(it.text, 'type').type);
  _afterRun(data.id);
}

// F3(c): data-action 위임용 등록은 snippets-lazy.js에 있다 — 부팅 시점 등록이
// 지연 청크 안으로 들어가면 안 된다.
