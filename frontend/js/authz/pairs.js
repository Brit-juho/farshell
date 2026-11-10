// 설정 「페어」 — 진행 중·최근에 끝난 페어, 코드 보기(실시간 남은 시간), 역할 교체 제안·수락, 끝내기/회수.
// 서버는 페어를 만들거나 코드를 받거나 역할을 바꿀 때 이벤트를 내지 않는다 — 합류·결정·종료 이벤트(store 의 `pairs` 알림)와 이 화면 자신의 동작 뒤에 다시 읽고,
// 「새로고침」으로도 읽는다. 기능이 꺼진 서버(409 pair_disabled)는 오류가 아니라 한 줄 안내다.
import { vtEsc } from '../core/api.js';
import { confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { authzApi } from './api.js';
import { onChange } from './store.js';
import { showPending } from './inbox.js';
import {
  memberName, roleLabel, pairStateLabel, pairBundleText, pairTimeLeft, mmss, PAIR_LIVE, PAIR_ENDED, pairErrorText, endReasonText,
} from './data.js';

const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };
const isDisabled = (e) => !!e && (e.code === 'pair_disabled' || (e.data && e.data.error === 'pair_disabled'));

function emptyHTML() {
  return `<p class="vt-authz-empty">${t('진행 중인 페어가 없습니다.')}</p>
    <p class="vt-authz-note">${vtEsc(t('페어는 에이전트가 시작하거나 터미널에서 {cmd} 로 시작합니다. 상대가 코드를 입력해 합류하면 「승인 대기」에서 묶음과 확인 문구를 보고 허용합니다.', { cmd: 'fsh pair start' }))}</p>
    <p class="vt-authz-note">${vtEsc(t('이 기능은 서버에서 켜야 합니다 — 서버 설정 {env} 에 {tok} 를 더합니다.', { env: 'VT_AUTHZ_ENFORCE', tok: 'pair' }))}</p>`;
}

function disabledHTML() {
  return `<p class="vt-authz-empty" data-disabled="1">${vtEsc(t('페어 기능이 꺼져 있습니다. 서버 설정 {env} 에 {tok} 를 더하면 켜집니다.', { env: 'VT_AUTHZ_ENFORCE', tok: 'pair' }))}</p>`;
}

/** 이 사람이 시작한 쪽인가(코드는 시작한 쪽만 볼 수 있다). */
const mineInitiator = (p) => (p.members || []).some((m) => m.you && m.seat === 'initiator');
const me = (p) => (p.members || []).find((m) => m.you) || null;

function actionsHTML(p) {
  const out = [];
  const mine = me(p);
  const live = PAIR_LIVE.includes(p.state);
  if (p.state === 'proposed' || p.state === 'code_issued') {
    if (mineInitiator(p)) out.push(`<button type="button" class="vt-btn lg vt-pair-act" data-act="code">${t('코드 보기')}</button>`);
  }
  if (p.state === 'joined' && p.approval && p.approval.state === 'pending') {
    out.push(`<button type="button" class="vt-btn lg vt-pair-act" data-act="approve">${t('승인하기')}</button>`);
  }
  if (p.state === 'active') {
    if (mine && mine.driver) out.push(`<button type="button" class="vt-btn lg vt-pair-act" data-act="handoff">${t('역할 넘기기 제안')}</button>`);
    else if (!mine) out.push(`<button type="button" class="vt-btn lg vt-pair-act" data-act="handoff">${t('역할 바꾸기')}</button>`);
  }
  if (p.state === 'handoff_pending' && mine && !mine.driver) {
    out.push(`<button type="button" class="vt-btn lg vt-pair-act" data-act="accept">${t('교체 수락')}</button>`);
  }
  if (live) out.push(`<button type="button" class="vt-btn lg danger vt-pair-act" data-act="end">${mine ? t('끝내기') : t('회수')}</button>`);
  return out.join('');
}

function membersHTML(p) {
  return (p.members || []).map((m) => `<div class="vt-pair-member"><span class="vt-authz-who">${vtEsc(memberName(m))}</span><span class="vt-authz-sub">${vtEsc(roleLabel(m.role))}${m.joined ? '' : ` · ${vtEsc(t('아직 합류 전'))}`}</span></div>`).join('');
}

/** 교체 제안의 받는 쪽 — 서버는 자리(initiator|joiner)로 알려 주므로 구성원 이름으로 바꾼다. */
function handoffTarget(p) {
  const m = (p.members || []).find((x) => x.seat === p.handoff.to);
  return m ? memberName(m) : t('상대');
}

function cleanupText(c) {
  if (!c) return '';
  if (c.done || (c.grants === 0 && c.leases === 0)) return t('권한·폴더 점유 정리 완료');
  if (c.grants < 0 || c.leases < 0) return t('정리 상태를 확인하지 못했습니다');
  return t('정리 중 — 남은 권한 {g}개 · 폴더 점유 {l}개', { g: c.grants, l: c.leases });
}

