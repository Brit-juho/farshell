// 98-rail-repos-2.1.6.md §3 — 「저장소 표시」 시트. 레일 그룹 헤더의 ⚙에서 연다.
//
// 왜 필요한가: `~/powerlevel10k`(zsh 테마)나 `_tp-qa2-*`(QA 임시 워크트리)는
// 탐색에 걸리지만 프로젝트가 아니다. 지금까지는 `VT_BROWSE_ROOTS`로 **경계를
// 통째로 좁히는 것** 말고 손쓸 방법이 없었는데, 그러면 진짜 프로젝트까지 같이
// 사라진다(실제로 그 맞바꿈이 §1 버그의 원인이었다).
//
// 숨김은 **서버에 저장한다**(`~/.vt/repos.json`, 2.1 D1부터 — 이전엔
// `rail-repos.json`이었다). 그룹 접힘(§2, device
// 스코프)과 반대 판단이다 — 접힘은 화면 상태고, "이게 내 프로젝트인가"는
// 사람의 판단이라 폰에서 숨긴 것이 맥에서도 숨겨져야 한다.
//
// ⚠ Rail.tsx 안에서만 마운트되는 자식이라 window 브리지 제약(Rail.tsx 상단
// 주석)은 없다 — Rail이 이미 가진 deps(vtFetch)를 그대로 받는다.
import { createSignal, onCleanup, onMount, For, Show } from 'solid-js';

export interface RepoVisibilityDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
}

interface RepoItem {
  id: string;
  path: string;
  repo: string;
  repoName: string;
  branch: string;
  isMain: boolean;
  hidden?: boolean;
}

interface Props {
  deps: RepoVisibilityDeps;
  onClose: () => void;
  /** 숨김이 바뀌면 레일을 다시 불러오라는 신호. */
  onChanged: () => void;
}

/** 홈 밑이면 `~/…`로 줄인다. 목록의 관심사는 "어디 있는 것인가"이지 절대경로가
 * 아니고, 252px 옆에 뜨는 시트에서 `/Users/<name>/`은 매 행에서 같은 폭을 낭비한다. */
function shortenPath(path: string, roots: string[]): string {
  for (const root of roots) {
    if (root && path.startsWith(root + '/')) {
      const rest = path.slice(root.length + 1);
      const cut = rest.lastIndexOf('/');
      return cut > 0 ? rest.slice(0, cut) : '~';
    }
  }
  const cut = path.lastIndexOf('/');
  return cut > 0 ? path.slice(0, cut) : path;
}

