import { vtFetch } from '../../../core/api.js';
import { getAction } from '../../../core/dom.js';
import { group, row, statLine, codeLine, fmtWhen } from '../controls.js';
import { icon } from '../../../ui/icons.js';
import { t } from '../../../core/i18n.js';

// 훅 점검 결과 → 이유 한 줄. 정상(ok)·점검 안 함(absent)이면 빈 문자열.
export function hookWhy(h) {
  if (!h) return '';
  const names = [...(h.missing || []), ...(h.mismatch || [])].join(', ');
  if (h.state === 'missing') return t('빠진 훅: {events}', { events: names });
  if (h.state === 'mismatch') {
    return h.transient
      ? t('다른 경로로 등록된 훅: {events}. 곧 사라질 수 있는 임시(runtime-*) 경로입니다 — 다른 도구(gstack 등)가 설정을 바꿨을 수 있습니다.', { events: names })
      : t('다른 경로로 등록된 훅: {events}. FarShell 폴더를 옮겼거나 다른 설치를 가리키고 있습니다.', { events: names });
  }
  if (h.state === 'unreadable') return t('설정 파일을 읽지 못했습니다(깨졌거나 권한이 없음).');
  if (h.state === 'absent') return t('설정 폴더가 없어 점검하지 않습니다(이 CLI를 쓰지 않는 것으로 봅니다).');
  return '';
}

// ── 「정보 및 진단」 ──────────────────────────────────────────────────────
// 훅: 정상은 조용한 회색 체크, **문제만 호박색 행**. 데스크톱은 두 열 표로 전체를 보이고, 폰(CSS)은
// 문제 행만 펼치고 정상은 「나머지 N개는 정상입니다」 아래로 접는다(눌러서 펼침).
const STATE_LABEL = () => ({ ok: t('등록됨'), add: t('미등록'), update: t('다른 경로') });

function hookGroup(tool, detail, health) {
  const rows = Object.entries((detail && detail.events) || {});
  const bad = rows.filter(([, st]) => st !== 'ok').length;
  const box = document.createElement('div');
  box.className = 'vt-set-hkgroup';
  const sum = document.createElement('div');
  sum.className = 'vt-set-hksum';
  sum.dataset.state = bad ? 'warn' : 'ok';
  sum.insertAdjacentHTML('beforeend', icon(bad ? 'alert-triangle' : 'check', 14, 2));
  const name = document.createElement('b');
  name.textContent = tool === 'codex' ? t('Codex') : t('Claude Code');
  const count = document.createElement('span');
  count.className = 'vt-set-hkcount';
  count.textContent = bad
    ? t('{ok} / {n} 등록됨 · {bad}개 문제', { ok: rows.length - bad, n: rows.length, bad })
    : t('{ok} / {n} 등록됨', { ok: rows.length, n: rows.length });
  sum.append(name, count);
  box.appendChild(sum);
  // 서버의 자동 점검(hook_health) 결과 — 왜 이상으로 보이는지 한 줄. 경로는 서버가 주지 않는다.
  const why = hookWhy(health);
  if (why) {
    const w = document.createElement('div');
    w.className = 'vt-set-help vt-set-hookwhy';
    w.dataset.hookState = health.state;
    w.textContent = why;
    box.appendChild(w);
  }
  const grid = document.createElement('div');
  grid.className = 'vt-set-hkgrid';
  const labels = STATE_LABEL();
  for (const [event, state] of rows) {
    const line = document.createElement('div');
    line.className = 'vt-set-hookrow';
    line.dataset.state = state;
    const ev = document.createElement('span'); ev.className = 'vt-set-hkev'; ev.textContent = event;
    const st = document.createElement('span'); st.className = 'vt-set-hkst';
    st.insertAdjacentHTML('beforeend', icon(state === 'ok' ? 'check' : 'alert-triangle', 12, 2));
    st.append(document.createTextNode(labels[state] || labels.update));
    line.append(ev, st);
    grid.appendChild(line);
  }
  box.appendChild(grid);
  const okCount = rows.length - bad;
  if (okCount && bad) {
    // 폰에서만 보이는 접기 단추. 정상 행은 CSS가 숨기고, 펼치면 data-open 으로 보인다.
    const more = document.createElement('button');
    more.type = 'button'; more.className = 'vt-set-hkmore';
    more.setAttribute('aria-expanded', 'false');
    more.textContent = t('나머지 {n}개는 정상입니다. 펼쳐서 보기', { n: okCount });
    more.addEventListener('click', () => {
      const open = grid.dataset.open !== '1';
      grid.dataset.open = open ? '1' : '0';
      more.setAttribute('aria-expanded', open ? 'true' : 'false');
      more.textContent = open ? t('정상 항목 접기') : t('나머지 {n}개는 정상입니다. 펼쳐서 보기', { n: okCount });
    });
    box.insertBefore(more, grid.nextSibling);
  }
  grid.dataset.collapsible = bad ? '1' : '0';
  if (!bad) grid.dataset.allOk = '1';
  return { box, bad, count: rows.length };
}

