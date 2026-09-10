// U2 — 사용량 게이지. rail의 「사용량」 버튼이 여는 패널.
//
// 왜 필요한가(계획서 §"왜 하는가"): 레퍼런스 3사 실측의 결론이 "사용량 상시
// 노출이 '이 앱은 내 상황을 지켜보고 있다'는 신뢰감을 만든다"였다. 동시에
// 3단 레이아웃의 우측 레일을 채울 가장 값싼 실데이터이기도 하다.
//
// **표시 규칙 두 가지가 이 파일의 핵심이다.**
//   1. `tier`와 `windows[].label`은 **표시 전용**이다 — 분기 키로 쓰지 않는다.
//      clauth가 새 tier("Max")나 새 창("30d")을 추가해도 이 화면은 안 깨진다.
//   2. **숫자보다 바가 먼저**다. 스캔 속도가 다르다 — 바 색만 보고 "괜찮다/
//      곧 막힌다"를 판단할 수 있어야 하고, 정확한 %는 그 다음이다.
import { openPanel, closePanel } from './panel.js';
import { vtFetch } from '../core/api.js';
import { registerAction } from '../core/dom.js';

const PANEL_ID = 'vt-usage';
const POLL_MS = 30000;   // 피드 자체가 90초 주기라 그보다 자주 볼 이유가 없다

// 80% 초과 앰버, 95% 초과 레드 — D3 상태 토큰을 그대로 쓴다(색 언어를 이
// 화면만 따로 만들지 않는다).
function barColor(pct) {
  if (pct > 95) return 'var(--color-st-error)';
  if (pct > 80) return 'var(--color-st-waiting)';
  return 'var(--color-acc)';
}

// "1시간 10분 후" — 서버가 계산해 준 초를 사람 단위로 바꾼다(클라이언트 시계가
// 틀어져 있어도 맞도록 계산 자체는 서버가 한다).
function humanIn(sec) {
  if (sec == null) return '';
  if (sec < 60) return '곧 리셋';
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}분 후 리셋`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}시간 ${m % 60}분 후 리셋` : `${h}시간 후 리셋`;
  return `${Math.round(h / 24)}일 후 리셋`;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function renderWindow(w) {
  const row = el('div', 'vt-usage-win');
  row.appendChild(el('span', 'vt-usage-label', w.label));

  const track = el('div', 'vt-usage-track');
  const fill = el('div', 'vt-usage-fill');
  const pct = typeof w.pct === 'number' ? Math.max(0, Math.min(100, w.pct)) : 0;
  fill.style.width = `${pct}%`;
  fill.style.background = barColor(pct);
  track.appendChild(fill);
  track.setAttribute('role', 'progressbar');
  track.setAttribute('aria-valuenow', String(Math.round(pct)));
  track.setAttribute('aria-valuemin', '0');
  track.setAttribute('aria-valuemax', '100');
  track.setAttribute('aria-label', `${w.label} 사용량 ${Math.round(pct)}%`);
  row.appendChild(track);

  row.appendChild(el('span', 'vt-usage-pct', `${Math.round(pct)}%`));
  row.appendChild(el('span', 'vt-usage-reset', humanIn(w.resets_in_sec)));
  return row;
}

