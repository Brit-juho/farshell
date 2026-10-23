import { openDialog, confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { historyApi, operationId } from './api.js';

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}
const when = value => value ? new Date(value * 1000).toLocaleString() : t('아직 저장되지 않음');
const size = bytes => `${(Number(bytes || 0) / 1024 / 1024).toFixed(1)} MiB`;

export function openHistory(options = {}) {
  let disposed = false, listRequest = 0, detailRequest = 0, timer;
  let listAbort, detailAbort, selected, next = null, messageNext = null;
  const controllers = new Set();
  const { body } = openDialog({ id: 'vt-history', title: t('대화 보관함'), bodyHTML: '',
    onClose: () => { disposed = true; clearTimeout(timer); listAbort?.abort(); detailAbort?.abort();
      for (const c of controllers) c.abort(); } });
  if (!body) return;
  body.closest('#vt-history')?.classList.add('vt-history-panel');
  body.classList.add('vt-history');
  body.replaceChildren();
  const notice = node('p', 'vt-history-notice', t('이 서버에 저장된 원본 사본과 중앙 복제본을 검색합니다. 기록 열람은 CLI를 실행하지 않습니다.'));
  const status = node('p', 'vt-history-status'); status.setAttribute('role', 'status');
  const tools = node('form', 'vt-history-tools');
  const search = node('input', 'vt-input'); search.type = 'search'; search.value = options.search || ''; search.placeholder = t('대화·코드·경로 검색'); search.setAttribute('aria-label', t('대화·코드·경로 검색'));
  const provider = node('select', 'vt-input'); provider.setAttribute('aria-label', t('에이전트 종류'));
  for (const [value, label] of [['', t('모든 에이전트')], ['claude', 'Claude Code'], ['codex', 'Codex']]) {
    const option = node('option', null, label); option.value = value; provider.append(option);
  }
  const archived = node('input'); archived.type = 'checkbox';
  const archivedLabel = node('label', 'vt-history-filter'); archivedLabel.append(archived, document.createTextNode(t('보관된 대화')));
  const settings = node('details', 'vt-history-settings'); settings.append(node('summary', null, t('수집·복제·백업 설정')));
  const settingsBody = node('div'); settings.append(settingsBody);
  const split = node('div', 'vt-history-split');
  const list = node('div', 'vt-history-list'); list.setAttribute('aria-label', t('대화 목록'));
  const detail = node('div', 'vt-history-detail'); detail.append(node('p', 'vt-history-empty', t('대화를 선택하세요')));
  split.append(list, detail); tools.append(search, provider, archivedLabel);
  body.append(notice, tools, status, settings, split);

  async function api(path, options = {}) {
    const controller = new AbortController(); controllers.add(controller);
    try { return await historyApi(path, { ...options, signal: options.signal || controller.signal }); }
    finally { controllers.delete(controller); }
  }
  async function action(fn) {
    if (disposed) return;
    try { status.textContent = ''; await fn(); }
    catch (error) { if (!disposed && error.name !== 'AbortError') status.textContent = error.message; }
  }
  function button(label, fn, cls = 'vt-btn') {
    const el = node('button', cls, label); el.type = 'button';
    el.addEventListener('click', () => action(async () => {
      if (el.disabled) return;
      el.disabled = true;
      try { await fn(); } finally { if (el.isConnected) el.disabled = false; }
    })); return el;
  }
  function emitChanged() { window.dispatchEvent(new CustomEvent('vt:catalog-changed')); }
  async function load(append = false) {
    const request = ++listRequest;
    listAbort?.abort(); listAbort = new AbortController();
    const params = new URLSearchParams({ search: search.value, provider: provider.value, archived: String(archived.checked) });
    if (append && next) params.set('after', next);
    const data = await api('/api/session-history?' + params, { signal: listAbort.signal });
    if (disposed || request !== listRequest) return;
    if (!append) list.replaceChildren();
    list.querySelector('.vt-history-more')?.remove();
    for (const item of data.items) {
      const row = button(item.title || item.native_id, () => select(item), 'vt-history-item');
      row.dataset.historyKey = item.key;
      row.setAttribute('aria-pressed', selected?.key === item.key ? 'true' : 'false');
      row.append(node('span', 'vt-history-meta', [item.provider, item.replica ? t('중앙 복제본') : t('로컬 보관본'), item.cwd].filter(Boolean).join(' · ')));
      list.append(row);
    }
    next = data.next;
    if (next) list.append(button(t('더 보기'), () => load(true), 'vt-btn vt-history-more'));
    if (!data.items.length && !append) list.append(node('p', 'vt-history-empty', t('조건에 맞는 대화가 없습니다')));
  }
  tools.addEventListener('submit', e => { e.preventDefault(); action(() => load()); });
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => action(() => load()), 300); });
  provider.addEventListener('change', () => action(() => load())); archived.addEventListener('change', () => action(() => load()));

  async function select(item, append = false) {
    selected = item;
    for (const row of list.querySelectorAll('.vt-history-item')) row.setAttribute('aria-pressed', row.dataset.historyKey === item.key ? 'true' : 'false');
    const request = ++detailRequest;
    detailAbort?.abort(); detailAbort = new AbortController();
    const path = item.replica ? `/api/session-history/replicas/${item.origin}/${item.id}` : `/api/session-history/conversations/${item.id}`;
    const data = await api(path + (append && messageNext ? `?after=${messageNext}` : ''), { signal: detailAbort.signal });
    if (disposed || request !== detailRequest) return;
    if (!append) {
      detail.replaceChildren(node('h3', null, data.conversation.title || data.conversation.native_id));
      detail.append(node('p', 'vt-history-meta', `${data.conversation.provider} · ${data.conversation.cwd || ''}`));
      const controls = node('div', 'vt-history-actions');
      if (!item.replica) {
        controls.append(button(t('이어가기'), async () => {
          if (!await confirmDialog(t('같은 대화를 새 CLI 실행에서 이어갈까요? 이전 실행 권한은 자동 승계되지 않습니다.'))) return;
          const result = await api(path + '/resume', { body: { operation_id: operationId(), ...(options.sessionId ? { session_id: options.sessionId } : {}) } });
          status.textContent = t('CLI를 시작했습니다. 대화 식별자 확인을 기다립니다.'); emitChanged();
          await window.vtOpenSessionFromRail?.({ tmuxName: result.name, catalogSessionId: result.session_id, runtimeId: result.runtime_id });
        }));
        controls.append(button(archived.checked ? t('보관 해제') : t('대화 보관'), async () => {
          await api(path, { body: { archived: !archived.checked } }); await load();
        }));
      }
      if (!item.replica) {
        const scope = node('select', 'vt-input'); scope.setAttribute('aria-label', t('기록 삭제 범위'));
        for (const [value, label] of [['local', t('이 호스트의 기록만')], ['replicas', t('이 호스트와 승인된 복제본')]]) {
          const option = node('option', null, label); option.value = value; scope.append(option);
        }
        controls.append(scope, button(t('기록 삭제'), async () => {
          if (!await confirmDialog(t('선택한 범위에서 기록을 숨기고 재수집을 막습니다. CLI 원본과 원본 보관 바이트는 지우지 않습니다. 계속할까요?'), {danger:true})) return;
          const result = await api(path, {body:{deleted:true,delete_scope:scope.value}});
          if (disposed) return;
          detail.replaceChildren(); selected = null;
          status.textContent = scope.value === 'replicas' && Object.keys(result.replica_delivery || {}).length
            ? t('로컬 기록을 삭제했습니다. 복제본 삭제 확인은 상대 호스트 연결 뒤 완료됩니다.') : t('기록을 삭제했습니다');
          await load();
        }));
      }
      detail.append(controls);
      const manifests = node('details', 'vt-history-manifests'); manifests.append(node('summary', null, t('원본 보관 상태')));
      for (const manifest of data.manifests) {
        const row = node('div', 'vt-history-manifest');
        row.append(node('span', 'vt-history-meta', `${when(manifest.created)} · ${size(manifest.length)} · ${({saved:t('저장 완료'),indexing:t('색인 중'),partial:t('일부 저장'),source_missing:t('원본 없음')})[manifest.state] || t('검증된 복제본')}`));
        row.append(button(t('원본 내보내기'), () => download(manifest.id, item.replica ? item.origin : null)));
        if (!item.replica) row.append(button(t('CLI 원본 복구'), async () => {
          const preview = await api('/api/session-history/preview/restore', { body: { manifest_id: manifest.id } });
          if (!await confirmDialog(t('이 위치에 대화 원본만 복구합니다. 기존 파일과 인증 설정은 덮어쓰지 않습니다.') + '\n' + preview.preview.root + '/' + preview.preview.relative_path)) return;
          await api('/api/session-history/apply/restore', { body: { token: preview.token } });
          status.textContent = t('원본 파일을 복구했습니다. 실제 CLI 대화 재개는 별도로 확인해야 합니다.');
        }));
        manifests.append(row);
      }
      detail.append(manifests);
      if (data.children?.length || data.native_children?.length) {
        const children = node('details'); children.append(node('summary', null, t('에이전트 내부')));
        for (const child of data.children || []) children.append(button(child.title || child.native_id, () => select({...child,key: `${data.conversation.host}:${child.id}`,replica:!!item.replica,origin:item.origin})));
        for (const child of data.native_children || []) children.append(node('p','vt-history-meta', child.detail?.description || child.native_id));
        detail.append(children);
      }
      const messages = node('div', 'vt-history-messages'); detail.append(messages);
    }
    const messages = detail.querySelector('.vt-history-messages'); messages.querySelector('.vt-history-more')?.remove();
    for (const message of data.messages) {
      const row = node('section', 'vt-history-message');
      const label = ({ user: t('사용자'), assistant: t('에이전트'), tool_call: t('도구 호출'), tool_result: t('도구 결과') })[message.role];
      row.append(node('h4', null, label), node('pre', null, message.body)); messages.append(row);
    }
    messageNext = data.next;
    if (messageNext) messages.append(button(t('이후 메시지 보기'), () => select(item, true), 'vt-btn vt-history-more'));
    if (!data.messages.length && !append) messages.append(node('p', 'vt-history-empty', t('아직 해석된 메시지가 없습니다. 원본 보관 상태를 확인하세요.')));
  }

  async function download(manifestId, origin) {
    const data = await api('/api/session-history/download', { body: { manifest_id: manifestId, origin } });
    if (!disposed) {
      const link = node('a'); link.href = data.url; link.download = data.filename;
      document.body.append(link); link.click(); link.remove();
    }
  }

  async function loadSettings() {
    const data = await api('/api/session-history/status');
    if (disposed) return;
    settingsBody.replaceChildren();
    settingsBody.append(node('p', 'vt-history-meta', `${t('마지막 로컬 저장')}: ${when(data.saved_at)}`));
    const migration = node('div','vt-history-migration');
    migration.append(button(t('기존 실행 권한 검토'),async () => {
      const plan = await api('/api/session-history/preview/identity',{body:{}});
      migration.replaceChildren(node('p',null,t('선택한 기존 권한만 현재 실행에 다시 연결합니다. 새 실행으로 자동 승계되지 않습니다.')));
      const choices=[];
      for (const record of plan.preview.records) {
        const label=node('label','vt-history-filter');
        const check=node('input'); check.type='checkbox'; check.disabled=!record.available;
        label.append(check,document.createTextNode(`${record.subject} → ${record.resource} · ${(record.scopes || []).join(', ')}`));
        if (!record.available) label.append(document.createTextNode(' · '+t('현재 실행 확인 불가')));
        migration.append(label); choices.push({id:record.id,check});
      }
      if (!choices.length) migration.append(node('p',null,t('다시 연결할 기존 권한이 없습니다')));
      else migration.append(button(t('선택한 권한 연결'),async () => {
        const selected=choices.filter(x=>x.check.checked && !x.check.disabled).map(x=>x.id);
        if (!selected.length) throw new Error(t('연결할 권한을 선택하세요'));
        if (!await confirmDialog(t('선택한 권한을 현재 확인된 실행에 연결할까요?'))) return;
        await api('/api/session-history/apply/identity',{body:{token:plan.token,selected}});
        await loadSettings();
      }));
    }));
    settingsBody.append(migration);

    if (data.worker?.errors) settingsBody.append(node('p', 'vt-history-status', t('일부 기록을 수집하지 못했습니다. 원본 경로와 용량을 확인하세요.')));
    for (const source of data.sources) {
      const row = node('div', 'vt-history-source'); row.append(node('span', null, `${source.provider} · ${source.root}`));
      row.append(button(source.enabled ? t('수집 중지') : t('수집 재개'), async () => {
        await api('/api/session-history/settings', { body: { source_id: source.id, revision: source.revision, enabled: !source.enabled } });
        await loadSettings();
      })); settingsBody.append(row);
    }
    const sourceForm = node('form', 'vt-history-source-form');
    const kind = node('select', 'vt-input'); kind.setAttribute('aria-label', t('수집할 에이전트'));
    for (const value of ['claude', 'codex']) { const option = node('option', null, value); option.value = value; kind.append(option); }
    const root = node('input', 'vt-input'); root.placeholder = t('CLI 설정 홈 경로'); root.setAttribute('aria-label', t('CLI 설정 홈 경로'));
    const add = button(t('수집 경로 검토'), async () => {
      const plan = await api('/api/session-history/preview/source', { body: { provider: kind.value, root: root.value } });
      if (!await confirmDialog(t('원본 사본에는 민감한 대화와 도구 출력이 포함될 수 있습니다. 이 경로의 기록을 자동 보존할까요?') + '\n' + plan.preview.root + '\n' + size(plan.preview.bytes))) return;
      await api('/api/session-history/apply/source', { body: { token: plan.token } }); await loadSettings(); await load();
    }); sourceForm.append(kind, root, add); sourceForm.addEventListener('submit', e => e.preventDefault()); settingsBody.append(sourceForm);
    const quotaRow = node('div', 'vt-history-source-form');
    const quotaLabel = node('label', null, t('보관 한도 (GiB)'));
    const quota = node('input', 'vt-input'); quota.type = 'text'; quota.inputMode = 'numeric'; quota.value = String(data.quota_bytes / 1024**3); quotaLabel.append(quota);
    quotaRow.append(quotaLabel, button(t('한도 저장'), async () => {
      if (!/^\d{1,4}$/.test(quota.value) || Number(quota.value) < 1 || Number(quota.value) > 1024) throw new Error(t('1~1024 GiB 범위로 입력하세요'));
      await api('/api/session-history/settings', { body: { quota_bytes: Number(quota.value) * 1024**3 } }); await loadSettings();
    })); settingsBody.append(quotaRow);
    const replicaLabel = node('label', null, t('복제 수신 한도 (GiB)'));
    const replicaQuota = node('input', 'vt-input'); replicaQuota.type = 'text'; replicaQuota.inputMode = 'numeric';
    replicaQuota.value = String(data.replica_quota_bytes / 1024**3); replicaLabel.append(replicaQuota);
    const replicaRow = node('div', 'vt-history-source-form');
    replicaRow.append(replicaLabel, button(t('한도 저장'), async () => {
      if (!/^\d{1,4}$/.test(replicaQuota.value) || Number(replicaQuota.value) < 1 || Number(replicaQuota.value) > 1024) throw new Error(t('1~1024 GiB 범위로 입력하세요'));
      await api('/api/session-history/settings', {body:{replica_quota_bytes:Number(replicaQuota.value)*1024**3}}); await loadSettings();
    })); settingsBody.append(replicaRow);
    settingsBody.append(button(t('전체 기록 백업 내보내기'), async () => {
      const result = await api('/api/session-history/download', { body: { backup: true } });
      const link = node('a'); link.href = result.url; link.download = result.filename; document.body.append(link); link.click(); link.remove();
    }));
    settingsBody.append(node('p', 'vt-history-meta', t('한도에 도달하면 새 수집을 멈춥니다. 기존 원본 보관본은 자동 삭제하지 않습니다.')));
    const replication = node('details'); replication.append(node('summary', null, t('중앙 복제 연결')));
    const identity = node('pre', 'vt-history-key');
    replication.append(button(t('복제 수신 키 교체'),async () => {
      if (!await confirmDialog(t('이전 키는 보존합니다. 새 공개키를 송신 호스트에서 다시 확인하고 승인해야 합니다. 교체할까요?'))) return;
      identity.textContent=JSON.stringify(await api('/api/session-history/replication/settings',{body:{rotate_key:true}}),null,2);
    }));
    if (data.replication_worker?.state==='degraded') replication.append(node('p','vt-history-status',t('복제가 지연되고 있습니다. 연결과 상대 서버의 수신 승인을 확인하세요.')));
    const sourceSelect=node('select','vt-input');sourceSelect.setAttribute('aria-label',t('송신할 기록 원본'));
    for (const source of [{id:'*',root:t('등록된 원본 전체')},...data.sources]) {
      const option=node('option',null,source.root);option.value=source.id;sourceSelect.append(option);
    }
    replication.append(sourceSelect);
    replication.append(button(t('이 서버의 복제 공개 정보'), async () => { identity.textContent = JSON.stringify(await api('/api/session-history/replication/identity', { body: {} }), null, 2); }), identity);
    const fields = {};
    for (const [key, label] of [['peer', t('페어링된 호스트 ID')], ['origin', t('상대 보관함 식별자')], ['public_key', t('상대 복제 공개키')]]) {
      const input = node('input', 'vt-input'); input.placeholder = label; input.setAttribute('aria-label', label); fields[key] = input; replication.append(input);
    }
    const direction = node('select', 'vt-input'); direction.setAttribute('aria-label', t('복제 방향'));
    for (const [value, label] of [['send', t('이 서버에서 주 Mac으로 보내기')], ['receive', t('다른 Mac의 기록 받기')]]) { const option = node('option', null, label); option.value = value; direction.append(option); }
    direction.addEventListener('change',()=>{sourceSelect.disabled=direction.value==='receive';});
    replication.append(direction, button(t('복제 연결 승인'), async () => {
      if (!await confirmDialog(t('양쪽 서버에서 공개 정보와 방향을 확인하세요. 원본 사본이 이 연결을 통해 복제됩니다.'))) return;
      const previous = data.replication.find(r => r.peer === fields.peer.value);
      await api('/api/session-history/replication/settings', { body: { peer: fields.peer.value, origin: fields.origin.value,
        public_key: fields.public_key.value, direction: direction.value, sources:direction.value==='send'?[sourceSelect.value]:['*'], enabled: true, revision: previous?.revision || 0 } }); await loadSettings();
    }));
    for (const connection of data.replication) {
      const row = node('div','vt-history-source');
      row.append(node('p', 'vt-history-meta', `${connection.peer} · ${connection.direction} · ${connection.enabled ? t('활성') : t('중지')}`));
      if (connection.enabled) row.append(button(t('연결 해제'), async () => {
        await api('/api/session-history/replication/settings', {body:{...connection,enabled:false}});
        await loadSettings();
      }));
      replication.append(row);
    }
    for (const checkpoint of data.checkpoints) replication.append(node('p', 'vt-history-meta', `${checkpoint.peer} · ${t('마지막 중앙 복제')}: ${when(checkpoint.updated)}`));
    settingsBody.append(replication);
  }
  settings.addEventListener('toggle', () => { if (settings.open) action(loadSettings); });
  action(() => load());
  return () => { disposed = true; clearTimeout(timer); listAbort?.abort(); detailAbort?.abort(); for (const c of controllers) c.abort(); };
}
