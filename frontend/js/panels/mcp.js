// 설정 →「MCP」 섹션 (97번 1단계 4/n). **지연 청크** — settings.js는 스텁만
// 갖고 이 파일은 섹션을 열 때 처음 내려온다(app.js 상한까지 여유가 410B뿐이다).
//
// 이 화면이 하는 일은 딱 둘이다: 지금 어디에 무엇이 켜져 있는지 **보여주기**,
// 그리고 **켜고 끄기**. 서버 정의를 만들거나 고치지 않는다 — 그건 각 CLI의
// 공식 명령(`claude mcp add` 등)이 이미 한다.
//
// ## 2단계 — 그룹 태그
//
// 「앱개발용」·「검증용」처럼 묶어두고 한 번에 켜고 끈다. 그룹은 **각 CLI
// 설정 파일에 전혀 기록되지 않는다**(§1-3) — FarShell 화면 안에만 있는
// 라벨이라, 도구가 설정을 다시 써도 사라지지 않고 도구가 모르는 키를 남기지도
// 않는다. 태그가 붙는 단위는 **서버 이름**이다(도구·스코프별 항목이 아니라):
// 같은 서버를 세 번 태그하게 만들면 한 곳이 빠진 채 그룹 켜기가 그 항목을
// 조용히 건너뛴다.
//
// 그룹 버튼은 **뒤집기가 아니라 목표 상태 지정**이다 — 섞인 상태에서 눌러도
// 결과가 결정적이고, 일부 실패 뒤 그대로 다시 눌러도 안전하다.
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
import { icon } from '../ui/icons.js';
import { showToast } from '../ui/toast.js';
import { promptDialog, confirmDialog } from '../core/dialog.js';

const TOOL_LABEL = { claude: 'Claude', codex: 'Codex', agy: 'agy', opencode: 'opencode' };
// 2026-09-28 — 「누구에게 적용되는가」 세 단계. 서버가 `level`로 내려준다
// (`scope`는 쓰기 기준이라 그대로다 — Claude의 local과 project가 둘 다 `local`).
const LEVEL_LABEL = { user: '유저', local: '로컬', project: '프로젝트' };
const LEVEL_HINT = {
  user: '나 · 모든 프로젝트',
  local: '나 · 이 프로젝트만 (커밋되지 않음)',
  project: '팀 공유 · 저장소에 커밋되는 파일',
};
const levelOf = (x) => x.level || (x.scope === 'global' ? 'user' : 'project');

function levelBadge(x) {
  const lv = levelOf(x);
  const b = el('span', 'vt-mcp-level', LEVEL_LABEL[lv] || lv);
  b.dataset.level = lv;
  b.title = `${LEVEL_HINT[lv] || ''}${x.source ? `\n${x.source}` : ''}`;
  return b;
}

// 2026-09-22(사용자 지적: "가독성이 너무 떨어져") — server/mcp_adapters.py가
// 워크트리를 안 골랐을 때 local-scope 항목마다 이 문장을 넣어준다
// (`notes.append("켜짐/꺼짐은 프로젝트마다 따로 기록된다 — 워크트리를
// 골라야 알 수 있다")`). 서버가 여러 개면 카드마다 같은 문장이 반복되는데,
// paint()가 이미 화면 맨 위에서 같은 내용을 한 번 말한다(host.appendChild
// 첫 줄, `wt` 없을 때). 뜻이 같은 문장을 카드 수만큼 복붙해 보여줄 이유가
// 없어 프론트에서만 걸러낸다 — 백엔드는 그대로 둔다(다른 소비처가 이
// notes를 쓸 수도 있어 API 응답 자체를 바꾸지 않는 편이 안전하다).
const REDUNDANT_WT_NOTE = '켜짐/꺼짐은 프로젝트마다 따로 기록된다 — 워크트리를 골라야 알 수 있다';

// 워크트리 id는 settings.js가 넘겨준다.
let _worktreeId = null;

// 마지막으로 그린 데이터 — 태그를 고치거나 그룹을 누른 뒤 다시 그릴 때 쓴다.
let _host = null;

// ADR-48 — 플러그인 목록은 따로 받는다. codex 쪽이 원격 조회라 느려서(첫 조회 최대
// 수십 초), 서버 목록과 한 번에 기다리면 섹션 전체가 그만큼 멈췄다. null = 아직 안 옴.
let _plugins = null;
let _pluginSeq = 0;

// ADR-48 — 다시 그려도(refresh) 사용자가 펼쳐 둔 것·걸러 둔 것은 그대로 둔다.
const _open = new Set();
let _filter = { q: '', tool: 'all', level: 'all', onlyOn: false, view: 'servers' };
let _lastData = null;

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
  const pw = await promptDialog('MCP 설정을 바꾸려면 비밀번호 확인이 필요합니다.', { title: '비밀번호 확인', password: true });
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
  const btn = el('button', 'vt-chip sm vt-mcp-toggle');
  const paint = () => {
    btn.textContent = entry.enabled ? '켬' : '끔';
    btn.setAttribute('aria-label', `${entry.name} ${TOOL_LABEL[entry.tool] || entry.tool} ${LEVEL_LABEL[levelOf(entry)]} — ${entry.enabled ? '켜짐' : '꺼짐'}`);
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

// ── 검증 (ADR-36 서버 · ADR-48 화면) ───────────────────────────────────────
//
// 설정대로 실제로 한 번 띄워 initialize + tools/list까지 해본다. 결과는 **저장하지
// 않는다** — 이 화면을 연 동안만 기억한다(새로고침하면 사라진다). 다시 그려도
// 남아 있도록 모듈 변수에 둔다.
const _verified = new Map();

function verifyKey(entry) {
  return [entry.tool, entry.scope, entry.name, entry.worktree_id || _worktreeId || ''].join('|');
}

function _ago(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 10) return '방금';
  if (s < 60) return `${s}초 전`;
  return `${Math.round(s / 60)}분 전`;
}

