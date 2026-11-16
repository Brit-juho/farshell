// dock 소스컨트롤 탭의 「에이전트 체크포인트」 절 — 에이전트가 턴을 끝낼 때마다 서버가 남긴 숨은 기록을 읽는다.
//
// 읽기 전용이다(복원은 이후 단계). 서버 API: GET /api/checkpoints, GET /api/checkpoints/diff, PUT /api/checkpoints/settings
// (docs/ref/worktree.md «Agent turn checkpoints»). 저장소별로 켜야 기록이 생긴다(기본 꺼짐).
//
// 이 파일은 viewer 지연 청크에 속한다(scm.js와 같다) — core 청크 상한(300KiB) 때문에 core에서 정적 import 금지.
// 순수 함수(턴 묶기·태그·문구)는 위에, DOM은 아래에 둔다. 색은 토큰만(.vt-ptag 톤), 이모지 없음.
import { vtFetch } from '../../core/api.js';
import { t } from '../../core/i18n.js';
import { icon } from '../../ui/icons.js';
import { showToast } from '../../ui/toast.js';
import { _renderDiffDOM } from './diff.js';
import { relCommitTime } from './git.js';

const MAX_TURNS_PER_SESSION = 30;

// ── 순수 함수 ────────────────────────────────────────────────────────────────

/** 서버 `sessions[]` → 화면 묶음. 세션마다 `turn-end` 행을 최신 턴부터, 기록이 없던 구간은 그대로 둔다. */
export function sessionGroups(sessions) {
  const out = [];
  for (const s of Array.isArray(sessions) ? sessions : []) {
    const rows = (s.checkpoints || []).filter((c) => c && c.kind === 'turn-end');
    rows.sort((a, b) => (b.seq || 0) - (a.seq || 0));
    out.push({
      id: s.id,
      label: s.tmux ? `${s.agent || ''} · ${s.tmux}`.trim() : (s.agent || t('에이전트')),
      last: s.last || 0,
      turns: rows.slice(0, MAX_TURNS_PER_SESSION),
      more: Math.max(0, rows.length - MAX_TURNS_PER_SESSION),
    });
  }
  return out.filter((g) => g.turns.length).sort((a, b) => b.last - a.last);
}

/** 한 턴 행의 태그: `[{tone, text, tip}]`. 색만으로 말하지 않는다 — 항상 낱말이 있다. */
export function turnTags(c) {
  const tags = [];
  const sm = c.summary;
  if (sm && sm.headMoved) tags.push({ tone: 'wait', text: t('HEAD 이동'), tip: t('이 턴 사이에 커밋·체크아웃으로 HEAD가 움직였습니다') });
  if (c.skipped > 0) tags.push({ tone: '', text: t('일부 제외'), tip: t('큰 파일·보호된 이름 {n}개는 기록하지 않았습니다', { n: c.skipped }) });
  if (!c.preTrusted) tags.push({ tone: '', text: t('턴 사이 변경 포함 가능'), tip: t('턴 시작 전 상태를 믿을 수 없어 직전 기록과 비교했습니다. 사람이 고친 내용이 섞일 수 있습니다') });
  return tags;
}

/** 두 줄 행의 문구. summary가 아직 없으면(최근 20개 밖) 둘째 줄은 비운다. */
export function turnLines(c, now = Date.now()) {
  const when = c.created ? relCommitTime(new Date(c.created * 1000).toISOString(), now) : '';
  const first = [t('턴 {n}', { n: c.turn == null ? '?' : c.turn }), when].filter(Boolean).join(' · ');
  const sm = c.summary;
  const second = sm ? [t('파일 {n}개', { n: sm.files }), `+${sm.added} −${sm.deleted}`].join(' · ') : '';
  return { first, second };
}

/** 켜기 상태 문구가 필요한 경우를 한 곳에서 정한다. */
export function sectionState(body, hookMissing) {
  if (!body || body.error) return 'hidden';             // 서버가 꺼 두었거나(404) 읽지 못했다 — 소스컨트롤 본문을 어지럽히지 않는다
  if (body.repo === false) return 'norepo';
  if (!body.enabled) return 'off';
  if (hookMissing) return 'nohook';
  return (body.sessions || []).length ? 'list' : 'empty';
}

// ── DOM ─────────────────────────────────────────────────────────────────────

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function psec(title, count) {
  const head = el('div', 'vt-vw-ghead vt-psec');
  head.appendChild(el('span', '', title));
  if (count != null) head.appendChild(el('span', 'n', String(count)));
  return head;
}