function renderProfile(p, activeName) {
  const card = el('div', 'vt-usage-card');
  if (p.name === activeName || p.active) card.classList.add('active');

  const head = el('div', 'vt-usage-head');
  const title = el('span', 'vt-usage-name', p.name);
  head.appendChild(title);
  if (p.tier) head.appendChild(el('span', 'vt-usage-tier', p.tier));
  // rolling 토큰은 고정 토큰과 성격이 다르다 — 같은 모양으로 그리면 사용자가
  // "왜 값이 계속 바뀌지"를 오해한다(계획서 §4).
  if (p.rolling_token) head.appendChild(el('span', 'vt-usage-chip', 'rolling'));
  if (p.has_live_session) {
    const live = el('span', 'vt-usage-live', '● live');
    live.title = '이 프로필로 실행 중인 세션이 있습니다';
    head.appendChild(live);
  }
  card.appendChild(head);

  // 경고는 게이지보다 **위**에 온다 — 재인증이 필요한데 숫자를 먼저 보여주면
  // 그 숫자가 신뢰할 수 있는 값이라고 착각하게 된다.
  if (!p.auth_ok) {
    const warn = el('div', 'vt-usage-warn', `재인증 필요 (${p.auth_status})`);
    card.appendChild(warn);
  } else if (p.fetch_status === 'AuthExpired') {
    // terminal 상태다 — 자동 재시도로 감추지 않는다(계획서 §4).
    card.appendChild(el('div', 'vt-usage-warn', '조치 필요 — 인증이 만료됐습니다'));
  }

  if (!p.windows || p.windows.length === 0) {
    // provider:"generic" 등은 바를 그릴 데이터가 없다. 빈 바를 0%로 그리면
    // "안 쓰고 있다"는 거짓 정보가 된다.
    card.appendChild(el('div', 'vt-usage-empty', '사용량 정보 없음'));
  } else {
    for (const w of p.windows) card.appendChild(renderWindow(w));
  }

  if (p.fallback && p.fallback.position != null) {
    const fb = el('div', 'vt-usage-fallback');
    fb.textContent = `폴백 #${p.fallback.position} · ${p.fallback.threshold ?? '?'}%`
      + (p.fallback.armed ? ' · 대기' : ' · 해제');
    fb.classList.toggle('armed', !!p.fallback.armed);
    card.appendChild(fb);
  }
  return card;
}

// N41(60-settings-palette.md §5) — 한도형 섹션만 그린다. 누적형은
// renderCounterSection이 별도로 그린다(둘 중 하나만 있는 사용자가 흔하다 —
// clauth 없이 로컬 LLM만 쓰는 경우, 또는 그 반대).
function renderLimitSection(data) {
  const wrap = el('div', 'vt-usage-limit-section');

  if (!data || !data.available) {
    const reason = data && data.reason;
    const msg = reason === 'disabled' ? '사용량 표시가 꺼져 있습니다 (VT_USAGE_PROVIDER=none).'
      : reason === 'schema' ? '사용량 피드 형식을 알 수 없습니다 (clauth 버전 확인 필요).'
      : reason === 'permission' ? '사용량 피드를 읽을 권한이 없습니다.'
      : reason === 'broken' ? '사용량 피드를 읽는 중입니다…'
      : null;
    if (msg) wrap.appendChild(el('p', 'vt-usage-none', msg));
    return wrap;
  }

  if (data.stale) {
    // 미터를 흐리게 + 이유를 적는다. 흐리기만 하면 사용자는 "왜 흐리지"를 모른다.
    wrap.classList.add('stale');
    wrap.appendChild(el('div', 'vt-usage-warn', '갱신 멈춤 — 아래 값은 마지막으로 받은 것입니다'));
  }

  for (const p of data.profiles || []) wrap.appendChild(renderProfile(p, data.active_profile));
  if (!(data.profiles || []).length) wrap.appendChild(el('p', 'vt-usage-none', '프로필이 없습니다.'));
  return wrap;
}

// N41 — "1840 tok" / "4.1M tok" 같은 사람 단위. 스파크라인·헤드라인 둘 다 쓴다.
function humanTokens(n) {
  const v = typeof n === 'number' ? n : 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M tok`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K tok`;
  return `${v} tok`;
}

// N41 — "1h 8m" 같은 GPU 시간 표시. seconds는 CounterProvider가 누적해 준 값.
function humanDuration(sec) {
  const v = typeof sec === 'number' ? sec : 0;
  if (v < 60) return `${Math.round(v)}s`;
  const m = Math.floor(v / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return remM ? `${h}h ${remM}m` : `${h}h`;
}

// N41 — 7일 일별 토큰 스파크라인을 순수 SVG로 그린다. 라이브러리 없이,
// 고정색이 아니라 **currentColor**로 그려 스킨마다 자동으로 톤이 맞는다
// (20-design-system.md: 그래픽 요소도 토큰만). 값이 전부 0이면(막 시작한
// 모델) 편평한 기준선만 그려 "데이터 없음"과 "0만 있음"을 구분한다.
function _buildSparkline(samples, w = 96, h = 24) {
  const vals = (samples || []).map((s) => (typeof s.tokens === 'number' ? s.tokens : 0));
  if (!vals.length) return { points: '', max: 0 };
  const max = Math.max(...vals, 0);
  const n = vals.length;
  const stepX = n > 1 ? w / (n - 1) : 0;
  const points = vals.map((v, i) => {
    const x = (i * stepX).toFixed(1);
    const y = max > 0 ? (h - (v / max) * h).toFixed(1) : (h - 1).toFixed(1);
    return `${x},${y}`;
  }).join(' ');
  return { points, max };
}

function renderSparkline(samples) {
  const w = 96, h = 24;
  const { points } = _buildSparkline(samples, w, h);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'vt-counter-spark');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  if (points) {
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points', points);
    poly.setAttribute('fill', 'none');
    poly.setAttribute('stroke', 'currentColor');
    poly.setAttribute('stroke-width', '1.5');
    poly.setAttribute('stroke-linejoin', 'round');
    poly.setAttribute('stroke-linecap', 'round');
    svg.appendChild(poly);
  }
  return svg;
}