/** 결과 한 줄. 상태를 **색으로만 말하지 않는다** — 글자가 같이 간다. */
function verifyResult(r) {
  const span = el('span', 'vt-mcp-verify');
  if (!r) return span;
  span.dataset.state = r.state;
  if (r.state === 'running') {
    span.textContent = '확인 중…';
  } else if (r.state === 'connected') {
    // http/sse는 initialize까지만 확인한다(tool_count null) — 「도구 0개」라고 하면 거짓말이다.
    span.textContent = r.tool_count == null ? '연결됨 · 초기화 확인' : `연결됨 · 도구 ${r.tool_count}개`;
  } else if (r.state === 'unverified') {
    // 실패가 아니다 — 에이전트 셸에는 그 변수가 있을 수 있다(ADR-46).
    span.textContent = `확인 불가 — 이 서버 환경에 없음: ${(r.missing || []).join(', ')}`;
    span.title = '값을 모르는 채로 띄우면 틀린 실패를 보고하게 되어 실행하지 않았습니다. 에이전트 안에서는 동작할 수 있습니다.';
  } else {
    span.textContent = `실패 — ${r.error || '알 수 없는 오류'}`;
  }
  if (r.checked_at && r.state !== 'running') {
    span.title = `${span.title ? `${span.title}\n` : ''}${_ago(r.checked_at)} 확인`;
  }
  return span;
}

/** [검증] 버튼 + 결과. 승격이 필요하다(토글과 같은 흐름) — 설정에 적힌 명령을 실제로
 *  실행하기 때문이다. http/sse는 네트워크 요청이라 먼저 묻는다. */
function verifyControl(entry, onResult) {
  const box = el('span', 'vt-mcp-verifybox');
  const btn = el('button', 'vt-chip sm vt-mcp-verifybtn', '검증');
  btn.type = 'button';
  btn.title = '설정대로 한 번 실행해 연결되는지 확인합니다 — 결과는 저장하지 않습니다';
  let result = verifyResult(_verified.get(verifyKey(entry)));
  box.append(btn, result);

  const show = (r) => {
    const next = verifyResult(r);
    result.replaceWith(next);
    result = next;
  };

  btn.addEventListener('click', async () => {
    const remote = entry.transport === 'http' || entry.transport === 'sse';
    if (remote) {
      const ok = await confirmDialog(
        `${entry.name} 서버에 요청을 한 번 보냅니다: ${entry.url_host || '(주소 확인 불가)'}`,
        { title: '네트워크 요청', okLabel: '보내기' });
      if (!ok) return;
    }
    btn.disabled = true;
    show({ state: 'running' });
    try {
      const r = await postElevated(
        `/api/mcp/servers/${encodeURIComponent(entry.scope)}/${encodeURIComponent(entry.name)}/verify`,
        { tool: entry.tool, worktree: entry.worktree_id || _worktreeId, confirm_network: remote });
      if (!r) { show(_verified.get(verifyKey(entry))); return; }   // 비밀번호 입력 취소
      _verified.set(verifyKey(entry), r);
      show(r);
    } catch (e) {
      const r = { state: 'error', error: e.message || '검증 요청이 실패했습니다', checked_at: Date.now() / 1000 };
      _verified.set(verifyKey(entry), r);
      show(r);
    } finally {
      if (onResult) onResult();
      btn.disabled = false;
    }
  });
  return box;
}

// ── 그룹 태그 (2단계) ─────────────────────────────────────────────────────

