// 설정 › 「대화 보관」(세션 로직 점검 단계 6). 예전에는 대화 기록 창 아래 「수집·복제·백업 설정」 접힘 안에 설명 없이
// 있었다(무엇을 하는지 모르겠다는 지적). 대화 기록 창은 검색·열람만 남기고, 보관 방식은 여기서 바꾼다.
//
//   수집   Claude Code·Codex가 자기 폴더(~/.claude/projects, ~/.codex/sessions …)에 남기는 대화 원본 파일을 이 맥의
//          보관함으로 복사해 둔다. CLI가 원본을 지우거나 정리해도 검색·열람·이어가기·원본 복구가 된다.
//   한도   보관함이 쓰는 디스크 상한. 다 차면 새 수집만 멈추고 있는 것은 지우지 않는다.
//   복제   다른 맥(페어링한 호스트)과 보관본을 주고받는다 — 여러 맥의 대화를 한 맥에서 찾을 때.
//   백업   보관함 전체를 파일 하나로 내려받는다(다른 곳에 보관하거나 옮길 때).
//
// 바꾸는 요청은 모두 승격(비밀번호 재확인)이 필요하다 — historyApi가 물어본다.
import { confirmDialog } from '../../../core/dialog.js';
import { t } from '../../../core/i18n.js';
import { historyApi } from '../../../history/api.js';
import { group } from '../controls.js';

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}
const when = (value) => (value ? new Date(value * 1000).toLocaleString() : t('아직 저장되지 않음'));
const size = (bytes) => `${(Number(bytes || 0) / 1024 / 1024).toFixed(1)} MiB`;
const note = (text) => node('p', 'vt-set-note vt-history-meta', text);

