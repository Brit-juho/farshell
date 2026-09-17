// N44 — 워크트리 만들기 다이얼로그(30-worktree.md §3). Rail.tsx 안에서만
// 마운트되는 자식 컴포넌트라 별도 window 브리지 제약(Rail.tsx 상단 주석)이
// 없다 — Rail.tsx가 이미 갖고 있는 deps(vtFetch)를 그대로 받는다.
//
// 기준 브랜치 선택은 문서 목업상 드롭다운([master ▾])이지만, 브랜치 목록을
// 내려주는 API가 없다(이번 범위는 "새 API를 만들지 않는다" — 90-verification.md
// §4-3). git dock(소스컨트롤 탭, 40-dock-git.md)이 브랜치 API를 갖게 되면
// 그걸로 바꿀 수 있게 텍스트 입력으로 둔다 — 자유 입력이라 기능은 동일하다.
import { createSignal, createEffect, onCleanup, For, Show } from 'solid-js';

export interface WorktreeDialogDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
}

interface Props {
  deps: WorktreeDialogDeps;
  defaultRepo: string;
  onClose: () => void;
  onCreated: (result: any) => void;
}

// 30-worktree.md §3 5단계: 워크트리를 만들자마자 붙일 에이전트 — server/agents.py의
// AGENTS 표와 같은 이름(claude/codex/aider/gemini). 그 표를 노출하는 API가 없어
// (새 API 금지, 위 주석과 같은 이유) 여기 작은 고정 목록으로 중복 정의한다 —
// bin/fsh 도움말에도 이미 같은 네 이름이 공개돼 있어(CLAUDE.md 기능표) 숨은
// 구현 세부사항은 아니다. server/agents.py가 늘어나면 이 목록도 같이 늘려야 한다.
const AGENT_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '없음' },
  { value: 'claude', label: 'Claude' },
  { value: 'codex', label: 'Codex' },
  { value: 'aider', label: 'Aider' },
  { value: 'gemini', label: 'Gemini' },
];

const NAME_RE = /^[A-Za-z0-9_-]+$/;

