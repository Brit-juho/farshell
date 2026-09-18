// 98-rail-repos-2.1.6.md §3 → ADR-29 C — 「저장소 표시」였던 시트가 **저장소→
// 워크트리→세션** 3단 관리 시트로 넓어졌다. ADR-29 B가 레일 행을 세션으로
// 되돌리면서 레일에서 워크트리 만들기·삭제·열기가 설 자리를 잃었다 — 그 셋을
// 여기로 옮긴다. 표시(숨김) 토글은 그대로 이 시트의 몫이다.
//
// 왜 필요한가(§3 원문): `~/powerlevel10k`(zsh 테마)나 `_tp-qa2-*`(QA 임시
// 워크트리)는 탐색에 걸리지만 프로젝트가 아니다. 지금까지는 `VT_BROWSE_ROOTS`로
// **경계를 통째로 좁히는 것** 말고 손쓸 방법이 없었는데, 그러면 진짜 프로젝트
// 까지 같이 사라진다(실제로 그 맞바꿈이 §1 버그의 원인이었다).
//
// 숨김은 **서버에 저장한다**(`~/.vt/repos.json`, 2.1 D1부터). 그룹 접힘(§2,
// device 스코프)과 반대 판단이다 — 접힘은 화면 상태고, "이게 내 프로젝트인가"는
// 사람의 판단이라 폰에서 숨긴 것이 맥에서도 숨겨져야 한다. 반대로 저장소
// 펼침/접힘(어느 저장소의 워크트리 목록을 보고 있는가)은 이 시트를 열 때마다
// 다시 판단해도 되는 화면 상태라 device·서버 어느 쪽에도 저장하지 않는다.
//
// ⚠ Rail.tsx 안에서만 마운트되는 자식이라 window 브리지 제약(Rail.tsx 상단
// 주석)은 없다 — Rail이 이미 가진 deps(vtFetch)를 그대로 받고, 세션 열기/깨우기
// 는 Rail.tsx의 openRow와 같은 방식으로 window 브리지(attachTmux/switchTo/
// allSessions)를 직접 부른다.
import { createSignal, onCleanup, onMount, For, Show } from 'solid-js';

import { openWorktree, deleteWorktreeRow, type RailDeps } from './rail-fetch.js';
import { WorktreeDialog } from './WorktreeDialog.js';

export interface RepoVisibilityDeps extends RailDeps {}

interface WorktreeItem {
  id: string;
  repo: string;
  repoName: string;
  branch: string;
  isMain: boolean;
  path: string;
  sessions: string[];
}

interface RepoGroup {
  id: string;
  host: string;
  path: string;
  name: string;
  hidden: boolean;
  worktrees: WorktreeItem[];
}

interface Props {
  deps: RepoVisibilityDeps;
  onClose: () => void;
  /** 숨김·워크트리 구성이 바뀌면 레일을 다시 불러오라는 신호. */
  onChanged: () => void;
}

