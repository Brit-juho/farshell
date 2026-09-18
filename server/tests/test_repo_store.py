"""저장소 숨김 저장소 — 2.1 D1. `rail_repos_store.py`(§3, `~/.vt/rail-repos.json`)를
흡수한 `repo_store.py`(`~/.vt/repos.json`)로 옮겨온 테스트 + 이관 자체를 검증한다.

고정하는 것:
  옛 §3 계약 그대로: 숨김 토글은 멱등, 트레일링 슬래시는 같은 저장소, 저장소를
  숨기면 부가 워크트리도 같이 숨고, 삭제된 저장소가 목록에 남아도 조용히
  무시되고, 파일이 깨지면 빈 목록으로 시작한다.
  + 새로 생긴 것: `rail-repos.json`이 있고 `repos.json`이 없을 때 1회
  이관하고 원본은 `.bak`으로 남긴다. `host`가 id 해시에 들어간다(2.2 기반).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import fsguard
import repo_store
import worktree


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    return repo_store


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
    path = tmp_path / "state" / "repos.json"
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
    (state / "repos.json").write_text("{not json", encoding="utf-8")
    assert store.list_hidden() == []


# --- id·host (2.2 기반) -------------------------------------------------------


def test_repo_id_depends_on_host():
    """같은 경로라도 host가 다르면 다른 id — 2.2에서 원격 저장소가 로컬과
    경로가 겹쳐도 안 부딪히게 하는 전제."""
    local = repo_store.repo_id("local", "/repos/a")
    remote = repo_store.repo_id("mac-mini", "/repos/a")
    assert local != remote
    assert len(local) == 12


def test_set_hidden_records_host(store):
    store.set_hidden("/repos/a", True, host="local")
    [rec] = store.list_repos()
    assert rec["host"] == "local"
    assert rec["path"] == "/repos/a"
    assert rec["id"] == repo_store.repo_id("local", "/repos/a")


# --- 이관(rail-repos.json → repos.json) ---------------------------------------


def test_migrates_hidden_list_from_legacy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "rail-repos.json").write_text(
        json.dumps({"version": 1, "hidden": ["/repos/a", "/repos/b"]}), encoding="utf-8",
    )

    assert repo_store.hidden_set() == {"/repos/a", "/repos/b"}
    # 원본은 지워지지 않고 .bak으로 남는다.
    assert not (state / "rail-repos.json").is_file()
    assert (state / "rail-repos.json.bak").is_file()
    # 이관 결과가 repos.json에 실제로 쓰여, 다음 읽기는 이관을 다시 안 돈다.
    assert (state / "repos.json").is_file()


def test_no_legacy_file_starts_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    assert repo_store.list_repos() == []


def test_corrupt_legacy_file_migrates_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "rail-repos.json").write_text("{not json", encoding="utf-8")
    assert repo_store.list_repos() == []


# --- 라우트(routes/worktree.py가 repo_store로 갈아 낀 뒤에도 그대로 동작) --------


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
    repo_store.set_hidden("/repos/a", True)

    visible = asyncio.run(worktree_routes.list_worktrees())
    assert [w["id"] for w in visible["worktrees"]] == ["2"]
    assert visible["hiddenCount"] == 1
    assert visible["roots"] == ["/repos"]

    everything = asyncio.run(worktree_routes.list_worktrees(include_hidden=1))
    assert [(w["id"], w["hidden"]) for w in everything["worktrees"]] == [("1", True), ("2", False)]
