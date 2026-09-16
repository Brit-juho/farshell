// 설정 →「MCP」 섹션 (97번 1단계 4/n). **지연 청크** — settings.js는 스텁만
// 갖고 이 파일은 섹션을 열 때 처음 내려온다(app.js 상한까지 여유가 410B뿐이다).
//
// 이 화면이 하는 일은 딱 둘이다: 지금 어디에 무엇이 켜져 있는지 **보여주기**,
// 그리고 **켜고 끄기**. 서버 정의를 만들거나 고치지 않는다 — 그건 각 CLI의
// 공식 명령(`claude mcp add` 등)이 이미 한다.
//
// ## 이 화면의 핵심은 "정직함"이다 (97번 §4)
//
// 네 도구 다 설정 파일을 **세션 시작 시 한 번만** 읽는다. 그래서 여기서 껐다고
// 해서 지금 떠 있는 창에서 즉시 꺼지는 게 아니다. 토글 스위치만 덜렁 그리면
// 사용자는 "껐으니 이제 안 불린다"고 믿게 되는데 그게 사실이 아니다. 그래서:
//   - 도구별 반영 시점(`facts`)을 항상 같이 보여준다
//   - **모르는 건 모른다고 쓴다** — `off_is_immediate: null`을 "즉시 꺼짐"으로
//     둔갑시키지 않는다
//   - 쓰기 결과가 `unknown`이면 성공 표시를 하지 않는다
import { vtFetch } from '../core/api.js';
import { showToast } from '../ui/toast.js';

const TOOL_LABEL = { claude: 'Claude', codex: 'Codex', agy: 'agy' };
const SCOPE_LABEL = { global: '전역', local: '이 워크트리' };

// 워크트리 id는 settings.js가 넘겨준다 — 이 지연 청크가 layout/store.js를
// 직접 import하면 탭 상태가 복제되고 지연 분리가 무너진다(실측).
let _worktreeId = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function help(text) {
  return el('div', 'vt-set-help', text);
}

/** 도구가 "언제 반영되는지"를 사실 그대로. 모르면 모른다고 쓴다. */
function applyNote(fact) {
  if (!fact) return '';
  if (fact.hot_reload === true) return '바로 반영됩니다.';
  return fact.apply || '다음 세션부터 반영됩니다.';
}

/** 끄기가 즉시인지 — `null`(모름)을 안전한 쪽으로 오해하게 두지 않는다. */
function offNote(fact) {
  if (!fact) return '';
  if (fact.off_is_immediate === false) {
    return fact.off_warning
      || '이미 열려 있는 세션은 재시작 전까지 계속 호출할 수 있습니다.';
  }
  if (fact.off_is_immediate == null) {
    return '끈 것이 실행 중인 세션에 즉시 적용되는지는 확인되지 않았습니다 — 재시작을 권합니다.';
  }
  return '';
}

