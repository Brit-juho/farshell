"""GET /api/repos — 2.1 D1. 평평한 워크트리 목록을 저장소 단위로 묶는 그루핑
규칙과, `/api/worktrees`와 같은 숨김 저장소(`repo_store`)를 보는지를 검증한다.
실제 화면은 아직 이 라우트를 안 쓴다 — 그룹핑 자체가 맞는지만 본다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import fsguard
import repo_store
import routes.repos as repos_routes
import worktree


def _wt(id_, repo, path, is_main, repo_name="x", remote=None, host="local"):
    return {
        "id": id_, "repo": repo, "repoName": repo_name, "path": path,
        "isMain": is_main, "remote": remote, "host": host,
        "branch": "main", "sessions": [],
    }


def test_groups_worktrees_under_their_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    items = [
        _wt("m1", "/repos/a", "/repos/a", True, repo_name="a", remote={"host": "github", "owner": "o", "name": "a"}),
        _wt("w1", "/repos/a", "/repos/a-feature", False, repo_name="a"),
        _wt("m2", "/repos/b", "/repos/b", True, repo_name="b"),
    ]
    monkeypatch.setattr(worktree, "list_worktrees", lambda: items)
    monkeypatch.setattr(worktree, "last_scan_truncated", lambda: False)
    monkeypatch.setattr(fsguard, "get_roots", lambda: [Path("/repos")])

    data = asyncio.run(repos_routes.list_repos())
    assert [r["path"] for r in data["repos"]] == ["/repos/a", "/repos/b"]
    repo_a = data["repos"][0]
    assert [w["id"] for w in repo_a["worktrees"]] == ["m1", "w1"]
    assert repo_a["name"] == "a"
    assert repo_a["remote"] == {"host": "github", "owner": "o", "name": "a"}
    assert repo_a["id"] == repo_store.repo_id("local", "/repos/a")
    assert data["roots"] == ["/repos"]
    assert data["truncated"] is False


def test_hidden_repo_is_filtered_like_worktrees_route(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    items = [
        _wt("m1", "/repos/a", "/repos/a", True, repo_name="a"),
        _wt("m2", "/repos/b", "/repos/b", True, repo_name="b"),
    ]
    monkeypatch.setattr(worktree, "list_worktrees", lambda: items)
    monkeypatch.setattr(worktree, "last_scan_truncated", lambda: False)
    monkeypatch.setattr(fsguard, "get_roots", lambda: [])
    repo_store.set_hidden("/repos/a", True)

    visible = asyncio.run(repos_routes.list_repos())
    assert [r["path"] for r in visible["repos"]] == ["/repos/b"]
    assert visible["hiddenCount"] == 1

    everything = asyncio.run(repos_routes.list_repos(include_hidden=1))
    assert [(r["path"], r["hidden"]) for r in everything["repos"]] == [
        ("/repos/a", True), ("/repos/b", False),
    ]


def test_empty_worktree_list_yields_empty_repos(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(worktree, "list_worktrees", lambda: [])
    monkeypatch.setattr(worktree, "last_scan_truncated", lambda: False)
    monkeypatch.setattr(fsguard, "get_roots", lambda: [])

    data = asyncio.run(repos_routes.list_repos())
    assert data["repos"] == []
    assert data["hiddenCount"] == 0
