"""소스 컨트롤 그래프: 로그에 parents/refs 가 실리고, ref 는 실제 브랜치만 허용한다."""

import subprocess
from pathlib import Path

import pytest

from routes.git import _collect_branches, _collect_log


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _run(r, "init", "-b", "main")
    _run(r, "config", "user.email", "t@example.com")
    _run(r, "config", "user.name", "t")
    (r / "a").write_text("1")
    _run(r, "add", "a")
    _run(r, "commit", "-m", "c1")
    _run(r, "checkout", "-b", "side")
    (r / "b").write_text("2")
    _run(r, "add", "b")
    _run(r, "commit", "-m", "c2")
    _run(r, "checkout", "main")
    return r


def test_log_has_parents_and_refs(repo):
    d = _collect_log(repo, "", 0, 20, "")
    assert len(d["commits"]) == 1
    c = d["commits"][0]
    assert c["parents"] == []
    assert any("main" in r for r in c["refs"])


def test_log_other_branch_by_ref(repo):
    d = _collect_log(repo, "", 0, 20, "side")
    assert [c["subject"] for c in d["commits"]] == ["c2", "c1"]
    assert d["commits"][0]["parents"] == [d["commits"][1]["hash"]]


def test_branches_listed(repo):
    assert set(_collect_branches(repo)["branches"]) == {"main", "side"}


@pytest.mark.parametrize("bad", ["--all", "nope", "main;ls", "-n1"])
def test_unknown_ref_rejected(repo, bad):
    d = _collect_log(repo, "", 0, 20, bad)
    assert d["commits"] == [] and d.get("error")


def test_commit_meta_fields_line_up(repo):
    """커밋 상세(`/api/git/show`)의 제목·본문·부모가 제자리에 들어간다 — 예전에는 제목에 부모 해시가 들어갔다."""
    from routes.git import _collect_commit_meta
    _run(repo, "commit", "--allow-empty", "-m", "subject line", "-m", "body text")
    m = _collect_commit_meta(repo, "HEAD")
    assert m["subject"] == "subject line"
    assert m["body"] == "body text"
    assert len(m["parents"]) == 1 and len(m["parents"][0]) == 40
