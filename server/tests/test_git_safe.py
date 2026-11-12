"""git_safe: a repository's config must not run programs through FarShell's own git calls.

The attack: a normal repository carries a bare-repository-shaped folder (`evil/` with HEAD, objects/,
refs/, config). After an ordinary `git clone`, `cd evil` makes git discover `evil` as the repository,
so its attacker-written `core.fsmonitor` runs on `git status` — which the rail badge issues for the
pane's cwd without any click.
"""
import subprocess
from pathlib import Path

import pytest

import git_safe
import worktree
from routes.git import _collect_diff, _collect_status, _commit, _git


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _identity(repo: Path) -> None:
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "test")


@pytest.fixture
def embedded_bare(tmp_path):
    """(victim/evil, marker) — a clone whose subfolder is a planted bare repository."""
    marker = tmp_path / "PWNED"
    outer = tmp_path / "outer"
    outer.mkdir()
    _run(outer, "init", "-q")
    _identity(outer)
    evil = outer / "evil"
    for d in ("objects", "refs"):
        (evil / d).mkdir(parents=True)
        (evil / d / ".keep").write_text("")  # git does not commit empty folders
    (evil / "HEAD").write_text("ref: refs/heads/main\n")
    (evil / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tworktree = .\n"
        f'\tfsmonitor = "touch {marker}; false"\n')
    _run(outer, "add", "-A")
    _run(outer, "commit", "-q", "-m", "x")
    victim = tmp_path / "victim"
    subprocess.run(["git", "clone", "-q", str(outer), str(victim)], check=True, capture_output=True)
    return victim / "evil", marker


def test_attack_runs_with_plain_git(embedded_bare):
    """Guards the fixture: without hardening the planted fsmonitor really runs."""
    evil, marker = embedded_bare
    subprocess.run(["git", "-C", str(evil), "status"], capture_output=True)
    assert marker.exists()


def test_status_paths_do_not_run_planted_fsmonitor(embedded_bare):
    evil, marker = embedded_bare
    _git(evil, "rev-parse", "--show-toplevel")
    _collect_status(evil, top=evil)
    _git(evil, "diff", *git_safe.DIFF_FLAGS, "--numstat", "HEAD")
    worktree._status_summary(evil)
    worktree._git(["status", "--porcelain"], cwd=evil)
    assert not marker.exists()


def test_argv_shape():
    a = git_safe.argv("status", cwd="/r")
    assert a[:3] == ["git", "-C", "/r"]
    assert "safe.bareRepository=explicit" in a and "core.fsmonitor=false" in a
    assert "core.hooksPath=/dev/null" in a and a[-1] == "status"
    assert "core.hooksPath=/dev/null" not in git_safe.argv("commit", hooks=True)
    env = git_safe.env()
    assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_TERMINAL_PROMPT"] == "0"


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _run(r, "init", "-q")
    _identity(r)
    (r / "a.txt").write_text("one\n")
    _run(r, "add", ".")
    _run(r, "commit", "-q", "-m", "init")
    return r


def test_diff_ignores_repository_textconv(repo, tmp_path):
    marker = tmp_path / "TEXTCONV"
    _run(repo, "config", "diff.evil.textconv", f"sh -c 'touch {marker}; cat \"$1\"' -")
    (repo / ".gitattributes").write_text("*.txt diff=evil\n")
    (repo / "a.txt").write_text("two\n")
    out = _collect_diff(repo, "a.txt", False)
    assert "+two" in out["diff"]
    assert not marker.exists()


def test_status_never_runs_hooks_but_commit_does(repo, tmp_path):
    marker = tmp_path / "HOOK"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    (repo / "a.txt").write_text("two\n")
    _run(repo, "add", "a.txt")
    _collect_status(repo, top=repo)
    assert not marker.exists()
    rc, _ = _commit(repo, "msg")  # a user-requested commit keeps the repository's hooks, like the CLI
    assert rc == 0 and marker.exists()


@pytest.mark.parametrize("base", ["--output=/tmp/x", "-b", "a\nb", "x" * 256])
def test_worktree_rejects_option_like_base(base):
    with pytest.raises(worktree.WorktreeError) as e:
        worktree._validate_create_body({"repo": "/r", "base": base, "name": "n"})
    assert e.value.status == 400
