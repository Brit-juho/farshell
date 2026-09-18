"""레일 저장소 목록 — 탐색 깊이·상한·원격 파싱·숨김 (98-rail-repos-2.1.6.md).

고정하는 것 네 가지:
  §1 컨테이너 디렉터리 아래로 한 단계를 더 판다. `~/Library` 계열은 안 판다.
     200개를 넘으면 멈추고 `truncated`를 올린다.
  §4 `.git/config`에서 origin을 읽고 URL을 파싱한다. **자격증명은 응답에
     실리지 않는다** — 이게 이 파일에서 가장 중요한 한 줄이다.
  §3 숨긴 저장소가 목록에서 빠지고, `include_hidden=1`이면 플래그를 달고 온다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import fsguard
import rail_repos_store
import worktree
from worktree_parse import parse_git_config_origin, parse_remote_url


def _mkrepo(path: Path, origin: str | None = None) -> Path:
    git = path / ".git"
    git.mkdir(parents=True)
    body = '[core]\n\trepositoryformatversion = 0\n'
    if origin:
        body += f'[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*\n'
    (git / "config").write_text(body, encoding="utf-8")
    return path


# --- §1 탐색 깊이 -------------------------------------------------------------


def test_container_dir_gets_one_extra_level(tmp_path):
    """`GitHub/side_project/tools/farshell`(깊이 4)이 들어온다 — 이게 버그의 실체."""
    deep = tmp_path / "GitHub" / "side_project" / "tools" / "farshell"
    _mkrepo(deep)
    found, truncated = worktree._find_git_entrypoints([tmp_path])
    assert deep in found
    assert truncated is False


def test_non_container_stays_at_base_depth(tmp_path):
    """보너스는 컨테이너 이름 밑에서만 나온다. 아무 데나 깊어지면 홈 전체가 느려진다."""
    deep = tmp_path / "a" / "b" / "c" / "d"
    _mkrepo(deep)
    found, _ = worktree._find_git_entrypoints([tmp_path])
    assert deep not in found


def test_scan_skips_library_and_friends(tmp_path):
    hidden = _mkrepo(tmp_path / "Library" / "Caches" / "repo")
    visible = _mkrepo(tmp_path / "proj")
    found, _ = worktree._find_git_entrypoints([tmp_path])
    assert visible in found
    assert hidden not in found


def test_scan_stops_at_limit_and_reports_truncated(tmp_path):
    for i in range(6):
        _mkrepo(tmp_path / f"r{i}")
    found, truncated = worktree._find_git_entrypoints([tmp_path], limit=3)
    assert len(found) == 3
    assert truncated is True


def test_not_truncated_when_under_limit(tmp_path):
    _mkrepo(tmp_path / "only")
    found, truncated = worktree._find_git_entrypoints([tmp_path], limit=200)
    assert len(found) == 1
    assert truncated is False


# --- §4 원격 파싱 -------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("git@github.com:fornerds/rapa-ai.git", {"host": "github", "owner": "fornerds", "name": "rapa-ai"}),
    ("https://github.com/neo/v1.git", {"host": "github", "owner": "neo", "name": "v1"}),
    ("https://github.com/neo/v1", {"host": "github", "owner": "neo", "name": "v1"}),
    ("git@gitlab.com:someteam/x.git", {"host": "gitlab", "owner": "someteam", "name": "x"}),
    ("ssh://git@gitlab.self.host:2222/g/p.git",
     {"host": "gitlab.self.host", "owner": "g", "name": "p"}),
    ("https://gitlab.com/group/sub/proj.git",
     {"host": "gitlab", "owner": "group/sub", "name": "proj"}),
    ("", None),
    (None, None),
    ("/plain/local/path", None),
])
def test_parse_remote_url(url, expected):
    assert parse_remote_url(url) == expected


def test_credentials_never_survive_parsing():
    """`https://user:token@host/...`가 실재한다. 토큰이 브라우저로 나가면 안 된다."""
    parsed = parse_remote_url("https://neo:ghp_SECRETTOKEN@github.com/o/r.git")
    assert parsed == {"host": "github", "owner": "o", "name": "r"}
    assert "ghp_SECRETTOKEN" not in json.dumps(parsed)


def test_parse_git_config_origin_ignores_other_remotes():
    text = (
        '[core]\n\tbare = false\n'
        '[remote "upstream"]\n\turl = git@github.com:up/stream.git\n'
        '[remote "origin"]\n\turl = git@github.com:me/mine.git\n'
    )
    assert parse_git_config_origin(text) == "git@github.com:me/mine.git"


def test_parse_git_config_origin_missing():
    assert parse_git_config_origin('[core]\n\tbare = false\n') is None


def test_read_origin_remote_from_repo(tmp_path):
    repo = _mkrepo(tmp_path / "repo", "git@github.com:fornerds/rapa-ai.git")
    assert worktree._read_origin_remote(repo) == {
        "host": "github", "owner": "fornerds", "name": "rapa-ai",
    }


def test_read_origin_remote_without_remote(tmp_path):
    repo = _mkrepo(tmp_path / "repo")
    assert worktree._read_origin_remote(repo) is None


def test_read_origin_remote_follows_gitdir_file(tmp_path):
    """부가 워크트리의 `.git`은 파일이다. 본체 config까지 따라가야 한다."""
    main = _mkrepo(tmp_path / "main", "git@github.com:o/r.git")
    wt_gitdir = main / ".git" / "worktrees" / "feature"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    assert worktree._read_origin_remote(linked) == {
        "host": "github", "owner": "o", "name": "r",
    }


# --- §3 숨김 저장소 -----------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    return rail_repos_store


def test_hide_and_unhide_roundtrip(store):
    assert store.list_hidden() == []
    assert store.set_hidden("/repos/a", True)["ok"] is True
    assert store.list_hidden() == ["/repos/a"]
    store.set_hidden("/repos/a", True)          # 중복은 멱등
    assert store.list_hidden() == ["/repos/a"]
    store.set_hidden("/repos/a", False)
    assert store.list_hidden() == []


def test_hidden_file_is_owner_only(store, tmp_path):
    store.set_hidden("/repos/a", True)
    path = tmp_path / "state" / "rail-repos.json"
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert json.loads(path.read_text())["version"] == 1


def test_trailing_slash_is_the_same_repo(store):
    store.set_hidden("/repos/a/", True)
    assert store.is_hidden({"path": "/repos/a"}, store.hidden_set()) is True


def test_hiding_a_repo_hides_its_worktrees(store):
    hidden = {"/repos/a"}
    assert store.is_hidden({"path": "/wt/feature", "repo": "/repos/a"}, hidden) is True
    assert store.is_hidden({"path": "/repos/b", "repo": "/repos/b"}, hidden) is False


def test_empty_path_is_rejected(store):
    result = store.set_hidden("   ", True)
    assert result["ok"] is False and result["error"] == "empty_path"


def test_vanished_repo_in_list_is_ignored(store):
    """삭제된 저장소가 목록에 남아도 오류 없이 무시된다(§3 수용 기준)."""
    store.set_hidden("/repos/gone", True)
    assert store.is_hidden({"path": "/repos/still-here"}, store.hidden_set()) is False
    assert store.list_hidden() == ["/repos/gone"]


def test_corrupt_file_falls_back_to_empty(store, tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "rail-repos.json").write_text("{not json", encoding="utf-8")
    assert store.list_hidden() == []


# --- §3 라우트 ----------------------------------------------------------------


def test_route_filters_hidden(monkeypatch, tmp_path):
    import routes.worktree as worktree_routes

    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    items = [
        {"id": "1", "path": "/repos/a", "repo": "/repos/a"},
        {"id": "2", "path": "/repos/b", "repo": "/repos/b"},
    ]
    monkeypatch.setattr(worktree, "list_worktrees", lambda: items)
    monkeypatch.setattr(worktree, "last_scan_truncated", lambda: False)
    monkeypatch.setattr(fsguard, "get_roots", lambda: [Path("/repos")])
    rail_repos_store.set_hidden("/repos/a", True)

    visible = asyncio.run(worktree_routes.list_worktrees())
    assert [w["id"] for w in visible["worktrees"]] == ["2"]
    assert visible["hiddenCount"] == 1
    assert visible["roots"] == ["/repos"]

    everything = asyncio.run(worktree_routes.list_worktrees(include_hidden=1))
    assert [(w["id"], w["hidden"]) for w in everything["worktrees"]] == [("1", True), ("2", False)]