async function postJson(url, payload) {
  return vtFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** 승격이 필요하면 한 번 묻고 재시도한다(toggle()과 같은 규칙). */
async function postElevated(url, payload) {
  try {
    return await postJson(url, payload);
  } catch (e) {
    if (e.status === 401 && /elevation_required/.test(e.message || '')) {
      if (!(await elevate())) return null;
      return postJson(url, payload);
    }
    throw e;
  }
}

/** 태그 하나의 현재 상태 — 'on'(전부 켬) / 'off'(전부 끔) / 'mixed'(섞임). */
function groupState(tag, data) {
  const names = new Set(
    Object.entries(data.tags || {})
      .filter(([, vals]) => vals.some((v) => v.toLowerCase() === tag.toLowerCase()))
      .map(([name]) => name),
  );
  const entries = (data.servers || []).filter((s) => names.has(s.name));
  if (!entries.length) return 'empty';
  const on = entries.filter((s) => s.enabled).length;
  if (on === entries.length) return 'on';
  if (on === 0) return 'off';
  return 'mixed';
}

/** 그룹 버튼 줄. 태그가 하나도 없으면 아무것도 안 그린다 — 빈 줄을 보여주는
 *  것보다 없는 편이 낫다(50 §4 "빈 패널을 보여주지 않는다"와 같은 규칙). */
function renderGroupBar(data) {
  const tags = data.allTags || [];
  if (!tags.length) return null;

  const bar = el('div', 'vt-mcp-groupbar');
  bar.appendChild(el('div', 'vt-set-label', '그룹'));
  const chips = el('div', 'vt-mcp-chips');

  for (const tag of tags) {
    const state = groupState(tag, data);
    const btn = el('button', 'vt-chip vt-mcp-groupchip');
    btn.type = 'button';
    btn.dataset.state = state;
    // 상태를 **색으로만 구분하지 않는다** — 글자로도 쓴다.
    const label = { on: '전부 켬', off: '전부 끔', mixed: '섞임', empty: '없음' }[state];
    btn.textContent = `${tag} · ${label}`;
    btn.title = state === 'mixed'
      ? '일부만 켜져 있습니다 — 누르면 전부 켜고, 한 번 더 누르면 전부 끕니다'
      : (state === 'on' ? '누르면 전부 끕니다' : '누르면 전부 켭니다');
    if (state === 'empty') {
      btn.disabled = true;
      btn.title = '이 워크트리에서 보이는 항목이 없습니다';
    }

    btn.addEventListener('click', async () => {
      // 섞임이면 먼저 전부 켠다(§1-3). 뒤집기가 아니라 목표 상태 지정이다.
      const next = state !== 'on';
      btn.disabled = true;
      try {
        const r = await postElevated('/api/mcp/group', {
          tag, enabled: next, worktree: _worktreeId,
        });
        if (!r) return;                       // 비밀번호 입력을 취소했다
        reportGroupResult(tag, next, r, data.facts || {});
      } catch (e) {
        showToast(e.message || '그룹을 바꾸지 못했습니다', 'error');
      } finally {
        refresh();
      }
    });
    chips.appendChild(btn);
  }
  bar.appendChild(chips);
  return bar;
}

/** 부분 실패를 성공으로 뭉개지 않는다 — 무엇이 안 됐는지 이름을 말한다. */
function reportGroupResult(tag, enabled, r, facts) {
  const word = enabled ? '켬' : '끔';
  if (r.status === 'ok') {
    if (!r.changed) { showToast(`${tag} — 이미 전부 ${word}`); return; }
    const tools = new Set((r.results || []).map((x) => x.tool));
    const note = [...tools].map((t) => applyNote(facts[t])).filter(Boolean)[0] || '';
    showToast(`${tag} 전부 ${word} (${r.changed}개) — ${note}`);
    return;
  }
  const bad = (r.results || []).filter((x) => x.status === 'failed').map((x) => x.name);
  const unsure = (r.results || []).filter((x) => x.status === 'unknown').map((x) => x.name);
  if (bad.length) {
    showToast(`${tag}: ${bad.join(', ')}를 못 바꿨습니다 — 다시 눌러도 안전합니다`, 'error');
  } else if (unsure.length) {
    showToast(`${tag}: ${unsure.join(', ')}의 반영 여부를 확인하지 못했습니다 — 다시 시도해 주세요`, 'error');
  } else {
    showToast(`${tag}를 바꾸지 못했습니다`, 'error');
  }
}

/** 서버 하나의 태그 편집 — 칩 목록 전체를 한 번에 교체한다(추가/삭제 API를
 *  따로 두지 않는 것과 짝이다). 승격은 요구하지 않는다: 태그는 라벨이고 CLI
 *  설정 파일을 건드리지 않는다. */
function tagEditor(name, tags) {
  const box = el('div', 'vt-mcp-tags');
  const current = tags[name] || [];

  for (const t of current) {
    const chip = el('button', 'vt-chip sm vt-mcp-tagchip');
    chip.type = 'button';
    chip.textContent = `${t} ×`;
    chip.title = `「${t}」 태그 떼기`;
    chip.addEventListener('click', async () => {
      chip.disabled = true;
      try {
        await postJson('/api/mcp/tags', {
          name, tags: current.filter((x) => x !== t),
        });
      } catch (e) {
        showToast(e.message || '태그를 떼지 못했습니다', 'error');
      } finally {
        refresh();
      }
    });
    box.appendChild(chip);
  }

  const add = el('button', 'vt-chip sm vt-mcp-tagadd');
  add.type = 'button';
  add.textContent = '+ 태그';
  add.title = '이 서버를 그룹에 넣습니다 — 그룹은 FarShell 안에만 기록되고 CLI 설정 파일은 건드리지 않습니다';
  add.addEventListener('click', async () => {
    const t = await promptDialog(`「${name}」에 붙일 그룹 이름`, { title: '그룹 추가' });
    if (t == null) return;
    if (!t.trim()) return;
    add.disabled = true;
    try {
      await postJson('/api/mcp/tags', { name, tags: [...current, t.trim()] });
    } catch (e) {
      showToast(e.message || '태그를 붙이지 못했습니다', 'error');
    } finally {
      refresh();
    }
  });
  box.appendChild(add);
  return box;
}


// ── 자격증명 · 가져오기 (97번 3단계) ──────────────────────────────────────
//
// **원문은 화면에 절대 다시 나타나지 않는다.** 서버가 마스킹된 형태만
// 내려주고, 여기서도 입력칸의 값을 보관하지 않는다. "한 번 넣으면 끝"이고,
// 바꾸려면 다시 넣는다(GitHub 토큰과 같은 취급).
//
// 화면이 반드시 말해야 하는 두 가지:
//   1. 값이 **평문 0600**으로 보관된다 — 암호화한다고 포장하지 않는다(§2-1).
//   2. CLI 설정 파일에는 **참조만** 들어간다 — 그게 실제 방어다(§2-2).

/** 서버 하나의 "필요한 값" 칸들 — scan이 알려준 env/headers 키. */
function credSlots(group) {
  const slots = [];
  for (const entry of group.entries) {
    for (const section of ['env', 'headers']) {
      for (const f of entry[section] || []) {
        if (!slots.some((x) => x.section === section && x.key === f.key)) {
          slots.push({ section, key: f.key, ref: f.ref, literal: f.literal });
        }
      }
    }
  }
  return slots;
}

function credRow(group, slot, creds) {
  const row = el('div', 'vt-mcp-row');
  row.appendChild(el('span', 'vt-mcp-tool', slot.key));

  const saved = (creds || []).find(
    (c) => c.server === group.name && c.key === slot.key);

  if (saved) {
    row.appendChild(el('span', 'vt-mcp-meta', `보관됨 ${saved.masked} → ${saved.env}`));
    const del = el('button', 'vt-chip sm vt-mcp-tagchip');
    del.type = 'button';
    del.textContent = '지우기';
    del.addEventListener('click', async () => {
      del.disabled = true;
      try {
        const r = await postElevated('/api/mcp/creds/delete', { id: saved.id });
        if (r) showToast(`${slot.key} 보관 해제 — 설정 파일의 참조는 그대로입니다`);
      } catch (e) {
        showToast(e.message || '지우지 못했습니다', 'error');
      } finally { refresh(); }
    });
    row.appendChild(del);
  } else {
    // 값이 아직 설정 파일에 그대로 박혀 있으면 그 사실을 먼저 말한다.
    if (slot.literal) {
      row.appendChild(el('span', 'vt-mcp-meta', '설정 파일에 값이 그대로 있습니다'));
    }
    const add = el('button', 'vt-chip sm vt-mcp-tagadd');
    add.type = 'button';
    add.textContent = '값 보관';
    add.title = 'FarShell이 보관하고, 설정 파일에는 참조만 씁니다';
    add.addEventListener('click', async () => {
      const secret = await promptDialog(
        `${group.name} · ${slot.key} 값\n\n`
        + 'FarShell이 ~/.vt/mcp.json(0600)에 평문으로 보관합니다. '
        + '암호화하지 않습니다 — 무인으로 도는 서버는 자기가 쓰는 값을 '
        + '언제든 읽을 수 있어야 하기 때문입니다. GitHub 토큰과 같은 수준입니다.',
        { title: '값 보관', password: true });
      if (!secret) return;
      add.disabled = true;
      try {
        const r = await postElevated('/api/mcp/creds', {
          server: group.name, key: slot.key, secret,
          fingerprint: group.entries[0] && group.entries[0].fingerprint,
        });
        if (r) showToast(`${slot.key} 보관했습니다 — 참조 이름 ${r.cred.env}`);
      } catch (e) {
        showToast(e.message || '보관하지 못했습니다', 'error');
      } finally { refresh(); }
    });
    row.appendChild(add);
  }
  return row;
}

/** 「다른 도구로 복제」 — 이 정의를 다른 도구·스코프에 심는다. 값은 안 따라간다.
 *  ADR-48: **지금 누를 수 있는 대상만** 그린다. 예전엔 서버마다 칩 5개를 늘 그리고 대부분
 *  비활성으로 두어 화면 전체에 죽은 칩이 60개였다. 대상이 없으면 줄 자체를 안 그린다. */
function deployControls(group, data) {
  // §2-5 — OAuth를 쓰는 서버는 복제 대상이 아니다. 만료·갱신·audience 제약이
  // 있어 옮겨봐야 받는 쪽에서 조용히 깨진다. 왜 안 되는지 먼저 말한다.
  if (group.entries.some((e) => e.oauth)) {
    const box = el('div', 'vt-mcp-row');
    box.appendChild(el('span', 'vt-mcp-tool', '복제'));
    box.appendChild(el('span', 'vt-mcp-meta',
      'OAuth 서버는 다른 스코프로 복제할 수 없습니다 — 대상 도구에서 직접 인증하세요'));
    return box;
  }

  const src = group.entries[0];
  const hasCreds = credSlots(group).length > 0;
  const targets = [
    ['claude', 'global', 'Claude 전역'],
    ['claude', 'local', 'Claude 이 워크트리'],
    ['agy', 'global', 'agy 전역'],
    ['opencode', 'global', 'opencode 전역'],
    ['opencode', 'local', 'opencode 이 워크트리'],
  ].filter(([tool, scope]) =>
    !group.entries.some((e) => e.tool === tool && e.scope === scope)
    && !(scope === 'local' && !data.worktree)
    // ADR-46 — agy는 참조를 확장하지 않아 서버가 env·headers를 가지면 복제를 거절한다.
    && !(tool === 'agy' && hasCreds));
  if (!targets.length) return null;

  const box = el('div', 'vt-mcp-row');
  box.appendChild(el('span', 'vt-mcp-tool', '복제'));
  for (const [tool, scope, label] of targets) {
    const btn = el('button', 'vt-chip sm vt-mcp-tagadd');
    btn.type = 'button';
    btn.textContent = label;
    btn.title = '값은 따라가지 않습니다 — 보관된 자격증명의 참조만 심습니다';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const r = await postElevated('/api/mcp/deploy', {
          name: group.name, defn: src.defn || srcDefn(src),
          tool, scope, worktree: _worktreeId,
          env_map: envMapFor(group, data.creds || []),
        });
        if (r) showToast(`${group.name} → ${label} (${r.target || ''})`);
      } catch (e) {
        showToast(e.message || '복제하지 못했습니다', 'error');
      } finally { refresh(); }
    });
    box.appendChild(btn);
  }
  return box;
}

