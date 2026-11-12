"""세션 context는 실제 현재 위치의 Git 정보이며 실패가 빈 성공으로 바뀌지 않는다."""
import asyncio
import subprocess

import pytest

import fsguard
import session_context as context


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    context._cache.clear()
    context._workspace_cache.clear()
    context._repo_cache.clear()
    p = tmp_path / "project"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    git(p, "config", "user.email", "test@example.com")
    git(p, "config", "user.name", "test")
    (p / "file.txt").write_text("one\n")
    git(p, "add", ".")
    git(p, "commit", "-qm", "initial")
    yield p
    context._cache.clear()
    context._workspace_cache.clear()
    context._repo_cache.clear()


def test_subdirectory_branch_changes_and_detached_head(repo, monkeypatch):
    sub = repo / "src"
    sub.mkdir()
    monkeypatch.setattr(context, "TTL", 0)
    first = context._context(str(sub))
    assert first["root"] == str(repo.resolve())
    assert first["cwd"] == str(sub)
    assert first["branch"] == "main"
    git(repo, "checkout", "-qb", "feature")
    (repo / "file.txt").write_text("one\ntwo\n")
    second = context._context(str(sub))
    assert second["branch"] == "feature"
    assert second["changed"] == {"files": 1, "add": 1, "del": 0}
    git(repo, "checkout", "--detach", "-q")
    assert context._context(str(sub))["branch"] is None
    assert context._context(str(sub))["status"] == "ready"


def test_nonrepo_is_not_a_lookup_failure(repo):
    out = context._context(str(repo.parent))
    assert out["status"] == "nonrepo"
    assert out["branch"] is None and out["changed"] is None


def test_failure_preserves_only_same_cwd_and_recovers(repo, monkeypatch):
    monkeypatch.setattr(context, "TTL", 0)
    monkeypatch.setattr(context, "RETRY_TTL", 0)
    good = context._context(str(repo))
    read = context._read
    monkeypatch.setattr(context, "_read", lambda _: (_ for _ in ()).throw(OSError("temporary")))
    failed = context._context(str(repo))
    assert failed["status"] == "unavailable"
    assert failed["branch"] == good["branch"] and failed["changed"] == good["changed"]
    moved = context._context(str(repo.parent))
    assert moved["status"] == "unavailable" and moved["branch"] is None
    monkeypatch.setattr(context, "_read", read)
    assert context._context(str(repo))["status"] == "ready"


def test_boundary_denial_discards_last_success(repo, monkeypatch):
    assert context._context(str(repo))["branch"] == "main"
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(repo / "different"))
    denied = context._context(str(repo))
    assert denied["status"] == "unavailable" and denied["root"] is None
    assert denied["changed"] is None and denied["branch"] is None


def test_corrupt_git_directory_is_unavailable_not_nonrepo(repo, monkeypatch):
    git(repo, "checkout", "-qb", "feature")
    (repo / ".git" / "HEAD").write_text("broken head\n")
    assert context._context(str(repo))["status"] == "unavailable"


def test_git_root_above_boundary_is_not_exposed(repo, monkeypatch):
    sub = repo / "src"
    sub.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(sub))
    assert context._context(str(sub))["root"] is None


def test_same_cwd_reads_once_across_sessions(repo, monkeypatch):
    calls = []
    read = context._read
    def counted(cwd):
        calls.append(cwd)
        return read(cwd)
    monkeypatch.setattr(context, "_read", counted)
    result = asyncio.run(context.contexts_for([str(repo)] * 4))
    assert calls == [str(repo)] and result[str(repo)]["branch"] == "main"
    asyncio.run(context.contexts_for([str(repo)]))
    assert calls == [str(repo)]


def test_main_linked_subdirectory_detached_and_nonrepo(repo, monkeypatch):
    tree = repo.parent / "task a 한글"
    git(repo, "worktree", "add", "-qb", "feature", str(tree))
    sub = tree / "packages" / "src"
    sub.mkdir(parents=True)
    main = context._context(str(repo))
    linked = context._context(str(sub))
    assert main["worktree"]["kind"] == "main"
    common = repo.resolve() / ".git"
    assert linked["worktree"] == {"kind": "linked", "status": "ready",
        "repositoryRoot": str(repo.resolve()), "name": tree.name, "relative": "packages/src",
        "gitDir": str(common / "worktrees" / tree.name.replace(" ", "-")), "commonDir": str(common)}
    assert main["worktree"]["commonDir"] == linked["worktree"]["commonDir"], "같은 저장소는 common-dir가 같다"
    assert (main["ahead"], main["behind"]) == (0, 0)
    assert linked["branch"] == "feature"
    monkeypatch.setattr(context, "TTL", 0)
    context._cache.clear()
    git(tree, "checkout", "--detach", "-q")
    assert context._context(str(tree))["worktree"]["kind"] == "linked"
    assert context._context(str(tree))["branch"] is None
    assert context._context(str(repo.parent))["worktree"] is None


