"""N8/N44 워크트리 — 30-worktree.md 수용 기준.

- porcelain 파싱 (main-only / 2 worktrees / detached)
- create 2단계 실패 시 롤백(`git worktree list`에 안 남음)
- lockfile 배너 판정
- `.env` 포트 치환(다른 키 불변)
"""

import pathlib
import subprocess

import pytest

import worktree

FIXTURE_MAIN_ONLY = """worktree /repo
HEAD abcdef1234567890abcdef1234567890abcdef12
branch refs/heads/main

"""

FIXTURE_TWO_WORKTREES = """worktree /repo
HEAD abcdef1234567890abcdef1234567890abcdef12
branch refs/heads/main

worktree /repo-wt
HEAD 1234567890abcdef1234567890abcdef12345678
branch refs/heads/feat/x

"""

FIXTURE_DETACHED = """worktree /repo
HEAD abcdef1234567890abcdef1234567890abcdef12
branch refs/heads/main

worktree /repo-wt2
HEAD fedcba0987654321fedcba0987654321fedcba09
detached

"""


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "t"], path)
    (path / "README.md").write_text("hi")
    _git(["add", "."], path)
    _git(["commit", "-q", "-m", "init"], path)
    _git(["branch", "-M", "main"], path)
    return path


def _head_sha(path: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(path), capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt-state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    worktree.invalidate_cache()
    yield
    worktree.invalidate_cache()


# --- porcelain 파싱 (픽스처 3종) ----------------------------------------------


def test_parse_main_only():
    blocks = worktree.parse_worktree_porcelain(FIXTURE_MAIN_ONLY)
    assert len(blocks) == 1
    assert blocks[0]["path"] == "/repo"
    assert worktree._branch_from_block(blocks[0]) == "main"
    assert blocks[0]["head"] == "abcdef1234567890abcdef1234567890abcdef12"


def test_parse_two_worktrees():
    blocks = worktree.parse_worktree_porcelain(FIXTURE_TWO_WORKTREES)
    assert [b["path"] for b in blocks] == ["/repo", "/repo-wt"]
    assert worktree._branch_from_block(blocks[0]) == "main"
    assert worktree._branch_from_block(blocks[1]) == "feat/x"


def test_parse_detached():
    blocks = worktree.parse_worktree_porcelain(FIXTURE_DETACHED)
    assert len(blocks) == 2
    assert blocks[1].get("detached") is True
    assert worktree._branch_from_block(blocks[1]) == "(detached)"


def test_parse_shortstat():
    text = " 3 files changed, 10 insertions(+), 2 deletions(-)\n"
    assert worktree.parse_shortstat(text) == {"files": 3, "add": 10, "del": 2}
    assert worktree.parse_shortstat("") == {"files": 0, "add": 0, "del": 0}


# --- create 롤백 ---------------------------------------------------------------


def test_create_rollback_on_node_modules_failure(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.txt").write_text("x")

    def boom(*a, **k):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(worktree.shutil, "copytree", boom)

    body = {
        "repo": str(repo), "base": "main", "name": "t1", "branch": "feat/t1",
        "ports": {"enabled": False}, "nodeModules": "copy", "env": "none", "agent": None,
    }
    with pytest.raises(worktree.WorktreeError):
        worktree.create_worktree(body)

    rc, out, _ = worktree._git(["worktree", "list", "--porcelain"], cwd=repo)
    assert rc == 0
    listed_paths = [b["path"] for b in worktree.parse_worktree_porcelain(out.decode())]
    dest = str(tmp_path / ".worktrees" / "repo" / "t1")
    assert dest not in listed_paths
    assert not (tmp_path / ".worktrees" / "repo" / "t1").exists()
    # 브랜치도 남지 않는다.
    rc2, out2, _ = worktree._git(["branch", "--list", "feat/t1"], cwd=repo)
    assert out2.decode().strip() == ""


def test_create_rollback_on_env_write_failure(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo-env")
    (repo / ".env").write_text("PORT=3000\n")

    orig_write_text = pathlib.Path.write_text

    def boom(self, *a, **k):
        if self.name == ".env":
            raise OSError("simulated env write failure")
        return orig_write_text(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", boom)

    body = {
        "repo": str(repo), "base": "main", "name": "t2", "branch": "feat/t2",
        "ports": {"enabled": True, "base": 5400}, "nodeModules": "none", "env": "inherit", "agent": None,
    }
    with pytest.raises(worktree.WorktreeError):
        worktree.create_worktree(body)

    rc, out, _ = worktree._git(["worktree", "list", "--porcelain"], cwd=repo)
    listed_paths = [b["path"] for b in worktree.parse_worktree_porcelain(out.decode())]
    assert str(tmp_path / ".worktrees" / "repo-env" / "t2") not in listed_paths


# --- lockfile 배너 판정 --------------------------------------------------------


def test_lockfile_mismatch_detected(tmp_path):
    repo = _init_repo(tmp_path / "repo2")
    (repo / "package.json").write_text('{"name":"a","version":"1.0.0"}')
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "add pkg"], repo)
    base_sha = _head_sha(repo)

    (repo / "package.json").write_text('{"name":"a","version":"2.0.0"}')
    assert worktree._lockfile_mismatch(repo, base_sha) is True


def test_lockfile_no_mismatch_when_identical(tmp_path):
    repo = _init_repo(tmp_path / "repo3")
    (repo / "package.json").write_text('{"name":"a"}')
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "pkg"], repo)
    assert worktree._lockfile_mismatch(repo, "HEAD") is False