export function WorktreeDialog(props: Props) {
  const [repo, setRepo] = createSignal(props.defaultRepo);
  const [base, setBase] = createSignal('master');
  const [name, setName] = createSignal('');
  const [branch, setBranch] = createSignal('');
  const [branchEdited, setBranchEdited] = createSignal(false);
  const [portsEnabled, setPortsEnabled] = createSignal(true);
  const [portBase, setPortBase] = createSignal('');
  const [nodeModules, setNodeModules] = createSignal<'symlink' | 'copy' | 'none'>('symlink');
  const [envMode, setEnvMode] = createSignal<'inherit' | 'empty' | 'none'>('inherit');
  const [agent, setAgent] = createSignal('');
  const [warnings, setWarnings] = createSignal<string[]>([]);
  const [submitting, setSubmitting] = createSignal(false);
  const [errorMsg, setErrorMsg] = createSignal<string | null>(null);

  // 이름을 입력하면 브랜치를 자동으로 따라간다(문서: "기본 = 이름에서 유도,
  // 편집 가능") — 사용자가 브랜치를 직접 건드리면(branchEdited) 더 이상 안 따라간다.
  createEffect(() => {
    if (!branchEdited()) setBranch(name() ? `feat/${name()}` : '');
  });

  // §3: "다이얼로그는 만들기 전에 GET /api/worktrees/precheck로 같은 판정을
  // 받아 배너를 먼저 띄운다." repo/base가 바뀔 때마다 디바운스로 다시 묻는다.
  let precheckTimer: ReturnType<typeof setTimeout> | undefined;
  createEffect(() => {
    const r = repo(), b = base();
    if (precheckTimer) clearTimeout(precheckTimer);
    if (!r || !b) { setWarnings([]); return; }
    precheckTimer = setTimeout(async () => {
      try {
        const data = await props.deps.vtFetch(`/api/worktrees/precheck?repo=${encodeURIComponent(r)}&base=${encodeURIComponent(b)}`) as { warnings?: string[] };
        setWarnings(data?.warnings || []);
      } catch (_) {
        setWarnings([]);
      }
    }, 400);
  });
  onCleanup(() => { if (precheckTimer) clearTimeout(precheckTimer); });

  const nameValid = () => NAME_RE.test(name());
  const canSubmit = () => !!repo() && !!base() && !!name() && nameValid() && !submitting();

  const submit = async () => {
    if (!canSubmit()) return;
    setErrorMsg(null);
    setSubmitting(true);
    const ports: { enabled: boolean; base?: number } = { enabled: portsEnabled() };
    if (portsEnabled() && portBase().trim() && Number.isFinite(Number(portBase()))) {
      ports.base = Number(portBase());
    }
    const body = {
      repo: repo(),
      base: base(),
      name: name(),
      branch: branch() || `feat/${name()}`,
      ports,
      nodeModules: nodeModules(),
      env: envMode(),
      agent: agent() || null,
    };
    try {
      const res = await props.deps.vtFetch('/api/worktrees', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      props.onCreated(res);
    } catch (e: any) {
      setErrorMsg(e?.data?.error || e?.message || '워크트리 생성 실패');
    } finally {
      setSubmitting(false);
    }
  };

  const onDocKey = (e: KeyboardEvent) => { if (e.key === 'Escape') props.onClose(); };
  document.addEventListener('keydown', onDocKey);
  onCleanup(() => document.removeEventListener('keydown', onDocKey));

  const onBackdropClick = (e: MouseEvent) => { if (e.target === e.currentTarget) props.onClose(); };

  return (
    <div class="vt-viewer-backdrop vt-wtd-backdrop" onClick={onBackdropClick}>
      <div class="vt-viewer-card vt-wtd-card" role="dialog" aria-modal="true" aria-label="워크트리 만들기">
        <div class="vt-wtd-head">
          <span class="vt-wtd-title">워크트리 만들기</span>
          <button type="button" class="vt-btn sm quiet vt-wtd-x" onClick={props.onClose} aria-label="닫기">×</button>
        </div>
        <div class="vt-wtd-body">
          <label class="vt-wtd-row">
            <span class="vt-wtd-label">저장소</span>
            <input class="vt-wtd-input" value={repo()} onInput={(e) => setRepo(e.currentTarget.value)} placeholder="/Users/.../repo" />
          </label>
          <label class="vt-wtd-row">
            <span class="vt-wtd-label">기준</span>
            <input class="vt-wtd-input" value={base()} onInput={(e) => setBase(e.currentTarget.value)} placeholder="master" />
          </label>
          <label class="vt-wtd-row">
            <span class="vt-wtd-label">이름</span>
            <input class="vt-wtd-input" value={name()} onInput={(e) => setName(e.currentTarget.value)} placeholder="queue-scope" />
          </label>
          <Show when={name() && !nameValid()}>
            <div class="vt-wtd-error">이름은 영숫자·-·_ 만 가능합니다</div>
          </Show>
          <label class="vt-wtd-row">
            <span class="vt-wtd-label">브랜치</span>
            <input
              class="vt-wtd-input"
              value={branch()}
              onInput={(e) => { setBranchEdited(true); setBranch(e.currentTarget.value); }}
              placeholder="feat/..."
            />
          </label>

          <div class="vt-wtd-section-title">무엇을 격리할까</div>

          <div class="vt-wtd-row">
            <span class="vt-wtd-label">포트 범위</span>
            <input type="checkbox" checked={portsEnabled()} onChange={(e) => setPortsEnabled(e.currentTarget.checked)} />
            <Show when={portsEnabled()}>
              <input
                class="vt-wtd-input vt-wtd-input-narrow"
                value={portBase()}
                onInput={(e) => setPortBase(e.currentTarget.value)}
                placeholder="자동(5200~)"
                inputmode="numeric"
              />
            </Show>
          </div>

          <div class="vt-wtd-row">
            <span class="vt-wtd-label">node_modules</span>
            <select class="vt-wtd-select" value={nodeModules()} onChange={(e) => setNodeModules(e.currentTarget.value as any)}>
              <option value="symlink">심링크</option>
              <option value="copy">복사</option>
              <option value="none">안 함</option>
            </select>
          </div>

          <Show when={warnings().includes('lockfile_mismatch')}>
            <div class="vt-wtd-warn">
              ⚠ {base()}와 package.json이 다릅니다 — 심링크로 두면 깨질 수 있습니다.
              <button type="button" class="vt-btn sm vt-wtd-warn-fix" onClick={() => setNodeModules('copy')}>복사로</button>
            </div>
          </Show>

          <div class="vt-wtd-row">
            <span class="vt-wtd-label">.env</span>
            <select class="vt-wtd-select" value={envMode()} onChange={(e) => setEnvMode(e.currentTarget.value as any)}>
              <option value="inherit">상속</option>
              <option value="empty">빈 값</option>
              <option value="none">안 함</option>
            </select>
          </div>

          <div class="vt-wtd-row">
            <span class="vt-wtd-label">에이전트</span>
            <select class="vt-wtd-select" value={agent()} onChange={(e) => setAgent(e.currentTarget.value)}>
              <For each={AGENT_OPTIONS}>
                {(o) => <option value={o.value}>{o.label}</option>}
              </For>
            </select>
          </div>

          <Show when={errorMsg()}>
            <div class="vt-wtd-error">{errorMsg()}</div>
          </Show>
        </div>
        <div class="vt-wtd-foot">
          <button type="button" class="vt-wtd-cancel" onClick={props.onClose}>취소</button>
          <button type="button" class="vt-wtd-submit" disabled={!canSubmit()} onClick={submit}>
            {submitting() ? '만드는 중…' : '만들고 전환'}
          </button>
        </div>
      </div>
    </div>
  );
}