def test_identity_failure_does_not_discard_git_status(repo, monkeypatch):
    original = context._git
    def failing(p, *args):
        if "--git-common-dir" in args:
            return 124, ""
        return original(p, *args)
    monkeypatch.setattr(context, "_git", failing)
    data = context._context(str(repo))
    assert data["status"] == "ready" and data["branch"] == "main"
    assert data["worktree"]["kind"] == "unknown"
    assert data["worktree"]["status"] == "unavailable"


def test_identity_boundary_rechecked_on_cached_success(repo, monkeypatch):
    tree = repo.parent / "task"
    git(repo, "worktree", "add", "-qb", "feature", str(tree))
    assert context._context(str(tree))["worktree"]["kind"] == "linked"
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tree))
    data = context._context(str(tree))
    assert data["branch"] == "feature" and data["root"] == str(tree.resolve())
    assert data["worktree"]["kind"] == "unknown"
    assert data["worktree"]["repositoryRoot"] is None


def test_separate_git_directory_is_main_not_linked(repo):
    other = repo.parent / "separate"
    git(repo, "init", "-q", "--separate-git-dir", str(repo.parent / "git-data"), str(other))
    git(other, "config", "user.email", "test@example.com")
    git(other, "config", "user.name", "test")
    (other / "file").write_text("test")
    git(other, "add", ".")
    git(other, "commit", "-qm", "initial")
    assert (other / ".git").is_file()
    assert context._context(str(other))["worktree"]["kind"] == "main"


def test_repository_lookup_shared_and_collision_names(repo, monkeypatch):
    a = repo.parent / "one" / "task"
    b = repo.parent / "two" / "task"
    git(repo, "worktree", "add", "-qb", "one", str(a))
    git(repo, "worktree", "add", "-qb", "two", str(b))
    calls = []
    original = context._git
    def counted(p, *args):
        if args[:2] == ("worktree", "list"):
            calls.append(args)
        return original(p, *args)
    monkeypatch.setattr(context, "_git", counted)
    result = asyncio.run(context.contexts_for([str(a), str(b), str(a)]))
    assert len(calls) == 1
    assert result[str(a)]["worktree"]["name"] == "one/task"
    assert result[str(b)]["worktree"]["name"] == "two/task"


def test_worktree_path_with_newline_is_preserved(repo):
    tree = repo.parent / "task\nline"
    git(repo, "worktree", "add", "-qb", "newline", str(tree))
    data = context._context(str(tree))
    assert data["root"] == str(tree.resolve())
    assert data["worktree"]["kind"] == "linked"
    assert data["worktree"]["repositoryRoot"] == str(repo.resolve())


def test_root_changed_during_lookup_discards_old_snapshot(repo, monkeypatch):
    assert context._context(str(repo))["branch"] == "main"
    monkeypatch.setattr(context, "TTL", 0)
    context._cache[str(repo)] = (0, context._cache[str(repo)][1])
    original = context._git_path
    monkeypatch.setattr(context, "_git_path", lambda p, option:
        repo.parent if option == "--show-toplevel" else original(p, option))
    changed = context._context(str(repo))
    assert changed["status"] == "unavailable"
    assert changed["root"] is None and changed["branch"] is None
    assert changed["worktree"] is None


def test_submodule_git_file_is_main_workspace(repo):
    parent = repo.parent / "parent"
    parent.mkdir()
    git(parent, "init", "-q", "-b", "main")
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(repo), "module")
    module = parent / "module"
    assert (module / ".git").is_file()
    data = context._context(str(module))
    assert data["worktree"]["kind"] == "main"
    assert data["root"] == str(module.resolve())


def test_busy_path_rechecks_the_boundary_before_returning_old_values(repo, monkeypatch):
    """조회가 밀려 예전 값을 줄 때도 지금 경계에서 빠진 경로면 내보내지 않는다(L6)."""
    first = context._context(str(repo))
    assert first["root"]
    stripe = context._reading[hash(str(repo)) % len(context._reading)]
    monkeypatch.setattr(context, "STRIPE_WAIT", 0.01)
    stripe.acquire()
    try:
        assert context._context(str(repo))["reason"] == "busy"
        assert context._context(str(repo))["root"] == first["root"]
        monkeypatch.setattr(context.fsguard, "resolve_under_roots", lambda *a, **k: (_ for _ in ()).throw(context.fsguard.FsDenied("x")))
        denied = context._context(str(repo))
        assert denied["root"] is None and denied["branch"] is None
    finally:
        stripe.release()


def test_name_suffix_only_counts_the_roots_on_screen(repo):
    a, b = repo.parent / "one" / "task", repo.parent / "two" / "task"
    result = {str(a): {"root": str(a), "worktree": {"name": "task"}}, str(b): {"root": str(b), "worktree": {"name": "task"}}}
    assert context.disambiguate(result)[str(a)]["worktree"]["name"] == "one/task"
    assert context.disambiguate(result, [str(a)])[str(a)]["worktree"]["name"] == "task", "화면에 없는 root 때문에 이름이 바뀌지 않는다"
