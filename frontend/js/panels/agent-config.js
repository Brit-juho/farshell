// Unified customization manager. Each mount owns its requests, drafts and timers;
// late responses can never rebind a change to a different host or session.
import { vtFetch } from '../core/api.js';
import { relayHost } from '../core/host-route.js';
import { promptDialog, confirmDialog } from '../core/dialog.js';
import { showToast } from '../ui/toast.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';

const KIND = { mcp: 'MCP', skill: t('스킬'), plugin: t('플러그인') };
const ORIGIN = { personal: t('개인 작성'), git: t('Git 저장소'), marketplace: t('마켓플레이스'), local: t('로컬 설치'), unknown: t('출처 미확인') };
const UPDATE = { unchecked: t('업데이트 미확인'), unavailable: t('업데이트 확인 불가'), current: t('최신'), available: t('업데이트 있음'), modified: t('로컬 수정 있음') };
const STATE = { waiting: t('안전한 재시작 대기'), restarting: t('재시작 중'), resumed: t('같은 대화로 재시작됨'), failed: t('재시작 실패'), cancelled: t('재시작 취소됨') };
const READ = new Set(['inventory', 'environment', 'sync-status', 'restart-status', 'restart-jobs', 'requests', 'preview', 'restart-preview', 'edit-preview']);

// 관리 창 안쪽 배치(리디자인 후속, 시안 panels P09-b): 위 막대(관리 대상 · 호스트 · 세션 · 새로고침) /
// 왼쪽 탐색 200(아이콘 + 이름 + 개수) / 오른쪽 내용(필터 막대 · 일괄 막대 · 56 높이 행) / 바닥 막대(안내 · 재시작 ·
// 옛 보관함). 창 크기(1240×860)와 큰 글자 결정은 그대로다 — 이 파일은 구획만 나누고 모양은 CSS(.vt-ac-*)가 맡는다.
// 기능·문구는 바꾸지 않는다(버튼 이름이 곧 테스트의 손잡이다).
const NAV = [
  ['items', 'list'], ['profiles', 'sliders-horizontal'], ['presets', 'layout-panels'],
  ['environment', 'braces'], ['sync', 'refresh-cw'], ['requests', 'shield'],
];

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function button(text, run, cls = 'vt-chip sm') {
  const node = el('button', cls, text);
  node.type = 'button';
  node.addEventListener('click', run);
  return node;
}

function select(label, entries, value, onChange) {
  const wrap = el('label', 'vt-ac-field');
  wrap.appendChild(el('span', 'vt-mcp-meta', label));
  const input = el('select', 'vt-input');
  input.setAttribute('aria-label', label);
  for (const [id, title] of entries) {
    const option = el('option', null, title);
    option.value = id;
    input.appendChild(option);
  }
  input.value = value;
  input.addEventListener('change', () => onChange(input.value));
  wrap.appendChild(input);
  return wrap;
}

