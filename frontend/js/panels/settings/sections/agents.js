import { vtFetch } from '../../../core/api.js';
import { statusLine } from '../controls.js';
import { t } from '../../../core/i18n.js';
import { icon } from '../../../ui/icons.js';

// ── 「에이전트」 (N9/N45 · 감지 방법과 정확도 표) ─────────────────────────────
// N9 — `report`는 `fsh pane report`로 에이전트가 직접 알려주는 경로다.
// 훅과 같은 1차 신호라 정확도도 같은 「높음」이다.
// 열 이름: 「경로」→「감지 방법」, 「신뢰도」→「정확도」(내부 용어 정리). 상태 값 waiting 은 화면 다른 곳과 같은 「개입 필요」.
const AGENT_PATH_LABEL = { hook: t('훅'), report: t('자기보고'), pty: t('화면 패턴(PTY)'), none: t('없음') };
const AGENT_TRUST_LABEL = { high: t('높음'), mid: t('보통'), low: t('낮음') };
const AGENT_STATE_LABEL = { waiting: t('개입 필요'), working: t('작업 중'), done: t('완료'), idle: t('대기') };

export function renderAgentsSection() {
  const frag = document.createDocumentFragment();

  const status = statusLine(t('확인 중…'));
  frag.appendChild(status);

  const tableHost = document.createElement('div');
  tableHost.className = 'vt-set-covtable-host';
  frag.appendChild(tableHost);

  const note = document.createElement('div');
  note.className = 'vt-set-note';
  // 한 문장으로 번역하고 명령 자리({cmd})만 코드 글씨로 바꿔 끼운다.
  const [before, after] = t('정확도가 「보통」 이하면 사이드바에서 입력 대기를 놓칠 수 있습니다. 훅이 없는 CLI는 래퍼에서 {cmd}로 상태를 알리면 「높음」이 됩니다.', { cmd: '\u0000' }).split('\u0000');
  const code = document.createElement('code'); code.className = 'vt-set-pcode'; code.textContent = 'fsh pane report';
  note.append(document.createTextNode(before), code, document.createTextNode(after || ''));
  frag.appendChild(note);

  const refresh = document.createElement('button');
  refresh.type = 'button'; refresh.className = 'vt-btn sm quiet';
  refresh.innerHTML = icon('refresh-cw', 14, 2);
  refresh.append(document.createTextNode(t('새로고침')));

  const load = () => vtFetch('/api/agents/coverage').then((rows) => {
    if (!Array.isArray(rows) || !rows.length) {
      status.hidden = false; status.textContent = t('커버리지 정보를 확인할 수 없습니다.');
      return;
    }
    status.hidden = true;
    tableHost.replaceChildren(renderCoverageTable(rows));
  }).catch(() => { status.hidden = false; status.textContent = t('커버리지 정보를 확인할 수 없습니다.'); });
  refresh.addEventListener('click', load);
  load();

  return { body: frag, aside: refresh };
}

function renderCoverageTable(rows) {
  const table = document.createElement('table');
  table.className = 'vt-set-covtable';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['CLI', t('감지 방법'), t('패턴 줄 수'), t('알 수 있는 상태'), t('정확도')]) {
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

    const pathText = AGENT_PATH_LABEL[r.path] || r.path;
    const statesText = (r.states && r.states.length) ? r.states.map((x) => AGENT_STATE_LABEL[x] || x).join(', ') : '—';

    const cli = document.createElement('td');
    cli.textContent = r.cli;
    cli.className = 'vt-set-covcli';
    // 폰: 표 대신 두 줄 행 — 이름 + 「감지 방법 · 패턴 줄 수 · 알 수 있는 상태」, 오른쪽에 정확도(CSS가 나머지 칸을 접는다).
    const sub = document.createElement('span');
    sub.className = 'vt-set-covsub';
    sub.textContent = t('{path} · 패턴 {n}줄 · 알 수 있는 상태 {states}', { path: pathText, n: r.patternLines, states: statesText });
    cli.appendChild(sub);

    const path = document.createElement('td');
    path.textContent = pathText;
    // 자기보고는 "마지막으로 보고받은 때"가 곧 그 경로가 살아 있다는 증거다.
    if (r.path === 'report' && r.lastReport) {
      path.title = t('마지막 보고: {time}', { time: new Date(r.lastReport * 1000).toLocaleString() });
    }

    const lines = document.createElement('td');
    lines.textContent = String(r.patternLines);

    const states = document.createElement('td');
    states.textContent = statesText;

    // 정확도: 높음 = 조용한 회색 체크, 보통 = 회색 칩, 낮음만 호박색 칩.
    const trust = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'vt-tag vt-set-covtrust';
    badge.dataset.trust = r.trust;
    if (r.trust === 'high') badge.insertAdjacentHTML('beforeend', icon('check', 12, 2));
    badge.append(document.createTextNode(AGENT_TRUST_LABEL[r.trust] || r.trust));
    trust.appendChild(badge);

    tr.append(cli, path, lines, states, trust);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}