/** scan은 정의 원본을 안 내려준다(값이 실릴 수 있으므로) — 필요한 뼈대만 만든다. */
function srcDefn(entry) {
  const d = {};
  if (entry.command) d.command = entry.command;
  if (entry.args) d.args = entry.args;
  if (entry.url) d.url = entry.url;
  // env/headers는 **키만** 넣는다. 값은 서버가 참조로 채운다.
  for (const section of ['env', 'headers']) {
    const keys = (entry[section] || []).map((f) => f.key);
    if (keys.length) d[section] = Object.fromEntries(keys.map((k) => [k, '']));
  }
  return d;
}

/** 어느 칸을 어느 환경변수 이름으로 바꿀지 — 보관된 자격증명에서만 만든다. */
function envMapFor(group, creds) {
  const map = {};
  for (const slot of credSlots(group)) {
    const saved = creds.find((c) => c.server === group.name && c.key === slot.key);
    if (!saved) continue;
    (map[slot.section] = map[slot.section] || {})[slot.key] = saved.env;
  }
  return map;
}


/** 접힌 줄에도 늘 보이는 경고. 숨기면 안 되는 것만(ADR-48). 글자로 말한다. */
function serverWarnings(group) {
  const out = [];
  const fields = group.entries.flatMap((e) => [...(e.env || []), ...(e.headers || [])]);
  if (group.entries.some((e) => e.auth_status === 'not_logged_in')) out.push('로그인 필요');
  if (fields.some((f) => f.dead_ref)) out.push('확장 안 되는 참조');
  if (fields.some((f) => f.literal && !f.dead_ref)) out.push('값이 파일에 있음');
  return out;
}

/** 접힌 줄의 검증 요약 — 항목들 중 가장 나쁜 결과. 한 번도 안 했으면 없음. */
function summaryVerify(group) {
  const span = el('span', 'vt-mcp-verify vt-mcp-sumverify');
  const results = group.entries.map((e) => _verified.get(verifyKey(e))).filter(Boolean);
  if (!results.length) return span;
  const worst = ['error', 'unverified', 'running', 'connected'].find((s) => results.some((r) => r.state === s));
  span.dataset.state = worst;
  // 실패·확인 불가는 경고와 같은 모양(아이콘 + UI 글꼴) — 가장 나쁜 소식이 가장 낮은 층에 있으면 안 된다.
  if (worst === 'error' || worst === 'unverified') span.innerHTML = icon('alert-triangle', 11);
  span.appendChild(document.createTextNode(
    { error: '검증 실패', unverified: '확인 불가', running: '확인 중…', connected: '연결됨' }[worst]));
  return span;
}

