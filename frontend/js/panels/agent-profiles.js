// Direct-host profile management. Only reviewed opaque tokens reach writes.
import { vtFetch } from '../core/api.js';
import { relayHost } from '../core/host-route.js';
import { promptDialog, confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';

function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value != null) node.textContent = value;
  return node;
}

function parseInput(text) {
  try { return JSON.parse(text); }
  catch { throw new Error(t('JSON 형식이 올바르지 않습니다')); }
}

export function mount(host) {
  let disposed = false;
  let busy = false;
  let profiles = [];
  let accounts = [];
  let sources = [];
  let selected = '';
  let source = '';
  let data = null;
  let status = '';
  let sessions = [];
  let jobs = [];
  const alive = () => !disposed && host.isConnected;

  async function api(action, payload = {}, apply = false) {
    if (relayHost() !== 'local') throw new Error(t('프로필과 로그인은 대상 호스트에 직접 접속해 관리하세요'));
    const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
    const path = apply ? '/api/agent-profiles/apply' : `/api/agent-profiles/read/${action}`;
    try { return await vtFetch(path, opts); }
    catch (error) {
      if (error.status !== 401 || !/elevation_required/.test(JSON.stringify(error.data || error.message))) throw error;
      const password = await promptDialog(t('설정을 변경하려면 비밀번호를 확인하세요'), { password: true });
      if (password === null || !alive()) throw new Error(t('변경을 취소했습니다'));
      await vtFetch('/api/auth/elevate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
      return vtFetch(path, opts);
    }
  }

  async function perform(run) {
    if (busy || !alive()) return;
    busy = true;
    host.setAttribute('aria-busy', 'true');
    host.querySelectorAll('button, input, select').forEach(n => { n.disabled = true; });
    try { await run(); } catch (error) { if (alive()) status = error.message || t('프로필 작업을 완료하지 못했습니다'); }
    finally { busy = false; if (alive()) { host.removeAttribute('aria-busy'); paint(); } }
  }

  function button(label, run) {
    const node = el('button', 'vt-chip sm', label);
    node.type = 'button';
    node.addEventListener('click', () => perform(run));
    return node;
  }

  function select(label, entries, value, change) {
    const field = el('label', 'vt-ac-field');
    field.appendChild(el('span', 'vt-mcp-meta', label));
    const node = el('select', 'vt-input');
    node.setAttribute('aria-label', label);
    for (const [key, title] of entries) { const item = el('option', null, title); item.value = key; node.appendChild(item); }
    node.value = value;
    node.addEventListener('change', () => perform(() => change(node.value)));
    field.appendChild(node);
    return field;
  }

  async function load() {
    const [registry, discovered] = await Promise.all([api('list'), api('sources')]);
    if (!alive()) return;
    profiles = registry.profiles || [];
    accounts = registry.accounts || [];
    jobs = registry.jobs || [];
    sources = discovered.sources || [];
    sessions = (registry.sessions || []).map(s => ({ ...s, session: s.name, applied_revision: s.profile_revision }));
    if (!profiles.some(p => p.id === selected)) selected = profiles[0]?.id || '';
    if (!sources.some(s => s.id === source)) source = sources[0]?.id || '';
  }

  async function review(operation, payload) {
    const plan = await api('preview', { operation, payload });
    if (!alive()) return null;
    const detail = { ...plan }; delete detail.token; delete detail.expires_at;
    if (!(await confirmDialog(`${t('변경 내용을 확인하세요')}\n${JSON.stringify(detail, null, 2)}`)) || !alive()) return null;
    const result = await api('', { token: plan.token }, true);
    if (!alive()) return null;
    status = result.note || result.status || result.state || t('처리됨');
    await load();
    return result;
  }

  function paint() {
    if (!alive()) return;
    host.replaceChildren();
    if (relayHost() !== 'local') {
      host.appendChild(el('p', 'vt-set-help', t('프로필과 로그인은 대상 호스트에 직접 접속해 관리하세요')));
      return;
    }
    const top = el('div', 'vt-ac-controls');
    top.appendChild(button(t('프로필 만들기'), async () => {
      const name = await promptDialog(t('프로필 이름'));
      if (!name) return;
      const tool = await promptDialog(t('CLI 이름: claude 또는 codex'), { defaultValue: 'claude' });
      if (!tool) return;
      const existing = profiles.find(p => p.id === selected);
      let account = null;
      if (existing?.tool === tool && await confirmDialog(t('선택한 프로필과 같은 논리 계정으로 묶을까요? 로그인은 별도로 진행합니다.'))) account = existing.account_id;
      const purpose = await confirmDialog(t('로그인·갱신 검증용 테스트 프로필로 만들까요?')) ? 'test' : 'work';
      const result = await review('create', { name, tool, purpose, ...(account ? { account_id: account } : {}) });
      if (result?.id) { selected = result.id; data = null; }
    }));
    top.appendChild(button(t('새로고침'), load));
    top.appendChild(button(t('clauth 대체 준비 확인'), async () => { data = { kind: 'retirement', value: await api('retirement') }; }));
    host.appendChild(top);
    host.appendChild(el('p', 'vt-set-help', t('같은 계정에 여러 프로필을 만들 수 있습니다. 로그인·설정은 프로필마다 분리하며 공통·프로젝트 설정은 상속될 수 있습니다.')));
    if (status) { const notice = el('p', 'vt-ac-notice', status); notice.setAttribute('role', 'status'); host.appendChild(notice); }
    if (!profiles.length) host.appendChild(el('p', 'vt-set-help', t('프로필을 만든 뒤 로그인하고 구성을 가져오세요. 기존 clauth는 그대로 유지됩니다.')));
    const row = profiles.find(p => p.id === selected);
    if (row) {
      host.appendChild(select(t('실행 프로필'), profiles.map(p => [p.id, `${p.name} · ${p.tool}`]), selected, async id => { selected = id; data = null; }));
      const account = accounts.find(a => a.id === row.account_id);
      host.appendChild(el('p', 'vt-mcp-meta', `${account?.label || ''} · ${t('계정 신원 미확인')} · ${t('구성 버전')} ${row.revision}`));
      const actions = el('div', 'vt-ac-controls');
      actions.appendChild(button(t('로그인 상태·방법'), async () => { data = { kind: 'auth', value: await api('auth-status', { profile_id: selected }) }; }));
      actions.appendChild(button(t('이 프로필로 새 세션'), async () => {
        const cwd = await promptDialog(t('작업 폴더의 절대경로'));
        if (!cwd) return;
        const result = await review('launch', { profile_id: selected, revision: row.revision, cwd });
        if (result?.session && !sessions.some(s => s.session === result.session)) sessions.push(result);
      }));
      if (row.tool === 'claude') {
        actions.appendChild(button(t('API 인증 설정'), async () => {
          const method = await promptDialog(t('키 전달 방식: apiKeyHelper, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_API_KEY'), { defaultValue: 'ANTHROPIC_AUTH_TOKEN' });
          if (method === null) return;
          if (!['apiKeyHelper', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'].includes(method)) throw new Error(t('API 키 전달 방식이 올바르지 않습니다'));
          const key = await promptDialog(t('새 API 키·토큰'), { password: true });
          if (!key) return;
          const endpoint = await promptDialog(t('API endpoint: 비워 두면 현재 값 유지'));
          if (endpoint === null) return;
          const env = { ANTHROPIC_AUTH_TOKEN: null, ANTHROPIC_API_KEY: null };
          if (method !== 'apiKeyHelper') env[method] = key;
          await review('provider-edit', { profile_id: selected, revision: row.revision, patch: { api_key: method === 'apiKeyHelper' ? key : null, env, ...(endpoint ? { base_url: endpoint } : {}) } });
        }));
        actions.appendChild(button(t('API 환경변수 수정'), async () => {
          const content = await promptDialog(t('환경변수 변경만 JSON으로 입력하세요. null은 제거하며 기존 비밀값은 표시하지 않습니다'));
          if (content !== null) await review('provider-edit', { profile_id: selected, revision: row.revision, patch: { env: parseInput(content) } });
        }));
        actions.appendChild(button(t('API 설정 제거'), () => review('provider-edit', { profile_id: selected, revision: row.revision, patch: { clear: true } })));
      }
      actions.appendChild(button(t('MCP·스킬·플러그인 읽기'), async () => { data = { kind: 'inventory', value: await api('inventory', { profile_id: selected, plugins: true }) }; }));
      actions.appendChild(button(t('MCP 추가'), async () => {
        const name = await promptDialog(t('MCP 이름'));
        if (!name) return;
        const content = await promptDialog(t('추가할 MCP 정의를 JSON으로 입력하세요'));
        if (content === null) return;
        await review('mcp-add', { profile_id: selected, revision: row.revision, name, definition: parseInput(content) });
      }));
      actions.appendChild(button(t('훅 관리'), async () => { data = { kind: 'hooks', value: await api('hooks', { profile_id: selected }) }; }));
      actions.appendChild(button(t('사용량 읽기'), async () => { data = { kind: 'usage', value: await api('usage') }; }));
      if (row.tool === 'claude') actions.appendChild(button(t('짧은 실제 호출 검증'), () => review('probe', { profile_id: selected, revision: row.revision })));
      if (row.purpose === 'test') actions.appendChild(button(t('사용 프로필로 전환'), () => review('adopt', { profile_id: selected, revision: row.revision })));
      if (row.purpose === 'test') actions.appendChild(button(t('네이티브 갱신 검증 준비'), () => review('refresh-test', { profile_id: selected, revision: row.revision })));
      actions.appendChild(button(t('플러그인 설치'), async () => {
        const plugin = await promptDialog(t('플러그인 이름: plugin@marketplace'));
        if (!plugin) return;
        const path = await promptDialog(t('검토할 로컬 플러그인 폴더의 절대경로'));
        if (path) await review('plugin', { profile_id: selected, revision: row.revision, action: 'install', plugin, path });
      }));
      if (row.state !== 'ready') actions.appendChild(button(t('중단 작업 복구 확인'), () => review('recover', { profile_id: selected, revision: row.revision })));
      host.appendChild(actions);
      for (const job of jobs.filter(j => j.profile_id === selected && ['config-import', 'conversation-import', 'provider-edit'].includes(j.kind) && ['done', 'partial', 'partial-rollback'].includes(j.state))) {
        const recovery = el('div', 'vt-ac-preset');
        recovery.appendChild(el('code', null, `${job.kind} · ${job.state}`));
        recovery.appendChild(button(t('가져오기 되돌리기'), () => review('rollback', { profile_id: selected, revision: row.revision, job_id: job.id })));
        host.appendChild(recovery);
      }
      const compatible = sources.filter(s => s.tool === row.tool);
      if (compatible.length) {
        if (!compatible.some(s => s.id === source)) source = compatible[0].id;
        host.appendChild(select(t('가져올 원본'), compatible.map(s => [s.id, `${s.name} · ${s.kind}`]), source, async id => { source = id; data = null; }));
        const imports = el('div', 'vt-ac-controls');
        imports.appendChild(button(t('구성 가져오기 검토'), () => review('config-import', { profile_id: selected, revision: row.revision, source_id: source })));
        if (row.purpose === 'test' && row.tool === 'claude' && compatible.find(s => s.id === source)?.kind === 'clauth') imports.appendChild(button(t('API 키 포함 테스트 복제 검토'), () => review('config-import', { profile_id: selected, revision: row.revision, source_id: source, include_private: true })));
        imports.appendChild(button(t('기존 대화 목록'), async () => { data = { kind: 'conversations', source_id: source, value: await api('conversations', { source_id: source }) }; }));
        host.appendChild(imports);
      }
    }
    if (data?.kind === 'auth') {
      host.appendChild(el('p', 'vt-set-help', data.value.authenticated ? t('네이티브 CLI 로그인 확인됨') : t('프로필에 먼저 로그인하세요')));
      host.appendChild(el('p', 'vt-set-help', t('이 명령을 해당 호스트의 대화형 터미널에서 실행하세요. 로그인 토큰은 웹에 표시하지 않습니다.')));
      host.appendChild(el('pre', 'vt-ac-review', data.value.login_command));
    } else if (data?.kind === 'conversations' && row) {
      for (const conversation of data.value.conversations || []) {
        const item = el('div', 'vt-ac-preset');
        item.appendChild(el('code', null, `${conversation.conversation} · ${conversation.bytes}`));
        item.appendChild(button(t('대화 복사 검토'), async () => {
          const result = await review('conversation-import', { profile_id: selected, revision: row.revision, source_id: data.source_id, conversation_id: conversation.id });
          if (result) data = { kind: 'copied', value: result };
        }));
        host.appendChild(item);
      }
    } else if (data?.kind === 'copied' && row) {
      host.appendChild(el('p', 'vt-set-help', t('원본을 보존해 복사했습니다. 실제 대화 재개는 아직 확인되지 않았습니다.')));
      host.appendChild(button(t('복사한 대화로 새 세션'), async () => {
        const cwd = await promptDialog(t('작업 폴더의 절대경로'));
        if (!cwd) return;
        const result = await review('launch', { profile_id: selected, revision: row.revision, cwd, conversation: data.value.conversation });
        if (result?.session && !sessions.some(s => s.session === result.session)) sessions.push(result);
      }));
    } else if (data?.kind === 'inventory' && row) {
      for (const resource of data.value.items || []) {
        const item = el('div', 'vt-ac-preset');
        item.append(el('strong', null, `${resource.kind} · ${resource.name}`), el('span', 'vt-mcp-meta', resource.source));
        item.appendChild(el('span', 'vt-mcp-meta', resource.enabled === true ? t('켜짐') : resource.enabled === false ? t('꺼짐') : t('상태 미확인')));
        if (resource.writable) item.appendChild(button(resource.enabled === false ? t('켜기') : t('끄기'), async () => {
          await review('native-toggle', { profile_id: selected, revision: row.revision, changes: [{ id: resource.id, enabled: resource.enabled === false }] });
          data = { kind: 'inventory', value: await api('inventory', { profile_id: selected, plugins: true }) };
        }));
        if (resource.kind === 'mcp' && resource.editable) item.appendChild(button(t('MCP 필드 수정'), async () => {
          const content = await promptDialog(t('변경할 MCP 필드만 JSON으로 입력하세요'));
          if (content === null) return;
          await review('native-edit', { profile_id: selected, revision: row.revision, item_id: resource.id, file_revision: resource.revision, content });
          data = { kind: 'inventory', value: await api('inventory', { profile_id: selected, plugins: true }) };
        }));
        host.appendChild(item);
      }
    } else if (data?.kind === 'hooks' && row) {
      host.appendChild(el('p', 'vt-set-help', data.value.note));
      for (const hook of data.value.hooks || []) {
        const item = el('div', 'vt-ac-preset');
        item.appendChild(el('code', null, `${hook.event} · ${hook.type} · ${hook.sha256.slice(0, 12)}`));
        if (hook.owned) item.appendChild(el('span', 'vt-mcp-meta', t('FarShell 상태 훅')));
        if (hook.writable) item.appendChild(button(t('제거'), async () => {
          await review('hook', { profile_id: selected, revision: row.revision, remove_id: hook.id });
          data = { kind: 'hooks', value: await api('hooks', { profile_id: selected }) };
        }));
        host.appendChild(item);
      }
      host.appendChild(button(t('훅 추가'), async () => {
        const event = await promptDialog(t('훅 이벤트 이름'), { defaultValue: 'Stop' });
        if (!event) return;
        const command = await promptDialog(t('실행할 훅 명령'));
        if (!command) return;
        await review('hook', { profile_id: selected, revision: row.revision, event, command });
        data = { kind: 'hooks', value: await api('hooks', { profile_id: selected }) };
      }));
    } else if (data) {
      host.appendChild(el('pre', 'vt-ac-review', JSON.stringify(data.value, null, 2)));
    }
    for (const session of sessions) {
      const item = el('div', 'vt-ac-preset');
      item.appendChild(el('code', null, `${session.session} · ${session.state}`));
      item.appendChild(button(t('실제 실행 확인'), async () => { Object.assign(session, await api('session-status', { session: session.session })); }));
      host.appendChild(item);
    }
    if (busy) host.querySelectorAll('button, input, select').forEach(n => { n.disabled = true; });
  }

  perform(load);
  return { dispose() { disposed = true; } };
}
