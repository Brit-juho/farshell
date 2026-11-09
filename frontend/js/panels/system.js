// 시스템 — 이 서버가 도는 기계의 CPU · GPU · RAM · 네트워크(GET /api/system/stats).
// 상태 줄(HUD)의 칩을 누르거나 dock의 「시스템」 탭을 열면 보인다. 값은 2초마다 갱신하고,
// 최근 값의 흐름은 이 화면이 열려 있는 동안 브라우저에 쌓는다(서버는 기록을 들고 있지 않다).
import { openPanel, setPanelPoll } from './panel.js';
import { vtFetch, vtEsc } from '../core/api.js';
import { t } from '../core/i18n.js';

const PANEL_ID = 'vt-system';
const POLL_MS = 2000;
const HISTORY = 60;   // 2초 × 60 = 최근 2분

// 패널이 닫혔다 다시 열려도 흐름이 이어지게 모듈에 둔다.
const _hist = { cpu: [], gpu: [], ram: [], rx: [], tx: [] };

function _push(arr, v) {
  arr.push(typeof v === 'number' && Number.isFinite(v) ? v : null);
  if (arr.length > HISTORY) arr.shift();
}

// 바이트 → "28.6 GB" (RAM·인터페이스 합계용). 단위는 2^10 기준이다.
export function fmtBytes(n) {
  if (!Number.isFinite(n) || n < 0) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

export function fmtRate(bps) {
  return `${fmtBytes(bps)}/s`;
}

// 스파크라인 점 목록. max를 주면(퍼센트는 100) 그 값을 기준으로, 안 주면 보이는 값의 최대를 기준으로 그린다.
export function sparkPoints(vals, w, h, max) {
  const nums = vals.map((v) => (typeof v === 'number' ? v : null));
  const top = max || Math.max(1, ...nums.filter((v) => v != null));
  const step = nums.length > 1 ? w / (HISTORY - 1) : 0;
  const offset = (HISTORY - nums.length) * step;   // 값이 적으면 오른쪽(최근)에 붙인다
  return nums
    .map((v, i) => (v == null ? null : `${(offset + i * step).toFixed(1)},${(h - (Math.min(v, top) / top) * (h - 2) - 1).toFixed(1)}`))
    .filter(Boolean)
    .join(' ');
}

function _spark(vals, max, cls = '') {
  const w = 240;
  const h = 36;
  return `<svg class="vt-sys-spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">`
    + `<polyline points="${sparkPoints(vals, w, h, max)}" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
}

function _tone(pct, warn, err) {
  return pct >= err ? 'err' : pct >= warn ? 'warn' : '';
}

function _row(label, valueHtml, subHtml, pct, tone, sparkHtml) {
  const bar = pct == null ? '' : `<div class="vt-sys-bar" role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100" aria-label="${vtEsc(label)}"><div class="vt-sys-fill ${tone}" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div>`;
  // 시안 P08 순서: 이름(12/600) … 값(18/500 mono) / 보조 정보(11 muted) / 막대 4px / 스파크라인 36.
  return `<section class="vt-sys-card vt-pcard">`
    + `<div class="vt-sys-head"><span class="vt-sys-label">${vtEsc(label)}</span><span class="vt-sys-value ${tone}">${valueHtml}</span></div>`
    + `<div class="vt-sys-sub">${subHtml}</div>`
    + bar + sparkHtml
    + `</section>`;
}

export function renderStats(d) {
  if (!d || !d.ok) return `<div class="vt-vw-empty">${vtEsc(t('시스템 정보를 읽지 못했습니다.'))}</div>`;
  _push(_hist.cpu, d.cpu && d.cpu.pct);
  _push(_hist.gpu, d.gpu && d.gpu.pct);
  _push(_hist.ram, d.ram && d.ram.pct);
  _push(_hist.rx, d.net && d.net.rx_bps);
  _push(_hist.tx, d.net && d.net.tx_bps);
  const parts = [`<div class="vt-sys-platform">${vtEsc(d.platform || '')}</div>`];
  if (d.cpu) {
    parts.push(_row('CPU', `${Math.round(d.cpu.pct)}%`, vtEsc(t('{n}코어', { n: d.cpu.cores })),
      d.cpu.pct, _tone(d.cpu.pct, 75, 90), _spark(_hist.cpu, 100)));
  }
  if (d.gpu) {
    const sub = [];
    if (d.gpu.name) sub.push(d.gpu.name);
    if (d.gpu.cores) sub.push(t('{n}코어', { n: d.gpu.cores }));
    if (d.gpu.mem_total) sub.push(`${fmtBytes(d.gpu.mem_used)} / ${fmtBytes(d.gpu.mem_total)}`);
    if (d.gpu.count > 1) sub.push(t('{n}개 중 가장 바쁜 것', { n: d.gpu.count }));
    parts.push(_row('GPU', `${Math.round(d.gpu.pct)}%`, vtEsc(sub.join(' · ')),
      d.gpu.pct, _tone(d.gpu.pct, 75, 90), _spark(_hist.gpu, 100)));
  }
  if (d.ram) {
    parts.push(_row('RAM', `${Math.round(d.ram.pct)}%`,
      vtEsc(`${fmtBytes(d.ram.used)} / ${fmtBytes(d.ram.total)}`),
      d.ram.pct, _tone(d.ram.pct, 75, 90), _spark(_hist.ram, 100)));
  }
  if (d.net) {
    // 받기·보내기는 같은 눈금으로 겹쳐 그린다 — 따로 맞추면 작은 쪽이 커 보인다.
    const top = Math.max(1, ..._hist.rx.concat(_hist.tx).filter((v) => v != null));
    const both = _spark(_hist.rx, top, 'rx') + _spark(_hist.tx, top, 'tx');
    parts.push(_row(t('네트워크'),
      `<span class="rx">↓ ${vtEsc(fmtRate(d.net.rx_bps))}</span> <span class="tx">↑ ${vtEsc(fmtRate(d.net.tx_bps))}</span>`,
      vtEsc((d.net.interfaces || []).join(', ')), null, '', `<div class="vt-sys-dual">${both}</div>`));
  }
  if (parts.length === 1) parts.push(`<div class="vt-vw-empty">${vtEsc(t('시스템 정보를 읽지 못했습니다.'))}</div>`);
  return parts.join('');
}

async function refresh(body) {
  let d = null;
  try {
    d = await vtFetch('/api/system/stats');
  } catch (_) { /* 아래에서 빈 상태로 그린다 */ }
  if (!body.isConnected) return;
  body.innerHTML = renderStats(d);
}

export function showSystem(opts = {}) {
  const panel = openPanel({
    paneHost: opts.paneHost || null,
    paneId: opts.paneId || null,
    id: PANEL_ID,
    ariaLabel: t('시스템'),
    headHTML: `<div class="vt-vw-title">${t('시스템')}</div><span class="vt-ph-ctx">${t('2초마다')}</span>`,
    bodyId: 'vt-system-body',
    extraClass: 'vt-system',
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  const body = panel.ctx.byId('vt-system-body');
  refresh(body);
  setPanelPoll(panel.el, POLL_MS, () => refresh(body));
}
