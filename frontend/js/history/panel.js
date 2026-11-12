import { openDialog, confirmDialog } from '../core/dialog.js';
import { getAction } from '../core/dom.js';
import { closePanel } from '../panels/panel.js';
import { t } from '../core/i18n.js';
import { historyApi, operationId } from './api.js';
import { notifyResumePolicy } from './session-actions.js';

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
  // 수집·한도·백업·복제는 설정 › 「대화 보관」에 있다(세션 로직 점검 단계 6) — 이 창은 검색·열람만 한다.
  const settings = node('p', 'vt-history-meta vt-history-settings-link');
  const settingsLink = node('button', 'vt-btn sm quiet', t('보관 방식 설정 (수집 · 한도 · 백업 · 복제)')); settingsLink.type = 'button';
  settingsLink.addEventListener('click', () => { closePanel('vt-history'); getAction('settings.section')?.('history'); });
  settings.append(settingsLink);
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
          if (!await confirmDialog(t('같은 대화를 새 CLI 실행에서 이어갈까요? 마지막으로 기록된 bypass·YOLO 켜짐/꺼짐과 권한 설정을 복원합니다. 설정을 확인할 수 없으면 시작하지 않습니다. FarShell 실행 권한은 자동 승계되지 않습니다.'), { title: t('대화 이어가기'), okLabel: t('이어가기') })) return;
          const result = await api(path + '/resume', { body: { operation_id: operationId(), ...(options.sessionId ? { session_id: options.sessionId } : {}) } });
          status.textContent = t('CLI를 시작했습니다. 대화 식별자 확인을 기다립니다.'); emitChanged();
          notifyResumePolicy(result);
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
          if (!await confirmDialog(t('선택한 범위에서 기록을 숨기고 재수집을 막습니다. CLI 원본과 원본 보관 바이트는 지우지 않습니다. 계속할까요?'), { title: t('기록 숨기기'), okLabel: t('숨기기'), danger: true })) return;
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
        if (manifest.projection?.omitted_reasons?.length) {
          const reasons = {oversized_record:t('1 MiB를 초과한 행'),invalid_record:t('해석할 수 없는 행'),
            projection_limit:t('색인 크기 한도 초과'),decode_error:t('압축 해제 실패')};
          const label = manifest.raw_complete ? t('원본 전체 보존 · 검색 본문 일부 생략') : t('검색 본문 일부 생략');
          row.append(node('span', 'vt-history-meta', `${label}: ${manifest.projection.omitted_reasons.map(reason => reasons[reason] || t('지원하지 않는 기록 형식')).join(', ')}`));
        }
        row.append(button(t('원본 내보내기'), () => download(manifest.id, item.replica ? item.origin : null)));
        if (!item.replica) row.append(button(t('CLI 원본 복구'), async () => {
          const preview = await api('/api/session-history/preview/restore', { body: { manifest_id: manifest.id } });
          if (!await confirmDialog(t('이 위치에 대화 원본만 복구합니다. 기존 파일과 인증 설정은 덮어쓰지 않습니다.') + '\n' + preview.preview.root + '/' + preview.preview.relative_path, { title: t('원본 복구'), okLabel: t('복구') })) return;
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

  action(() => load());
  return () => { disposed = true; clearTimeout(timer); listAbort?.abort(); detailAbort?.abort(); for (const c of controllers) c.abort(); };
}
