"""워크트리 탐색 — 탐색 깊이·상한·원격 파싱 (98-rail-repos-2.1.6.md §1/§4).

고정하는 것 둘:
  §1 컨테이너 디렉터리 아래로 한 단계를 더 판다. `~/Library` 계열은 안 판다.
     200개를 넘으면 멈추고 `truncated`를 올린다.
  §4 `.git/config`에서 origin을 읽고 URL을 파싱한다. **자격증명은 응답에
     실리지 않는다** — 이게 이 파일에서 가장 중요한 한 줄이다.

§3(숨김 저장소)은 2.1 D1에서 `rail_repos_store.py`가 `repo_store.py`로
흡수되며 `test_repo_store.py`로 옮겼다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_parse import parse_git_config_origin, parse_remote_url
import worktree


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