export function mountPairs(host, now = () => Date.now()) {
  host.innerHTML = `<div class="vt-authz-toolbar"><button type="button" class="vt-btn quiet vt-pair-refresh">${t('새로고침')}</button></div>
    <div class="vt-authz-err" role="alert"></div>
    <div class="vt-authz-list" aria-live="polite"></div>`;
  const list = host.querySelector('.vt-authz-list');
  const errEl = host.querySelector('.vt-authz-err');
  const codes = new Map();       // pairId → {code, phrase, deadline(ms)} — 보기 요청 뒤에만 있다(코드 값은 목록 응답에 없다)
  let pairs = [];
  let fetchedAt = 0;
  let seq = 0;
  let busy = false;
  let timer = null;
  let keepErr = '';

  const deadlineOf = (sec) => fetchedAt + sec * 1000;

  function codeBlock(p) {
    const c = codes.get(p.id);
    if (!c) return '';
    return `<div class="vt-pair-code" data-pair-code="${vtEsc(p.id)}">
      <div class="vt-pair-code-line"><strong class="vt-pair-code-val">${vtEsc(c.code)}</strong>
      <span class="vt-pair-countdown" data-deadline="${c.deadline}" data-kind="code"></span></div>
      <div class="vt-authz-row-meta">${t('확인 문구')} <strong class="vt-pair-phrase-inline">${vtEsc(c.phrase)}</strong> — ${t('상대가 코드를 입력하면 이 문구가 두 쪽 화면에 같이 보입니다.')}</div>
    </div>`;
  }

  function rowHTML(p) {
    const ended = PAIR_ENDED.includes(p.state);
    const granted = (p.bundle && p.bundle.elevated && p.bundle.elevated.granted) || [];
    const meta = [];
    const left = pairTimeLeft(p, now() / 1000);
    if (left) meta.push(left);
    if (p.generation > 0) meta.push(t('역할 교체 {n}회', { n: p.generation }));
    if (ended && p.endReason) meta.push(endReasonText(p.endReason));
    const clean = ended ? cleanupText(p.cleanup) : '';
    if (clean) meta.push(clean);
    const ho = p.state === 'handoff_pending' && p.handoff
      ? `<div class="vt-authz-row-meta">${vtEsc(t('{to} 의 수락을 기다리는 중', { to: handoffTarget(p) }))} · <span class="vt-pair-countdown" data-deadline="${deadlineOf(p.handoff.expiresIn)}" data-kind="handoff"></span></div>` : '';
    return `<li class="vt-authz-row vt-pair-row" data-pair="${vtEsc(p.id)}" data-state="${vtEsc(p.state)}"${ended ? ' data-ended="1"' : ''}>
      <div class="vt-authz-row-main">
        <div class="vt-authz-row-top"><span class="vt-authz-kind">${vtEsc(pairStateLabel(p.state))}</span>${granted.length ? `<span class="vt-authz-flag">${vtEsc(t('높은 권한 {n}개 허용됨', { n: granted.length }))}</span>` : ''}</div>
        <div class="vt-authz-row-ask">${membersHTML(p)}</div>
        <div class="vt-pair-bundle-line">${vtEsc(pairBundleText(p))}</div>
        ${meta.length ? `<div class="vt-authz-row-meta">${vtEsc(meta.join(' · '))}</div>` : ''}
        ${ho}${codeBlock(p)}
      </div>
      ${ended ? '' : `<div class="vt-authz-row-acts">${actionsHTML(p)}</div>`}
    </li>`;
  }

  function paint() {
    if (!pairs.length) { list.innerHTML = emptyHTML(); tickAll(); return; }
    const live = pairs.filter((p) => !PAIR_ENDED.includes(p.state));
    const ended = pairs.filter((p) => PAIR_ENDED.includes(p.state));
    list.innerHTML = `${live.length ? `<ul class="vt-authz-rows">${live.map(rowHTML).join('')}</ul>` : `<p class="vt-authz-empty">${t('진행 중인 페어가 없습니다.')}</p>`}
      ${ended.length ? `<h4 class="vt-authz-h">${t('최근에 끝난 페어')}</h4><ul class="vt-authz-rows">${ended.map(rowHTML).join('')}</ul>` : ''}`;
    tickAll();
  }

  // 남은 시간 — 1초마다 글자만 고친다(목록을 다시 그리지 않는다). 그릴 것이 없으면 타이머를 멈춘다.
  function tickAll() {
    const els = host.querySelectorAll('.vt-pair-countdown');
    for (const el of els) {
      const left = (Number(el.dataset.deadline) - now()) / 1000;
      if (left <= 0) {
        el.textContent = el.dataset.kind === 'code' ? t('만료됨 — 다시 받으려면 「코드 보기」') : t('시간 초과');
        el.dataset.expired = '1';
      } else {
        el.textContent = el.dataset.kind === 'code' ? t('{left} 남음', { left: mmss(left) }) : mmss(left);
        el.removeAttribute('data-expired');
      }
    }
    const running = [...els].some((el) => !el.dataset.expired);
    if (running && !timer) timer = setInterval(() => { if (!host.isConnected) { stop(); return; } tickAll(); }, 1000);
    if (!running && timer) stop();
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  async function load() {
    const mine = ++seq;
    try {
      const rows = await authzApi.pairs();
      if (mine !== seq || !host.isConnected) return;
      pairs = rows;
      fetchedAt = now();
      // 목록에서 사라졌거나 코드를 쓸 수 없게 된 페어의 코드는 지운다.
      for (const id of [...codes.keys()]) {
        const p = rows.find((x) => x.id === id);
        if (!p || !(p.state === 'proposed' || p.state === 'code_issued')) codes.delete(id);
      }
      errEl.textContent = keepErr; keepErr = '';
      paint();
    } catch (e) {
      if (mine !== seq || !host.isConnected) return;
      if (isDisabled(e)) { pairs = []; errEl.textContent = ''; list.innerHTML = disabledHTML(); stop(); return; }
      list.innerHTML = `<p class="vt-authz-empty">${t('페어 목록을 불러오지 못했습니다.')}</p><button type="button" class="vt-btn lg vt-pair-refresh">${t('다시 시도')}</button>`;
      errEl.textContent = pairErrorText(e);
    }
  }

  async function run(row, act) {
    const id = row.dataset.pair;
    const p = pairs.find((x) => x.id === id);
    if (!p) return;
    if (act === 'approve') { showPending(p.approval && p.approval.id); return; }
    const mine = me(p);
    if (act === 'end') {
      const msg = mine
        ? t('이 페어를 끝냅니다. 두 쪽이 받은 권한과 폴더 점유가 모두 거두어집니다. 계속할까요?')
        : t('이 페어를 회수합니다. 두 에이전트가 받은 권한과 폴더 점유가 모두 거두어집니다. 계속할까요?');
      if (!(await confirmDialog(msg, { title: mine ? t('페어 끝내기') : t('페어 회수'), okLabel: mine ? t('끝내기') : t('회수'), danger: true }))) return;
    }
    if (act === 'handoff' && !mine) {
      if (!(await confirmDialog(t('사람이 직접 역할을 바꾸면 상대의 수락 없이 곧바로 운전자와 길잡이가 서로 바뀝니다. 계속할까요?'), { title: t('역할 바꾸기'), okLabel: t('바꾸기') }))) return;
    }
    busy = true;
    row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      if (act === 'code') {
        const res = await authzApi.pairCode(id);
        codes.set(id, { code: res.code, phrase: res.phrase, deadline: now() + (res.ttl || 300) * 1000 });
        // 목록의 code.expiresIn 이 더 정확하다(같은 코드를 다시 받으면 남은 시간이 짧다 — 서버와 이 화면의 시계도 다를 수 있다) — 읽은 뒤 맞춘다.
        await load();
        const q = pairs.find((x) => x.id === id);
        const c = codes.get(id);
        if (q && q.code && c) { c.deadline = deadlineOf(q.code.expiresIn); paint(); }
        return;
      }
      if (act === 'end') { await authzApi.pairEnd(id); toast(mine ? t('페어를 끝냈습니다') : t('페어를 회수했습니다'), 'success'); }
      else if (act === 'handoff') { await authzApi.pairHandoff(id); toast(mine ? t('역할 교체를 제안했습니다 — 상대가 수락하면 바뀝니다') : t('역할을 바꿨습니다'), 'success'); }
      else if (act === 'accept') { await authzApi.pairAccept(id); toast(t('역할을 바꿨습니다'), 'success'); }
      errEl.textContent = '';
    } catch (e) {
      keepErr = pairErrorText(e);
      errEl.textContent = keepErr;
    } finally {
      busy = false;
    }
    void load();
  }

  host.addEventListener('click', (ev) => {
    if (ev.target.closest('.vt-pair-refresh')) { void load(); return; }
    const b = ev.target.closest('.vt-pair-act');
    if (!b || busy) return;
    void run(b.closest('.vt-pair-row'), b.dataset.act);
  });

  const off = onChange((ev) => {
    if (!host.isConnected) { off(); stop(); return; }
    if (ev.type === 'pairs' && !busy) void load();
  });
  void load();
  return { reload: load, dispose: () => { off(); stop(); seq++; } };
}