function renderServer(group, facts, tags, data) {
  const box = el('details', 'vt-mcp-group vt-mcp-server');
  box.dataset.name = group.name;
  if (_open.has(group.name)) box.open = true;
  box.addEventListener('toggle', () => {
    if (box.open) _open.add(group.name); else _open.delete(group.name);
  });

  // ── 접힌 줄 ──
  // <summary>는 네이티브라 Enter/Space·aria-expanded·포커스가 공짜다. 대신 안에 버튼을
  // 넣으면 안 된다(중첩 인터랙티브) — 조작은 전부 펼친 쪽에 있다.
  const sum = el('summary', 'vt-mcp-sum');
  const chev = el('span', 'vt-mcp-chev');
  chev.innerHTML = icon('chevron-down', 12);
  sum.appendChild(chev);
  sum.appendChild(el('span', 'vt-set-label vt-mcp-name', group.name));

  const shownEntries = group.entries.filter(_entryMatches);
  const states = el('span', 'vt-mcp-sumtools');
  shownEntries.forEach((e, i) => {
    if (i) states.appendChild(document.createTextNode(' · '));
    const s = el('span', null, `${TOOL_LABEL[e.tool] || e.tool} ${LEVEL_LABEL[levelOf(e)]} ${e.enabled ? '켬' : '끔'}`);
    s.dataset.on = e.enabled ? '1' : '0';
    s.title = LEVEL_HINT[levelOf(e)] || '';
    states.appendChild(s);
  });
  sum.appendChild(states);

  const right = el('span', 'vt-mcp-sumright');
  for (const w of serverWarnings(group)) {
    const b = el('span', 'vt-mcp-warnbadge');
    b.innerHTML = icon('alert-triangle', 11);
    b.appendChild(document.createTextNode(w));
    right.appendChild(b);
  }
  // 검증 결과는 전송 방식(메타)보다 **앞** — 상태가 메타보다 먼저 읽혀야 한다.
  let sv = summaryVerify(group);
  right.appendChild(sv);
  const transports = [...new Set(group.entries.map((e) => e.transport).filter((x) => x && x !== 'unknown'))];
  if (transports.length) right.appendChild(el('span', 'vt-mcp-meta', transports.join('·')));
  sum.appendChild(right);
  box.appendChild(sum);

  const syncSummary = () => {
    const next = summaryVerify(group);
    sv.replaceWith(next);
    sv = next;
  };

  // ── 펼친 쪽 ──
  const body = el('div', 'vt-mcp-body');

  for (const entry of shownEntries) {
    const row = el('div', 'vt-mcp-row');
    row.appendChild(el('span', 'vt-mcp-tool', TOOL_LABEL[entry.tool] || entry.tool));
    row.appendChild(levelBadge(entry));
    row.appendChild(toggleButton(entry, facts, refresh));

    if (entry.transport && entry.transport !== 'unknown') {
      // P2-2(ADR-35) — 선언된 transport 값을 그대로. `unknown`(정보 없음)과 우리가
      // 모르는 선언값은 사용자에게 다른 말이라 구분한다.
      const known = entry.transport === 'stdio' || entry.transport === 'sse' || entry.transport === 'http';
      row.appendChild(el('span', 'vt-mcp-meta', known ? entry.transport : `모르는 전송: ${entry.transport}`));
    }
    if (entry.auth_status === 'not_logged_in') {
      const auth = el('span', 'vt-mcp-meta vt-mcp-authwarn', '로그인 필요');
      auth.title = `codex mcp login ${entry.name}`;
      row.appendChild(auth);
    }
    row.appendChild(verifyControl(entry, syncSummary));

    // 필요한 환경변수 이름은 보여준다(시크릿이 아니라 "무엇이 필요한지"다).
    const refs = [...(entry.env || []), ...(entry.headers || [])]
      .filter((f) => f.ref).map((f) => f.ref);
    if (refs.length) row.appendChild(el('span', 'vt-mcp-meta', `필요: ${refs.join(', ')}`));

    body.appendChild(row);
    const hasWorktree = !!(data && data.worktree);
    for (const note of entry.notes || []) {
      if (!hasWorktree && note === REDUNDANT_WT_NOTE) continue;
      body.appendChild(help(note));
    }
  }

  // 3단계 — 값 보관 / 복제. 보관할 칸이 없으면 자격증명 줄은 안 그린다.
  for (const slot of credSlots(group)) {
    body.appendChild(credRow(group, slot, (data && data.creds) || []));
  }
  const deploy = deployControls(group, data || {});
  if (deploy) body.appendChild(deploy);
  const tagRow = el('div', 'vt-mcp-row');
  tagRow.appendChild(el('span', 'vt-mcp-tool', '태그'));
  tagRow.appendChild(tagEditor(group.name, tags));
  body.appendChild(tagRow);
  box.appendChild(body);
  return box;
}

function renderFacts(facts) {
  const box = el('div', 'vt-mcp-facts');
  // 도구 4개가 대개 같은 사실을 공유한다(전부 "다음 세션부터 반영 + 끄기 즉시
  // 여부 미확인"). 줄마다 CLI 이름만 바꿔 같은 두 문장을 반복하면 네 줄이
  // 전부 같은 덩어리로 보여서, 정작 **다른** 도구가 있어도 눈에 안 들어온다.
  // 같은 문구끼리 묶어 "Claude · Codex · agy: …" 한 줄로 낸다.
  const byNote = new Map();
  for (const [tool, fact] of Object.entries(facts || {})) {
    const off = offNote(fact);
    const note = off ? `${applyNote(fact)} ${off}` : applyNote(fact);
    if (!byNote.has(note)) byNote.set(note, []);
    byNote.get(note).push(TOOL_LABEL[tool] || tool);
  }
  for (const [note, tools] of byNote) {
    box.appendChild(help(`${tools.join(' · ')}: ${note}`));
  }
  return box;
}

/** 부연 설명용 접기 줄 — 서버 줄과 같은 chevron을 쓴다(브라우저 기본 ▶ 대신). */
function infoSummary(text) {
  const s = el('summary');
  const chev = el('span', 'vt-mcp-chev');
  chev.innerHTML = icon('chevron-down', 11);
  s.appendChild(chev);
  s.appendChild(document.createTextNode(text));
  return s;
}

// ── 거르기 ─────────────────────────────────────────────────────────────────
// 2026-09-28(사용자 지적: "필터도 제대로 적용되게") — 예전엔 도구 칩이 **그룹**
// 단위로만 걸러 Codex를 골라도 같은 서버의 Claude 줄이 그대로 보였고, 도구가
// 하나뿐이면 칩 줄이 통째로 사라졌다. 이제 모든 조건이 **항목 단위**로 걸리고
// 서버·플러그인 두 목록에 같은 막대가 걸린다.

function _entryMatches(e) {
  if (_filter.tool !== 'all' && e.tool !== _filter.tool) return false;
  if (_filter.level !== 'all' && levelOf(e) !== _filter.level) return false;
  if (_filter.onlyOn && !e.enabled) return false;
  return true;
}

function _textMatches(...fields) {
  const q = _filter.q.trim().toLowerCase();
  return !q || fields.some((f) => f && String(f).toLowerCase().includes(q));
}

function _matches(group, tags) {
  const t = (tags && tags[group.name]) || [];
  if (!_textMatches(group.name, ...t)) return false;
  return group.entries.some(_entryMatches);
}