export function RepoVisibility(props: Props) {
  const [items, setItems] = createSignal<RepoItem[]>([]);
  const [roots, setRoots] = createSignal<string[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [errorMsg, setErrorMsg] = createSignal<string | null>(null);
  // 서버 왕복 중인 경로. 연타로 같은 행을 두 번 보내면 마지막 응답이 이기는
  // 경합이 생긴다 — 그 행만 잠근다(시트 전체를 잠그면 목록 전체가 굳는다).
  const [busy, setBusy] = createSignal<Record<string, boolean>>({});

  const load = async () => {
    try {
      // 설정 화면이므로 **숨긴 것까지** 받는다 — 되돌릴 수 없으면 숨김은
      // 일방통행이 된다(§3 수용 기준).
      const data = (await props.deps.vtFetch('/api/worktrees?include_hidden=1')) as
        { worktrees?: RepoItem[]; roots?: string[] } | null;
      const all = data?.worktrees || [];
      // 저장소 단위로 접는다. 시트에서 고르는 단위는 "이 저장소"이고, 부가
      // 워크트리는 본체를 숨기면 같이 숨는다(repo_store.is_hidden).
      const seen = new Set<string>();
      const repos: RepoItem[] = [];
      for (const w of all) {
        if (!w.isMain || seen.has(w.repo)) continue;
        seen.add(w.repo);
        repos.push(w);
      }
      repos.sort((a, b) => a.repoName.localeCompare(b.repoName));
      setItems(repos);
      setRoots(data?.roots || []);
    } catch (e: any) {
      setErrorMsg(e?.data?.error || e?.message || '저장소 목록을 불러오지 못했습니다');
    } finally {
      setLoading(false);
    }
  };

  onMount(load);

  const toggle = async (item: RepoItem) => {
    const nextHidden = !item.hidden;
    setBusy((b) => ({ ...b, [item.path]: true }));
    // 낙관적 갱신 — 토글은 되돌리기 쉬운 조작이라 왕복을 기다리면 손맛만 죽는다.
    // 실패하면 아래에서 되돌리고 이유를 적는다.
    setItems((list) => list.map((x) => (x.path === item.path ? { ...x, hidden: nextHidden } : x)));
    try {
      await props.deps.vtFetch('/api/worktrees/hidden', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: item.path, hidden: nextHidden }),
      });
      setErrorMsg(null);
      props.onChanged();
    } catch (e: any) {
      setItems((list) => list.map((x) => (x.path === item.path ? { ...x, hidden: !nextHidden } : x)));
      setErrorMsg(e?.data?.reason || e?.data?.error || e?.message || '저장에 실패했습니다');
    } finally {
      setBusy((b) => { const n = { ...b }; delete n[item.path]; return n; });
    }
  };

  const onDocKey = (e: KeyboardEvent) => { if (e.key === 'Escape') props.onClose(); };
  document.addEventListener('keydown', onDocKey);
  onCleanup(() => document.removeEventListener('keydown', onDocKey));

  const onBackdropClick = (e: MouseEvent) => { if (e.target === e.currentTarget) props.onClose(); };

  return (
    <div class="vt-viewer-backdrop vt-wtd-backdrop" onClick={onBackdropClick}>
      <div class="vt-viewer-card vt-wtd-card vt-repovis-card" role="dialog" aria-modal="true" aria-label="저장소 표시">
        <div class="vt-wtd-head">
          <span class="vt-wtd-title">저장소 표시</span>
          <button type="button" class="vt-btn sm quiet vt-wtd-x" onClick={props.onClose} aria-label="닫기">×</button>
        </div>
        <div class="vt-wtd-body vt-repovis-body">
          <Show when={loading()}><div class="vt-repovis-empty">불러오는 중…</div></Show>
          <Show when={!loading() && items().length === 0}>
            <div class="vt-repovis-empty">탐색 경계 안에 git 저장소가 없습니다.</div>
          </Show>
          {/* ⚠ 숨김 표시 클래스를 `hidden`으로 두면 안 된다. 사용자 브라우저에서
              그 행이 통째로 사라지는 것을 실제로 봤다 — 확장 프로그램이 주입한
              사용자 스타일시트(`document.styleSheets`에 안 잡힌다)에
              `.hidden { display:none }`이 있었다. 숨긴 저장소를 **되돌릴 수 있는
              것**이 이 화면의 존재 이유라, 흔한 유틸리티 이름을 피해 `off`를 쓴다. */}
          <For each={items()}>
            {(item) => (
              <label class="vt-repovis-row" classList={{ off: !!item.hidden }}>
                <input
                  type="checkbox"
                  checked={!item.hidden}
                  disabled={!!busy()[item.path]}
                  onChange={() => toggle(item)}
                />
                <span class="vt-repovis-name">{item.repoName}</span>
                <span class="vt-repovis-path" data-tip={item.path}>{shortenPath(item.path, roots())}</span>
              </label>
            )}
          </For>
        </div>
        <Show when={errorMsg()}><div class="vt-wtd-error vt-repovis-error">{errorMsg()}</div></Show>
        {/* §3 — "왜 이것들이 목록에 있나"의 답. 지금까지 화면 어디에도 없었다.
            경계 자체는 **읽기 전용**이다: `~/.vt.env`는 `lib/vt_env.sh`가 단일
            소유자이고, 설정 파일을 다른 경로로 고쳐 쓰지 않는다는 규칙이 있다. */}
        <div class="vt-repovis-foot">
          <span class="vt-repovis-foot-label">탐색 경계</span>
          <span class="vt-repovis-foot-value" data-tip="VT_BROWSE_ROOTS" data-tip-sub="~/.vt.env">
            {roots().join(' · ') || '—'}
          </span>
        </div>
      </div>
    </div>
  );
}