function note(text) {
  return el('div', 'vt-vw-note', text);
}

function tagEl(tag) {
  const e = el('span', `vt-ptag mono${tag.tone ? ` ${tag.tone}` : ''}`, tag.text);
  if (tag.tip) e.title = tag.tip;
  return e;
}

function backBar(label, onBack, title) {
  const bar = el('div', 'vt-vw-cbar vt-psub');
  const back = el('button', 'vt-icon-btn sm vt-vw-cback');
  back.type = 'button';
  back.setAttribute('aria-label', label);
  back.dataset.tip = label;
  back.innerHTML = icon('arrow-left', 14);
  back.addEventListener('click', onBack);
  bar.appendChild(back);
  if (title) bar.appendChild(el('span', 'vt-psub-file', title));
  return bar;
}

async function hookMissing() {
  try {
    const rows = await vtFetch('/api/agents/coverage');
    if (!Array.isArray(rows)) return false;
    const live = rows.filter((r) => r && (r.cli === 'claude' || r.cli === 'codex'));
    return live.length > 0 && !live.some((r) => r.path === 'hook');
  } catch (_) { return false; }
}

async function setEnabled(repo, enabled) {
  return vtFetch('/api/checkpoints/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, enabled }),
  });
}

/**
 * `host`(빈 div)에 절을 그린다. 자기 안에서 다시 그리려고 `reload()`를 쓴다.
 * opts.isCurrent: 바깥 화면이 아직 이 절을 보고 있는가. opts.onDetail(fn): 하위 화면으로 들어갈 때 바깥이 listing을 끈다.
 */
export async function renderCheckpoints(host, repo, opts = {}) {
  const isCurrent = opts.isCurrent || (() => true);
  const body = await vtFetch(`/api/checkpoints?repo=${encodeURIComponent(repo)}`).catch((e) => ({ error: e.data && e.data.error ? e.data.error : 'failed' }));
  if (!isCurrent() || !host.isConnected) return null;
  const missing = body && body.enabled ? await hookMissing() : false;
  if (!isCurrent() || !host.isConnected) return null;
  const state = sectionState(body, missing);
  host.replaceChildren();
  if (state === 'hidden') return body;

  const sec = el('div', 'vt-vw-gsec vt-ckpt');
  const groups = state === 'list' || state === 'nohook' ? sessionGroups(body.sessions) : [];
  const count = body.enabled ? groups.reduce((n, g) => n + g.turns.length, 0) : null;
  const head = psec(t('에이전트 체크포인트'), count);
  if (body.enabled) {
    const off = el('button', 'vt-icon-btn sm hov vt-ckpt-off');
    off.type = 'button';
    off.setAttribute('aria-label', t('이 저장소에서 끄기'));
    off.dataset.tip = t('이 저장소에서 끄기');
    off.innerHTML = icon('x', 14);
    off.addEventListener('click', async () => {
      off.disabled = true;
      try { await setEnabled(repo, false); } catch (e) { showToast(t('끄지 못했습니다: {msg}', { msg: e.message }), 'error'); }
      opts.reload && opts.reload();
    });
    head.appendChild(off);
  }
  sec.appendChild(head);

  if (state === 'norepo') {
    sec.appendChild(note(t('git 저장소가 아니라 체크포인트를 만들 수 없습니다')));
  } else if (state === 'off') {
    sec.appendChild(note(t('에이전트가 턴을 끝낼 때마다 이 저장소의 작업 상태를 숨은 기록으로 남깁니다. 브랜치·커밋은 바뀌지 않습니다.')));
    const on = el('button', 'vt-btn sm', t('이 저장소에서 켜기'));
    on.type = 'button';
    on.addEventListener('click', async () => {
      on.disabled = true;
      try {
        await setEnabled(repo, true);
      } catch (e) {
        showToast(t('켜지 못했습니다: {msg}', { msg: e.message }), 'error');
        on.disabled = false;
        return;
      }
      opts.reload && opts.reload();
    });
    sec.appendChild(on);
  } else {
    if (state === 'nohook') sec.appendChild(note(t('에이전트 훅이 없어 자동 기록이 만들어지지 않습니다 — fsh hooks install')));
    if (state === 'empty') sec.appendChild(note(t('아직 기록이 없습니다. 에이전트가 한 턴을 끝내면 여기에 나타납니다.')));
    for (const g of groups) {
      sec.appendChild(el('div', 'vt-ckpt-sess', g.label));
      for (const c of g.turns) sec.appendChild(turnRow(c, () => opts.onTurn && opts.onTurn(c, g)));
      if (g.more) sec.appendChild(note(t('이전 {n}개는 생략했습니다', { n: g.more })));
    }
    if (body.lastJob && body.lastJob.status === 'error') {
      sec.appendChild(note(t('마지막 기록이 실패했습니다')));
    }
  }
  host.appendChild(sec);
  return body;
}

