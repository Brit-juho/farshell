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

const TOOL_LABEL = { claude: 'Claude', codex: 'Codex', agy: 'agy', opencode: 'opencode' };
const SCOPE_LABEL = { global: '전역', local: '이 워크트리' };

// 워크트리 id는 settings.js가 넘겨준다.
let _worktreeId = null;

// 마지막으로 그린 데이터 — 태그를 고치거나 그룹을 누른 뒤 다시 그릴 때 쓴다.
let _host = null;

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
  const btn = el('button', 'vt-chip sm vt-mcp-toggle');
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
    const t = prompt(`「${name}」에 붙일 그룹 이름`);
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
      const secret = prompt(
        `${group.name} · ${slot.key} 값\n\n`
        + 'FarShell이 ~/.vt/mcp.json(0600)에 평문으로 보관합니다. '
        + '암호화하지 않습니다 — 무인으로 도는 서버는 자기가 쓰는 값을 '
        + '언제든 읽을 수 있어야 하기 때문입니다. GitHub 토큰과 같은 수준입니다.');
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

/** 「가져오기」 — 이 정의를 다른 도구·스코프에 심는다. 값은 안 따라간다. */
function deployControls(group, data) {
  const box = el('div', 'vt-mcp-row');
  box.appendChild(el('span', 'vt-mcp-tool', '가져오기'));

  const src = group.entries[0];

  // §2-5 — OAuth를 쓰는 서버는 복제 대상이 아니다. 만료·갱신·audience 제약이
  // 있어 옮겨봐야 받는 쪽에서 조용히 깨진다. 버튼을 그려놓고 누르면 409를
  // 주는 것보다, 왜 안 되는지 먼저 말하는 게 낫다.
  if (group.entries.some((e) => e.oauth)) {
    box.appendChild(el('span', 'vt-mcp-meta',
      'OAuth 서버는 다른 스코프로 복제할 수 없습니다 — 대상 도구에서 직접 인증하세요'));
    return box;
  }

  for (const [tool, scope, label] of [
    ['claude', 'global', 'Claude 전역'],
    ['claude', 'local', 'Claude 이 워크트리'],
    ['agy', 'global', 'agy 전역'],
    ['opencode', 'global', 'opencode 전역'],
    ['opencode', 'local', 'opencode 이 워크트리'],
  ]) {
    const already = group.entries.some((e) => e.tool === tool && e.scope === scope);
    const btn = el('button', 'vt-chip sm vt-mcp-tagadd');
    btn.type = 'button';
    btn.textContent = label;
    if (already) {
      btn.disabled = true;
      btn.title = '이미 여기에 있습니다';
      box.appendChild(btn);
      continue;
    }
    if (scope === 'local' && !_worktreeId) {
      btn.disabled = true;
      btn.title = '워크트리를 열어야 로컬 스코프에 심을 수 있습니다';
      box.appendChild(btn);
      continue;
    }
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
        showToast(e.message || '가져오지 못했습니다', 'error');
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


function renderServer(group, facts, tags, data) {
  const box = el('div', 'vt-mcp-group');
  const head = el('div', 'vt-mcp-servhead');
  head.appendChild(el('div', 'vt-set-label', group.name));
  head.appendChild(tagEditor(group.name, tags));
  box.appendChild(head);

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

  // 3단계 — 값 보관 / 가져오기. 보관할 칸이 없으면 자격증명 줄은 안 그린다.
  for (const slot of credSlots(group)) {
    box.appendChild(credRow(group, slot, (data && data.creds) || []));
  }
  box.appendChild(deployControls(group, data || {}));
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

function paint(host, data) {
  host.innerHTML = '';

  const wt = data.worktree;
  host.appendChild(help(wt
    ? `기준 워크트리: ${wt.label} — 「이 워크트리」 토글은 이 위치에만 적용됩니다.`
    : '워크트리를 고르지 않아 전역 설정만 보여줍니다. Claude는 켜짐/꺼짐을 프로젝트마다 따로 기록하므로, 워크트리를 열면 그 위치 기준으로 다시 보여줍니다.'));

  host.appendChild(renderFacts(data.facts));

  for (const err of data.errors || []) {
    // ⚠ 이모지를 SVG로 바꾼다. `help()`는 el()을 거쳐 **textContent**를 쓰므로
    // 아이콘 문자열을 본문에 섞으면 마크업이 글자로 찍힌다. 그리고 err.reason은
    // 설정 파일에서 온 값이라 innerHTML로 바꾸면 XSS가 된다 — 아이콘만 별도
    // 노드로 앞에 붙이고 본문은 textContent로 남긴다.
    const warnRow = help(`${err.source}: ${err.reason} — 이 파일은 건드리지 않습니다.`);
    warnRow.classList.add('vt-set-help-warn');
    const warnIcon = document.createElement('span');
    warnIcon.className = 'vt-set-help-ico';
    warnIcon.innerHTML = icon('alert-triangle', 13);
    warnRow.prepend(warnIcon);
    host.appendChild(warnRow);
  }

  const groups = data.groups || [];
  if (!groups.length) {
    host.appendChild(help('정의된 MCP 서버가 없습니다. 추가는 각 CLI의 공식 명령을 쓰세요 — `claude mcp add`, `codex mcp add`, `agy mcp add`.'));
    return;
  }

  // 그룹 줄은 서버 목록 **위**에 둔다 — "한 번에 켜고 끄기"가 이 화면에서
  // 가장 값싼 동작이고, 아래로 밀면 서버가 많을 때 스크롤해야 보인다.
  const bar = renderGroupBar(data);
  if (bar) host.appendChild(bar);

  // §2-5 회수 경로 — 우리가 **어디에** 참조를 심었는지. 이 목록이 없으면
  // "키를 거뒀다"고 말할 근거가 없다.
  if ((data.refs || []).length) {
    const box = el('div', 'vt-mcp-facts');
    box.appendChild(el('div', 'vt-set-label', '심어둔 참조'));
    for (const r of data.refs) {
      box.appendChild(help(`${r.server} · ${r.env} → ${r.source}`));
    }
    host.appendChild(box);
  }

  for (const g of groups) host.appendChild(renderServer(g, data.facts || {}, data.tags || {}, data));

  host.appendChild(renderPlugins(data));
}

/** 스캔 + 자격증명을 함께 읽는다. 자격증명은 **마스킹된 형태만** 온다.
 *  둘을 따로 요청하는 이유: 스캔은 CLI 파일을 읽은 사실이고 자격증명은 우리
 *  저장소라, 한쪽이 실패해도 다른 쪽은 보여야 한다. */
function load() {
  const q = _worktreeId ? `?worktree=${encodeURIComponent(_worktreeId)}` : '';
  return Promise.all([
    vtFetch(`/api/mcp${q}`),
    vtFetch('/api/mcp/creds').catch(() => ({ creds: [], refs: [] })),
    vtFetch(`/api/mcp/plugins${q}`).catch(() => ({ plugins: [] })),
  ]).then(([scan, credData, pluginData]) => ({
    ...scan, creds: credData.creds || [], refs: credData.refs || [],
    plugins: pluginData.plugins || [],
  }));
}

/** 서버에서 다시 읽어 그린다. 토글·그룹·태그·자격증명 조작 뒤 항상 이걸
 *  거친다 — 화면이 자기 기억으로 상태를 추측하면 파일을 직접 고친 사용자와
 *  어긋난다. */
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
  load()
    .then((data) => paint(host, data))
    .catch((e) => {
      host.innerHTML = '';
      host.appendChild(help(`MCP 설정을 읽지 못했습니다: ${e.message || ''}`));
    });
}

// ── 플러그인 (97번 4단계) ──────────────────────────────────────────────────
//
// MCP와 같은 메커니즘이지만 **설치는 하지 않는다**(§0-2). 목록에 없는 것을
// 켜는 버튼을 그리면 "켰는데 아무 일도 안 일어난다"가 되는데, 그게 이 화면이
// 가장 피해야 할 상태다.

function renderPlugins(data) {
  const list = data.plugins || [];
  const box = el('div', 'vt-mcp-group');
  box.appendChild(el('div', 'vt-set-label', '플러그인'));

  if (!list.length) {
    box.appendChild(help(
      '설치된 플러그인이 없습니다. 설치는 각 CLI의 공식 명령을 쓰세요 — '
      + '`claude plugin install`. FarShell은 설치된 것의 켜기·끄기만 다룹니다.'));
    return box;
  }

  for (const p of list) {
    const row = el('div', 'vt-mcp-row');
    row.appendChild(el('span', 'vt-mcp-tool', TOOL_LABEL[p.tool] || p.tool));

    const btn = el('button', 'vt-chip sm vt-mcp-toggle');
    btn.type = 'button';
    btn.textContent = `${p.plugin} · ${p.enabled ? '켬' : '끔'}`;
    btn.dataset.on = p.enabled ? '1' : '0';
    btn.title = `${SCOPE_LABEL[p.scope] || p.scope}${p.marketplace ? ` · ${p.marketplace}` : ''}`;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const r = await postElevated('/api/mcp/plugins/toggle', {
          name: p.name, enabled: !p.enabled, tool: p.tool,
          scope: p.scope, worktree: _worktreeId,
        });
        if (!r) return;
        if (r.status === 'unknown') {
          showToast('반영 여부를 확인하지 못했습니다 — 다시 시도해 주세요', 'error');
        } else {
          showToast(`${p.plugin} ${!p.enabled ? '켬' : '끔'} — 다음 세션부터 반영됩니다`);
        }
      } catch (e) {
        showToast(e.message || '바꾸지 못했습니다', 'error');
      } finally { refresh(); }
    });
    row.appendChild(btn);

    if (p.marketplace) row.appendChild(el('span', 'vt-mcp-meta', p.marketplace));
    box.appendChild(row);
  }
  return box;
}
