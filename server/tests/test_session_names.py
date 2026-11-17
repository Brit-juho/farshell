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


def test_allocate_gives_a_random_id_and_keeps_the_base_as_an_alias_request(monkeypatch):
    """2026-11 설계: 새 세션의 tmux 이름은 랜덤 ID(`s-` + 16진 8자리)이고, 기준 이름은 별칭 요청으로 남는다."""
    taken = set()
    monkeypatch.setattr(tmux_runner, "has_session", lambda n: n in taken)
    name = session_names.allocate("app")
    assert session_names.is_id(name) and tmux_runner.valid_session_name(name)
    assert session_names.take_alias_request(name) == "app"
    assert session_names.take_alias_request(name) is None          # 한 번만 가져간다


def test_spawn_unique_retries_with_a_new_id_only_for_duplicates():
    tried = []

    def spawn(name):
        tried.append(name)
        return (1, b"duplicate session: " + name.encode()) if len(tried) < 3 else (0, b"")
    rc, name, err = session_names.spawn_unique("w", spawn)
    assert rc == 0 and len(set(tried)) == 3 and name == tried[-1] and session_names.is_id(name)
    assert session_names.take_alias_request(name) == "w"            # 별칭 요청은 실제로 쓴 ID로 옮겨 간다
    assert all(session_names.take_alias_request(n) is None for n in tried[:-1])

    def broken(name):
        return 1, b"some other failure"
    rc, name, err = session_names.spawn_unique("w", broken)
    assert rc == 1 and err == b"some other failure"


def test_spawn_unique_can_start_from_an_allocated_name():
    tried = []

    def spawn(name):
        tried.append(name)
        return 0, b""
    rc, name, _ = session_names.spawn_unique("w", spawn, start="s-0000abcd")
    assert (rc, name, tried) == (0, "s-0000abcd", ["s-0000abcd"])


def test_delegated_children_get_a_random_id_and_ask_for_a_readable_alias():
    import delegation
    child = delegation._child_name("app", "", set())
    assert session_names.is_id(child) and session_names.take_alias_request(child) == "app-sub"
    child = delegation._child_name("app", "tests", set())
    assert session_names.take_alias_request(child) == "tests"