function turnRow(c, onClick) {
  const row = el('div', 'vt-vw-grow vt-prow two vt-ckpt-row');
  row.tabIndex = 0;
  row.setAttribute('role', 'button');
  const { first, second } = turnLines(c);
  const col = el('span', 'vt-ckpt-cols');
  col.appendChild(el('span', 'vt-ckpt-l1', first));
  if (second) col.appendChild(el('span', 'vt-ckpt-l2 mono', second));
  row.appendChild(col);
  for (const tag of turnTags(c)) row.appendChild(tagEl(tag));
  row.addEventListener('click', onClick);
  row.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onClick(); } });
  return row;
}

/** 턴 하위 화면: 바뀐 파일 목록. 파일을 누르면 그 파일의 diff. */
export async function renderTurn(container, repo, c, opts = {}) {
  container.replaceChildren();
  const title = turnLines(c).first;
  container.appendChild(backBar(t('체크포인트로'), opts.onBack, title));
  const loading = el('div', 'vt-vw-loading', t('불러오는 중…'));
  container.appendChild(loading);
  let d;
  try {
    d = await vtFetch(`/api/checkpoints/diff?repo=${encodeURIComponent(repo)}&id=${encodeURIComponent(c.id)}`);
  } catch (e) {
    if (loading.isConnected) loading.replaceWith(el('div', 'vt-vw-empty', e.message));
    return;
  }
  if (!loading.isConnected) return;       // 기다리는 사이 뒤로 가서 다른 화면이 그려졌다 — 늦게 온 응답이 덮지 않게
  loading.remove();
  const tags = turnTags(c);
  if (tags.length) {
    const line = el('div', 'vt-ckpt-tags');
    tags.forEach((tag) => line.appendChild(tagEl(tag)));
    container.appendChild(line);
    if (!c.preTrusted) container.appendChild(note(t('이 턴 시작 전 상태를 믿을 수 없어, 턴 사이에 사람이 고친 내용이 함께 보일 수 있습니다.')));
  }
  const files = (d && d.files) || [];
  const sec = el('div', 'vt-vw-gsec');
  sec.appendChild(psec(t('변경된 파일'), files.length));
  if (!files.length) sec.appendChild(el('div', 'vt-vw-empty', t('변경된 내용이 없습니다.')));
  for (const f of files) {
    const row = el('div', 'vt-vw-grow vt-prow');
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    const name = el('span', 'vt-vw-name', f.path.split('/').pop());
    name.title = f.path;
    const dir = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : '';
    row.appendChild(name);
    if (dir) row.appendChild(el('span', 'vt-vw-dir', dir));
    row.appendChild(el('span', 'lab', f.binary ? t('바이너리') : `+${f.added} −${f.deleted}`));
    const open = () => renderTurnFile(container, repo, c, f.path, { onBack: () => renderTurn(container, repo, c, opts) });
    row.addEventListener('click', open);
    row.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
    sec.appendChild(row);
  }
  container.appendChild(sec);
  if (d && d.head_moved) container.appendChild(note(t('이 턴 사이에 HEAD가 움직여, 병합으로 들어온 파일이 섞여 있을 수 있습니다.')));
}

async function renderTurnFile(container, repo, c, file, opts) {
  container.replaceChildren();
  container.appendChild(backBar(t('턴으로'), opts.onBack, file.split('/').pop()));
  const loading = el('div', 'vt-vw-loading', t('불러오는 중…'));
  container.appendChild(loading);
  let d;
  try {
    d = await vtFetch(`/api/checkpoints/diff?repo=${encodeURIComponent(repo)}&id=${encodeURIComponent(c.id)}&file=${encodeURIComponent(file)}`);
  } catch (e) {
    if (loading.isConnected) loading.replaceWith(el('div', 'vt-vw-empty', e.message));
    return;
  }
  if (!loading.isConnected) return;
  loading.remove();
  if (!d || !d.diff || !d.diff.trim()) {
    container.appendChild(el('div', 'vt-vw-empty', t('변경된 내용이 없습니다.')));
    return;
  }
  _renderDiffDOM(container, d.diff, { head: false });
  if (d.truncated) container.appendChild(el('div', 'vt-vw-note warn', t('변경 내용이 커서 일부만 표시했습니다.')));
}