export function mount(host, initialContext = {}, onLegacy = null) {
  let disposed = false;
  let generation = 0;
  let busy = false;
  let view = initialContext.session ? 'session' : 'all';
  let selectedHost = initialContext.host || relayHost();
  let session = initialContext.session || '';
  let hosts = [];
  let snapshots = [];
  let rows = [];
  let section = initialContext.section === 'profiles' ? 'profiles' : 'items';
  let profilePanel = null;
  let kind = 'all';
  let tool = 'all';
  let category = 'all';
  let scope = 'all';
  let profile = 'all';
  let q = '';
  let plugins = false;
  let selected = new Map();
  const expanded = new Set();
  let errors = [];
  let envData = null;
  let syncData = null;
  let requests = [];
  let job = null;
  let timer = null;
  let notice = '';
  let loading = false;
  let editorState = null;

  const alive = () => !disposed && host.isConnected;
  const contextPayload = () => ({ session: session || null, view });
  const rowKey = (row) => `${row.host}:${row.id}`;
  const hostLabel = (id) => hosts.find((h) => h.id === id)?.label || id;

  async function api(target, action, payload = {}) {
    if (!alive()) throw new Error(t('변경을 취소했습니다'));
    const path = target === 'local'
      ? `/api/agent-config/${READ.has(action) ? 'read' : 'write'}/${action}`
      : `/api/hosts/${encodeURIComponent(target)}/agent-config/${action}`;
    const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
    try { return await vtFetch(path, opts); }
    catch (e) {
      if (e.status !== 401 || !/elevation_required/.test(JSON.stringify(e.data || e.message))) throw e;
      const password = await promptDialog(t('설정을 변경하려면 비밀번호를 확인하세요'), { password: true, title: t('비밀번호 확인') });
      if (password === null) throw new Error(t('변경을 취소했습니다'));
      await vtFetch('/api/auth/elevate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
      return vtFetch(path, opts);
    }
  }

  async function perform(run) {
    if (busy || !alive()) return;
    busy = true;
    host.setAttribute('aria-busy', 'true');
    host.querySelectorAll('button, input, select, textarea').forEach((n) => { n.disabled = true; });
    try { await run(); }
    catch (e) { if (alive()) { notice = e.message || t('요청을 처리하지 못했습니다'); showToast(notice, 'error'); } }
    finally { busy = false; if (alive()) { host.removeAttribute('aria-busy'); paint(); } }
  }

  async function changeContext(change) {
    if (selected.size && !(await confirmDialog(t('선택한 항목을 비우고 관리 대상을 바꿀까요?'), { title: t('관리 대상 변경'), okLabel: t('비우고 변경') }))) return;
    selected.clear();
    change();
    envData = null;
    syncData = null;
    notice = '';
    await load();
  }

  async function load() {
    const seq = ++generation;
    loading = true;
    const targets = selectedHost === 'all' ? hosts.map((h) => h.id) : [selectedHost];
    const payload = { ...contextPayload(), plugins };
    snapshots = [];
    errors = [];
    const settled = await Promise.allSettled(targets.map(async (target) => {
      let offset = 0;
      let revision = null;
      let combined = null;
      do {
        const page = await api(target, 'inventory', { ...payload, offset, inventory_revision: revision });
        if (!alive() || seq !== generation) return;
        if (!combined) combined = { ...page, host: target, items: [], errors: [] };
        const found = new Map(combined.items.map((r) => [r.id, r]));
        for (const row of page.items || []) found.set(row.id, row);
        combined.items = [...found.values()];
        combined.errors = [...combined.errors, ...(page.errors || [])];
        snapshots = [...snapshots.filter((s) => s.host !== target), combined];
        rows = snapshots.flatMap((s) => s.items.map((r) => ({ ...r, host: s.host })));
        revision = page.inventory_revision;
        offset = page.next_offset ?? null;
        paint();
      } while (offset !== null);
      return combined;
    }));
    if (!alive() || seq !== generation) return;
    loading = false;
    settled.forEach((result, i) => {
      if (result.status === 'fulfilled' && result.value) {
        for (const error of result.value.errors || []) errors.push(`${hostLabel(targets[i])}: ${error.reason}`);
      } else if (result.status === 'rejected') errors.push(`${hostLabel(targets[i])}: ${result.reason.message || t('연결되지 않음')}`);
    });
    selected = new Map([...selected.keys()].map((key) => [key, rows.find((r) => rowKey(r) === key)]).filter(([, row]) => row));
    paint();
    if (!job && selectedHost === 'local' && session) {
      const jobs = await api('local', 'restart-jobs');
      if (!alive() || seq !== generation) return;
      const pending = (jobs.jobs || []).find((j) => j.session === session);
      if (pending) { job = { ...pending, host: 'local' }; paint(); pollJob(); }
    }
  }

  async function applyPlan(target, action, plan) {
    if (target !== 'local') return api(target, 'request', { action, payload: { token: plan.token } });
    return api(target, action, { token: plan.token });
  }

  function planText(plan) {
    return (plan.changes || []).map((c) => `${KIND[c.kind]} · ${c.name} → ${c.enabled ? t('켬') : t('끔')}\n${c.profile}\n${c.source}${c.project ? `\n${c.project}` : ''}`).join('\n\n');
  }

  async function applySelection(enabled, preset = null, single = null) {
    const groups = new Map();
    const members = preset ? [] : single ? [single] : [...selected.values()];
    for (const row of members) {
      if (!groups.has(row.host)) groups.set(row.host, []);
      groups.get(row.host).push({ id: row.id, enabled });
    }
    if (preset) groups.set(preset.host, null);
    if (!groups.size) return;
    const plans = [];
    for (const [target, changes] of groups) {
      const plan = await api(target, 'preview', { ...contextPayload(), changes, preset: preset?.id });
      plans.push({ target, plan });
    }
    const review = plans.map(({ target, plan }) => `${hostLabel(target)}\n${planText(plan)}`).join('\n\n');
    if (!(await confirmDialog(`${t('아래 항목만 변경합니다. 나머지 구성은 유지합니다.')}\n\n${review}\n\n${t('전역·프로필 설정 변경은 다른 세션에도 영향을 줍니다. 프로젝트 공용 파일은 팀과 공유됩니다.')}`, { title: t('설정 변경 적용'), okLabel: t('적용') }))) return;
    const results = [];
    for (const { target, plan } of plans) {
      try {
        const result = await applyPlan(target, 'apply', plan);
        results.push(`${hostLabel(target)}: ${result.status === 'pending' ? t('대상 호스트 승인 대기') : result.status === 'ok' ? t('설정 저장됨 — 재시작 필요') : t('일부 변경 실패 — 결과를 확인하세요')}`);
        if (result.status !== 'ok' && result.results) {
          for (const row of result.results.filter((r) => r.status !== 'ok')) results.push(`${row.name}: ${row.reason || row.status}`);
        }
      } catch (e) { results.push(`${hostLabel(target)}: ${e.message}`); }
    }
    notice = results.join('\n');
    if (!single && !preset) selected.clear();
    await load();
    if (plans.length === 1 && plans[0].target === 'local' && view === 'session' && session
        && results.every((r) => r.includes(t('설정 저장됨 — 재시작 필요')))) {
      if (await confirmDialog(t('선택한 CLI를 안전할 때 재시작해 같은 대화로 이어갈까요?'), { title: t('CLI 재시작'), okLabel: t('재시작') })) await scheduleRestart();
    }
  }

  async function scheduleRestart() {
    if (!session || selectedHost === 'all') return;
    const plan = await api(selectedHost, 'restart-preview', { session });
    if (!(await confirmDialog(`${plan.note}\n\n${plan.context.root}\n${plan.context.cwd}`, { title: t('CLI 재시작'), okLabel: t('재시작') }))) return;
    const result = await applyPlan(selectedHost, 'restart-start', plan);
    if (result.status === 'pending') { notice = t('대상 호스트 승인 대기'); return; }
    job = { ...result, host: selectedHost };
    pollJob();
  }

  function pollJob() {
    if (timer) clearTimeout(timer);
    if (!job || !alive() || !['waiting', 'restarting'].includes(job.state)) return;
    timer = setTimeout(async () => {
      try {
        const result = await api(job.host, 'restart-status', { job: job.id });
        if (!alive()) return;
        job = { ...result, host: job.host };
        if (!busy) paint();
        pollJob();
      } catch (e) { if (alive()) { notice = e.message; if (!busy) paint(); } }
    }, 2000);
  }

  async function classify(row) {
    const categoryValue = await promptDialog(t('용도 카테고리 — 예: 개발, 조사, 문서'), { title: t('용도 분류'), okLabel: t('저장'), defaultValue: row.metadata?.category || '' });
    if (categoryValue === null) return;
    const description = await promptDialog(t('이 항목의 용도와 관리 메모'), { title: t('용도 분류'), okLabel: t('저장'), defaultValue: row.metadata?.description || '' });
    if (description === null) return;
    await api(row.host, 'metadata', { ...contextPayload(), id: row.id,
      values: { category: categoryValue, description, origin: row.metadata?.origin || row.origin || 'unknown' } });
    await load();
  }

  async function edit(row) {
    const seq = generation;
    const info = await api(row.host, 'editor', { ...contextPayload(), id: row.id });
    if (!alive() || seq !== generation) return;
    editorState = { row, info, content: info.kind === 'skill' ? info.content : '{}' };
    paint();
  }

  function renderEditor() {
    const { row, info } = editorState;
    const area = el('textarea', 'vt-input vt-ac-editor');
    area.setAttribute('aria-label', t('편집 내용'));
    area.value = editorState.content;
    area.addEventListener('input', () => { editorState.content = area.value; });
    const detail = el('section', 'vt-ac-edit');
    detail.append(el('h3', null, row.name), el('p', 'vt-set-help', row.source), el('p', 'vt-set-help', info.note || t('스킬 원본을 편집합니다')), area);
    detail.appendChild(button(t('검토 후 저장'), () => perform(async () => {
      const plan = await api(row.host, 'edit-preview', { ...contextPayload(), id: row.id, revision: info.revision, content: area.value });
      if (!(await confirmDialog(`${plan.name}\n${plan.source}\n${plan.note}`, { title: t('스킬 편집 저장'), okLabel: t('저장') }))) return;
      const result = await applyPlan(row.host, 'edit-apply', plan);
      notice = result.status === 'ok' ? t('설정 저장됨 — 재시작 필요') : t('저장 결과를 확인하지 못했습니다');
      if (result.status === 'ok') editorState = null;
      await load();
    })));
    detail.appendChild(button(t('닫기'), () => { editorState = null; paint(); }));
    host.replaceChildren(detail);
  }

  function item(row) {
    const entry = el('div', 'vt-ac-entry');
    const box = el('details', 'vt-ac-item');
    box.open = expanded.has(rowKey(row));
    box.addEventListener('toggle', () => {
      if (box.open) expanded.add(rowKey(row)); else expanded.delete(rowKey(row));
    });
    const summary = el('summary', 'vt-ac-itemhead');
    const scopeText = row.scope === 'global' ? (row.profile === `${row.context?.home}/.${row.tool}` ? t('기본 유저') : t('프로필 유저')) : row.level === 'local' ? t('프로젝트 개인') : t('프로젝트 공용');
    const contextName = row.project ? row.project.split('/').pop() : row.profile?.split('/').pop();
    // 행 56(P09-b): 1줄 = 이름(mono 13/500) · 종류 태그(mono) · 도구·호스트 · 범위, 2줄 = 원본 위치(mono 11, 말줄임).
    const head = el('span', 'vt-ac-headmain');
    const line1 = el('span', 'vt-ac-line1');
    const kindTag = el('span', 'vt-ac-tag mono', KIND[row.kind]);
    kindTag.dataset.kind = row.kind;
    line1.append(el('strong', null, row.name), kindTag, el('span', 'vt-mcp-meta', `${row.tool} · ${hostLabel(row.host)}`),
      el('span', 'vt-mcp-meta', `${scopeText}${contextName ? ` · ${contextName}` : ''}`));
    head.append(line1, el('span', 'vt-ac-line2', row.source || ''));
    const status = el('span', 'vt-ac-status', row.enabled == null ? t('기본값 또는 확인 불가') : row.enabled ? t('설정상 켜짐') : t('설정상 꺼짐'));
    status.dataset.state = row.enabled == null ? 'unknown' : row.enabled ? 'on' : 'off';
    summary.append(el('span', 'vt-ac-chev', null), head, status);
    summary.querySelector('.vt-ac-chev').innerHTML = icon('chevron-right', 12, 2);
    summary.querySelector('.vt-ac-chev').setAttribute('aria-hidden', 'true');
    box.appendChild(summary);
    const info = el('div', 'vt-ac-itembody');
    info.append(el('p', 'vt-mcp-meta', `${ORIGIN[row.origin] || ORIGIN.unknown} · ${row.metadata?.category || t('미분류')} · ${UPDATE[row.update?.state] || UPDATE.unchecked}`));
    if (row.metadata?.description) info.appendChild(el('p', 'vt-set-help', row.metadata.description));
    info.append(el('p', 'vt-ac-path', row.source), el('p', 'vt-mcp-meta', `${t('프로필')}: ${row.profile}`),
      el('p', 'vt-mcp-meta', `${t('적용 범위')}: ${row.scope === 'global' ? t('이 프로필의 모든 프로젝트') : row.level === 'local' ? t('이 프로젝트의 개인 설정') : t('프로젝트 공용 설정')}`));
    if (row.tool === 'claude' && row.kind === 'mcp') info.appendChild(el('p', 'vt-set-help', t('정의 위치와 별개로 Claude MCP 활성화는 프로젝트별로 저장됩니다')));
    if (row.effective_status === 'shadowed') info.appendChild(el('p', 'vt-set-help', t('더 높은 우선순위 설정이 이 항목을 덮어씁니다')));
    if (row.kind === 'plugin') {
      if (row.version) info.appendChild(el('p', 'vt-mcp-meta', `${t('설치 버전')}: ${row.version}`));
      for (const name of row.skills || []) info.appendChild(el('div', 'vt-ac-child', `${t('제공 스킬')}: ${name}`));
      for (const server of row.mcp_servers || []) info.appendChild(el('div', 'vt-ac-child', `MCP: ${server.name}`));
      info.appendChild(el('p', 'vt-set-help', t('설치·업데이트·제거는 부모 플러그인 단위로 관리합니다')));
    }
    const actions = el('div', 'vt-ac-actions');
    {
      const label = el('label', 'vt-ac-pick');
      const check = el('input');
      check.type = 'checkbox';
      check.checked = selected.has(rowKey(row));
      check.setAttribute('aria-label', t('{name} 선택', { name: row.name }));
      check.addEventListener('change', () => {
        if (check.checked) selected.set(rowKey(row), row); else selected.delete(rowKey(row));
        updateSelection();
      });
      label.append(check, document.createTextNode(t('일괄 관리에 선택')));
      entry.appendChild(label);
    }
    if (row.writable) {
      const quick = button(row.enabled ? t('끄기') : t('켜기'), () => perform(async () => {
        await applySelection(!row.enabled, null, row);
      }), 'vt-chip sm vt-ac-quick');
      entry.appendChild(quick);
    } else info.appendChild(el('p', 'vt-set-help', t('이 항목의 활성화는 해당 CLI에서 관리합니다')));
    if (row.host === 'local') {
      actions.appendChild(button(t('분류·설명'), () => perform(() => classify(row))));
      actions.appendChild(select(t('출처'), Object.entries(ORIGIN), row.origin || 'unknown', (origin) => perform(async () => {
        await api(row.host, 'metadata', { ...contextPayload(), id: row.id, values: { ...row.metadata, origin } });
        await load();
      })));
      if (row.kind !== 'plugin' && !row.linked && (row.kind === 'skill' || row.editable)) actions.appendChild(button(t('원본 편집'), () => { edit(row).catch((e) => showToast(e.message, 'error')); }));
      actions.appendChild(button(t('업데이트 확인'), () => perform(async () => {
        const result = await api(row.host, 'update-check', { ...contextPayload(), id: row.id });
        notice = `${row.name}: ${UPDATE[result.state]}${result.reason ? ` · ${result.reason}` : ''}`;
        await load();
      })));
    }
    info.appendChild(actions);
    box.appendChild(info);
    entry.appendChild(box);
    return entry;
  }

  function updateSelection() {
    const count = host.querySelector('.vt-ac-selected');
    if (count) count.textContent = t('{n}개 선택', { n: selected.size });
    host.querySelectorAll('[data-requires-selection]').forEach((b) => {
      b.disabled = !selected.size || busy || (b.dataset.requiresSelection === 'writable' && [...selected.values()].some((r) => !r.writable))
        || (b.dataset.requiresKnown === 'true' && [...selected.values()].some((r) => typeof r.enabled !== 'boolean'));
    });
  }

  function renderItems(body) {
    const filters = el('div', 'vt-ac-controls vt-ac-filters');
    filters.appendChild(select(t('종류'), [['all', t('모든 종류')], ...Object.entries(KIND)], kind, (v) => { kind = v; paint(); }));
    filters.appendChild(select(t('도구'), [['all', t('모든 도구')], ['claude', 'Claude'], ['codex', 'Codex'], ['agy', 'agy'], ['opencode', 'OpenCode']], tool, (v) => { tool = v; paint(); }));
    filters.appendChild(select(t('적용 범위'), [['all', t('전체')], ['global', t('기본·프로필 유저')], ['local', t('프로젝트 개인')], ['project', t('프로젝트 공용')]], scope, (v) => { scope = v; paint(); }));
    const profiles = [...new Set(rows.map((r) => r.profile))].filter(Boolean).sort();
    if (profiles.length > 1) filters.appendChild(select(t('프로필'), [['all', t('모든 프로필')], ...profiles.map((p) => [p, p])], profile, (v) => { profile = v; paint(); }));
    const categories = [...new Set(rows.map((r) => r.metadata?.category).filter(Boolean))].sort();
    filters.appendChild(select(t('카테고리'), [['all', t('모든 카테고리')], ['uncategorized', t('미분류')], ...categories.map((c) => [c, c])], category, (v) => { category = v; paint(); }));
    const search = el('input', 'vt-input vt-ac-search');
    search.type = 'search'; search.placeholder = t('이름·설명·출처 검색'); search.value = q;
    search.setAttribute('aria-label', t('이름·설명·출처 검색'));
    search.addEventListener('input', () => { q = search.value; renderList(); });
    filters.appendChild(search);
    body.appendChild(filters);
    const bulk = el('div', 'vt-ac-controls vt-ac-bulk');
    bulk.appendChild(el('span', 'vt-ac-selected', t('{n}개 선택', { n: selected.size })));
    for (const [text, enabled] of [[t('선택 켜기'), true], [t('선택 끄기'), false]]) {
      const btn = button(text, () => perform(() => applySelection(enabled)));
      btn.dataset.requiresSelection = 'writable'; bulk.appendChild(btn);
    }
    const save = button(t('프리셋으로 저장'), () => perform(async () => {
      const targets = new Set([...selected.values()].map((r) => r.host));
      if (targets.size !== 1 || !targets.has('local')) throw new Error(t('프리셋은 한 호스트의 항목으로 저장하세요. 원격 자료는 해당 호스트에서 관리합니다.'));
      const name = await promptDialog(t('프리셋 이름'), { title: t('프리셋으로 저장'), okLabel: t('저장') });
      if (!name) return;
      const changes = [...selected.values()].map((r) => ({ id: r.id, enabled: r.enabled !== false }));
      await api('local', 'preset-save', { ...contextPayload(), name, changes });
      notice = t('현재 선택의 켬·끔 상태를 저장했습니다');
      await load();
    }));
    save.dataset.requiresSelection = 'writable'; bulk.appendChild(save);
    save.dataset.requiresKnown = 'true';
    save.title = t('켜짐 여부가 확인되지 않은 항목은 먼저 켬·끔을 지정하세요');
    const categorize = button(t('선택 항목 분류'), () => perform(async () => {
      if ([...selected.values()].some((r) => r.host !== 'local')) throw new Error(t('이 원격 작업은 대상 호스트에서 직접 수행하세요'));
      const value = await promptDialog(t('용도 카테고리 — 예: 개발, 조사, 문서'), { title: t('용도 분류'), okLabel: t('저장') });
      if (value === null) return;
      await api('local', 'metadata-category', { ...contextPayload(), ids: [...selected.values()].map((r) => r.id), category: value });
      selected.clear();
      await load();
    }));
    categorize.dataset.requiresSelection = 'true'; bulk.appendChild(categorize);
    bulk.appendChild(button(t('선택 해제'), () => { selected.clear(); paint(); }));
    body.appendChild(bulk);
    if (!plugins) bulk.appendChild(button(t('플러그인 묶음 불러오기'), () => perform(async () => { plugins = true; await load(); }), 'vt-chip sm vt-ac-plugins'));
    const list = el('div', 'vt-ac-list');
    body.appendChild(list);
    function renderList() {
      list.replaceChildren();
      const query = q.trim().toLocaleLowerCase();
      const visible = rows.filter((r) => (kind === 'all' || r.kind === kind) && (tool === 'all' || r.tool === tool)
        && (profile === 'all' || r.profile === profile)
        && (scope === 'all' || (scope === 'global' ? r.scope === 'global' : scope === 'local' ? r.level === 'local' : r.scope !== 'global' && r.level !== 'local'))
        && (category === 'all' || (category === 'uncategorized' ? !r.metadata?.category : r.metadata?.category === category))
        && (!query || [r.name, r.source, r.profile, r.metadata?.description, r.metadata?.category, ORIGIN[r.origin]].join(' ').toLocaleLowerCase().includes(query)));
      visible.sort((a, b) => `${a.metadata?.category || ''}:${a.name}:${a.kind}`.localeCompare(`${b.metadata?.category || ''}:${b.name}:${b.kind}`));
      list.appendChild(el('p', 'vt-mcp-meta vt-ac-count', t('{n}개 항목', { n: visible.length })));
      for (const row of visible) list.appendChild(item(row));
      if (!visible.length) list.appendChild(el('p', 'vt-set-help', t('이 범위에 표시할 자료가 없습니다. 세션·도구·필터를 확인하세요.')));
      updateSelection();
    }
    renderList();
  }

  function renderPresets(body) {
    body.appendChild(el('p', 'vt-set-help', t('프리셋에 포함된 항목만 변경합니다. 포함되지 않은 항목은 그대로 둡니다.')));
    const presets = snapshots.flatMap((s) => (s.presets || []).map((p) => ({ ...p, host: s.host })));
    if (!presets.length) body.appendChild(el('p', 'vt-set-help', t('목록에서 항목을 선택한 뒤 프리셋으로 저장하세요')));
    for (const preset of presets) {
      const row = el('div', 'vt-ac-preset');
      row.append(el('strong', null, preset.name), el('span', 'vt-mcp-meta', `${hostLabel(preset.host)} · ${preset.changes.length}`));
      row.appendChild(button(t('검토 후 적용'), () => perform(() => applySelection(null, preset))));
      if (preset.host === 'local') row.appendChild(button(t('삭제'), () => perform(async () => {
        if (!(await confirmDialog(t('이 프리셋을 삭제할까요? 설치된 자료는 유지됩니다.'), { title: t('프리셋 삭제'), okLabel: t('삭제') }))) return;
        await api('local', 'preset-delete', { id: preset.id });
        await load();
      })));
      body.appendChild(row);
    }
  }

  function renderEnvironment(body) {
    if (!session || selectedHost !== 'local') {
      body.appendChild(el('p', 'vt-set-help', t('환경변수는 해당 호스트에서 실행 중인 세션을 선택해 관리하세요')));
      return;
    }
    body.appendChild(button(t('환경변수 읽기'), () => perform(async () => { envData = await api('local', 'environment', { session }); })));
    if (!envData) return;
    body.appendChild(el('p', 'vt-set-help', envData.note));
    for (const scope of envData.scopes) {
      const title = { machine: t('머신 공통'), profile: t('프로필 유저'), project: t('프로젝트 전용') }[scope.scope];
      body.appendChild(el('h3', null, title));
      for (const variable of scope.variables) {
        const row = el('div', 'vt-ac-envrow');
        row.appendChild(el('code', null, `${variable.name} · ${t('값 저장됨')}`));
        row.appendChild(button(t('삭제'), () => perform(async () => {
          if (!(await confirmDialog(t('{name}의 저장된 값을 삭제할까요?', { name: variable.name }), { title: t('저장된 값 삭제'), okLabel: t('삭제') }))) return;
          await api('local', 'environment-set', { session, scope: scope.scope, name: variable.name, value: null, revision: envData.context.identity });
          envData = await api('local', 'environment', { session });
        })));
        body.appendChild(row);
      }
      body.appendChild(button(t('변수 추가·갱신'), () => perform(async () => {
        const name = await promptDialog(t('환경변수 이름'));
        if (!name) return;
        const value = await promptDialog(t('환경변수 값 — 저장 후 다시 표시하지 않습니다'), { password: true });
        if (value === null) return;
        if (!(await confirmDialog(`${title}\n${name}\n${t('이 범위의 CLI 재시작 후 적용됩니다')}`, { title: t('환경변수 저장'), okLabel: t('저장') }))) return;
        await api('local', 'environment-set', { session, scope: scope.scope, name, value, revision: envData.context.identity });
        envData = await api('local', 'environment', { session });
        notice = t('설정 저장됨 — 재시작 필요');
      })));
    }
  }

  function renderSync(body) {
    body.appendChild(button(t('동기화 상태 확인'), () => perform(async () => {
      if (selectedHost === 'all') throw new Error(t('호스트를 하나 선택하세요'));
      syncData = await api(selectedHost, 'sync-status');
    })));
    if (!syncData) return;
    body.appendChild(el('p', 'vt-set-help', syncData.note));
    body.appendChild(el('p', 'vt-mcp-meta', syncData.runner ? t('이 호스트가 기존 동기화 실행 주체입니다') : t('이 호스트에는 활성 동기화 스케줄러가 없습니다')));
    body.appendChild(el('p', 'vt-mcp-meta', syncData.running ? t('동기화 실행 중 또는 잠금 기록 있음') : t('동기화 대기')));
    body.appendChild(el('p', 'vt-mcp-meta', t('MCP 충돌 기록 {n}개', { n: syncData.conflict_count })));
    if (syncData.last_log_at) body.appendChild(el('p', 'vt-mcp-meta', `${t('최근 로그 기록')}: ${new Date(syncData.last_log_at * 1000).toLocaleString()}`));
    if (selectedHost === 'local') for (const scheduler of syncData.schedulers) {
      const btn = button(t('기존 동기화 실행'), () => perform(async () => {
        if (!(await confirmDialog(t('기존 peer-sync 정책으로 양방향 동기화를 실행할까요?'), { title: t('동기화 실행'), okLabel: t('실행') }))) return;
        const result = await api('local', 'sync-run', { id: scheduler.id });
        notice = result.note;
        syncData = await api('local', 'sync-status');
      }));
      btn.disabled = syncData.running;
      body.appendChild(btn);
    }
  }

  function renderRequests(body) {
    body.appendChild(el('p', 'vt-set-help', t('다른 호스트의 변경 요청입니다. 이 호스트에서 검토하고 승인한 계획만 적용됩니다.')));
    body.appendChild(button(t('승인 요청 읽기'), () => perform(async () => { requests = (await api('local', 'requests')).requests; })));
    for (const request of requests) {
      const row = el('section', 'vt-ac-preset');
      row.append(el('strong', null, `${request.peer} · ${request.action}`), el('pre', 'vt-ac-review', planText(request.summary) || request.summary.note));
      for (const [decision, label] of [['approve', t('승인 후 적용')], ['deny', t('거절')]]) {
        row.appendChild(button(label, () => perform(async () => {
          if (!(await confirmDialog(`${request.peer}\n${planText(request.summary) || request.summary.note}\n${label}`, { title: label, okLabel: label }))) return;
          // The target server requires both a human cookie and elevation.
          try {
            const result = await vtFetch(`/api/agent-config/requests/${request.id}/${decision}`, { method: 'POST' });
            notice = result.status || result.state || t('처리됨');
          } catch (e) {
            if (e.status !== 401) throw e;
            const password = await promptDialog(t('설정을 변경하려면 비밀번호를 확인하세요'), { password: true, title: t('비밀번호 확인') });
            if (password === null) return;
            await vtFetch('/api/auth/elevate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
            const result = await vtFetch(`/api/agent-config/requests/${request.id}/${decision}`, { method: 'POST' });
            notice = result.status || result.state || t('처리됨');
          }
          requests = (await api('local', 'requests')).requests;
          await load();
        })));
      }
      body.appendChild(row);
    }
  }

  function paint() {
    if (!alive()) return;
    profilePanel?.dispose();
    profilePanel = null;
    host.replaceChildren();
    host.classList.add('vt-agent-config');
    if (editorState) { renderEditor(); return; }
    const top = el('div', 'vt-ac-top');
    const tabs = el('div', 'vt-ac-mode');
    tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('관리 대상'));
    for (const [mode, title] of [['all', t('FarShell 전체')], ['session', t('선택한 세션')]]) {
      const btn = button(title, () => perform(() => changeContext(() => {
        view = mode;
        if (mode === 'session' && selectedHost === 'all') selectedHost = initialContext.host || 'local';
      })), 'vt-chip');
      btn.setAttribute('role', 'tab'); btn.setAttribute('aria-selected', String(view === mode));
      tabs.appendChild(btn);
    }
    top.appendChild(tabs);
    const controls = el('div', 'vt-ac-controls vt-ac-target');
    const hostOptions = hosts.map((h) => [h.id, h.label + (h.online === false ? ` · ${t('연결되지 않음')}` : '')]);
    if (view === 'all') hostOptions.unshift(['all', t('모든 호스트')]);
    controls.appendChild(select(t('호스트'), hostOptions, selectedHost, (v) => perform(() => changeContext(() => { selectedHost = v; session = ''; }))));
    if (view === 'session') {
      const available = hosts.find((h) => h.id === selectedHost)?.sessions || [];
      const entries = [['', t('세션 선택')], ...available.map((s) => [s.name, s.name])];
      if (session && !available.some((s) => s.name === session)) entries.push([session, session]);
      controls.appendChild(select(t('세션'), entries, session, (v) => perform(() => changeContext(() => { session = v; }))));
    }
    top.appendChild(controls);
    top.appendChild(button(t('새로고침'), () => perform(load), 'vt-chip sm vt-ac-refresh'));
    host.appendChild(top);
    const main = el('div', 'vt-ac-main');
    const navigation = el('nav', 'vt-ac-nav');
    navigation.setAttribute('aria-label', t('관리 메뉴'));
    const presetCount = snapshots.reduce((n, s2) => n + (s2.presets || []).length, 0);
    const counts = { items: rows.length, presets: presetCount, requests: requests.length };
    const labels = { items: t('목록·분류'), profiles: t('실행 프로필'), presets: t('프리셋'), environment: t('환경변수'), sync: t('동기화'), requests: t('원격 승인') };
    for (const [key, ic] of NAV) {
      // 이름은 버튼의 글자 그대로 — 아이콘·개수는 aria-hidden이라 접근 이름이 바뀌지 않는다.
      const btn = button(labels[key], () => { section = key; paint(); }, 'vt-ac-navitem');
      btn.setAttribute('aria-current', section === key ? 'page' : 'false');
      const ico = el('span', 'vt-ac-navico');
      ico.setAttribute('aria-hidden', 'true');
      ico.innerHTML = icon(ic, 16, 2);
      btn.prepend(ico);
      if (counts[key]) {
        const n = el('span', 'vt-ac-navcount', String(counts[key]));
        n.setAttribute('aria-hidden', 'true');
        btn.appendChild(n);
      }
      navigation.appendChild(btn);
    }
    main.appendChild(navigation);
    const pane = el('div', 'vt-ac-pane');
    main.appendChild(pane);
    host.appendChild(main);
    const intro = el('div', 'vt-ac-intro');
    if (loading) intro.appendChild(el('p', 'vt-mcp-meta', t('나머지 위치를 확인하는 중…')));
    const current = snapshots.find((s) => s.host === selectedHost)?.selected;
    if (view === 'session' && current) intro.appendChild(el('p', 'vt-ac-context', `${current.tool} · ${current.root}\n${current.project}`));
    if (notice) { const status = el('p', 'vt-ac-notice', notice); status.setAttribute('role', 'status'); intro.appendChild(status); }
    if (errors.length) {
      const details = el('details', 'vt-ac-errors'); details.appendChild(el('summary', null, t('확인하지 못한 항목 {n}개', { n: errors.length })));
      for (const error of errors) details.appendChild(el('p', 'vt-set-help', error));
      intro.appendChild(details);
    }
    if (intro.childElementCount) pane.appendChild(intro);
    const body = el('div', 'vt-ac-body');
    body.dataset.section = section;
    pane.appendChild(body);
    if (section === 'items') renderItems(body);
    else if (section === 'profiles') import('./agent-profiles.js').then((module) => {
      if (alive() && body.isConnected) profilePanel = module.mount(body);
    }).catch((error) => { if (alive() && body.isConnected) body.textContent = error.message; });
    else if (section === 'presets') renderPresets(body);
    else if (section === 'environment') renderEnvironment(body);
    else if (section === 'sync') renderSync(body);
    else renderRequests(body);
    // 바닥 막대 — 안내 한 줄(왼쪽) · 재시작 · 옛 보관함(오른쪽). 재시작 작업 상태도 여기서 말한다.
    const footer = el('div', 'vt-ac-footer');
    footer.appendChild(el('p', 'vt-set-help vt-ac-foothelp', view === 'all'
      ? t('설치된 원본을 한곳에서 관리합니다. 파일은 자동으로 이동하지 않습니다.')
      : t('선택한 세션이 사용하는 프로필과 프로젝트 설정입니다. 저장 상태와 실행 중 반영은 다를 수 있습니다.')));
    if (job) {
      const status = el('div', 'vt-ac-job'); status.setAttribute('role', 'status');
      status.appendChild(el('p', null, `${STATE[job.state] || job.state}${job.reason ? ` · ${job.reason}` : ''}${job.note ? ` · ${job.note}` : ''}`));
      if (job.state === 'waiting') status.appendChild(button(t('재시작 예약 취소'), () => perform(async () => { job = { ...await api(job.host, 'restart-cancel', { job: job.id }), host: job.host }; })));
      footer.appendChild(status);
    }
    if (view === 'session' && session && current) {
      footer.appendChild(el('span', 'vt-mcp-meta', t('동일 대화·프로필로 이어가기')));
      footer.appendChild(button(t('안전할 때 CLI 재시작'), () => perform(scheduleRestart)));
    }
    if (onLegacy) {
      const legacy = button(t('기존 MCP 보관함·상세 관리'), () => { dispose(); onLegacy(); }, 'vt-chip sm vt-ac-legacy');
      const ico = el('span', 'vt-ac-navico');
      ico.setAttribute('aria-hidden', 'true');
      ico.innerHTML = icon('archive', 14, 2);
      legacy.prepend(ico);
      footer.appendChild(legacy);
    }
    host.appendChild(footer);
    updateSelection();
    if (busy) host.querySelectorAll('button, input, select, textarea').forEach((node) => { node.disabled = true; });
  }

  // 옛 보관함(mcp.js)이 같은 본문에 그려지므로 이 화면의 배치 클래스를 거둔다.
  function dispose() { disposed = true; generation++; profilePanel?.dispose(); if (timer) clearTimeout(timer); host.classList.remove('vt-agent-config'); }
  host.replaceChildren(el('p', 'vt-set-help', t('설정과 설치 위치를 확인하는 중…')));
  vtFetch('/api/hosts').then((data) => {
    if (!alive()) return;
    hosts = data.hosts || [];
    if (!hosts.some((h) => h.id === 'local')) hosts.unshift({ id: 'local', label: t('이 호스트'), sessions: [] });
    return load();
  }).catch((e) => {
    if (!alive()) return;
    hosts = [{ id: 'local', label: t('이 호스트'), sessions: [] }];
    notice = e.message;
    load().catch((error) => { if (alive()) { notice = error.message; paint(); } });
  });
  return { dispose };
}
