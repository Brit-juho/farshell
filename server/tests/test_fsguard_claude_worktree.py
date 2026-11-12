"""Claude Code가 만든 워크트리(`<저장소>/.claude/worktrees/<이름>`)의 좁은 예외(세션 로직 점검 단계 4, D3).

- Git 위치 정보 호출(`git_meta=True`)만 그 `.claude` 한 칸을 거부에서 뺀다. 파일 열람 경로는 그대로 막힌다.
- Git 연결이 확인될 때만이다: `<저장소>/.git`이 폴더, `<워크트리>/.git`의 gitdir가 `<저장소>/.git/worktrees/` 아래.
"""

import subprocess

import pytest

import fsguard


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "code"
    repo = root / "app"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
    tree = repo / ".claude" / "worktrees" / "fix-x"
    _git(repo, "worktree", "add", "-q", "-b", "fix-x", str(tree))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    return repo.resolve(), tree.resolve()


def test_git_metadata_calls_see_a_claude_worktree(repo):
    _, tree = repo
    (tree / "src").mkdir()
    assert fsguard.resolve_under_roots(str(tree), git_meta=True) == tree
    assert fsguard.resolve_under_roots(str(tree / "src"), git_meta=True) == tree / "src"


def test_file_browsing_stays_denied(repo):
    _, tree = repo
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(tree))
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(tree / "README"))


def test_other_hidden_or_denied_parts_inside_stay_denied(repo):
    _, tree = repo
    for sub in (".ssh", ".codex", ".secret"):
        (tree / sub).mkdir()
        with pytest.raises(fsguard.FsDenied):
            fsguard.resolve_under_roots(str(tree / sub), git_meta=True)


@pytest.mark.parametrize("case", ["plain_folder", "foreign_gitdir", "settings_folder"])
def test_only_a_verified_worktree_is_excepted(repo, tmp_path, case):
    main, _ = repo
    if case == "plain_folder":
        target = main / ".claude" / "worktrees" / "fake"
        target.mkdir()
    elif case == "foreign_gitdir":
        target = main / ".claude" / "worktrees" / "spoof"
        target.mkdir()
        other = tmp_path / "elsewhere" / ".git" / "worktrees" / "spoof"
        other.mkdir(parents=True)
        (target / ".git").write_text(f"gitdir: {other}\n")
    else:
        target = main / ".claude" / "settings"
        target.mkdir()
    with pytest.raises(fsguard.FsDenied):
        fsguard.resolve_under_roots(str(target), git_meta=True)


def test_session_context_reads_a_claude_worktree(repo):
    import session_context
    main, tree = repo
    data = session_context._context(str(tree))
    assert data["status"] == "ready" and data["branch"] == "fix-x"
    assert data["worktree"]["kind"] == "linked" and data["worktree"]["repositoryRoot"] == str(main)


def test_catalog_places_a_claude_worktree_under_its_repository(repo):
    from session_catalog import inventory
    main, tree = repo
    placement = inventory.inspect_workspace(str(tree))
    assert placement and placement["kind"] == "worktree" and placement["project_path"] == str(main)