function renderList(listHost, data) {
  listHost.innerHTML = '';
  const shown = (data.groups || []).filter((g) => _matches(g, data.tags));
  if (!shown.length) {
    listHost.appendChild(help((data.groups || []).length
      ? '조건에 맞는 서버가 없습니다.'
      : '정의된 MCP 서버가 없습니다. 추가는 각 CLI의 공식 명령을 쓰세요 — `claude mcp add`, `codex mcp add`, `agy mcp add`.'));
    return;
  }
  for (const g of shown) listHost.appendChild(renderServer(g, data.facts || {}, data.tags || {}, data));
}

function chipRow(label, options, current, onPick) {
  const row = el('div', 'vt-mcp-chips');
  row.setAttribute('role', 'group');
  row.setAttribute('aria-label', label);
  for (const [value, text, hint] of options) {
    const c = el('button', 'vt-chip sm vt-mcp-filterchip', text);
    c.type = 'button';
    if (hint) c.title = hint;
    c.dataset.value = value;
    c.setAttribute('aria-pressed', String(current === value));
    c.addEventListener('click', () => {
      for (const b of row.children) b.setAttribute('aria-pressed', String(b === c));
      onPick(value);
    });
    row.appendChild(c);
  }
  return row;
}

/** 검색 + 도구 + 적용 범위 + 켬만. 목록만 다시 그린다 — 입력칸을 다시 만들면 타이핑 중 포커스가 튄다. */
function renderFilter(data, rerender) {
  const bar = el('div', 'vt-mcp-filter');
  const input = el('input', 'vt-input vt-mcp-search');
  input.type = 'search';
  input.placeholder = '이름 · 태그 · 마켓플레이스';
  input.setAttribute('aria-label', 'MCP 서버·플러그인 찾기');
  input.value = _filter.q;
  input.addEventListener('input', () => { _filter.q = input.value; rerender(); });
  bar.appendChild(input);

  bar.appendChild(toolChips(data, rerender));
  bar.appendChild(chipRow('적용 범위로 거르기',
    [['all', '모든 범위'], ...Object.keys(LEVEL_LABEL).map((lv) => [lv, LEVEL_LABEL[lv], LEVEL_HINT[lv]])],
    _filter.level, (v) => { _filter.level = v; rerender(); }));

  const on = el('button', 'vt-chip sm vt-mcp-filterchip vt-mcp-onlyon', '켜진 것만');
  on.type = 'button';
  on.setAttribute('aria-pressed', String(_filter.onlyOn));
  on.addEventListener('click', () => {
    _filter.onlyOn = !_filter.onlyOn;
    on.setAttribute('aria-pressed', String(_filter.onlyOn));
    rerender();
  });
  bar.appendChild(on);
  return bar;
}

/** 도구 칩 — 플러그인 조회가 늦게 끝나면 그 도구가 더해지므로 이 줄만 다시 만든다. */
function toolChips(data, rerender) {
  const seen = new Set([
    ...(data.servers || []).map((e) => e.tool),
    ...((_plugins && _plugins.list) || []).map((p) => p.tool),
  ]);
  const tools = Object.keys(TOOL_LABEL).filter((t) => seen.has(t) || _filter.tool === t);
  const row = chipRow('도구로 거르기',
    [['all', '모든 도구'], ...tools.map((t) => [t, TOOL_LABEL[t]])],
    _filter.tool, (v) => { _filter.tool = v; rerender(); });
  row.classList.add('vt-mcp-toolchips');
  row._rerender = rerender;
  return row;
}

/** 서버 | 플러그인. 둘 다 그려 두고 `hidden`만 바꾼다 — 느린 플러그인 조회가
 *  탭을 눌렀을 때 비로소 시작되면 그만큼 빈 화면을 보게 된다. */
function renderTabs(counts, onPick) {
  const tabs = el('div', 'vt-mcp-tabs');
  tabs.setAttribute('role', 'tablist');
  for (const [view, label] of [['servers', 'MCP 서버'], ['plugins', '플러그인']]) {
    const t = el('button', 'vt-mcp-tab');
    t.type = 'button';
    t.setAttribute('role', 'tab');
    t.dataset.view = view;
    t.setAttribute('aria-selected', String(_filter.view === view));
    t.appendChild(document.createTextNode(label));
    const n = el('span', 'vt-mcp-tabcount', counts[view]);
    t.appendChild(n);
    t.addEventListener('click', () => {
      _filter.view = view;
      for (const b of tabs.children) b.setAttribute('aria-selected', String(b === t));
      onPick();
    });
    tabs.appendChild(t);
  }
  return tabs;
}

function contextBar(wt) {
  const top = el('div', 'vt-mcp-top');
  const ctx = el('div', 'vt-mcp-ctx');
  ctx.appendChild(el('span', 'vt-mcp-ctxlabel', '기준'));
  if (wt) {
    const name = el('span', 'vt-mcp-ctxname', wt.label || wt.path);
    name.title = wt.path || '';
    ctx.appendChild(name);
    if (wt.session) ctx.appendChild(el('span', 'vt-mcp-meta', `포커스된 칸 · ${wt.session}`));
  } else {
    ctx.appendChild(el('span', 'vt-mcp-ctxname', '유저 설정만'));
    ctx.appendChild(el('span', 'vt-mcp-meta', '프로젝트 안의 칸을 포커스하면 그 프로젝트 기준으로 보여줍니다'));
  }
  top.appendChild(ctx);
  return top;
}

