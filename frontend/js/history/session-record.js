// 「작업 기록」 창 — 세션 기록과 서브에이전트·위임 세션 기록을 한 창에(리디자인 6단계, 시안 O15).
//
// 예전에는 둘이 따로였다: 행 메뉴 「작업 기록」은 이 파일의 작은 대화상자(상태·동작·연결된 대화·위임 관계·실행 이력),
// 하위 행 「기록 보기」·내부 칩은 panels/subagent-viewer.js의 「서브에이전트 기록」 창. 같은 세션의 이야기를 두 창에서
// 오가야 했다. 이제 한 창이다:
//   왼쪽(460) — 세션: 상태 · 이어서 시작 · 실행 종료 · 이름 변경 · 세션 보관 · 삭제 · 연결된 대화 · 위임 관계 ·
//               내부 서브에이전트 · 실행 이력
//   오른쪽    — 고른 내부 서브에이전트나 위임 세션의 기록(지시 · 답변 · 도구 호출 · 도구 결과) + 새로고침
// 어디서 열든 같은 창이고 오른쪽에 무엇을 미리 고를지만 다르다(openSessionRecord의 select, 시안 O15-B):
//   행 메뉴 「작업 기록」  → 가장 최근 내부 서브에이전트, 없으면 가장 최근 위임 세션
//   행 메뉴 「보고 보기」  → 보고가 온 위임 세션(부모 세션 기록을 연다)
//   하위 행 「기록 보기」  → 그 내부 서브에이전트
//   내부 칩               → 작업 중인 첫 서브에이전트
//
// 서버가 준 글자(제목·경로·기록 본문)는 전부 textContent로 넣는다. 카탈로그 API는 이 맥을 직접 볼 때만 쓸 수 있다
// (API.md 「Durable sessions」 — 중계 제외). 다른 맥을 보는 중이면 왼쪽은 세션 이름과 내부 서브에이전트만 보인다.
import { openDialog, confirmDialog } from '../core/dialog.js';
import { closePanel } from '../panels/panel.js';
import { t } from '../core/i18n.js';
import { stateIcon } from '../ui/icons.js';
import { shellIcon } from '../ui/icons-shell.js';
import { historyApi, operationId } from './api.js';
import { resumeSession, renameSession, setSessionArchived, deleteSession, recheckCatalog, executionStatus, agoLabel } from './session-actions.js';
import { normalizeSubagents, nativeStateLabel } from '../shell/native-subagents.js';
import { mountSubagentTranscript, drawEntry } from '../panels/subagent-viewer.js';

export const RECORD_ID = 'vt-session-record';

const RESUME_BLOCKED = () => ({
  archived: t('보관된 세션은 보관을 해제한 뒤 시작할 수 있습니다'),
  running: t('이미 실행 중입니다'),
  no_conversation: t('이어갈 수 있는 대화가 없습니다'),
});

const relayHost = () => window.vtRelayHost?.() || 'local';
const hhmm = (sec) => { try { return new Date(sec * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }); } catch { return ''; } };
const parseDetail = (v) => { try { const o = JSON.parse(v || '{}'); return o && typeof o === 'object' ? o : {}; } catch { return {}; } };

/** 카탈로그 목록(이 맥) — tmux 이름으로 실행 중인 세션을 찾고, 위임 하위 세션의 제목을 붙이는 데 쓴다. */
async function catalogIndex(signal) {
  try {
    const data = await historyApi('/api/session-catalog?limit=200', { signal, elevated: false });
    const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
    return {
      byId: new Map(sessions.map((s) => [s.id, s])),
      byTmux: (name) => sessions.find((s) => s.ended == null && s.tmux_name === name) || null,
    };
  } catch (_) {
    return { byId: new Map(), byTmux: () => null };
  }
}