function renderCounter(c) {
  const card = el('div', 'vt-counter-card');
  const head = el('div', 'vt-counter-head');
  head.appendChild(el('span', 'vt-counter-name', c.label));
  if (c.running) {
    const live = el('span', 'vt-counter-live', '● 실행 중');
    live.title = 'ollama에서 지금 로드되어 있는 모델입니다';
    head.appendChild(live);
  }
  card.appendChild(head);

  const stats = el('div', 'vt-counter-stats');
  stats.appendChild(el('span', 'vt-counter-stat', humanTokens(c.tokens)));
  stats.appendChild(el('span', 'vt-counter-stat', humanDuration(c.seconds)));
  stats.appendChild(el('span', 'vt-counter-stat',
    c.tok_per_sec != null ? `${c.tok_per_sec} tok/s 평균` : '—'));
  card.appendChild(stats);

  card.appendChild(renderSparkline(c.samples));
  return card;
}

// N41 — "누적형 · 한도 없음" 섹션. 소스가 아예 없으면(available:false) 아무것도
// 안 그린다 — 한도형만 쓰는 사용자에게 빈 섹션 타이틀만 보이면 안 된다.
function renderCounterSection(data) {
  const wrap = el('div', 'vt-usage-counter-section');
  if (!data || !data.available) return wrap;

  wrap.appendChild(el('div', 'vt-usage-section-title', '누적형 · 한도 없음'));
  const counters = data.counters || [];
  if (!counters.length) {
    wrap.appendChild(el('p', 'vt-usage-none', '기록이 없습니다 — fsh usage add로 기록하세요.'));
    return wrap;
  }
  for (const c of counters) wrap.appendChild(renderCounter(c));
  return wrap;
}

function renderBody(limitData, counterData, target) {
  const body = target || document.getElementById('vt-usage-body');
  if (!body) return;
  body.innerHTML = '';
  body.appendChild(renderLimitSection(limitData));

  // 한도형·누적형 둘 다 실제로 값이 있을 때만 구분선을 넣는다 — 한쪽만 쓰는
  // 사용자에게 빈 섹션 위 가로줄만 남는 걸 막는다.
  const hasLimit = !!(limitData && limitData.available && (limitData.profiles || []).length);
  const hasCounter = !!(counterData && counterData.available);
  if (hasLimit && hasCounter) body.appendChild(el('hr', 'vt-usage-divider'));
  if (hasCounter) body.appendChild(renderCounterSection(counterData));

  if (!hasLimit && !hasCounter) {
    body.innerHTML = '';
    body.appendChild(el('p', 'vt-usage-none', '사용량 소스가 없습니다.'));
  }
}