function paint(host, data) {
  _lastData = data;
  host.innerHTML = '';
  const groups = data.groups || [];
  const wt = data.worktree;

  host.appendChild(contextBar(wt));

  // 설명 3문단 → 「적용 시점」 한 줄. 내용은 그대로, 펼치면 나온다(정직함은 유지).
  const facts = el('details', 'vt-mcp-applyinfo');
  facts.appendChild(infoSummary('적용 시점 — 켜고 끈 것은 대부분 다음 세션부터 반영됩니다'));
  for (const lv of Object.keys(LEVEL_LABEL)) facts.appendChild(help(`${LEVEL_LABEL[lv]}: ${LEVEL_HINT[lv]}`));
  if (!wt) {
    facts.appendChild(help('Claude는 켜짐/꺼짐을 프로젝트마다 따로 기록하므로, 프로젝트 칸을 포커스하면 그 위치 기준으로 다시 보여줍니다.'));
  }
  facts.appendChild(renderFacts(data.facts));
  host.appendChild(facts);

  for (const err of data.errors || []) {
    // err.reason은 설정 파일에서 온 값이라 textContent로만 넣는다 — 아이콘만 별도 노드.
    const warnRow = help(`${err.source}: ${err.reason} — 이 파일은 건드리지 않습니다.`);
    warnRow.classList.add('vt-set-help-warn');
    const warnIcon = document.createElement('span');
    warnIcon.className = 'vt-set-help-ico';
    warnIcon.innerHTML = icon('alert-triangle', 13);
    warnRow.prepend(warnIcon);
    host.appendChild(warnRow);
  }

  const onCount = groups.filter((g) => g.entries.some((e) => e.enabled)).length;
  const serverPane = el('div', 'vt-mcp-pane');
  serverPane.dataset.view = 'servers';
  const pluginPane = el('div', 'vt-mcp-pane');
  pluginPane.dataset.view = 'plugins';

  const listHost = el('div', 'vt-mcp-list');
  const rerender = () => {
    renderList(listHost, data);
    const box = pluginPane.querySelector('.vt-mcp-plugins');
    if (box) box.replaceWith(renderPlugins());
  };
  const sync = () => {
    serverPane.hidden = _filter.view !== 'servers';
    pluginPane.hidden = _filter.view !== 'plugins';
  };
  host.appendChild(renderTabs({
    servers: groups.length ? `${onCount}/${groups.length}` : '0',
    plugins: '',
  }, sync));
  host.appendChild(renderFilter(data, rerender));

  // 그룹 줄은 목록 **위** — "한 번에 켜고 끄기"가 이 화면에서 가장 값싼 동작이다.
  const bar = renderGroupBar(data);
  if (bar) serverPane.appendChild(bar);

  // §2-5 회수 경로 — 우리가 **어디에** 참조를 심었는지.
  if ((data.refs || []).length) {
    const box = el('details', 'vt-mcp-applyinfo');
    box.appendChild(infoSummary(`심어둔 참조 ${data.refs.length}개`));
    for (const r of data.refs) box.appendChild(help(`${r.server} · ${r.env} → ${r.source}`));
    serverPane.appendChild(box);
  }
  serverPane.appendChild(listHost);
  renderList(listHost, data);

  pluginPane.appendChild(renderPlugins());
  host.appendChild(serverPane);
  host.appendChild(pluginPane);
  sync();
  _syncPluginCount();
}

function _syncPluginCount() {
  const n = _host && _host.querySelector('.vt-mcp-tab[data-view="plugins"] .vt-mcp-tabcount');
  if (!n) return;
  if (_plugins === null) { n.textContent = '…'; return; }
  const names = new Map();
  for (const p of _plugins.list) {
    const k = `${p.tool}|${p.name}`;
    names.set(k, names.get(k) || (p.effective ?? p.enabled) === true);
  }
  const on = [...names.values()].filter(Boolean).length;
  n.textContent = names.size ? `${on}/${names.size}` : '0';
}

function _q() {
  return _worktreeId ? `?worktree=${encodeURIComponent(_worktreeId)}` : '';
}

/** 스캔 + 자격증명. 둘 다 빠르다(파일 읽기). 자격증명은 **마스킹된 형태만** 온다.
 *  따로 요청하는 이유: 스캔은 CLI 파일을 읽은 사실이고 자격증명은 우리 저장소라,
 *  한쪽이 실패해도 다른 쪽은 보여야 한다. */
function load() {
  return Promise.all([
    vtFetch(`/api/mcp${_q()}`),
    vtFetch('/api/mcp/creds').catch(() => ({ creds: [], refs: [] })),
  ]).then(([scan, credData]) => ({
    ...scan, creds: credData.creds || [], refs: credData.refs || [],
  }));
}

/** 플러그인만 다시 받아 그 칸만 바꾼다. 늦게 온 옛 응답은 버린다. */
function loadPlugins({ refresh: force = false } = {}) {
  const seq = ++_pluginSeq;
  const sep = _q() ? '&' : '?';
  return vtFetch(`/api/mcp/plugins${_q()}${force ? `${sep}refresh=1` : ''}`)
    .catch((e) => ({ plugins: [], errors: [{ source: '플러그인', reason: e.message || '읽지 못했습니다' }] }))
    .then((d) => {
      if (seq !== _pluginSeq) return;
      _plugins = { list: d.plugins || [], errors: d.errors || [] };
      const box = _host && _host.querySelector('.vt-mcp-plugins');
      if (box) box.replaceWith(renderPlugins());
      _syncPluginCount();
      const chips = _host && _host.querySelector('.vt-mcp-toolchips');
      if (chips && _lastData) chips.replaceWith(toolChips(_lastData, chips._rerender));
    });
}

/** 서버 목록을 다시 읽어 그린다. 토글·그룹·태그·자격증명 조작 뒤 항상 이걸
 *  거친다 — 화면이 자기 기억으로 상태를 추측하면 파일을 직접 고친 사용자와
 *  어긋난다. 플러그인은 안 다시 읽는다(바뀐 게 없다). */
function refresh() {
  if (!_host) return;
  return load()
    .then((data) => paint(_host, data))
    .catch((e) => showToast(e.message || 'MCP 설정을 다시 읽지 못했습니다', 'error'));
}

/** settings.js의 스텁이 부른다. 실패해도 설정 화면 전체를 깨뜨리지 않는다. */
export function mount(host, worktreeId) {
  _worktreeId = worktreeId || null;
  _host = host;
  _plugins = null;
  load()
    .then((data) => paint(host, data))
    .catch((e) => {
      host.innerHTML = '';
      host.appendChild(help(`MCP 설정을 읽지 못했습니다: ${e.message || ''}`));
    });
  loadPlugins();
}

// ── 플러그인 (97번 4단계) ──────────────────────────────────────────────────
//
// MCP와 같은 메커니즘이지만 **설치는 하지 않는다**(§0-2). 목록에 없는 것을
// 켜는 버튼을 그리면 "켰는데 아무 일도 안 일어난다"가 되는데, 그게 이 화면이
// 가장 피해야 할 상태다.

/** 플러그인 하나(도구+id)의 여러 적용 범위 항목을 한 줄로 묶는다. */
function groupPlugins(list) {
  const out = new Map();
  for (const p of list) {
    const k = `${p.tool}|${p.name}`;
    if (!out.has(k)) out.set(k, { key: k, head: p, entries: [] });
    out.get(k).entries.push(p);
  }
  return [...out.values()];
}