/** 홈 밑이면 `~/…`로 줄인다. 목록의 관심사는 "어디 있는 것인가"이지 절대경로가
 * 아니고, 440px 시트에서 `/Users/<name>/`은 매 행에서 같은 폭을 낭비한다. */
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
  const [groups, setGroups] = createSignal<RepoGroup[]>([]);
  const [roots, setRoots] = createSignal<string[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [errorMsg, setErrorMsg] = createSignal<string | null>(null);
  // 펼친 저장소 id 집합 — 시트를 열 때마다 다 접힌 채로 시작한다(위 머리말
  // 주석: 이건 device·서버 어디에도 저장하지 않는 화면 상태다).
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set());
  // 서버 왕복 중인 대상. 연타로 같은 행을 두 번 보내면 마지막 응답이 이기는
  // 경합이 생긴다 — 그 행만 잠근다(시트 전체를 잠그면 목록 전체가 굳는다).
  // 저장소 숨김은 path로, 워크트리 조작은 id로 키를 잡는다(서로 다른 API).
  const [busyRepo, setBusyRepo] = createSignal<Record<string, boolean>>({});
  const [busyWt, setBusyWt] = createSignal<Record<string, boolean>>({});
  // "+ 워크트리" — 이 값이 있으면 WorktreeDialog가 그 저장소를 기본값으로
  // 띄운다. Rail.tsx가 예전에 하던 "기본 저장소 추측"을 여기서는 안 한다 —
  // 사용자가 이미 어느 저장소 카드에서 눌렀는지로 정해진다. groupId/label을
  // 함께 들고 있는 이유는 생성 완료 후 그 그룹의 탭을 열어야 하기 때문이다
  // (ADR-29 D, onWorktreeCreated).
  const [wtDialogRepo, setWtDialogRepo] = createSignal<{ path: string; groupId: string; label: string } | null>(null);

  const load = async () => {
    try {
      // 설정 화면이므로 **숨긴 것까지** 받는다 — 되돌릴 수 없으면 숨김은
      // 일방통행이 된다(§3 수용 기준).
      const data = (await props.deps.vtFetch('/api/repos?include_hidden=1')) as
        { repos?: RepoGroup[]; roots?: string[] } | null;
      const repos = [...(data?.repos || [])].sort((a, b) => a.name.localeCompare(b.name));
      setGroups(repos);
      setRoots(data?.roots || []);
    } catch (e: any) {
      setErrorMsg(e?.data?.error || e?.message || '저장소 목록을 불러오지 못했습니다');
    } finally {
      setLoading(false);
    }
  };

  onMount(load);

  const toggleExpand = (id: string) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const toggleHidden = async (group: RepoGroup) => {
    const nextHidden = !group.hidden;
    setBusyRepo((b) => ({ ...b, [group.path]: true }));
    // 낙관적 갱신 — 토글은 되돌리기 쉬운 조작이라 왕복을 기다리면 손맛만 죽는다.
    // 실패하면 아래에서 되돌리고 이유를 적는다.
    setGroups((list) => list.map((x) => (x.path === group.path ? { ...x, hidden: nextHidden } : x)));
    try {
      await props.deps.vtFetch('/api/worktrees/hidden', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: group.path, hidden: nextHidden }),
      });
      setErrorMsg(null);
      props.onChanged();
    } catch (e: any) {
      setGroups((list) => list.map((x) => (x.path === group.path ? { ...x, hidden: !nextHidden } : x)));
      setErrorMsg(e?.data?.reason || e?.data?.error || e?.message || '저장에 실패했습니다');
    } finally {
      setBusyRepo((b) => { const n = { ...b }; delete n[group.path]; return n; });
    }
  };

  // 세션 하나를 연다/깨운다 — Rail.tsx의 openRow와 같은 두 갈래(웹 세션이
  // 이미 있으면 전환, 없으면 attach). 여기서는 항상 클릭 = 열기이므로 행
  // 자체가 그룹/수면 개념을 가질 필요가 없다(이 시트의 일은 탐색이 아니라
  // 관리 — 주 탐색은 레일이 한다).
  // ADR-29 D — 세션을 열기 전에 그 그룹의 탭부터 연다(Rail.tsx의 openRow와
  // 같은 순서 — switchTo/attachTmux는 항상 "지금 활성 탭"에 배정하므로
  // 먼저 탭을 옮겨야 한다). 커스텀 그룹(E, 아직 미구현)이 없는 지금은
  // group.id(저장소 sha1)가 곧 groupId 자동 제안 값과 같다.
  const openSession = async (tmuxName: string, groupId: string, worktreeId: string, label: string) => {
    const w = window as any;
    w.openGroupTab?.({ groupId, worktreeId, hostId: 'local', label });
    const all = w.allSessions ? w.allSessions() : {};
    let sid: string | null = null;
    for (const [id, s] of Object.entries<any>(all)) {
      const tn = s.tmuxName || s.tmux_name;
      if (tn === tmuxName) { sid = id; break; }
    }
    if (sid) w.switchTo?.(sid);
    else await w.attachTmux?.(tmuxName);
    props.onClose();
  };

  const newSessionIn = async (wt: WorktreeItem, groupId: string, label: string) => {
    setBusyWt((b) => ({ ...b, [wt.id]: true }));
    try {
      const tmuxName = await openWorktree(props.deps, wt.id);
      if (!tmuxName) { setErrorMsg('세션을 여는 데 실패했습니다'); return; }
      setErrorMsg(null);
      (window as any).openGroupTab?.({ groupId, worktreeId: wt.id, hostId: 'local', label });
      await (window as any).attachTmux?.(tmuxName);
      props.onChanged();
      props.onClose();
    } finally {
      setBusyWt((b) => { const n = { ...b }; delete n[wt.id]; return n; });
    }
  };

  const removeWorktree = async (wt: WorktreeItem) => {
    setBusyWt((b) => ({ ...b, [wt.id]: true }));
    try {
      const label = wt.isMain ? wt.repoName : `${wt.repoName}/${wt.branch}`;
      const result = await deleteWorktreeRow(props.deps, wt.id, label, wt.sessions.length > 0);
      if (result.error) { setErrorMsg(`워크트리 삭제 실패: ${result.error}`); return; }
      if (result.ok) { setErrorMsg(null); await load(); props.onChanged(); }
    } finally {
      setBusyWt((b) => { const n = { ...b }; delete n[wt.id]; return n; });
    }
  };

  const onWorktreeCreated = async (result: any) => {
    const target = wtDialogRepo();
    setWtDialogRepo(null);
    const tmuxName = result?.opened?.tmux_session || null;
    setErrorMsg(null);
    await load();
    props.onChanged();
    if (tmuxName) {
      if (target) (window as any).openGroupTab?.({ groupId: target.groupId, hostId: 'local', label: target.label });
      await (window as any).attachTmux?.(tmuxName);
      props.onClose();
    }
  };

  const onDocKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !wtDialogRepo()) props.onClose(); };
  document.addEventListener('keydown', onDocKey);
  onCleanup(() => document.removeEventListener('keydown', onDocKey));

  const onBackdropClick = (e: MouseEvent) => { if (e.target === e.currentTarget) props.onClose(); };

  return (
    <>
      <div class="vt-viewer-backdrop vt-wtd-backdrop" onClick={onBackdropClick}>
        <div class="vt-viewer-card vt-wtd-card vt-repovis-card" role="dialog" aria-modal="true" aria-label="저장소 관리">
          <div class="vt-wtd-head">
            <span class="vt-wtd-title">저장소 관리</span>
            <button type="button" class="vt-btn sm quiet vt-wtd-x" onClick={props.onClose} aria-label="닫기">×</button>
          </div>
          <div class="vt-wtd-body vt-repovis-body">
            <Show when={loading()}><div class="vt-repovis-empty">불러오는 중…</div></Show>
            <Show when={!loading() && groups().length === 0}>
              <div class="vt-repovis-empty">탐색 경계 안에 git 저장소가 없습니다.</div>
            </Show>
            {/* ⚠ 숨김 표시 클래스를 `hidden`으로 두면 안 된다. 사용자 브라우저에서
                그 행이 통째로 사라지는 것을 실제로 봤다 — 확장 프로그램이 주입한
                사용자 스타일시트(`document.styleSheets`에 안 잡힌다)에
                `.hidden { display:none }`이 있었다. 숨긴 저장소를 **되돌릴 수 있는
                것**이 이 화면의 존재 이유라, 흔한 유틸리티 이름을 피해 `off`를 쓴다. */}
            <For each={groups()}>
              {(group) => (
                <div class="vt-repovis-group">
                  <div class="vt-repovis-row" classList={{ off: !!group.hidden }}>
                    <button
                      type="button"
                      class="vt-repovis-expand"
                      classList={{ expanded: expanded().has(group.id) }}
                      aria-expanded={expanded().has(group.id)}
                      aria-label={expanded().has(group.id) ? '접기' : '펼치기'}
                      onClick={() => toggleExpand(group.id)}
                    >▸</button>
                    <input
                      type="checkbox"
                      checked={!group.hidden}
                      disabled={!!busyRepo()[group.path]}
                      aria-label={`${group.name} 표시`}
                      onChange={() => toggleHidden(group)}
                    />
                    <span class="vt-repovis-name" onClick={() => toggleExpand(group.id)}>{group.name}</span>
                    <Show when={group.worktrees.length > 1}>
                      <span class="vt-repovis-count">워크트리 {group.worktrees.length}</span>
                    </Show>
                    <span class="vt-repovis-path" data-tip={group.path}>{shortenPath(group.path, roots())}</span>
                  </div>
                  <Show when={expanded().has(group.id)}>
                    <div class="vt-repovis-wts">
                      <For each={group.worktrees}>
                        {(wt) => (
                          <div class="vt-repovis-wt">
                            <div class="vt-repovis-wt-head">
                              <span class="vt-repovis-wt-branch">{wt.isMain ? '기본' : wt.branch}</span>
                              <button
                                type="button"
                                class="vt-btn sm quiet"
                                disabled={!!busyWt()[wt.id]}
                                onClick={() => newSessionIn(wt, group.id, group.name)}
                              >+ 새 세션</button>
                              {/* 30-worktree.md §3: 메인 워크트리는 삭제할 수 없다
                                  (서버도 400으로 거절한다) — 메뉴에 애초에 안 띄운다. */}
                              <Show when={!wt.isMain}>
                                <button
                                  type="button"
                                  class="vt-btn sm quiet danger"
                                  disabled={!!busyWt()[wt.id]}
                                  onClick={() => removeWorktree(wt)}
                                >삭제</button>
                              </Show>
                            </div>
                            <Show
                              when={wt.sessions.length > 0}
                              fallback={<div class="vt-repovis-wt-empty">세션 없음</div>}
                            >
                              <div class="vt-repovis-sessions">
                                <For each={wt.sessions}>
                                  {(name) => (
                                    <button type="button" class="vt-repovis-session" onClick={() => openSession(name, group.id, wt.id, group.name)}>
                                      {name}
                                    </button>
                                  )}
                                </For>
                              </div>
                            </Show>
                          </div>
                        )}
                      </For>
                      <button
                        type="button"
                        class="vt-btn sm quiet vt-repovis-add-wt"
                        onClick={() => setWtDialogRepo({ path: group.path, groupId: group.id, label: group.name })}
                      >
                        + 워크트리
                      </button>
                    </div>
                  </Show>
                </div>
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
      <Show when={wtDialogRepo()}>
        {(target) => (
          <WorktreeDialog
            deps={props.deps}
            defaultRepo={target().path}
            onClose={() => setWtDialogRepo(null)}
            onCreated={onWorktreeCreated}
          />
        )}
      </Show>
    </>
  );
}