// U3 — 프로필 표시. **탭·pane 헤더에 프로필 배지를 달지 않았다.**
// 피드의 `has_live_session`은 "이 프로필로 도는 세션이 있다"는 **전역 사실**이고,
// 어느 tmux 세션이 어느 프로필인지는 `~/.clauth/session_profiles.json`에 있는데
// 계획서 §5가 그 파일 접근을 금지한다(status.json 하나만 읽는다). 매핑 없이
// 탭마다 배지를 붙이면 **틀린 계정을 확신 있게 표시**하게 된다 — A2에서 세운
// "모호하면 아무것도 강조하지 않는다"와 정면으로 어긋난다.
// 그래서 rail 버튼에 전역 지표만 단다: 활성 프로필 이름(title) + 라이브 점.
// 세션별 귀속은 2.1의 `fsh agent claude --profile`(우리가 실행 주체가 되는 시점)
// 이후에 정확해진다.
function paintRailBadge(data) {
  const btn = document.getElementById('vt-rail-usage');
  if (!btn) return;
  const badge = btn.querySelector('.vt-rail-badge')
    || btn.appendChild(Object.assign(document.createElement('span'), { className: 'vt-rail-badge' }));
  const live = (data?.profiles || []).filter((p) => p.has_live_session).map((p) => p.name);
  const active = data?.active_profile;
  btn.title = data?.available
    ? `사용량${active ? ` — 활성: ${active}` : ''}${live.length ? ` · 실행 중: ${live.join(', ')}` : ''}`
    : '사용량';
  badge.hidden = !live.length;
  badge.textContent = live.length ? '●' : '';
  badge.classList.toggle('vt-rail-badge-dot', true);
}

let _timer = null;
let _bgTimer = null;

// N41 — 한도형(/api/usage)과 누적형(/api/usage/counter)을 따로 요청한다.
// 하나가 실패/없음이어도 다른 하나는 그려야 한다(clauth 없이 로컬 LLM만
// 쓰는 사용자, 또는 그 반대) — Promise.allSettled로 서로를 막지 않는다.
async function fetchBoth() {
  const [limitR, counterR] = await Promise.allSettled([
    vtFetch('/api/usage'),
    vtFetch('/api/usage/counter'),
  ]);
  const limitData = limitR.status === 'fulfilled' ? limitR.value : { available: false, reason: 'read-failed' };
  const counterData = counterR.status === 'fulfilled' ? counterR.value : { available: false, reason: 'read-failed' };
  return { limitData, counterData };
}

async function refresh() {
  const { limitData, counterData } = await fetchBoth();
  renderBody(limitData, counterData);
  renderBody(limitData, counterData, document.getElementById('vt-right-rail-body'));
  paintRailBadge(limitData);
}

// 패널이 닫혀 있어도 rail 배지는 최신이어야 한다(그게 "상시 노출"의 의미다).
// 피드가 90초 주기라 60초면 충분하고, 서버 쪽 비용은 mtime 비교 하나다.
// 배지는 한도형(%) 전용이라(rail 버튼 하나에 %를 하나만 실을 수 있다) 누적형은
// 요청하지 않는다 — 자세한 값은 패널/우측 레일에서 본다.
async function refreshBadgeOnly() {
  try {
    const data = await vtFetch('/api/usage');
    paintRailBadge(data);
    // L8/U2: 우측 레일은 **패널이 닫혀 있어도** 최신이어야 한다 — 그게 "상시
    // 노출"의 의미다. 화면에 없으면(compact/regular) 아무 일도 안 한다.
    const rr = document.getElementById('vt-right-rail-body');
    if (rr && rr.offsetParent !== null) {
      let counterData = { available: false, reason: 'read-failed' };
      try { counterData = await vtFetch('/api/usage/counter'); } catch (_) { /* 조용히 무시 */ }
      renderBody(data, counterData, rr);
    }
  } catch (_) { /* 조용히 무시 */ }
}

function showUsage() {
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: '사용량',
    headHTML: '<div class="vt-vw-title">사용량</div>',
    bodyId: 'vt-usage-body',
    extraClass: 'vt-usage',
    onClose: () => { clearInterval(_timer); _timer = null; },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  refresh();
  _timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
}


registerAction('usage.open', showUsage);

// rail 항목이 보이는 환경(=사용량 소스가 있는 환경)에서만 배지를 돌린다.
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('vt-rail-usage');
  if (!btn) return;
  setTimeout(() => {
    if (getComputedStyle(btn).display === 'none') return;   // capability 게이팅에 걸린 환경
    refreshBadgeOnly();
    _bgTimer = setInterval(() => { if (!document.hidden) refreshBadgeOnly(); }, 60000);
  }, 1500);
});

// 테스트 전용 export — DOM/fetch가 없는 순수 계산만(ports.js의 _groupByPid와 같은 패턴).
export { _buildSparkline, humanTokens, humanDuration };
