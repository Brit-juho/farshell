import { vtFetch, STT_PRELOAD_TIMEOUT_MS } from '../../../core/api.js';
import { get as setting, set as setSetting } from '../../../core/settings.js';
import { confirmDialog } from '../../../core/dialog.js';
import { row, statusLine, toast, group, fmtBytes } from '../controls.js';
import { t } from '../../../core/i18n.js';

// ── 「음성 입력」 구역 — 모델 카드 · 준비 체크리스트 · 받아 둔 항목 관리 ──
// 서버(/voice/stt/*)가 주는 값을 그대로 보여준다(측정치·특징 하드코딩 금지).
// 받기는 **항상 확인 창을 거친다** — 몰래 받지 않는다. pip 패키지는 명령만 안내한다.

let _stop = null;
/** 섹션을 떠날 때(다른 탭·패널 닫기) 불러 폴링을 끊는다. voice.js의 cleanupVoiceClients가 부른다. */
export function cleanupVoiceModels() { if (_stop) { _stop(); _stop = null; } }

const ENGINES = [
  ['mlx_qwen3', t('MLX Qwen3-ASR (맥 애플칩)')],
  ['llamacpp_qwen3', t('llama.cpp Qwen3-ASR (모든 환경)')],
];
// 영숫자 . _ - 와 '/' 한 개. 경로(..)·URL은 거부. 최종 판단은 서버.
const REPO_RE = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
export function validRepo(s) {
  if (!REPO_RE.test(s)) return false;
  return s.split('/').every((p) => p !== '.' && p !== '..');
}
const fmtMb = (mb) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)}GB` : `${mb}MB`);
const sizeText = (mb) => (mb ? t('약 {size}', { size: fmtMb(mb) }) : t('크기 정보 없음'));

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function btn(label, cls = 'vt-btn sm quiet vt-set-reset') {
  const b = el('button', cls, label);
  b.type = 'button';
  return b;
}

/** 모델 상태 한 단어 — 사용 가능 / 받아야 함 / 설치 필요. */
export function modelState(m) {
  if (m.ready) return t('사용 가능');
  const manual = (m.requirements || []).some((r) => !r.satisfied && !r.can_install);
  return manual ? t('설치 필요') : t('받아야 함');
}

export function renderVoiceInput() {
  cleanupVoiceModels();
  let alive = true;
  let poll = null;
  let ticking = false;
  let cat = null;          // 마지막 /voice/stt/catalog 응답
  let chosen = null;       // 카드에서 고른 모델 id(아직 적용 전일 수 있다)
  let customModel = null;  // 직접 입력으로 만든 임시 항목(서버 목록에 없을 때)
  let job = null;          // 진행 중/마지막 준비 작업
  let busy = false;
  const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
  _stop = () => { alive = false; stopPoll(); };

  const frag = document.createDocumentFragment();

  // ── a. 상태 줄 ─────────────────────────────────────────────
  const preloadBtn = btn(t('미리 적재'));
  preloadBtn.setAttribute('aria-label', t('음성 인식 모델을 메모리에 미리 올리기'));
  const unloadBtn = btn(t('내리기'));
  unloadBtn.setAttribute('aria-label', t('음성 인식 모델을 메모리에서 내리기'));
  const btns = el('div', 'vt-set-btns');
  btns.append(preloadBtn, unloadBtn);
  const sttRow = row(t('음성 인식 상태'), btns,
    t('말을 글자로 바꾸는 기능입니다. 모델을 미리 올려 두면 첫 입력이 빨라지고, 내리면 메모리를 돌려받습니다.'));
  const sttStatus = statusLine(t('확인 중…'));
  const sttError = statusLine('');
  sttError.classList.add('vt-vm-error');
  sttError.hidden = true;
  sttRow.querySelector('.vt-set-label').append(sttStatus, sttError);
  frag.appendChild(sttRow);

  const labelOf = (id) => {
    const all = cat ? [...(cat.models || []), ...(cat.unsupported || [])] : [];
    const m = all.find((x) => x.id === id);
    return m ? m.label : id;
  };
  function paintStatus(st) {
    if (!alive || !st) return;
    if (!st.available) sttStatus.textContent = t('이 서버에서는 음성 입력을 쓸 수 없습니다');
    else if (st.loaded) {
      const model = labelOf(st.loaded_model || st.model);
      sttStatus.textContent = st.rss_mb
        ? t('메모리에 올라가 있음 · {model} · 메모리 {size}', { model, size: fmtMb(Math.round(st.rss_mb)) })
        : t('메모리에 올라가 있음 · {model}', { model });
    } else sttStatus.textContent = t('꺼져 있음 · 선택: {model}', { model: st.model ? labelOf(st.model) : t('없음') });
    preloadBtn.disabled = !st.available || !!st.loaded;
    unloadBtn.disabled = !st.loaded;
    sttError.hidden = !st.last_error;
    sttError.textContent = st.last_error ? t('마지막 오류: {error}', { error: st.last_error }) : '';
  }
  async function refreshStatus() {
    try { const st = await vtFetch('/voice/stt/status'); paintStatus(st); return st; }
    catch (e) { if (alive) sttStatus.textContent = t('상태를 확인할 수 없습니다.'); return null; }
  }
  preloadBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/voice/stt/preload', { method: 'POST', timeout: STT_PRELOAD_TIMEOUT_MS });
      toast(r.loaded ? t('메모리에 올렸습니다') : (r.error || t('올리지 못했습니다')), r.loaded ? undefined : 'error');
    } catch (e) { toast(e.message || t('올리지 못했습니다'), 'error'); }
    refreshStatus();
  });
  unloadBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/voice/stt/unload', { method: 'POST' });
      toast(r.unloaded ? t('내렸습니다') : t('이미 내려가 있습니다'));
    } catch (e) { toast(e.message || t('내리지 못했습니다'), 'error'); }
    refreshStatus();
  });

  // ── 카드 · 체크리스트 · 직접 입력 자리 ───────────────────────
  const body = el('div', 'vt-vm');
  const legacyEl = el('p', 'vt-set-note vt-vm-legacy');
  legacyEl.hidden = true;
  const listEl = el('div', 'vt-vm-list');
  listEl.setAttribute('role', 'radiogroup');
  listEl.setAttribute('aria-label', t('음성 인식 모델'));
  const checkEl = el('div', 'vt-vm-check');
  const unsupDet = el('details', 'vt-vm-fold');
  unsupDet.append(el('summary', null, t('이 서버에서는 쓸 수 없는 모델')));
  const unsupList = el('div', 'vt-vm-unsup');
  unsupDet.appendChild(unsupList);
  unsupDet.hidden = true;
  const customDet = el('details', 'vt-vm-fold');
  customDet.append(el('summary', null, t('직접 입력(고급)')));
  body.append(legacyEl, listEl, checkEl, unsupDet, customDet);
  frag.appendChild(body);

  const allModels = () => (cat ? [...(cat.models || []), ...(customModel ? [customModel] : [])] : []);
  const findModel = (id) => allModels().find((m) => m.id === id) || null;

  // ── b. 카드 ────────────────────────────────────────────────
  function metric(label, text, frac) {
    const d = el('div', 'vt-vm-metric');
    d.append(el('span', 'vt-vm-mlabel', label), el('span', 'vt-vm-mval', text));
    const bar = el('span', 'vt-vm-bar');
    const fill = el('span', 'vt-vm-fill');
    fill.style.setProperty('--vm-w', `${Math.max(4, Math.round(frac * 100))}%`);
    bar.appendChild(fill);
    bar.title = t('짧을수록 좋습니다');
    d.appendChild(bar);
    return d;
  }
  function buildCard(m, maxes) {
    const card = el('div', 'vt-vm-card');
    const pick = el('div', 'vt-vm-pick');
    pick.setAttribute('role', 'radio');
    pick.dataset.id = m.id;
    pick.setAttribute('aria-checked', m.id === chosen ? 'true' : 'false');
    pick.tabIndex = -1;
    card.dataset.checked = m.id === chosen ? 'true' : 'false';

    const head = el('div', 'vt-vm-head');
    head.appendChild(el('span', 'vt-vm-name', m.label));
    for (const tag of m.tags || []) head.appendChild(el('span', tag === t('권장') ? 'vt-tag vt-vm-tag-rec' : 'vt-tag', tag));
    if (m.selected) head.appendChild(el('span', 'vt-tag vt-vm-tag-cur', t('사용 중')));
    const st = el('span', 'vt-tag vt-vm-state', modelState(m));
    st.dataset.state = m.ready ? 'ready' : (modelState(m) === t('설치 필요') ? 'need' : 'get');
    head.appendChild(st);
    pick.appendChild(head);
    if (m.tagline) pick.appendChild(el('div', 'vt-vm-tagline', m.tagline));

    const b = m.bench;
    if (b) {
      const ms = el('div', 'vt-vm-metrics');
      ms.append(
        metric(t('정확도(오류율)'), `${b.cer}%`, maxes.cer ? b.cer / maxes.cer : 0),
        metric(t('문장당 속도'), t('{n}초', { n: b.sec }), maxes.sec ? b.sec / maxes.sec : 0),
        metric(t('메모리'), fmtMb(Math.round(b.rss_mb)), maxes.rss ? b.rss_mb / maxes.rss : 0),
      );
      pick.appendChild(ms);
    } else pick.appendChild(el('div', 'vt-vm-nobench', t('아직 측정하지 않음')));

    const facts = el('div', 'vt-vm-facts');
    facts.textContent = t('디스크 {size} · {engine} · {license}', { size: m.size_mb ? fmtMb(m.size_mb) : t('시스템이 관리'), engine: m.engine_label || m.engine, license: m.license || t('라이선스 정보 없음') });
    pick.appendChild(facts);
    if (b && b.basis) pick.appendChild(el('div', 'vt-vm-basis', b.basis));
    card.appendChild(pick);

    if ((m.notes || []).length) {
      const det = el('details', 'vt-vm-notes');
      det.appendChild(el('summary', null, t('자세히')));
      const ul = el('ul');
      for (const n of m.notes) ul.appendChild(el('li', null, n));
      det.appendChild(ul);
      card.appendChild(det);
    }
    pick.addEventListener('click', () => choose(m.id));
    pick.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(m.id); return; }
      const keys = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
      if (!keys[e.key]) return;
      e.preventDefault();
      const picks = Array.from(listEl.querySelectorAll('[role="radio"]'));
      const i = picks.indexOf(pick);
      const nx = picks[(i + keys[e.key] + picks.length) % picks.length];
      nx.focus();
    });
    return card;
  }
  function paintCards() {
    const focusId = document.activeElement && listEl.contains(document.activeElement)
      ? document.activeElement.dataset.id : null;
    listEl.textContent = '';
    const models = allModels();
    const max = (f) => Math.max(0, ...models.filter((m) => m.bench).map(f));
    const maxes = { cer: max((m) => m.bench.cer), sec: max((m) => m.bench.sec), rss: max((m) => m.bench.rss_mb) };
    for (const m of models) listEl.appendChild(buildCard(m, maxes));
    const picks = Array.from(listEl.querySelectorAll('[role="radio"]'));
    // roving tabindex — 고른 카드(없으면 첫 카드)만 탭으로 들어간다.
    const tabbable = picks.find((p) => p.getAttribute('aria-checked') === 'true') || picks[0];
    if (tabbable) tabbable.tabIndex = 0;
    if (focusId) picks.find((p) => p.dataset.id === focusId)?.focus();
    if (!models.length) listEl.appendChild(statusLine(t('이 서버에서 쓸 수 있는 모델이 없습니다.')));

    // d. 옛 값 안내
    if (cat.selected_valid === false && cat.legacy_value) {
      legacyEl.textContent = t('이전에 고른 모델(‘{value}’)은 더 이상 지원하지 않습니다. 아래에서 모델을 다시 골라 주세요', { value: cat.legacy_value });
      legacyEl.hidden = false;
    } else legacyEl.hidden = true;

    // e. 못 쓰는 모델
    unsupList.textContent = '';
    const un = cat.unsupported || [];
    unsupDet.hidden = !un.length;
    for (const m of un) {
      const it = el('div', 'vt-vm-unsup-item');
      it.append(el('div', 'vt-vm-name', m.label), el('div', 'vt-set-help', m.unsupported_reason || t('이 서버에서는 쓸 수 없습니다.')));
      unsupList.appendChild(it);
    }
    paintEngineOptions();
  }

  // ── c. 준비 체크리스트 ─────────────────────────────────────
  async function runStep(m, step, message, okLabel) {
    if (busy) return;
    // 받기는 항상 확인 창을 거친다. 취소하면 아무것도 보내지 않는다.
    const ok = await confirmDialog(message, { title: t('받기'), okLabel });
    if (!ok || !alive) return;
    busy = true; paintChecklist();
    try {
      job = await vtFetch('/voice/stt/setup', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: m.id, step }),
      });
      if (cat) cat.setup = job;
      startPoll();
    } catch (e) { toast(e.message || t('받기를 시작하지 못했습니다'), 'error'); }
    busy = false;
    if (alive) paintChecklist();
  }
  const running = () => !!(job && job.state === 'running');

  function progressView(m) {
    if (!job || job.model !== m.id) return null;
    const box = el('div', 'vt-vm-prog');
    for (const s of job.steps || []) {
      const line = el('div', 'vt-vm-step');
      const pct = s.total ? Math.min(100, Math.round((s.done / s.total) * 100)) : null;
      const word = { pending: t('대기 중'), running: pct != null ? `${pct}%` : t('받는 중…'), done: t('완료'), error: t('실패') }[s.state] || s.state;
      line.append(el('span', null, s.label), el('span', 'vt-vm-step-state', word));
      line.dataset.state = s.state;
      box.appendChild(line);
      if (s.state === 'running') {
        const bar = el('span', 'vt-vm-bar');
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-label', s.label);
        bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
        if (pct != null) bar.setAttribute('aria-valuenow', String(pct));
        const f = el('span', 'vt-vm-fill'); f.style.setProperty('--vm-w', `${pct == null ? 30 : Math.max(4, pct)}%`);
        bar.appendChild(f); box.appendChild(bar);
      }
    }
    if (job.state === 'error' && job.error) box.appendChild(el('div', 'vt-vm-error', t('받지 못했습니다: {error}', { error: job.error })));
    return box;
  }
  function paintChecklist() {
    checkEl.textContent = '';
    const m = findModel(chosen);
    if (!m || m.ready && !m.custom && m.selected) { checkEl.hidden = true; return; }
    checkEl.hidden = false;
    checkEl.appendChild(el('div', 'vt-vm-check-title', t('{label} 준비하기', { label: m.label })));
    const disabled = busy || running();
    const todo = (m.requirements || []).filter((r) => !r.satisfied);
    for (const r of todo) {
      const it = el('div', 'vt-vm-req');
      it.appendChild(el('div', 'vt-vm-req-label', r.label));
      if (r.kind === 'pip') {
        it.appendChild(el('div', 'vt-set-help', t('아래 명령을 터미널에서 실행한 뒤 「다시 확인」을 눌러 주세요. 자동으로 설치하지 않습니다.')));
        const cr = el('div', 'vt-vm-code');
        cr.appendChild(el('code', null, r.hint || ''));
        const cp = btn(t('복사'));
        cp.setAttribute('aria-label', t('{label} 설치 명령 복사', { label: r.label }));
        cp.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(r.hint || ''); toast(t('복사했습니다')); }
          catch (_) { toast(t('복사하지 못했습니다. 직접 선택해 복사해 주세요'), 'error'); }
        });
        cr.appendChild(cp);
        it.appendChild(cr);
      } else if (r.can_install) {
        const go = btn(t('받기 ({size})', { size: sizeText(r.size_mb) }));
        go.disabled = disabled;
        go.addEventListener('click', () => runStep(m, `req:${r.id}`,
          t('{label}을(를) 받을까요?\n\n용량: {size}\n인터넷 연결이 필요합니다.', { label: r.label, size: sizeText(r.size_mb) }), t('받기')));
        it.appendChild(go);
      } else if (r.hint) it.appendChild(el('div', 'vt-set-help', r.hint));
      checkEl.appendChild(it);
    }
    if (!m.model_present) {
      const it = el('div', 'vt-vm-req');
      it.appendChild(el('div', 'vt-vm-req-label', t('모델 파일')));
      const go = btn(m.size_mb ? t('모델 받기 (약 {size})', { size: fmtMb(m.size_mb) }) : t('모델 받기'));
      go.disabled = disabled;
      go.addEventListener('click', () => runStep(m, 'model',
        t('{label} 모델을 받을까요?\n\n용량: {size}\n인터넷 연결이 필요하고, 받는 동안 다른 항목을 받을 수 없습니다.', { label: m.label, size: sizeText(m.size_mb) }), t('받기')));
      it.appendChild(go);
      checkEl.appendChild(it);
    }
    const pv = progressView(m);
    if (pv) checkEl.appendChild(pv);
    const actions = el('div', 'vt-set-btns vt-vm-actions');
    const recheck = btn(t('다시 확인'));
    recheck.disabled = disabled;
    recheck.addEventListener('click', () => reload());
    actions.appendChild(recheck);
    // 서버 목록에 없는 직접 입력 항목은 서버가 ready를 판단하지 못했으므로, 받기가 끝났으면 확정은 열어 둔다.
    const canUse = m.ready || (m.custom && customModel && m.id === customModel.id && m.model_present !== false && !todo.length);
    if (canUse) {
      const use = btn(t('이 모델 사용'), 'vt-btn sm solid');
      use.addEventListener('click', () => applyModel(m.id));
      actions.appendChild(use);
    } else if (!todo.length && m.model_present) {
      checkEl.appendChild(el('div', 'vt-set-help', t('준비를 확인하는 중입니다. 「다시 확인」을 눌러 주세요.')));
    }
    checkEl.appendChild(actions);
  }

  // ── 선택 · 적용 ────────────────────────────────────────────
  async function applyModel(id) {
    try {
      await setSetting('voice.sttModel', id);
      try {
        // 이미 메모리에 올라가 있으면 새 모델로 바로 교체한다(없으면 다음 녹음 때 적재).
        const st = await vtFetch('/voice/stt/status');
        if (st && st.loaded) await vtFetch('/voice/stt/preload', { method: 'POST', timeout: STT_PRELOAD_TIMEOUT_MS });
      } catch (_) { /* 적용은 끝났다 — 적재는 다음 녹음 때 다시 시도된다 */ }
      toast(t('음성 인식 모델: {model}', { model: labelOf(id) }));
    } catch (e) { toast(e.message || t('적용하지 못했습니다'), 'error'); }
    if (alive) { await reload(); refreshStatus(); refreshManage(); }
  }
  function choose(id) {
    chosen = id;
    const m = findModel(id);
    paintCards(); paintChecklist();
    // 준비가 끝난 카드는 확인 없이 바로 적용한다(받기가 아니므로 확인할 것이 없다).
    if (m && m.ready && !m.selected) applyModel(id);
  }

  // ── 진행 폴링(1초) ─────────────────────────────────────────
  function startPoll() {
    if (poll || !alive) return;
    poll = setInterval(tick, 1000);
    paintChecklist();
  }
  async function tick() {
    if (ticking || !alive) return;
    ticking = true;
    try {
      const st = await refreshStatus();
      if (!alive) return;
      job = (st && st.setup) || job;
      if (!job || job.state !== 'running') {
        stopPoll();
        if (job && job.state === 'error') toast(job.error || t('받지 못했습니다'), 'error');
        await reload();
        return;
      }
      paintChecklist();
    } finally { ticking = false; }
  }

  async function reload() {
    try {
      const r = await vtFetch('/voice/stt/catalog');
      if (!alive) return;
      cat = r || { models: [], unsupported: [] };
      if (cat.setup) job = cat.setup;
      if (customModel && (cat.models || []).some((m) => m.id === customModel.id)) customModel = null;
      if (chosen == null && cat.selected_valid !== false) chosen = cat.selected || null;
      if (chosen && !findModel(chosen)) chosen = null;
      paintCards(); paintChecklist();
      if (running()) startPoll();
    } catch (e) {
      if (alive) { listEl.textContent = ''; listEl.appendChild(statusLine(t('모델 목록을 불러오지 못했습니다.'))); }
    }
  }

  // ── f. 직접 입력(고급) ─────────────────────────────────────
  const engSel = el('select', 'vt-select');
  engSel.setAttribute('aria-label', t('엔진'));
  for (const [v, l] of ENGINES) { const o = el('option', null, l); o.value = v; engSel.appendChild(o); }
  const repoIn = el('input', 'vt-input');
  repoIn.type = 'text'; repoIn.placeholder = 'owner/repo'; repoIn.autocomplete = 'off'; repoIn.spellcheck = false;
  repoIn.setAttribute('aria-label', t('HuggingFace 모델 주소 (owner/repo)'));
  const customGo = btn(t('준비하기'));
  const customMsg = statusLine('');
  const customBox = el('div', 'vt-vm-custom');
  const selWrap = el('div', 'vt-select-wrap');
  selWrap.appendChild(engSel);
  customBox.append(
    el('div', 'vt-set-help', t('목록에 없는 모델을 쓰고 싶을 때만 쓰세요. 엔진을 고르고 HuggingFace의 「owner/repo」를 적습니다. 서버가 최종으로 확인합니다.')),
    selWrap, repoIn, customGo, customMsg);
  customDet.appendChild(customBox);
  function paintEngineOptions() {
    // 서버 환경에서 못 쓰는 엔진은 비활성 — 목록(models)에 없고 unsupported에만 있는 엔진.
    for (const o of engSel.options) {
      const usable = (cat.models || []).some((m) => m.engine === o.value);
      const blocked = !usable && (cat.unsupported || []).some((m) => m.engine === o.value);
      o.disabled = blocked;
      if (blocked) o.textContent = t('{engine} — 이 서버에서는 쓸 수 없음', { engine: ENGINES.find((e) => e[0] === o.value)[1] });
    }
    if (engSel.selectedOptions[0]?.disabled) {
      const ok = Array.from(engSel.options).find((o) => !o.disabled);
      if (ok) engSel.value = ok.value;
    }
  }
  customGo.addEventListener('click', () => {
    const repo = repoIn.value.trim();
    if (!validRepo(repo)) {
      customMsg.textContent = t('형식이 맞지 않습니다. 「owner/repo」처럼 영문·숫자·. _ - 와 슬래시(/) 하나만 쓸 수 있습니다.');
      return;
    }
    customMsg.textContent = '';
    const id = `custom:${engSel.value}:${repo}`;
    if (!findModel(id)) {
      // 서버 목록에 없으면 임시 항목 — 어떤 준비가 필요한지는 받기 단계에서 서버가 알려 준다.
      customModel = {
        id, engine: engSel.value, engine_label: ENGINES.find((e) => e[0] === engSel.value)[1],
        label: repo, tagline: t('직접 입력한 모델'), tags: [], bench: null, notes: [], license: '',
        size_mb: 0, requirements: [], model_present: false, ready: false, selected: false, custom: true,
      };
    }
    chosen = id;
    paintCards(); paintChecklist();
  });
  repoIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') customGo.click(); });

  // ── 받아 둔 항목 관리 ──────────────────────────────────────
  const manageEl = el('div', 'vt-vm-manage');
  async function refreshManage() {
    if (!alive) return;
    manageEl.textContent = t('확인 중…');
    try {
      const r = await vtFetch('/voice/stt/model');
      if (!alive) return;
      const models = (r && r.models) || [];
      const bins = (r && r.binaries) || [];
      manageEl.textContent = '';
      if (!models.length && !bins.length) { manageEl.textContent = t('내려받은 파일이 없습니다.'); return; }
      const add = (name, size, selected, payload, warnSel) => {
        const it = el('div', 'vt-set-model-item');
        const lab = el('span', 'vt-set-model-name', `${name} · ${fmtBytes(size || 0)}`);
        it.appendChild(lab);
        if (selected) it.appendChild(el('span', 'vt-tag vt-vm-tag-cur', t('선택 중')));
        const del = btn(t('삭제'), 'vt-btn sm quiet vt-set-reset danger');
        del.setAttribute('aria-label', t('{name} 삭제', { name }));
        del.addEventListener('click', async () => {
          const warn = selected
            ? t('지금 선택 중인 모델입니다. 삭제하면 다시 받기 전까지 음성 입력을 쓸 수 없습니다.')
            : warnSel;
          const ok = await confirmDialog(t('{name}({size})을(를) 삭제할까요?\n\n{warn}', { name, size: fmtBytes(size || 0), warn }),
            { title: t('삭제'), okLabel: t('삭제'), danger: true });
          if (!ok) return;
          try {
            const d = await vtFetch('/voice/stt/model', {
              method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
            toast(d && d.freed_bytes ? t('삭제했습니다 · {size} 확보', { size: fmtBytes(d.freed_bytes) }) : t('삭제했습니다'));
          } catch (e) { toast(e.message || t('삭제하지 못했습니다'), 'error'); }
          if (alive) { refreshStatus(); reload(); refreshManage(); }
        });
        it.appendChild(del);
        manageEl.appendChild(it);
      };
      for (const m of models) add(m.label || m.model_id, m.size_bytes, m.selected, { model_id: m.model_id }, t('나중에 쓰려면 다시 받아야 합니다.'));
      for (const b of bins) add(b.label || b.id, b.size_bytes, false, { binary: b.id }, t('이 엔진을 쓰려면 다시 받아야 합니다.'));
    } catch (e) { manageEl.textContent = t('상태를 확인할 수 없습니다.'); }
  }

  const manageGroup = group(t('내려받은 파일 관리'), row(t('내려받은 파일'), manageEl,
    t('모델과 엔진 파일을 삭제해 저장 공간을 돌려받습니다. 자동으로 받지 않으며, 삭제한 것은 다시 받아야 합니다.')));
  // 호출부가 구역 순서를 정할 수 있게 둘로 나눠 돌려준다.
  const input = group(t('음성 입력'), frag);
  reload();
  refreshStatus();
  refreshManage();
  return { input, manage: manageGroup };
}
