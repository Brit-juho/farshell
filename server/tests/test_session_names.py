"""세션 이름 짓기 한 곳(server/session_names.py, 세션 로직 점검 단계 1)."""

import subprocess

import pytest

import session_names
import tmux_runner


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "farshell"
    main.mkdir()
    _git("init", "-q", "-b", "dev", cwd=main)
    _git("-c", "user.email=a@example.com", "-c", "user.name=alice", "commit", "-q", "--allow-empty", "-m", "init", cwd=main)
    return main


def test_repo_root_and_subfolder_use_the_repo_name(repo):
    (repo / "server" / "deep").mkdir(parents=True)
    assert session_names.base_for(str(repo)) == "farshell"
    assert session_names.base_for(str(repo / "server" / "deep")) == "farshell"


def test_linked_worktree_uses_its_own_folder_name(repo, tmp_path):
    wt = tmp_path / "farshell-rail"
    _git("worktree", "add", "-q", "-b", "rail", str(wt), cwd=repo)
    assert session_names.base_for(str(wt)) == "farshell-rail"


def test_plain_folder_home_and_missing(tmp_path, monkeypatch):
    plain = tmp_path / "notes"
    plain.mkdir()
    assert session_names.base_for(str(plain)) == "notes"
    monkeypatch.setenv("HOME", str(tmp_path))
    assert session_names.base_for(str(tmp_path)) == "home"
    assert session_names.base_for(None) == "home"
    assert session_names.base_for(str(tmp_path / "gone")) == "home"


@pytest.mark.parametrize("raw,expected", [("my build: v1.2", "my-build-v1-2"), ("한글", ""), ("-x-", "x"), ("a" * 50, "a" * 40)])
def test_slug(raw, expected):
    assert session_names.slug(raw) == expected


def test_non_ascii_folder_falls_back_to_session(tmp_path):
    folder = tmp_path / "작업"
    folder.mkdir()
    assert session_names.base_for(str(folder)) == "session"


def test_allocate_numbers_from_two_and_matches_exactly(monkeypatch):
    taken = {"app", "app-2"}
    asked = []
    monkeypatch.setattr(tmux_runner, "has_session", lambda n: asked.append(n) or n in taken)
    assert session_names.allocate("app") == "app-3"
    assert asked == ["app", "app-2", "app-3"]


def test_spawn_unique_moves_on_only_for_duplicates():
    tried = []

    def spawn(name):
        tried.append(name)
        return (1, b"duplicate session: " + name.encode()) if name in ("w", "w-2") else (0, b"")
    assert session_names.spawn_unique("w", spawn) == (0, "w-3", b"")
    assert tried == ["w", "w-2", "w-3"]

    def broken(name):
        return 1, b"some other failure"
    rc, name, err = session_names.spawn_unique("w", broken)
    assert (rc, name) == (1, "w") and err == b"some other failure"


def test_spawn_unique_can_start_from_an_allocated_name():
    tried = []
    assert session_names.spawn_unique("w", lambda n: tried.append(n) or (0, b""), start="w-4")[1] == "w-4"
    assert tried == ["w-4"]


def test_delegated_children_use_the_same_numbering():
    import delegation
    assert delegation._child_name("app", "", set()) == "app-sub"
    assert delegation._child_name("app", "", {"app-sub"}) == "app-sub-2"
    assert delegation._child_name("app", "tests", {"tests", "tests-2"}) == "tests-3"