async function elevate() {
  const pw = prompt('MCP 설정을 바꾸려면 비밀번호 확인이 필요합니다.');
  if (pw === null) return false;
  try {
    await vtFetch('/api/auth/elevate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    return true;
  } catch (_) {
    showToast('비밀번호 확인 실패', 'error');
    return false;
  }
}

async function postToggle(payload) {
  return vtFetch('/api/mcp/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** 토글 한 번. 승격이 필요하면 한 번 묻고 재시도한다. */
async function toggle(entry, enabled) {
  const payload = {
    tool: entry.tool,
    name: entry.name,
    enabled,
    scope: entry.scope,
    shared: !!entry.shared,
    worktree: entry.worktree_id || _worktreeId,
  };
  try {
    return await postToggle(payload);
  } catch (e) {
    if (e.status === 401 && /elevation_required/.test(e.message || '')) {
      if (!(await elevate())) return null;
      return postToggle(payload);
    }
    throw e;
  }
}

function toggleButton(entry, facts, onDone) {
  const btn = el('button', 'vt-mcp-toggle');
  const paint = () => {
    btn.textContent = `${SCOPE_LABEL[entry.scope] || entry.scope} · ${entry.enabled ? '켬' : '끔'}`;
    btn.dataset.on = entry.enabled ? '1' : '0';
  };
  paint();

  btn.addEventListener('click', async () => {
    const next = !entry.enabled;
    btn.disabled = true;
    try {
      const r = await toggle(entry, next);
      if (!r) return;                       // 사용자가 비밀번호 입력을 취소했다
      if (r.status === 'unknown') {
        // 썼는지 확인하지 못했다 — 성공 표시를 하면 안 된다. 멱등이라
        // 그대로 다시 눌러도 안전하다는 것까지 알려준다.
        showToast(`반영 여부를 확인하지 못했습니다 — 다시 시도해 주세요 (${r.reason || ''})`, 'error');
      } else {
        entry.enabled = next;
        paint();
        const fact = facts[entry.tool];
        const note = next ? applyNote(fact) : (offNote(fact) || applyNote(fact));
        showToast(`${entry.name} ${next ? '켬' : '끔'} — ${note}`);
      }
    } catch (e) {
      showToast(e.message || '바꾸지 못했습니다', 'error');
    } finally {
      btn.disabled = false;
      if (onDone) onDone();
    }
  });
  return btn;
}

function renderGroup(group, facts) {
  const box = el('div', 'vt-mcp-group');
  box.appendChild(el('div', 'vt-set-label', group.name));

  for (const entry of group.entries) {
    const row = el('div', 'vt-mcp-row');
    row.appendChild(el('span', 'vt-mcp-tool', TOOL_LABEL[entry.tool] || entry.tool));
    row.appendChild(toggleButton(entry, facts));

    if (entry.transport && entry.transport !== 'unknown') {
      row.appendChild(el('span', 'vt-mcp-meta', entry.transport));
    }
    // 어떤 파일을 고치게 되는지 — 특히 `.mcp.json`은 저장소에 커밋된다.
    if (entry.shared) row.appendChild(el('span', 'vt-mcp-meta', '저장소 공유'));

    // 필요한 환경변수 이름은 보여준다(시크릿이 아니라 "무엇이 필요한지"다).
    const refs = [...(entry.env || []), ...(entry.headers || [])]
      .filter((f) => f.ref).map((f) => f.ref);
    if (refs.length) row.appendChild(el('span', 'vt-mcp-meta', `필요: ${refs.join(', ')}`));

    box.appendChild(row);
    for (const note of entry.notes || []) box.appendChild(help(note));
  }
  return box;
}

function renderFacts(facts) {
  const box = el('div', 'vt-mcp-facts');
  for (const [tool, fact] of Object.entries(facts || {})) {
    const line = `${TOOL_LABEL[tool] || tool}: ${applyNote(fact)}`;
    const off = offNote(fact);
    box.appendChild(help(off ? `${line} ${off}` : line));
  }
  return box;
}

function paint(host, data) {
  host.innerHTML = '';

  const wt = data.worktree;
  host.appendChild(help(wt
    ? `기준 워크트리: ${wt.label} — 「이 워크트리」 토글은 이 위치에만 적용됩니다.`
    : '워크트리를 고르지 않아 전역 설정만 보여줍니다. Claude는 켜짐/꺼짐을 프로젝트마다 따로 기록하므로, 워크트리를 열면 그 위치 기준으로 다시 보여줍니다.'));

  host.appendChild(renderFacts(data.facts));

  for (const err of data.errors || []) {
    host.appendChild(help(`⚠ ${err.source}: ${err.reason} — 이 파일은 건드리지 않습니다.`));
  }

  const groups = data.groups || [];
  if (!groups.length) {
    host.appendChild(help('정의된 MCP 서버가 없습니다. 추가는 각 CLI의 공식 명령을 쓰세요 — `claude mcp add`, `codex mcp add`, `agy mcp add`.'));
    return;
  }
  for (const g of groups) host.appendChild(renderGroup(g, data.facts || {}));
}

/** settings.js의 스텁이 부른다. 실패해도 설정 화면 전체를 깨뜨리지 않는다. */
export function mount(host, worktreeId) {
  _worktreeId = worktreeId || null;
  vtFetch(`/api/mcp${_worktreeId ? `?worktree=${encodeURIComponent(_worktreeId)}` : ''}`)
    .then((data) => paint(host, data))
    .catch((e) => {
      host.innerHTML = '';
      host.appendChild(help(`MCP 설정을 읽지 못했습니다: ${e.message || ''}`));
    });
}