def test_lockfile_no_package_json_is_not_a_mismatch(tmp_path):
    repo = _init_repo(tmp_path / "repo3b")
    assert worktree._lockfile_mismatch(repo, "HEAD") is False


def test_precheck_endpoint_reports_warning(tmp_path):
    repo = _init_repo(tmp_path / "repo4")
    (repo / "package.json").write_text('{"v":1}')
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "pkg"], repo)
    base_sha = _head_sha(repo)
    (repo / "package.json").write_text('{"v":2}')

    result = worktree.precheck(str(repo), base_sha)
    assert "lockfile_mismatch" in result["warnings"]


def test_precheck_no_warning_when_matching(tmp_path):
    repo = _init_repo(tmp_path / "repo4b")
    (repo / "package.json").write_text('{"v":1}')
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "pkg"], repo)
    result = worktree.precheck(str(repo), "HEAD")
    assert result["warnings"] == []


# --- .env 포트 치환 -------------------------------------------------------------


def test_substitute_env_ports_only_known_keys():
    text = "PORT=3000\nAPI_KEY=abc\nVITE_PORT=3001\nOTHER=1\n"
    out = worktree.substitute_env_ports(text, 5200)
    lines = out.split("\n")
    assert "PORT=5200" in lines
    assert "VITE_PORT=5200" in lines
    assert "API_KEY=abc" in lines
    assert "OTHER=1" in lines


def test_substitute_env_ports_all_four_keys():
    text = "PORT=1\nVITE_PORT=2\nDEV_PORT=3\nNEXT_PUBLIC_PORT=4\n"
    out = worktree.substitute_env_ports(text, 9999)
    for key in ("PORT", "VITE_PORT", "DEV_PORT", "NEXT_PUBLIC_PORT"):
        assert f"{key}=9999" in out.split("\n")


def test_substitute_env_ports_does_not_add_missing_keys():
    text = "API_KEY=abc\n"
    out = worktree.substitute_env_ports(text, 5200)
    assert "PORT" not in out
    assert out == text


# --- 생성 통합 흐름 -------------------------------------------------------------


def test_create_worktree_full_flow_with_env_and_ports(tmp_path):
    repo = _init_repo(tmp_path / "repo5")
    (repo / ".env").write_text("PORT=3000\nFOO=bar\n")
    body = {
        "repo": str(repo), "base": "main", "name": "feat-x", "branch": "feat/x",
        "ports": {"enabled": True, "base": 5300}, "nodeModules": "none", "env": "inherit", "agent": None,
    }
    result = worktree.create_worktree(body)
    assert result["ok"] is True
    dest = pathlib.Path(result["worktree"]["path"])
    assert dest.is_dir()
    env_text = (dest / ".env").read_text()
    assert "PORT=5300" in env_text
    assert "FOO=bar" in env_text

    items = worktree.list_worktrees(force=True)
    assert any(w["path"] == str(dest) for w in items)


