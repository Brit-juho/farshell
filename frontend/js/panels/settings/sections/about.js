import { vtFetch } from '../../../core/api.js';
import { statusLine, secRow, fmtWhen } from '../controls.js';

// ── 「정보」 ──────────────────────────────────────────────────────────────
export function renderAboutSection() {
  const frag = document.createDocumentFragment();
  const hooks = document.createElement('div');
  hooks.className = 'vt-set-about';
  hooks.textContent = '에이전트 훅 상태 확인 중…';
  frag.appendChild(hooks);

  // A0 연동 — 훅이 등록돼 있지 않으면 상태 배지·큐 자동 투입·TTS가 전부 조용히
  // 동작하지 않는다. "왜 아무 일도 안 일어나지"의 1번 원인이라 여기 보여준다.
  vtFetch('/api/hooks/status').then((r) => {
    const tools = r && r.tools ? r.tools : { claude: { events: r && r.events } };
    if (!Object.keys(tools).length) { hooks.textContent = '훅 상태를 확인할 수 없습니다.'; return; }
    hooks.textContent = '';
    let missing = false;
    for (const [tool, detail] of Object.entries(tools)) {
      const rows = Object.entries((detail && detail.events) || {});
      const title = document.createElement('div');
      title.className = 'vt-set-label';
      title.textContent = tool === 'codex' ? 'Codex 훅' : 'Claude Code 훅';
      hooks.appendChild(title);
      for (const [event, state] of rows) {
        const line = document.createElement('div');
        line.className = 'vt-set-hookrow';
        line.textContent = `${event} — ${state === 'ok' ? '등록됨' : state === 'add' ? '미등록' : '다른 경로'}`;
        line.dataset.state = state;
        hooks.appendChild(line);
        if (state !== 'ok') missing = true;
      }
    }
    if (missing) {
      const hint = document.createElement('div');
      hint.className = 'vt-set-help';
      hint.textContent = "터미널에서 'fsh hooks install'을 실행하면 등록됩니다. 등록 전에는 상태 배지·프롬프트 큐 자동 투입·TTS 요약이 동작하지 않습니다.";
      hooks.appendChild(hint);
    }
  }).catch(() => { hooks.textContent = '훅 상태를 확인할 수 없습니다.'; });

  // U1 — 사용량 소스가 없으면 탭도 HUD 칩도 통째로 사라진다(2.0 게이팅 규칙).
  // 조용히 사라지는 건 의도지만 "왜 사라졌는지"를 볼 곳이 한 군데는 있어야
  // 한다 — 실제로 clauth가 schema 2로 올라가며 꺼진 걸 몇 주 동안 아무도
  // 몰랐다. 켜져 있으면 한 줄, 꺼져 있으면 이유까지 적는다.
  const usage = document.createElement('div');
  usage.className = 'vt-set-about';
  usage.textContent = '사용량 소스 확인 중…';
  frag.appendChild(usage);

  vtFetch('/api/capabilities').then((r) => {
    const cap = (r && r.usage) || {};
    usage.textContent = '';
    const title = document.createElement('div');
    title.className = 'vt-set-label';
    title.textContent = '사용량 소스';
    usage.appendChild(title);

    const line = document.createElement('div');
    line.className = 'vt-set-hookrow';
    line.dataset.state = cap.available ? 'ok' : 'add';
    line.textContent = cap.available
      ? `${cap.provider} — 사용 중 (프로필 ${cap.profiles || 0}개)`
      : `${cap.provider || 'none'} — 표시 안 함`;
    usage.appendChild(line);

    const reason = cap.available ? null : cap.reason;
    const hint = reason === 'disabled' ? '설정에서 껐습니다 (VT_USAGE_PROVIDER=none).'
      : reason === 'schema' ? `사용량 피드 형식(schema ${cap.schema_seen ?? '?'})을 이 버전이 모릅니다. `
        + `지원: ${(cap.schema_supported || []).join(', ') || '-'}. clauth 또는 FarShell을 올리세요.`
      : reason === 'permission' ? '사용량 피드를 읽을 권한이 없습니다 (~/.clauth/status.json).'
      : reason === 'broken' ? '사용량 피드가 깨져 있습니다 (쓰는 중일 수 있습니다).'
      : reason ? '사용량 피드(~/.clauth/status.json)가 없습니다.'
      : null;
    if (hint) {
      const h = document.createElement('div');
      h.className = 'vt-set-help';
      h.textContent = hint;
      usage.appendChild(h);
    }

    // 96번 계획서 — Codex는 clauth와 독립된 두 번째 한도형 소스라 같은
    // 자리에 한 줄 더 둔다(합쳐서 하나로 보여주면 "어느 쪽이 문제인지"를
    // 알 수 없다 — 이 진단 패널의 존재 이유 그대로).
    const codexCap = (r && r.usage_codex) || {};
    const codexLine = document.createElement('div');
    codexLine.className = 'vt-set-hookrow';
    codexLine.dataset.state = codexCap.available ? 'ok' : 'add';
    codexLine.textContent = codexCap.available
      ? `codex — 사용 중`
      : 'codex — 표시 안 함';
    usage.appendChild(codexLine);
    const codexReason = codexCap.available ? null : codexCap.reason;
    const codexHint = codexReason === 'disabled' ? '설정에서 껐습니다 (VT_USAGE_PROVIDER=none).'
      : codexReason === 'expired' ? 'Codex 로그인이 만료됐습니다 — 터미널에서 codex로 다시 로그인하세요.'
      : codexReason === 'no-auth' ? 'Codex 인증 파일이 없습니다 (~/.codex/auth.json) — codex CLI로 먼저 로그인하세요.'
      : codexReason ? 'Codex 사용량을 잠시 가져오지 못했습니다.'
      : null;
    if (codexHint) {
      const h = document.createElement('div');
      h.className = 'vt-set-help';
      h.textContent = codexHint;
      usage.appendChild(h);
    }
  }).catch(() => { usage.textContent = '사용량 소스를 확인할 수 없습니다.'; });

  return frag;
}