export function renderHistorySection() {
  const root = node('div', 'vt-history-settings-page');
  const status = node('p', 'vt-history-status'); status.setAttribute('role', 'status');
  root.append(status, node('p', 'vt-history-meta', t('불러오는 중…')));
  // 섹션을 옮기면 이 노드는 떨어져 나간다 — 그 뒤 도착한 응답은 버린다(떨어진 노드에 그려도 해는 없지만 쓸모도 없다).
  let shown = false;
  const disposedNow = () => shown && !root.isConnected;

  async function action(fn) {
    if (disposedNow()) return;
    try { status.textContent = ''; await fn(); } catch (error) { if (!disposedNow() && error.name !== 'AbortError') status.textContent = error.message; }
  }
  function button(label, fn, cls = 'vt-btn') {
    const el = node('button', cls, label); el.type = 'button';
    el.addEventListener('click', () => action(async () => {
      if (el.disabled) return;
      el.disabled = true;
      try { await fn(); } finally { if (el.isConnected) el.disabled = false; }
    }));
    return el;
  }
  function gibField(label, value, save) {
    const wrap = node('div', 'vt-history-source-form');
    const lab = node('label', null, label);
    const input = node('input', 'vt-input'); input.type = 'text'; input.inputMode = 'numeric'; input.value = String(value);
    lab.append(input);
    wrap.append(lab, button(t('한도 저장'), async () => {
      if (!/^\d{1,4}$/.test(input.value) || Number(input.value) < 1 || Number(input.value) > 1024) throw new Error(t('1~1024 GiB 범위로 입력하세요'));
      await save(Number(input.value));
    }));
    return wrap;
  }

  async function load() {
    const data = await historyApi('/api/session-history/status');
    shown = shown || root.isConnected;
    if (disposedNow()) return;
    root.replaceChildren(status);

    // ── 수집 ──
    const collect = node('div');
    collect.append(note(t('Claude Code·Codex가 자기 폴더에 남기는 대화 원본 파일을 이 맥의 보관함으로 복사해 둡니다. CLI가 원본을 지우거나 정리해도 대화 기록 창에서 검색·열람·이어가기·원본 복구를 할 수 있습니다. 원본에는 민감한 대화와 도구 출력이 들어 있을 수 있어, 경로는 직접 승인한 것만 수집합니다.')));
    collect.append(node('p', 'vt-history-meta', `${t('마지막 로컬 저장')}: ${when(data.saved_at)}`));
    if (data.worker?.errors) collect.append(node('p', 'vt-history-status', t('일부 기록을 수집하지 못했습니다. 원본 경로와 용량을 확인하세요.')));
    for (const source of data.sources) {
      const row = node('div', 'vt-history-source');
      row.append(node('span', null, `${source.provider} · ${source.root}`), node('span', 'vt-history-meta', source.enabled ? t('수집 중') : t('멈춤')));
      row.append(button(source.enabled ? t('수집 중지') : t('수집 재개'), async () => {
        await historyApi('/api/session-history/settings', { body: { source_id: source.id, revision: source.revision, enabled: !source.enabled } });
        await load();
      }));
      collect.append(row);
    }
    if (!data.sources.length) collect.append(node('p', 'vt-history-meta', t('아직 수집하는 경로가 없습니다.')));
    const sourceForm = node('form', 'vt-history-source-form');
    const kind = node('select', 'vt-input'); kind.setAttribute('aria-label', t('수집할 에이전트'));
    for (const value of ['claude', 'codex']) { const option = node('option', null, value); option.value = value; kind.append(option); }
    const rootInput = node('input', 'vt-input'); rootInput.placeholder = t('CLI 설정 홈 경로'); rootInput.setAttribute('aria-label', t('CLI 설정 홈 경로'));
    sourceForm.append(kind, rootInput, button(t('수집 경로 검토'), async () => {
      const plan = await historyApi('/api/session-history/preview/source', { body: { provider: kind.value, root: rootInput.value } });
      if (!await confirmDialog(t('원본 사본에는 민감한 대화와 도구 출력이 포함될 수 있습니다. 이 경로의 기록을 자동 보존할까요?') + '\n' + plan.preview.root + '\n' + size(plan.preview.bytes), { title: t('원본 사본 자동 보존'), okLabel: t('보존') })) return;
      await historyApi('/api/session-history/apply/source', { body: { token: plan.token } });
      await load();
    }));
    sourceForm.addEventListener('submit', (e) => e.preventDefault());
    collect.append(sourceForm);
    root.append(group(t('수집'), collect));

    // ── 한도 ──
    const limits = node('div');
    limits.append(note(t('보관함이 이 맥 디스크를 얼마나 쓸지 정합니다. 한도에 도달하면 새 수집을 멈춥니다. 이미 보관한 원본은 자동으로 지우지 않습니다.')));
    limits.append(gibField(t('보관 한도 (GiB)'), data.quota_bytes / 1024 ** 3, async (gib) => {
      await historyApi('/api/session-history/settings', { body: { quota_bytes: gib * 1024 ** 3 } }); await load();
    }));
    limits.append(gibField(t('복제 수신 한도 (GiB)'), data.replica_quota_bytes / 1024 ** 3, async (gib) => {
      await historyApi('/api/session-history/settings', { body: { replica_quota_bytes: gib * 1024 ** 3 } }); await load();
    }));
    root.append(group(t('보관 한도'), limits));

    // ── 백업 ──
    const backup = node('div');
    backup.append(note(t('보관함 전체(원본 사본과 세션 기록)를 파일 하나로 내려받습니다. 다른 곳에 따로 보관하거나 다른 맥으로 옮길 때 씁니다. 파일에는 대화 원문이 들어 있으니 안전한 곳에 두세요.')));
    backup.append(button(t('전체 기록 백업 내보내기'), async () => {
      const result = await historyApi('/api/session-history/download', { body: { backup: true } });
      const link = node('a'); link.href = result.url; link.download = result.filename; document.body.append(link); link.click(); link.remove();
    }));
    root.append(group(t('백업'), backup));

    // ── 복제 ──
    const replication = node('div');
    replication.append(note(t('페어링한 다른 맥과 보관본을 주고받습니다. 여러 맥에서 한 대화를 한 맥(주 Mac)에서 모두 찾고 싶을 때만 켭니다. 양쪽 서버에서 공개 정보와 방향을 확인하고 승인해야 연결됩니다.')));
    if (data.replication_worker?.state === 'degraded') replication.append(node('p', 'vt-history-status', t('복제가 지연되고 있습니다. 연결과 상대 서버의 수신 승인을 확인하세요.')));
    for (const connection of data.replication) {
      const row = node('div', 'vt-history-source');
      row.append(node('p', 'vt-history-meta', `${connection.peer} · ${connection.direction === 'send' ? t('보내기') : t('받기')} · ${connection.enabled ? t('활성') : t('중지')}`));
      if (connection.enabled) row.append(button(t('연결 해제'), async () => {
        await historyApi('/api/session-history/replication/settings', { body: { ...connection, enabled: false } });
        await load();
      }));
      replication.append(row);
    }
    for (const checkpoint of data.checkpoints) replication.append(node('p', 'vt-history-meta', `${checkpoint.peer} · ${t('마지막 중앙 복제')}: ${when(checkpoint.updated)}`));
    const connect = node('details'); connect.append(node('summary', null, t('새 복제 연결')));
    const identity = node('pre', 'vt-history-key');
    connect.append(button(t('이 서버의 복제 공개 정보'), async () => {
      identity.textContent = JSON.stringify(await historyApi('/api/session-history/replication/identity', { body: {} }), null, 2);
    }), identity);
    const sourceSelect = node('select', 'vt-input'); sourceSelect.setAttribute('aria-label', t('송신할 기록 원본'));
    for (const source of [{ id: '*', root: t('등록된 원본 전체') }, ...data.sources]) {
      const option = node('option', null, source.root); option.value = source.id; sourceSelect.append(option);
    }
    const fields = {};
    for (const [key, label] of [['peer', t('페어링된 호스트 ID')], ['origin', t('상대 보관함 식별자')], ['public_key', t('상대 복제 공개키')]]) {
      const input = node('input', 'vt-input'); input.placeholder = label; input.setAttribute('aria-label', label); fields[key] = input; connect.append(input);
    }
    const direction = node('select', 'vt-input'); direction.setAttribute('aria-label', t('복제 방향'));
    for (const [value, label] of [['send', t('이 서버에서 주 Mac으로 보내기')], ['receive', t('다른 Mac의 기록 받기')]]) {
      const option = node('option', null, label); option.value = value; direction.append(option);
    }
    direction.addEventListener('change', () => { sourceSelect.disabled = direction.value === 'receive'; });
    connect.append(sourceSelect, direction, button(t('복제 연결 승인'), async () => {
      if (!await confirmDialog(t('양쪽 서버에서 공개 정보와 방향을 확인하세요. 원본 사본이 이 연결을 통해 복제됩니다.'), { title: t('복제 연결 승인'), okLabel: t('승인') })) return;
      const previous = data.replication.find((r) => r.peer === fields.peer.value);
      await historyApi('/api/session-history/replication/settings', { body: { peer: fields.peer.value, origin: fields.origin.value,
        public_key: fields.public_key.value, direction: direction.value, sources: direction.value === 'send' ? [sourceSelect.value] : ['*'],
        enabled: true, revision: previous?.revision || 0 } });
      await load();
    }));
    connect.append(button(t('복제 수신 키 교체'), async () => {
      if (!await confirmDialog(t('이전 키는 보존합니다. 새 공개키를 송신 호스트에서 다시 확인하고 승인해야 합니다. 교체할까요?'), { title: t('복제 수신 키 교체'), okLabel: t('교체') })) return;
      identity.textContent = JSON.stringify(await historyApi('/api/session-history/replication/settings', { body: { rotate_key: true } }), null, 2);
    }));
    replication.append(connect);
    root.append(group(t('다른 맥과 복제'), replication));

    // ── 고급: 옛 실행 권한 다시 연결 ──
    const advanced = node('div');
    advanced.append(note(t('예전 버전에서 세션 실행에 붙어 있던 권한을 지금 확인된 실행에 다시 연결합니다. 고른 것만 연결하고, 새 실행으로 자동 승계되지 않습니다. 보통은 쓸 일이 없습니다.')));
    const migration = node('div', 'vt-history-migration');
    migration.append(button(t('기존 실행 권한 검토'), async () => {
      const plan = await historyApi('/api/session-history/preview/identity', { body: {} });
      migration.replaceChildren(node('p', null, t('선택한 기존 권한만 현재 실행에 다시 연결합니다. 새 실행으로 자동 승계되지 않습니다.')));
      const choices = [];
      for (const record of plan.preview.records) {
        const label = node('label', 'vt-history-filter');
        const check = node('input'); check.type = 'checkbox'; check.disabled = !record.available;
        label.append(check, document.createTextNode(`${record.subject} → ${record.resource} · ${(record.scopes || []).join(', ')}`));
        if (!record.available) label.append(document.createTextNode(' · ' + t('현재 실행 확인 불가')));
        migration.append(label); choices.push({ id: record.id, check });
      }
      if (!choices.length) migration.append(node('p', null, t('다시 연결할 기존 권한이 없습니다')));
      else migration.append(button(t('선택한 권한 연결'), async () => {
        const selected = choices.filter((x) => x.check.checked && !x.check.disabled).map((x) => x.id);
        if (!selected.length) throw new Error(t('연결할 권한을 선택하세요'));
        if (!await confirmDialog(t('선택한 권한을 현재 확인된 실행에 연결할까요?'), { title: t('권한 연결'), okLabel: t('연결') })) return;
        await historyApi('/api/session-history/apply/identity', { body: { token: plan.token, selected } });
        await load();
      }));
    }));
    advanced.append(migration);
    root.append(group(t('고급'), advanced));
  }
  void action(load);
  return root;
}
