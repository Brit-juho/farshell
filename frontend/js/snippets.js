import { operationId } from './core/operation-id.js';
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
import { pIcon } from './panels/panel-icons.js';
import { confirmDialog } from './core/dialog.js';
import { isMac } from './core/env.js';
import { t } from './core/i18n.js';
import {
  splitText, composeText, toLines, hasHangul, displayName, matchesQuery, sessionNameFor, sessionNameAdjusted, planInput,
} from './snippets-util.js';

// '전체' 탭 기본 — 스코프 구분 없이 쓰던 예전 동작과 가장 가깝다.
let _scopeTab = 'global';
let _query = '';
let _items = [];
let _projectKey = null;
let _editing = null;        // null = 닫힘, { id: string|null } = 열림(null이면 새 스니펫)
let _namingId = null;       // 「새 세션」 이름 입력이 열린 행
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
  // 리디자인 5단계(시안 P05): 머리 = 「스니펫」 + 맥락(프로젝트) + 「새 스니펫」. 도구 줄 = 검색 · [프로젝트|전체].
  // 편집(P05-b)은 목록 위에 펼치지 않고 **같은 자리에서 한 단계 들어간 화면**이다 — ← 「스니펫 수정」 하위 머리.
  const panel = openPanel({
    id: 'vt-snippets',
    ariaLabel: t('프롬프트 스니펫'),
    headHTML: `
      <div class="vt-vw-title">${t('스니펫')}</div>
      <span class="vt-ph-ctx" id="vt-sn-ctx"></span>
      <button type="button" class="vt-btn sm" id="vt-sn-new">${icon('plus', 12)}${t('새 스니펫')}</button>`,
    extraHTML: `
      <div class="vt-sn-tools">
        <label class="vt-sn-search">
          ${icon('search', 14)}
          <input id="vt-sn-q" class="vt-input" type="search" autocomplete="off" spellcheck="false"
                 placeholder="${t('이름·내용 검색')}" aria-label="${t('스니펫 검색')}" />
        </label>
        <div class="vt-sn-seg vt-pseg s28" role="tablist" aria-label="${t('스니펫 스코프')}">
          <button type="button" class="vt-segment" role="tab" id="vt-sn-tab-project" data-scope="project">${t('프로젝트')}<b>0</b></button>
          <button type="button" class="vt-segment" role="tab" id="vt-sn-tab-global" data-scope="global">${t('전체')}<b>0</b></button>
        </div>
      </div>
      <form class="vt-sn-editor" id="vt-sn-editor" hidden autocomplete="off">
        <div class="vt-psub">
          <button type="button" class="vt-icon-btn sm" id="vt-sn-back" aria-label="${t('목록으로')}" data-tip="${t('목록으로')}">${icon('arrow-left', 14)}</button>
          <span class="vt-psub-t" id="vt-sn-edit-title">${t('새 스니펫')}</span>
        </div>
        <div class="vt-sn-fields">
          <label class="vt-fld"><span class="vt-fld-l">${t('이름')}</span>
            <input id="vt-sn-label" class="vt-input lg" type="text" maxlength="60"
                   placeholder="${t('이름 (비우면 첫 줄)')}" /></label>
          <label class="vt-fld"><span class="vt-fld-l">${t('내용')}</span>
            <textarea id="vt-sn-text" class="vt-input lg" rows="5" spellcheck="false"
                      placeholder="${t('명령이나 지시문. 줄바꿈은 Enter로 입력됩니다.')}"></textarea></label>
          <div class="vt-sn-preview" id="vt-sn-preview" aria-live="off"></div>
          <div class="vt-sn-opt" id="vt-sn-proj-opt">
            <div class="vt-sn-opt-text"><b>${t('이 프로젝트에만')}</b>
              <span>${t('지금 세션의 git 저장소에서만 보입니다.')}</span></div>
            <button type="button" class="vt-switch" role="switch" aria-checked="false" id="vt-sn-proj" aria-label="${t('이 프로젝트에만')}"></button>
          </div>
          <div class="vt-fld" role="radiogroup" aria-label="${t('전송 방식')}">
            <span class="vt-fld-l">${t('전송 방식')}</span>
            <label class="vt-sn-mode"><input type="radio" name="vt-sn-enter" value="1">
              <span><b>${t('끝에 Enter')}</b><em>${t('마지막 줄까지 실행합니다')}</em></span></label>
            <label class="vt-sn-mode"><input type="radio" name="vt-sn-enter" value="0">
              <span><b>${t('입력만')}</b><em>${t('앞 줄은 실행하고 마지막 줄은 입력만 해 두어 고쳐서 보냅니다')}</em></span></label>
          </div>
          <div class="vt-sn-editor-foot">
            <button type="button" class="vt-btn danger" id="vt-sn-del" hidden>${icon('trash-2', 14)}${t('삭제')}</button>
            <span class="vt-sn-hint">${t('{key}+Enter로 저장', { key })}</span>
            <button type="button" class="vt-btn quiet" id="vt-sn-cancel">${t('취소')}</button>
            <button type="submit" class="vt-btn solid" id="vt-sn-save">${t('저장')}</button>
          </div>
        </div>
      </form>`,
    bodyId: 'vt-sn-body',
    onClose: () => { _panelEl = null; _editing = null; _namingId = null; },
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
  $('#vt-sn-back').addEventListener('click', _closeEditor);
  $('#vt-sn-del').addEventListener('click', () => {
    const it = _editing && _editing.id ? _find(_editing.id) : null;
    if (it) _askDelete(it);
  });
  $('#vt-sn-text').addEventListener('input', () => {
    // 본문 끝에 줄바꿈을 직접 치면 「끝에 Enter」를 고른 것으로 본다(예전 규칙: 끝 줄바꿈 = 마지막 Enter).
    const ta = $('#vt-sn-text');
    if (/\n$/.test(ta.value)) _setEnterMode(true);
    _paintEditorText();
  });
  for (const r of _panelEl.querySelectorAll('input[name="vt-sn-enter"]')) r.addEventListener('change', _paintEditorText);
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

const _enterMode = () => $('input[name="vt-sn-enter"]:checked')?.value === '1';
function _setEnterMode(on) {
  const r = $(`input[name="vt-sn-enter"][value="${on ? '1' : '0'}"]`);
  if (r) r.checked = true;
}
/** 편집 화면의 본문 + 「전송 방식」 → 저장할 본문. 끝 줄바꿈 하나가 곧 마지막 Enter다(snippets-util.js). */
function _editorText() {
  const body = composeText($('#vt-sn-text').value).replace(/\n+$/, '');
  if (!body) return '';
  return _enterMode() ? `${body}\n` : body;
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
  const ctxEl = $('#vt-sn-ctx');
  if (ctxEl) { ctxEl.textContent = _projectKey ? String(_projectKey).split('/').filter(Boolean).pop() || '' : ''; ctxEl.title = _projectKey || ''; }
  _paintTabs();
  _render();
}

function _paintTabs() {
  for (const scope of ['project', 'global']) {
    const tab = $(`#vt-sn-tab-${scope}`);
    if (!tab) continue;
    const n = scope === 'project' ? _projectItems().length : _items.length;
    tab.querySelector('b').textContent = String(n);
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
const _projectItems = () => _items.filter((it) => it.scope === 'project' && _projectKey && it.project === _projectKey);

function _visibleItems() {
  // 「전체」 탭은 전부 보인다 — 프로젝트 스니펫은 태그로 구분한다.
  const scoped = _scopeTab === 'project' ? _projectItems() : _items;
  return scoped.filter((it) => matchesQuery(it, _query));
}

function _emptyHTML() {
  const box = (title, desc, withNew) => `<div class="vt-empty" role="status">${icon('braces', 20, 1.75)}
    <div class="vt-empty-title">${vtEsc(title)}</div>${desc ? `<div class="vt-empty-desc">${vtEsc(desc)}</div>` : ''}
    ${withNew ? `<button type="button" class="vt-btn sm" data-act="new">${icon('plus', 12)}${vtEsc(t('새 스니펫'))}</button>` : ''}</div>`;
  if (_query.trim()) return box(t('일치하는 스니펫이 없습니다.'), '', false);
  if (_scopeTab === 'project') {
    return _projectKey
      ? box(t('이 프로젝트에 저장된 스니펫이 없습니다.'), '', true)
      : box(t('프로젝트 스니펫을 볼 수 없습니다'), t('현재 세션이 git 저장소 안에 있어야 프로젝트 스니펫을 볼 수 있습니다.'), false);
  }
  return box(t('저장된 스니펫이 없습니다.'), t('자주 쓰는 명령이나 지시문을 저장해 두면 한 번에 입력합니다.'), true);
}

// 줄마다 ↵ 마커 — 줄바꿈이 곧 Enter 키라는 것을 글자 옆에서 바로 읽게 한다.
// 마커 뒤의 실제 \n 은 pre-wrap 줄바꿈이고, 마커는 aria-hidden(스크린리더는 본문만 읽는다).
function _linesHTML(text) {
  const mark = `<span class="vt-nl" aria-hidden="true">${icon('corner-down-left', 11)}</span>`;
  return toLines(text).map((l) => `${vtEsc(l.text)}${l.nl ? mark : ''}`).join('\n');
}

function _paintEditorText() {
  const ta = $('#vt-sn-text');
  const pv = $('#vt-sn-preview');
  if (!ta || !pv) return;
  const v = ta.value;
  ta.classList.toggle('code', !!v.trim() && !hasHangul(v));
  const text = _editorText();
  pv.hidden = !text;
  pv.innerHTML = text
    ? `<pre class="${hasHangul(text) ? 'prose' : ''}">${_linesHTML(text)}</pre>
       <span class="vt-sn-preview-cap">${text.endsWith('\n') ? t('마지막 줄까지 실행') : t('마지막 줄은 입력만 하고 멈춤')}</span>`
    : '';
}

// 행 52(시안 P05): 1줄 = 이름(13/600) · [Enter|입력만] · [프로젝트] … 머문 행에서 입력 · 분할 실행 · 수정 · 삭제.
// 2줄 = 본문 첫 줄(mono 12 muted, 말줄임). 여러 줄이거나 긴 본문은 2줄을 눌러 전체를 펼친다.
function _rowHTML(it) {
  const { body, enter } = splitText(it.text);
  const open = _expanded.has(it.id);
  const multi = body.includes('\n') || body.length > 90;
  const id = vtEsc(it.id);
  const flags = [
    `<span class="vt-ptag vt-sn-enter${enter ? ' on' : ''}" data-tip="${enter ? t('끝에 Enter') : t('입력만')}">${enter ? `${icon('corner-down-left', 12)}Enter` : t('입력만')}</span>`,
    it.scope === 'project' ? `<span class="vt-ptag" title="${vtEsc(it.project || '')}">${t('프로젝트')}</span>` : '',
  ].join('');
  const acts = `
    <span class="vt-sn-acts">
      <button type="button" class="vt-icon-btn sm" data-act="paste" data-id="${id}"
              aria-label="${t('이 세션에 입력')}" data-tip="${t('지금 보고 있는 세션에 입력')}">${pIcon('play', 14)}</button>
      <button type="button" class="vt-icon-btn sm" data-act="new-session" data-id="${id}"
              aria-label="${t('새 세션')}" data-tip="${t('오른쪽으로 분할해 새 세션에서 실행')}">${icon('columns-2', 14)}</button>
      <button type="button" class="vt-icon-btn sm" data-act="edit" data-id="${id}"
              aria-label="${t('수정')}" data-tip="${t('수정')}">${icon('pencil', 14)}</button>
      <button type="button" class="vt-icon-btn sm danger" data-act="ask-delete" data-id="${id}"
              aria-label="${t('삭제')}" data-tip="${t('삭제')}">${icon('trash-2', 14)}</button>
    </span>`;

  // 한글이 섞인 본문은 지시문(문장)이라 UI 글꼴로, 그 밖에는 명령이라 고정폭으로 보인다.
  const prose = hasHangul(body);
  const cls = `vt-sn-text${open ? ' open' : ''}${multi ? ' foldable' : ''}${prose ? ' prose' : ''}`;
  const pre = `<pre>${open ? _linesHTML(it.text) : vtEsc(body.split('\n')[0])}</pre>`;
  const preview = multi
    ? `<button type="button" class="${cls}" data-act="toggle" data-id="${id}" aria-expanded="${open}"
               data-tip="${open ? t('접기') : t('전체 보기')}">${pre}</button>`
    : `<div class="${cls}">${pre}</div>`;

  const naming = _namingId === it.id ? `
      <form class="vt-sn-name-form" data-id="${id}">
        <input class="vt-input" name="name" maxlength="40" autocomplete="off" spellcheck="false"
               placeholder="${vtEsc(it.label ? it.label : t('세션 이름 (비우면 폴더 이름)'))}" aria-label="${t('새 세션 이름')}" />
        <button type="submit" class="vt-btn sm solid">${t('실행')}</button>
        <button type="button" class="vt-btn sm quiet" data-act="cancel-name">${t('취소')}</button>
      </form>` : '';

  return `
    <article class="vt-sn-row${open ? ' open' : ''}" data-id="${id}">
      <div class="vt-sn-head">
        <span class="vt-sn-name">${vtEsc(displayName(it))}</span>
        <span class="vt-sn-flags">${flags}</span>
        ${acts}
      </div>
      ${preview}
      ${naming}
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
    case 'new-session': _namingId = el.dataset.id; _render(); break;
    case 'cancel-name': _namingId = null; _render(); break;
    case 'edit': if (it) _openEditor(it); break;
    case 'ask-delete': if (it) _askDelete(it); break;
    case 'new': _openEditor(null); break;
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
// 편집은 패널 안의 하위 화면이다(시안 P05-b) — 목록·도구 줄은 숨기고 ← 로 돌아온다.
function _openEditor(it) {
  _editing = { id: it ? it.id : null };
  _namingId = null;
  $('#vt-sn-label').value = it ? it.label || '' : '';
  $('#vt-sn-text').value = it ? splitText(it.text).body : '';
  _setEnterMode(it ? splitText(it.text).enter : false);
  _paintEditorText();
  _setToggle($('#vt-sn-proj'), !!it && it.scope === 'project');
  $('#vt-sn-edit-title').textContent = it ? t('스니펫 수정') : t('새 스니펫');
  $('#vt-sn-save').textContent = it ? t('수정 저장') : t('저장');
  $('#vt-sn-del').hidden = !it;
  $('#vt-sn-editor').hidden = false;
  _panelEl.classList.add('vt-sn-editing');
  $('#vt-sn-new').setAttribute('aria-pressed', 'true');
  (it ? $('#vt-sn-text') : $('#vt-sn-label')).focus();
}

function _closeEditor() {
  _editing = null;
  const ed = $('#vt-sn-editor');
  if (ed) ed.hidden = true;
  _panelEl?.classList.remove('vt-sn-editing');
  const btn = $('#vt-sn-new');
  if (btn) btn.removeAttribute('aria-pressed');
  _render();
}

async function _save() {
  if (!_editing) return;
  const text = _editorText();
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

// 삭제 확인(시안 P05-c) — 공용 확인 대화상자. 무엇을 지우는지(이름 + 본문 첫 줄)와 되돌릴 수 없다는 것을 말한다.
async function _askDelete(it) {
  const first = splitText(it.text).body.split('\n')[0];
  const lines = [t('「{name}」을(를) 삭제할까요?', { name: displayName(it) }), first.length > 80 ? `${first.slice(0, 80)}…` : first,
    it.scope === 'project' ? t('되돌릴 수 없습니다. 이 프로젝트에서만 쓰던 스니펫입니다.') : t('되돌릴 수 없습니다.')];
  if (!await confirmDialog(lines.join('\n\n'), { title: t('스니펫 삭제'), okLabel: t('삭제'), danger: true })) return;
  if (_editing && _editing.id === it.id) _closeEditor();
  await _remove(it.id);
}

async function _remove(id) {
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
        screen: screenId(), operation_id:operationId(),
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
  if (s) { s.tmuxName = data.tmux_session; s.catalogSessionId=data.catalog_session_id; s.runtimeId=data.runtime_id; }

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