/**
 * 작업 기록 창을 연다.
 *   id      — 카탈로그 세션 id(없으면 tmuxName으로 찾는다)
 *   changed — 이름 변경·보관·삭제 뒤 부를 콜백(레일 카탈로그 다시 받기)
 *   opts.tmuxName — 그 세션의 tmux 이름(내부 서브에이전트 목록·기록을 찾는다)
 *   opts.select   — 오른쪽에 미리 고를 것: { kind:'subagent', id } · { kind:'delegate', id?, tmuxName?, summary? }
 *                   · { kind:'working' }(작업 중인 첫 서브에이전트). 없으면 가장 최근 서브에이전트 → 위임 세션.
 */
/** @param {string | null} id @param {() => void} [changed] @param {{ tmuxName?: string, select?: any }} [opts] */
export function openSessionRecord(id, changed = () => {}, opts = {}) {
  closePanel(RECORD_ID, { history: 'replace' });
  const controller = new AbortController();
  let right = null;   // { stop, refresh }
  const stopRight = () => { right?.stop?.(); right = null; };
  const { body } = openDialog({ id: RECORD_ID, title: t('작업 기록'), bodyHTML: '', extraClass: 'vt-worklog',
    onClose: () => { controller.abort(); stopRight(); clearInterval(ageTimer); } });
  if (!body) return;
  const doc = body.ownerDocument;
  const el = (tag, text, cls) => { const n = doc.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const api = (path, options = {}) => historyApi(path, { ...options, signal: controller.signal });
  const host = relayHost();
  const local = host === 'local';

  // ── 머리: [기록 아이콘] 작업 기록 | [상태] 세션 이름 · 브랜치 ……… n초 전 갱신 [새로고침] [×]
  const head = body.closest('.vt-viewer-card')?.querySelector('.vt-viewer-head');
  const headIc = el('span', null, 'vt-dialog-ic'); headIc.setAttribute('aria-hidden', 'true'); headIc.innerHTML = shellIcon('history', 16);
  const headSep = el('span', null, 'vt-wl-sep');
  const headState = el('span', null, 'vt-wl-head-state');
  const headName = el('b', '', 'vt-wl-head-name');
  const headSub = el('span', '', 'vt-wl-head-sub');
  const headGap = el('span', null, 'vt-wl-gap');
  const updated = el('span', '', 'vt-wl-updated');
  const refreshAll = el('button', null, 'vt-icon-btn vt-wl-refresh'); refreshAll.type = 'button';
  refreshAll.setAttribute('aria-label', t('새로고침')); refreshAll.setAttribute('data-tip', t('새로고침')); refreshAll.innerHTML = shellIcon('refresh-cw', 14);
  if (head) {
    head.prepend(headIc);
    const x = head.querySelector('.vt-vw-x');
    for (const n of [headSep, headState, headName, headSub, headGap, updated, refreshAll]) head.insertBefore(n, x);
  }
  let loadedAt = 0;
  const paintAge = () => { updated.textContent = loadedAt ? t('{ago} 갱신', { ago: agoLabel(loadedAt / 1000) }) : ''; };
  const ageTimer = setInterval(paintAge, 5000);

  // ── 몸통: 왼쪽 세션 · 오른쪽 기록
  const wrap = el('div', null, 'vt-wl');
  const left = el('div', null, 'vt-wl-left');
  const rightCol = el('div', null, 'vt-wl-right');
  wrap.append(left, rightCol);
  body.replaceChildren(wrap);
  const message = el('p', t('불러오는 중…'), 'vt-wl-msg'); message.setAttribute('role', 'status');
  left.append(message);

  // 오른쪽 칸 뼈대
  const rHead = el('header', null, 'vt-wl-rhead');
  const rIc = el('span', null, 'vt-wl-rhead-ic'); rIc.setAttribute('aria-hidden', 'true');
  const rName = el('b', '', 'vt-wl-rhead-name');
  const rChip = el('span', '', 'vt-wl-chip');
  const rMeta = el('span', '', 'vt-wl-rhead-meta');
  const rActs = el('span', null, 'vt-wl-rhead-acts');
  rHead.append(rIc, rName, rChip, rMeta, rActs);
  const filterBar = el('div', null, 'vt-wl-filter');
  const seg = el('div', null, 'vt-seg vt-wl-seg'); seg.setAttribute('role', 'radiogroup'); seg.setAttribute('aria-label', t('기록 종류'));
  const FILTERS = [['all', t('전체')], ['talk', t('지시 · 답변')], ['tools', t('도구만')]];
  let filter = 'all';
  const segButtons = FILTERS.map(([key, label]) => {
    const b = el('button', label, 'vt-wl-seg-btn'); b.type = 'button'; b.dataset.filter = key;
    b.setAttribute('role', 'radio'); b.setAttribute('aria-checked', String(key === filter));
    b.onclick = () => { filter = key; paintFilter(); };
    seg.append(b); return b;
  });
  const count = el('span', '', 'vt-wl-count');
  filterBar.append(seg, el('span', null, 'vt-wl-gap'), count);
  const rStatus = el('p', '', 'vt-wl-rstatus'); rStatus.setAttribute('role', 'status');
  const scroller = el('div', null, 'vt-wl-scroll');
  const list = el('div', null, 'vt-wl-list');
  scroller.append(list);
  rightCol.append(rHead, filterBar, rStatus, scroller);
  const paintFilter = () => {
    list.dataset.filter = filter;
    segButtons.forEach((b) => { const on = b.dataset.filter === filter; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); });
  };
  const paintCount = () => { const n = list.querySelectorAll('.vt-wl-entry').length; count.textContent = n ? t('{n}개', { n }) : ''; };
  new MutationObserver(paintCount).observe(list, { childList: true });
  paintFilter();
  const emptyRight = (text) => {
    stopRight();
    rIc.innerHTML = ''; rName.textContent = ''; rChip.textContent = ''; rChip.removeAttribute('data-tone'); rMeta.textContent = ''; rActs.replaceChildren();
    rStatus.textContent = '';
    list.replaceChildren(el('p', text, 'vt-wl-empty'));
    rightCol.dataset.kind = 'none';
  };

  let selectedKey = '';
  const markSelected = () => {
    for (const n of left.querySelectorAll('[data-wl-key]')) {
      const on = n.dataset.wlKey === selectedKey;
      n.classList.toggle('on', on);
      n.setAttribute('aria-pressed', String(on));
    }
  };
  const actButton = (label, iconName, run) => {
    const b = el('button', null, 'vt-btn sm quiet'); b.type = 'button';
    if (iconName) b.insertAdjacentHTML('afterbegin', shellIcon(iconName, 12));
    b.append(doc.createTextNode(label));
    b.onclick = run;
    return b;
  };
  const refreshBtn = (run) => actButton(t('새로고침'), 'refresh-cw', run);

  // 오른쪽: 내부 서브에이전트
  function showSubagent(tmuxName, parentId, child) {
    stopRight();
    selectedKey = 'sub:' + child.id; markSelected();
    rightCol.dataset.kind = 'subagent';
    rIc.innerHTML = shellIcon('git-branch', 16);
    rName.textContent = child.label;
    const paintChip = (state) => { rChip.textContent = nativeStateLabel(state); rChip.dataset.tone = state; };
    paintChip(child.state);
    rMeta.textContent = [t('내부 서브에이전트'), child.started_at ? t('{time} 시작', { time: hhmm(child.started_at) }) : ''].filter(Boolean).join(' · ');
    list.replaceChildren();
    rStatus.textContent = '';
    right = mountSubagentTranscript(list, { tmuxName, parentId, child, host,
      status: (text) => { rStatus.textContent = text; },
      meta: ({ label, state }) => { if (label) rName.textContent = label; paintChip(state); },
    });
    rActs.replaceChildren(refreshBtn(() => right?.refresh()));
  }

  // 오른쪽: 위임 세션(별도 터미널) — 그 세션의 가장 최근 대화를 대화 보관함에서 읽는다(지시 · 답변 · 도구).
  async function showDelegate(childId, { title = '', relation = null, summary = '' } = {}) {
    stopRight();
    selectedKey = 'del:' + childId; markSelected();
    rightCol.dataset.kind = 'delegate';
    rIc.innerHTML = shellIcon('corner-down-right', 16);
    rName.textContent = title || childId.slice(0, 8);
    const d = relation ? parseDetail(relation.detail) : {};
    const chip = d.state === 'reported' ? (d.outcome === 'failed' ? t('보고 · 실패') : t('보고 · 성공'))
      : d.state === 'running' ? t('진행 중') : d.state === 'closed' ? t('닫힘') : d.state === 'orphaned' ? t('부모 없음') : '';
    rChip.textContent = chip; rChip.dataset.tone = d.state === 'reported' ? (d.outcome === 'failed' ? 'failed' : 'done') : d.state === 'running' ? 'working' : 'idle';
    rMeta.textContent = t('위임 세션 · 별도 터미널');
    list.replaceChildren();
    rStatus.textContent = t('불러오는 중…');
    let live = true;
    let next = null;
    let conversationId = null;
    right = { stop: () => { live = false; }, refresh: () => { void showDelegate(childId, { title, relation, summary }); } };
    rActs.replaceChildren(refreshBtn(() => right?.refresh()));
    const mine = right;
    const appendMessages = (messages) => {
      const frag = doc.createDocumentFragment();
      for (const m of messages) frag.append(drawEntry(doc, { kind: m.role, text: m.body }));
      list.append(frag);
    };
    const more = el('button', t('이후 메시지 보기'), 'vt-btn sm vt-wl-more'); more.type = 'button';
    more.onclick = async () => {
      if (!next || !conversationId) return;
      more.disabled = true;
      try {
        const data = await api(`/api/session-history/conversations/${encodeURIComponent(conversationId)}?after=${encodeURIComponent(next)}`, { elevated: false });
        if (!live || right !== mine) return;
        more.remove(); appendMessages(data.messages || []); next = data.next || null;
        if (next) list.append(more);
      } catch (e) { if (live) rStatus.textContent = e.message; }
      finally { more.disabled = false; }
    };
    try {
      if (summary) list.append(drawEntry(doc, { kind: 'assistant', text: summary, note: t('보고') }));
      if (!local) { rStatus.textContent = t('위임 세션 기록은 이 맥을 직접 볼 때만 볼 수 있습니다'); return; }
      const detail = await api('/api/session-catalog/sessions/' + encodeURIComponent(childId), { elevated: false });
      if (!live || right !== mine) return;
      if (!title && detail?.session?.title) rName.textContent = detail.session.title;
      const conv = (detail?.conversations || [])[0];
      if (!conv) { rStatus.textContent = summary ? '' : t('이 위임 세션에 연결된 대화 기록이 아직 없습니다'); return; }
      conversationId = conv.id;
      rMeta.textContent = [t('위임 세션 · 별도 터미널'), conv.provider].filter(Boolean).join(' · ');
      const data = await api(`/api/session-history/conversations/${encodeURIComponent(conv.id)}`, { elevated: false });
      if (!live || right !== mine) return;
      rStatus.textContent = (data.messages || []).length || summary ? '' : t('아직 해석된 메시지가 없습니다. 원본 보관 상태를 확인하세요.');
      appendMessages(data.messages || []);
      next = data.next || null;
      if (next) list.append(more);
    } catch (e) {
      if (live && right === mine && !controller.signal.aborted) rStatus.textContent = e.message;
    }
  }

  // ── 왼쪽을 그린다(새로고침마다 다시)
  let catalog = null;
  let sessionId = id || null;
  let firstPick = true;
  const sec = (title, n) => {
    const s = el('section', null, 'vt-wl-sec');
    const h = el('h4', title, 'vt-wl-sec-h');
    if (n != null) h.append(el('span', String(n), 'vt-wl-n'));
    s.append(h);
    return s;
  };
  const row = (cls = '') => { const b = el('button', null, 'vt-wl-row' + (cls ? ' ' + cls : '')); b.type = 'button'; return b; };

  async function load() {
    message.textContent = t('불러오는 중…');
    let data = null;
    try {
      if (local && !catalog) catalog = await catalogIndex(controller.signal);
      if (!sessionId && opts.tmuxName && catalog) sessionId = catalog.byTmux(opts.tmuxName)?.id || null;
      if (local && sessionId) data = await api('/api/session-catalog/sessions/' + encodeURIComponent(sessionId));
    } catch (error) {
      if (controller.signal.aborted) return;
      message.textContent = error.message;
    }
    if (controller.signal.aborted) return;
    loadedAt = Date.now(); paintAge();
    paintLeft(data);
    if (firstPick) { firstPick = false; pickInitial(data); }
    markSelected();
  }

  function subagentSummary(tmuxName) {
    return normalizeSubagents(tmuxName ? window.agentDetails?.()[tmuxName]?.subagents : null);
  }

  function paintLeft(data) {
    const session = data?.session || null;
    const runtimes = data?.runtimes || [];
    const active = runtimes.find((r) => r.ended == null);
    const last = runtimes[0];
    const tmuxName = opts.tmuxName || active?.name || '';
    // 터미널 목록에 실제로 보이는가 — 행에서 연 창(tmux 이름을 안다)이거나 에이전트 상태 요약에 그 세션이 있으면 붙어 있다.
    const attached = !!active && (opts.tmuxName === active.name || !!window.agentDetails?.()[active.name]);
    const legacy = !!data && !active && !session.archived && data.resumable?.reason === 'no_conversation' && !!last?.ended;
    const status = !session ? { label: tmuxName ? t('기록 없음') : t('세션을 찾을 수 없습니다'), tone: 'idle' }
      : legacy ? { label: t('과거 기록'), hint: t('연결된 대화 기록이 생기기 전에 끝난 세션이라 이어서 시작할 수 없습니다') }
        : executionStatus({ ...session, ended: last ? last.ended : null, execution_state: last ? last.state : 'never_started' }, attached);
    const stateName = active ? (window.agentDetails?.()[tmuxName]?.status || 'working') : session?.archived ? 'idle' : 'stopped';
    headState.innerHTML = stateIcon(stateName === 'stopped' ? 'idle' : stateName, 14);
    headName.textContent = session?.title || tmuxName || '';
    headSub.textContent = active?.name && active.name !== headName.textContent ? active.name : '';
    const nodes = [];

    // 상태 블록 + 동작
    const block = el('section', null, 'vt-wl-status');
    const top = el('div', null, 'vt-wl-status-top');
    const st = el('span', null, 'vt-wl-status-ic'); st.innerHTML = stateIcon(stateName === 'stopped' ? 'idle' : stateName, 16);
    top.append(st, el('b', status.label, 'vt-wl-status-label'), el('span', null, 'vt-wl-gap'),
      el('span', last ? agoLabel(last.ended || last.started) : '', 'vt-wl-status-time'));
    block.append(top);
    const sub = [session?.provider, session?.cwd || session?.workspace, active?.name].filter(Boolean).join(' · ');
    if (sub) block.append(el('div', sub, 'vt-wl-status-sub'));
    if (status.hint) block.append(el('div', status.hint, 'vt-wl-status-sub'));
    const acts = el('div', null, 'vt-wl-acts');
    const button = (label, run, { solid = false, danger = false, disabled = false, title = '', iconName = '' } = {}) => {
      const n = el('button', null, 'vt-btn sm' + (solid ? ' solid' : danger ? ' danger' : ' quiet')); n.type = 'button';
      if (iconName) n.insertAdjacentHTML('afterbegin', shellIcon(iconName, 12));
      n.append(doc.createTextNode(label));
      n.disabled = disabled; if (title) n.title = title;
      n.onclick = async () => {
        n.disabled = true; message.textContent = '';
        try { if (await run() !== false) { changed(); await load(); } }
        catch (error) { if (!controller.signal.aborted) message.textContent = error.message; }
        finally { n.disabled = false; }
      };
      return n;
    };
    if (session) {
      if (status.recheck) {
        acts.append(button(t('다시 확인'), async () => {
          const r = await recheckCatalog({ signal: controller.signal });
          if (!r.complete) throw new Error(t('아직 터미널 상태를 읽지 못했습니다. 잠시 뒤 다시 시도하세요'));
        }, { solid: true }));
      }
      if (session.archived) {
        acts.append(button(t('보관 해제'), () => setSessionArchived(session, false, { signal: controller.signal }), { solid: !status.recheck }));
      } else if (!legacy) {
        const blocked = data.resumable?.ok ? '' : (RESUME_BLOCKED()[data.resumable?.reason] || '');
        // 종료된 세션은 「이어서 시작」이 주 버튼, 실행 중이면 비활성(시안 O15-B 다섯째 줄).
        acts.append(button(t('이어서 시작'), async () => {
          await resumeSession(sessionId, { signal: controller.signal });
          closePanel(RECORD_ID); changed(); return false;
        }, { solid: !active && !blocked && !status.recheck, disabled: !!blocked || !!active, title: active ? t('실행 중인 세션입니다') : blocked, iconName: 'play' }));
      }
      acts.append(button(t('실행 종료'), async () => {
        if (!await confirmDialog(t('이 실행의 모든 터미널 창과 pane을 종료합니다. 별도 하위 세션은 유지합니다. 계속할까요?'), { danger: true })) return false;
        await api('/api/session-catalog/sessions/' + sessionId + '/stop', { body: { operation_id: operationId(), force: true } });
      }, { danger: true, disabled: !active, iconName: 'square' }));
      acts.append(button(t('이름 변경'), () => renameSession(session, { signal: controller.signal })));
      if (!active && !session.archived) acts.append(button(t('세션 보관'), () => setSessionArchived(session, true, { signal: controller.signal })));
      if (!active && session.archived) {
        acts.append(button(t('삭제'), async () => {
          if (!await deleteSession(session, { signal: controller.signal })) return false;
          closePanel(RECORD_ID); changed(); return false;
        }, { danger: true }));
      }
      block.append(acts);
    } else if (!local) {
      block.append(el('div', t('세션 기록은 이 맥을 직접 볼 때만 볼 수 있습니다'), 'vt-wl-status-sub'));
    }
    nodes.push(block);

    // 연결된 대화
    if (session) {
      const conv = data.conversations || [];
      const s = sec(t('연결된 대화'), conv.length);
      if (!conv.length) s.append(el('p', t('연결된 대화가 없습니다'), 'vt-wl-none'));
      for (const c of conv) {
        const r = row();
        r.insertAdjacentHTML('afterbegin', `<span class="vt-wl-row-ic" aria-hidden="true">${shellIcon('list', 14)}</span>`);
        r.append(el('span', c.title || c.native_id, 'vt-wl-row-name'), el('span', null, 'vt-wl-gap'), el('span', [c.provider, agoLabel(c.updated)].filter(Boolean).join(' · '), 'vt-wl-row-meta'));
        r.onclick = async () => { const { openHistory } = await import('./panel.js'); openHistory({ search: c.native_id, sessionId }); };
        s.append(r);
      }
      nodes.push(s);
    }

    // 위임 관계 — 부모는 그 세션의 기록으로 왼쪽을 바꾸고, 하위는 오른쪽에 그 세션 기록을 연다.
    if (session) {
      const relations = (data.relations || []).filter((r) => r.kind === 'delegation').slice(0, 200);
      const children = relations.filter((r) => r.parent_id === sessionId);
      const parents = relations.filter((r) => r.child_id === sessionId);
      const reported = children.filter((r) => parseDetail(r.detail).state === 'reported').length;
      const s = sec(t('위임 관계'), children.length ? (reported ? t('보고 {n}/{m}', { n: reported, m: children.length }) : children.length) : null);
      if (!relations.length) s.append(el('p', t('부모 없음 · 위임 세션 0'), 'vt-wl-none'));
      for (const r of parents) {
        const b = row();
        b.insertAdjacentHTML('afterbegin', `<span class="vt-wl-row-ic" aria-hidden="true">${shellIcon('arrow-up', 14)}</span>`);
        const title = catalog?.byId.get(r.parent_id)?.title || r.parent_id.slice(0, 8);
        b.append(el('span', title, 'vt-wl-row-name'), el('span', t('상위 작업 기록'), 'vt-wl-row-meta'));
        b.onclick = () => openSessionRecord(r.parent_id, changed);
        s.append(b);
      }
      for (const r of children) {
        const d = parseDetail(r.detail);
        const b = row('vt-wl-child');
        b.dataset.wlKey = 'del:' + r.child_id;
        const title = catalog?.byId.get(r.child_id)?.title || r.child_id.slice(0, 8);
        b.insertAdjacentHTML('afterbegin', `<span class="vt-wl-row-ic" aria-hidden="true">${shellIcon('corner-down-right', 12)}</span>${stateIcon(d.state === 'running' ? 'working' : d.state === 'reported' ? 'done' : 'idle', 14)}`);
        const stateText = d.state === 'reported' ? (d.outcome === 'failed' ? t('보고 · 실패') : t('보고 · 성공')) : d.state === 'running' ? t('진행 중') : d.state === 'closed' ? t('닫힘') : '';
        b.append(el('span', title, 'vt-wl-row-name'), el('span', t('FarShell 위임 세션'), 'vt-wl-row-meta'), el('span', null, 'vt-wl-gap'), el('span', stateText, 'vt-wl-row-state'));
        b.dataset.tone = d.state === 'reported' ? (d.outcome === 'failed' ? 'failed' : 'done') : d.state === 'running' ? 'working' : 'idle';
        b.onclick = () => { void showDelegate(r.child_id, { title, relation: r }); };
        s.append(b);
      }
      nodes.push(s);
    }

    // 내부 서브에이전트(그 세션 안의 Task 등) — 실시간 요약(window.agentDetails)에서.
    const summary = subagentSummary(tmuxName);
    {
      const s = sec(t('내부 서브에이전트'), summary.items.length);
      if (!summary.items.length) s.append(el('p', tmuxName ? t('이 세션에서 내부 작업을 쓰지 않았습니다') : t('실행 중인 세션이 아닙니다'), 'vt-wl-none'));
      const sorted = [...summary.items].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
      for (const child of sorted) {
        const b = row('vt-wl-sub');
        b.dataset.wlKey = 'sub:' + child.id;
        b.dataset.tone = child.state;
        b.insertAdjacentHTML('afterbegin', stateIcon(child.state, 14));
        b.append(el('span', child.label, 'vt-wl-row-name'), el('span', null, 'vt-wl-gap'),
          el('span', [nativeStateLabel(child.state), child.started_at ? agoLabel(child.started_at) : ''].filter(Boolean).join(' · '), 'vt-wl-row-state'));
        b.onclick = () => { if (host === relayHost()) showSubagent(tmuxName, summary.parentId, child); };
        s.append(b);
      }
      if (summary.limited) s.append(el('p', t('표시 한도에 도달함'), 'vt-wl-none'));
      nodes.push(s);
    }

    // 실행 이력
    if (runtimes.length) {
      const s = sec(t('실행 이력'), runtimes.length);
      for (const runtime of runtimes) {
        const item = el('div', null, 'vt-wl-run');
        item.append(el('span', hhmm(runtime.started), 'vt-wl-run-time'));
        item.insertAdjacentHTML('beforeend', stateIcon(runtime.ended ? 'done' : 'working', 12));
        item.append(el('span', `${runtime.ended ? t('실행 종료됨') : t('실행 중')} · ${runtime.name}`, 'vt-wl-row-name'),
          el('span', null, 'vt-wl-gap'), el('span', new Date(runtime.started * 1000).toLocaleDateString(), 'vt-wl-row-meta'));
        s.append(item);
      }
      nodes.push(s);
    }
    message.textContent = message.textContent === t('불러오는 중…') ? '' : message.textContent;
    left.replaceChildren(...nodes.slice(0, 1), message, ...nodes.slice(1));
    left._vtSummary = summary; left._vtTmux = tmuxName; left._vtData = data;
  }

  function pickInitial(data) {
    const sel = opts.select || null;
    const tmuxName = left._vtTmux;
    const summary = left._vtSummary;
    const items = [...summary.items].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
    const children = (data?.relations || []).filter((r) => r.kind === 'delegation' && r.parent_id === sessionId);
    const title = (cid) => catalog?.byId.get(cid)?.title || '';
    if (sel?.kind === 'subagent') {
      const child = items.find((c) => c.id === sel.id) || (sel.child ? sel.child : null);
      if (child) { showSubagent(tmuxName, summary.parentId, child); return; }
    }
    if (sel?.kind === 'working') {
      const child = items.find((c) => c.state === 'working') || items[0];
      if (child) { showSubagent(tmuxName, summary.parentId, child); return; }
    }
    if (sel?.kind === 'delegate') {
      const childId = sel.id || (sel.tmuxName && catalog?.byTmux(sel.tmuxName)?.id) || null;
      const relation = childId ? children.find((r) => r.child_id === childId) || null
        : sel.delegation ? children.find((r) => r.source_id === sel.delegation) || null : null;
      const cid = childId || relation?.child_id || null;
      if (cid) { void showDelegate(cid, { title: title(cid) || sel.name || '', relation, summary: sel.summary || '' }); return; }
      if (sel.summary) {
        // 장부에 아직 위임 관계가 없을 때(하위 세션이 카탈로그에 늦게 잡힘) — 받은 보고만 보인다.
        stopRight(); rightCol.dataset.kind = 'delegate';
        rIc.innerHTML = shellIcon('corner-down-right', 16); rName.textContent = sel.name || ''; rChip.textContent = t('보고'); rChip.dataset.tone = 'done';
        rMeta.textContent = t('위임 세션 · 별도 터미널'); rActs.replaceChildren(); rStatus.textContent = '';
        list.replaceChildren(drawEntry(doc, { kind: 'assistant', text: sel.summary, note: t('보고') }));
        return;
      }
    }
    // 기본: 가장 최근 내부 서브에이전트 → 가장 최근 위임 세션 → 비움.
    if (items.length) { showSubagent(tmuxName, summary.parentId, items[0]); return; }
    const lastChild = children[children.length - 1];
    if (lastChild) { void showDelegate(lastChild.child_id, { title: title(lastChild.child_id), relation: lastChild }); return; }
    emptyRight(t('왼쪽에서 내부 서브에이전트나 위임 세션을 고르면 그 기록이 여기에 나옵니다.'));
  }

  refreshAll.onclick = () => { catalog = null; void load(); right?.refresh?.(); };
  void load();
}

/**
 * 작업 기록 창을 tmux 이름으로 연다(카탈로그 id를 모르는 진입점 — 하위 행 「기록 보기」·내부 칩·「보고 보기」).
 * @param {{ sessionId?: string | null, tmuxName?: string | null, select?: any, changed?: () => void }} [arg]
 */
export function openWorkLog({ sessionId = null, tmuxName = '', select = null, changed = () => {} } = {}) {
  openSessionRecord(sessionId, changed, { tmuxName: tmuxName || '', select });
}
