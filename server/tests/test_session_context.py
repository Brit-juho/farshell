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