function pluginToggle(p) {
  const btn = el('button', 'vt-chip sm vt-mcp-toggle vt-mcp-plugtoggle');
  btn.type = 'button';
  const lv = levelOf(p);
  const state = p.enabled === true ? '켬' : (p.enabled === false ? '끔' : '미지정');
  btn.textContent = `${LEVEL_LABEL[lv]} · ${state}`;
  btn.dataset.on = p.enabled ? '1' : '0';
  btn.dataset.level = lv;
  btn.title = `${LEVEL_HINT[lv]}${p.source ? `\n${p.source}` : ''}`;
  if (p.writable === false) {
    // 팀 공유 파일(프로젝트) · 설치 안 됨 — 누르면 안 되는 이유를 글로 남긴다.
    btn.disabled = true;
    btn.classList.add('is-readonly');
    btn.title += lv === 'project'
      ? '\n팀 공유 파일이라 여기서 바꾸지 않습니다 — 저장소에서 직접 고치세요'
      : '\n설치되지 않은 플러그인이라 값만 바꿔도 켜지지 않습니다';
    return btn;
  }
  btn.addEventListener('click', async () => {
    const next = p.enabled !== true;
    btn.disabled = true;
    try {
      const r = await postElevated('/api/mcp/plugins/toggle', {
        name: p.name, enabled: next, tool: p.tool,
        scope: p.scope, worktree: p.worktree_id || _worktreeId,
      });
      if (!r) return;
      if (r.status === 'unknown') {
        showToast('반영 여부를 확인하지 못했습니다 — 다시 시도해 주세요', 'error');
      } else {
        showToast(`${p.plugin} ${LEVEL_LABEL[lv]} ${next ? '켬' : '끔'} — 다음 세션부터 반영됩니다`);
        if (r.warning) showToast(r.warning, 'error');
      }
    } catch (e) {
      showToast(e.message || '바꾸지 못했습니다', 'error');
    } finally { loadPlugins(); }
  });
  return btn;
}

function renderPluginRow(g) {
  const p = g.head;
  const shown = g.entries.filter(_entryMatches);
  const row = el('div', 'vt-mcp-plugrow');
  const eff = g.entries.find((e) => e.effective !== undefined);
  const effective = eff ? eff.effective : p.enabled;
  row.dataset.on = effective === true ? '1' : '0';

  const main = el('div', 'vt-mcp-plugmain');
  const dot = el('span', 'vt-mcp-dot');
  dot.dataset.on = effective === true ? '1' : (effective === false ? '0' : '');
  dot.title = effective === true ? '지금 적용: 켬' : (effective === false ? '지금 적용: 끔' : '지금 적용: 도구 기본값');
  main.appendChild(dot);
  main.appendChild(el('span', 'vt-set-label vt-mcp-name', p.plugin));
  main.appendChild(el('span', 'vt-mcp-tool', TOOL_LABEL[p.tool] || p.tool));
  const meta = [];
  if (p.marketplace) meta.push(p.marketplace);
  if (p.version) meta.push(`v${p.version}`);
  if (meta.length) main.appendChild(el('span', 'vt-mcp-meta', meta.join(' · ')));
  if (p.skill_count) {
    const skill = el('span', 'vt-mcp-meta', `스킬 ${p.skill_count}`);
    skill.title = (p.skills || []).join(', ');
    main.appendChild(skill);
  }
  if (p.bundles_mcp) main.appendChild(el('span', 'vt-mcp-meta', 'MCP 포함'));
  if (p.installed === false) {
    const b = el('span', 'vt-mcp-warnbadge');
    b.innerHTML = icon('alert-triangle', 11);
    b.appendChild(document.createTextNode('설치 안 됨'));
    main.appendChild(b);
  }
  row.appendChild(main);

  const toggles = el('div', 'vt-mcp-plugtoggles');
  for (const e of shown) toggles.appendChild(pluginToggle(e));
  row.appendChild(toggles);
  return row;
}

function renderPlugins() {
  const box = el('div', 'vt-mcp-plugins');

  const tools = el('div', 'vt-mcp-plughead');
  tools.appendChild(el('span', 'vt-mcp-meta', '설치는 각 CLI에서 — `claude plugin install`, `codex plugin add`. 여기서는 켜기·끄기만 합니다.'));
  const again = el('button', 'vt-chip sm vt-mcp-tagadd', '새로고침');
  again.type = 'button';
  again.title = 'CLI에서 새로 설치한 플러그인을 바로 반영합니다 — codex는 원격 조회라 오래 걸릴 수 있습니다';
  again.disabled = _plugins === null;
  again.addEventListener('click', () => {
    again.disabled = true;
    _plugins = null;
    box.replaceWith(renderPlugins());
    _syncPluginCount();
    loadPlugins({ refresh: true });
  });
  tools.appendChild(again);
  box.appendChild(tools);

  if (_plugins === null) {
    box.appendChild(help('불러오는 중… codex는 원격 마켓플레이스를 조회해 처음 한 번은 오래 걸릴 수 있습니다.'));
    return box;
  }
  // 예전엔 codex 조회 실패가 버려져 「설치된 플러그인이 없습니다」로 보였다 — 실패는 실패로 말한다.
  for (const err of _plugins.errors) {
    const w = help(`${err.source}: ${err.reason}`);
    w.classList.add('vt-set-help-warn');
    box.appendChild(w);
  }
  const list = _plugins.list;
  // 조회가 실패했는데 「설치된 플러그인이 없습니다」까지 쓰면 서로 모순이다.
  if (!list.length && _plugins.errors.length) return box;
  if (!list.length) {
    box.appendChild(help(
      '설치된 플러그인이 없습니다. 설치는 각 CLI의 공식 명령을 쓰세요 — '
      + '`claude plugin install`, `codex plugin add`. FarShell은 설치된 것의 켜기·끄기만 다룹니다.'));
    return box;
  }

  const groups = groupPlugins(list).filter((g) =>
    _textMatches(g.head.plugin, g.head.name, g.head.marketplace, ...(g.head.skills || []))
    && g.entries.some(_entryMatches));
  if (!groups.length) {
    box.appendChild(help('조건에 맞는 플러그인이 없습니다.'));
    return box;
  }
  const listEl = el('div', 'vt-mcp-pluglist');
  for (const g of groups) listEl.appendChild(renderPluginRow(g));
  box.appendChild(listEl);
  return box;
}