def test_create_worktree_env_inherit_skips_when_source_missing(tmp_path):
    repo = _init_repo(tmp_path / "repo6")  # .env 없음
    body = {
        "repo": str(repo), "base": "main", "name": "t3", "branch": "feat/t3",
        "ports": {"enabled": False}, "nodeModules": "none", "env": "inherit", "agent": None,
    }
    result = worktree.create_worktree(body)
    dest = pathlib.Path(result["worktree"]["path"])
    assert not (dest / ".env").exists()


def test_create_worktree_env_empty_always_creates_file(tmp_path):
    repo = _init_repo(tmp_path / "repo6b")
    body = {
        "repo": str(repo), "base": "main", "name": "t4", "branch": "feat/t4",
        "ports": {"enabled": False}, "nodeModules": "none", "env": "empty", "agent": None,
    }
    result = worktree.create_worktree(body)
    dest = pathlib.Path(result["worktree"]["path"])
    assert (dest / ".env").is_file()
    assert (dest / ".env").read_text() == ""


def test_create_worktree_default_branch_from_name(tmp_path):
    repo = _init_repo(tmp_path / "repo9")
    body = {
        "repo": str(repo), "base": "main", "name": "myname",
        "ports": {"enabled": False}, "nodeModules": "none", "env": "none", "agent": None,
    }
    r = worktree.create_worktree(body)
    assert r["worktree"]["branch"] == "feat/myname"


def test_create_worktree_rejects_bad_name(tmp_path):
    repo = _init_repo(tmp_path / "repoA")
    body = {
        "repo": str(repo), "base": "main", "name": "../evil",
        "ports": {}, "nodeModules": "none", "env": "none", "agent": None,
    }
    with pytest.raises(worktree.WorktreeError):
        worktree.create_worktree(body)


def test_create_worktree_rejects_unknown_agent(tmp_path):
    repo = _init_repo(tmp_path / "repoB")
    body = {
        "repo": str(repo), "base": "main", "name": "t5",
        "ports": {}, "nodeModules": "none", "env": "none", "agent": "notarealcli",
    }
    with pytest.raises(worktree.WorktreeError):
        worktree.create_worktree(body)


def test_create_worktree_duplicate_path_conflicts(tmp_path):
    repo = _init_repo(tmp_path / "repoC")
    body = {
        "repo": str(repo), "base": "main", "name": "dup",
        "ports": {}, "nodeModules": "none", "env": "none", "agent": None,
    }
    worktree.create_worktree(body)
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree.create_worktree(body)
    assert exc.value.status == 409


# --- 삭제/열기 -------------------------------------------------------------------


def test_delete_worktree_removes_from_list(tmp_path):
    repo = _init_repo(tmp_path / "repo7")
    body = {
        "repo": str(repo), "base": "main", "name": "t6", "branch": "feat/t6",
        "ports": {"enabled": False}, "nodeModules": "none", "env": "none", "agent": None,
    }
    r = worktree.create_worktree(body)
    wt_id = r["worktree"]["id"]

    result = worktree.delete_worktree(wt_id, force=True)
    assert result["ok"] is True
    items = worktree.list_worktrees(force=True)
    assert all(w["id"] != wt_id for w in items)


def test_delete_main_worktree_blocked(tmp_path):
    repo = _init_repo(tmp_path / "repo8")
    items = worktree.list_worktrees(force=True)
    main_wt = next(w for w in items if pathlib.Path(w["repo"]) == repo.resolve() and w["isMain"])
    with pytest.raises(worktree.WorktreeError):
        worktree.delete_worktree(main_wt["id"])


def test_delete_dirty_worktree_requires_force(tmp_path):
    repo = _init_repo(tmp_path / "repo10")
    body = {
        "repo": str(repo), "base": "main", "name": "t7", "branch": "feat/t7",
        "ports": {"enabled": False}, "nodeModules": "none", "env": "none", "agent": None,
    }
    r = worktree.create_worktree(body)
    wt_id = r["worktree"]["id"]
    dest = pathlib.Path(r["worktree"]["path"])
    (dest / "dirty.txt").write_text("uncommitted")

    with pytest.raises(worktree.WorktreeError) as exc:
        worktree.delete_worktree(wt_id, force=False)
    assert exc.value.status == 409
    assert exc.value.payload.get("dirty") is True

    # force면 지워진다.
    result = worktree.delete_worktree(wt_id, force=True)
    assert result["ok"] is True