export function renderAboutSection() {
  const frag = document.createDocumentFragment();
  const hooks = document.createElement('div');
  hooks.className = 'vt-set-about vt-set-hooks';
  hooks.appendChild(statLine(t('에이전트 훅 상태 확인 중…')));

  const recheck = document.createElement('button');
  recheck.type = 'button'; recheck.className = 'vt-btn sm quiet';
  recheck.innerHTML = icon('refresh-cw', 14, 2);
  recheck.append(document.createTextNode(t('상태 다시 확인')));

  // A0 연동 — 훅이 등록돼 있지 않으면 상태 배지·큐 자동 투입·TTS가 전부 조용히
  // 동작하지 않는다. "왜 아무 일도 안 일어나지"의 1번 원인이라 여기 보여준다.
  const loadHooks = () => vtFetch('/api/hooks/status').then((r) => {
    const tools = r && r.tools ? r.tools : { claude: { events: r && r.events } };
    if (!Object.keys(tools).length) { hooks.replaceChildren(statLine(t('훅 상태를 확인할 수 없습니다.'), 'warn')); return; }
    hooks.textContent = '';
    let bad = 0;
    const groups = [];
    for (const [tool, detail] of Object.entries(tools)) {
      const g = hookGroup(tool, detail, r && r.health && r.health[tool]);
      bad += g.bad;
      groups.push(g.box);
    }
    // 맨 위 한 줄: 전체 상태 + 마지막 점검 시각(서버 자동 점검이 켜져 있을 때).
    const top = statLine(bad ? t('훅 {n}개에 문제가 있습니다', { n: bad }) : t('모든 훅이 정상입니다'), bad ? 'warn' : 'ok');
    top.classList.add('vt-set-hooktop');
    if (r && r.checkedAt) {
      const when = document.createElement('span');
      when.className = 'vt-set-hookchecked';
      when.textContent = r.monitor
        ? t('서버가 {time}에 마지막으로 확인했습니다 (10분마다, 설정 파일이 바뀌면 바로).', { time: fmtWhen(r.checkedAt) })
        : t('자동 점검이 꺼져 있습니다 (VT_HOOK_HEALTH=0). 이 화면을 열 때만 확인합니다.');
      top.appendChild(when);
    }
    hooks.append(top, ...groups);
    if (bad) {
      const box = document.createElement('div');
      box.className = 'vt-set-note vt-set-hooknote';
      box.dataset.state = 'warn';
      box.insertAdjacentHTML('beforeend', icon('alert-triangle', 14, 2));
      const inner = document.createElement('div'); inner.style.flex = '1';
      const msg = document.createElement('div'); msg.className = 'vt-set-help';
      msg.textContent = t('미등록 훅이 있으면 상태 배지, 프롬프트 큐 자동 투입, 음성 요약이 동작하지 않습니다. 터미널에서 아래 명령을 실행하면 등록됩니다.');
      inner.append(msg, codeLine('fsh hooks install', t('훅 등록 명령 복사')));
      box.appendChild(inner);
      hooks.appendChild(box);
    }
  }).catch(() => { hooks.replaceChildren(statLine(t('훅 상태를 확인할 수 없습니다.'), 'warn')); });
  recheck.addEventListener('click', loadHooks);
  loadHooks();
  frag.appendChild(group(t('에이전트 훅'), hooks, recheck));

  // U1 — 사용량 소스가 없으면 탭도 HUD 칩도 통째로 사라진다(2.0 게이팅 규칙).
  // 조용히 사라지는 건 의도지만 "왜 사라졌는지"를 볼 곳이 한 군데는 있어야
  // 한다 — 실제로 clauth가 schema 2로 올라가며 꺼진 걸 몇 주 동안 아무도
  // 몰랐다. 켜져 있으면 한 줄, 꺼져 있으면 이유까지 적는다.
  const usage = document.createElement('div');
  usage.className = 'vt-set-about vt-set-provs';
  usage.appendChild(statLine(t('사용량 공급자 확인 중…')));
  frag.appendChild(group(t('사용량 공급자'), usage));

  // state: ok(사용 중) | warn(고장: 형식·권한·깨짐·만료) | off(안 쓰는 중: 피드 없음·끔 — 문제가 아니므로 조용히)
  const provRow = (name, state, text, reason) => {
    const line = document.createElement('div');
    line.className = 'vt-set-hookrow vt-set-provrow';
    line.dataset.state = state;
    const nm = document.createElement('span'); nm.className = 'vt-set-hkev'; nm.textContent = name;
    const st = document.createElement('span'); st.className = 'vt-set-hkst';
    if (state !== 'off') st.insertAdjacentHTML('beforeend', icon(state === 'ok' ? 'check' : 'alert-triangle', 12, 2));
    st.append(document.createTextNode(text));
    line.append(nm, st);
    if (reason) { const h = document.createElement('div'); h.className = 'vt-set-help vt-set-provwhy'; h.textContent = reason; line.appendChild(h); }
    return line;
  };

  vtFetch('/api/capabilities').then((r) => {
    const cap = (r && r.usage) || {};
    usage.textContent = '';
    const reason = cap.available ? null : cap.reason;
    const hint = reason === 'disabled' ? t('설정에서 껐습니다 (VT_USAGE_PROVIDER=none).')
      : reason === 'schema' ? t('사용량 피드 형식(스키마 {seen})을 이 버전이 모릅니다. 지원: {supported}. clauth 또는 FarShell을 올리세요.', {
        seen: cap.schema_seen ?? '?', supported: (cap.schema_supported || []).join(', ') || '-' })
      : reason === 'permission' ? t('사용량 피드를 읽을 권한이 없습니다 (~/.clauth/status.json).')
      : reason === 'broken' ? t('사용량 피드가 깨져 있습니다 (쓰는 중일 수 있습니다).')
      : reason ? t('사용량 피드(~/.clauth/status.json)가 없습니다.')
      : null;
    const broken = ['schema', 'permission', 'broken'].includes(reason);
    usage.appendChild(provRow(cap.provider || 'none', cap.available ? 'ok' : broken ? 'warn' : 'off',
      cap.available ? t('사용 중 (프로필 {n}개)', { n: cap.profiles || 0 }) : t('표시 안 함'), hint));

    // 96번 계획서 — Codex는 clauth와 독립된 두 번째 한도형 소스라 같은
    // 자리에 한 줄 더 둔다(합쳐서 하나로 보여주면 "어느 쪽이 문제인지"를
    // 알 수 없다 — 이 진단 패널의 존재 이유 그대로).
    const codexCap = (r && r.usage_codex) || {};
    const codexReason = codexCap.available ? null : codexCap.reason;
    const codexHint = codexReason === 'disabled' ? t('설정에서 껐습니다 (VT_USAGE_PROVIDER=none).')
      : codexReason === 'expired' ? t('Codex 로그인이 만료됐습니다 — 터미널에서 codex로 다시 로그인하세요.')
      : codexReason === 'no-auth' ? t('Codex 인증 파일이 없습니다 (~/.codex/auth.json) — codex CLI로 먼저 로그인하세요.')
      : codexReason ? t('Codex 사용량을 잠시 가져오지 못했습니다.')
      : null;
    usage.appendChild(provRow('codex', codexCap.available ? 'ok' : codexReason === 'expired' ? 'warn' : 'off', codexCap.available ? t('사용 중') : t('표시 안 함'), codexHint));
  }).catch(() => { usage.replaceChildren(statLine(t('사용량 공급자를 확인할 수 없습니다.'), 'warn')); });

  // 앱 파일 — 폰에는 FarShell 메뉴(제목 막대)가 없어 「캐시 지우기」를 여기서도 할 수 있게 한다.
  // 새 로직은 없다: 팔레트·메뉴와 같은 액션(app.clear-cache, 확인 대화상자 포함)을 부른다.
  const clear = document.createElement('button');
  clear.type = 'button'; clear.className = 'vt-btn sm quiet vt-set-clearcache';
  clear.textContent = t('캐시 지우기');
  clear.addEventListener('click', () => {
    const run = getAction('app.clear-cache');
    if (typeof run === 'function') run();
  });
  frag.appendChild(group(t('앱 파일'), row(t('앱 파일을 지우고 새로 받습니다. 설정과 로그인은 그대로입니다.'), clear)));

  return frag;
}
