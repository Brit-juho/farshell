import { vtFetch } from '../../../core/api.js';
import { statusLine } from '../controls.js';

// ── 「에이전트」 (N9/N45 · 감지 커버리지 표) ────────────────────────────────
// N9 — `report`는 `fsh pane report`로 에이전트가 직접 알려주는 경로다.
// 훅과 같은 1차 신호라 신뢰도도 같은 「높음」이다.
const AGENT_PATH_LABEL = { hook: '훅', report: '자기보고', pty: 'PTY 패턴', none: '없음' };
const AGENT_TRUST_LABEL = { high: '높음', mid: '보통', low: '낮음' };

export function renderAgentsSection() {
  const frag = document.createDocumentFragment();

  const title = document.createElement('div');
  title.className = 'vt-set-label';
  title.textContent = '감지 커버리지';
  frag.appendChild(title);

  const status = statusLine('확인 중…');
  frag.appendChild(status);

  const tableHost = document.createElement('div');
  tableHost.className = 'vt-set-covtable-host';
  frag.appendChild(tableHost);

  const note = document.createElement('div');
  note.className = 'vt-set-help';
  note.textContent = '"보통" 이하는 레일에서 승인 대기를 놓칠 수 있다는 뜻입니다. '
    + '훅이 없는 CLI는 래퍼에서 fsh pane report로 상태를 알리면 "높음"이 됩니다.';
  frag.appendChild(note);

  vtFetch('/api/agents/coverage').then((rows) => {
    if (!Array.isArray(rows) || !rows.length) {
      status.textContent = '커버리지 정보를 확인할 수 없습니다.';
      return;
    }
    status.remove();
    tableHost.appendChild(renderCoverageTable(rows));
  }).catch(() => { status.textContent = '커버리지 정보를 확인할 수 없습니다.'; });

  return frag;
}

function renderCoverageTable(rows) {
  const table = document.createElement('table');
  table.className = 'vt-set-covtable';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['CLI', '경로', '패턴 줄 수', '상태', '신뢰도']) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.dataset.trust = r.trust;

    const cli = document.createElement('td');
    cli.textContent = r.cli;
    cli.className = 'vt-set-covcli';

    const path = document.createElement('td');
    path.textContent = AGENT_PATH_LABEL[r.path] || r.path;
    // 자기보고는 "마지막으로 보고받은 때"가 곧 그 경로가 살아 있다는 증거다.
    if (r.path === 'report' && r.lastReport) {
      path.title = `마지막 보고: ${new Date(r.lastReport * 1000).toLocaleString()}`;
    }

    const lines = document.createElement('td');
    lines.textContent = String(r.patternLines);

    const states = document.createElement('td');
    states.textContent = (r.states && r.states.length) ? r.states.join(', ') : '—';

    const trust = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'vt-set-covtrust';
    badge.dataset.trust = r.trust;
    badge.textContent = AGENT_TRUST_LABEL[r.trust] || r.trust;
    trust.appendChild(badge);

    tr.append(cli, path, lines, states, trust);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

