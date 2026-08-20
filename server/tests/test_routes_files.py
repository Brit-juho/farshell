"""D12: routes/files.py 라우터 레이어 HTTP 테스트.

fsguard.py(경로 판정)와 stage/commit 핵심 로직(_collect_status/_commit 등)은 각각
test_fsguard.py / test_git_stage_commit.py에서 이미 단위 테스트한다. 여기서는 그
로직들이 실제 HTTP 요청 경로에서 올바른 상태코드로 나오는지만 확인한다 — 특히
fsguard.FsDenied → 403, 존재하지 않는 경로 → 404, stage/commit의 입력 검증 → 4xx.
"""

import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import main


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # fsguard.get_roots()는 요청마다 VT_BROWSE_ROOTS를 다시 읽으므로 setenv만으로 충분하다.
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    r = tmp_path / "proj"
    r.mkdir()
    _run_git(r, "init", "-q")
    _run_git(r, "config", "user.email", "test@example.com")
    _run_git(r, "config", "user.name", "test")
    (r / "a.py").write_text("print('a')\n")
    _run_git(r, "add", ".")
    _run_git(r, "commit", "-q", "-m", "init")
    return r


# --- fs (열람) -----------------------------------------------------------------


def test_fs_roots_reflects_env(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    r = client.get("/api/fs/roots")
    assert r.status_code == 200
    assert str(tmp_path) in r.json()["roots"]


def test_fs_tree_outside_root_is_denied(client, repo):
    r = client.get("/api/fs/tree", params={"path": "/etc"})
    assert r.status_code == 403
    assert r.json()["error"] == "denied"


def test_fs_tree_on_a_file_is_not_a_directory(client, repo):
    r = client.get("/api/fs/tree", params={"path": str(repo / "a.py")})
    assert r.status_code == 404


def test_fs_file_not_found(client, repo):
    r = client.get("/api/fs/file", params={"path": str(repo / "missing.py")})
    assert r.status_code == 404


def test_fs_file_denylisted_secret_is_denied(client, repo):
    (repo / ".env").write_text("SECRET=x\n")
    r = client.get("/api/fs/file", params={"path": str(repo / ".env")})
    assert r.status_code == 403
    assert r.json()["error"] == "denied"


def test_fs_file_reads_ok(client, repo):
    r = client.get("/api/fs/file", params={"path": str(repo / "a.py")})
    assert r.status_code == 200
    assert "print" in r.json()["content"]


# --- git 열람 --------------------------------------------------------------------


def test_git_status_denied_outside_root(client, repo):
    r = client.get("/api/git/status", params={"repo": "/etc"})
    assert r.status_code == 403


def test_git_status_non_repo_dir_returns_repo_false(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    r = client.get("/api/git/status", params={"repo": str(plain)})
    assert r.status_code == 200
    assert r.json()["repo"] is False


def test_git_status_not_a_directory(client, repo):
    r = client.get("/api/git/status", params={"repo": str(repo / "a.py")})
    assert r.status_code == 404


def test_git_diff_rejects_path_traversal_file(client, repo):
    r = client.get("/api/git/diff", params={"repo": str(repo), "file": "../../etc/passwd"})
    assert r.status_code == 403
    assert r.json()["error"] == "denied"


def test_git_diff_rejects_absolute_file(client, repo):
    r = client.get("/api/git/diff", params={"repo": str(repo), "file": "/etc/passwd"})
    assert r.status_code == 403


def test_git_diff_ok(client, repo):
    (repo / "a.py").write_text("print('a changed')\n")
    r = client.get("/api/git/diff", params={"repo": str(repo)})
    assert r.status_code == 200
    assert "a changed" in r.json()["diff"]


# --- git 쓰기(stage/unstage/commit) — D16 --------------------------------------


def test_git_stage_missing_repo_is_denied(client):
    r = client.post("/api/git/stage", json={"files": ["a.py"]})
    assert r.status_code == 403


def test_git_stage_repo_outside_root_is_denied(client, repo):
    r = client.post("/api/git/stage", json={"repo": "/etc", "files": ["passwd"]})
    assert r.status_code == 403


def test_git_stage_non_repo_dir_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    r = client.post("/api/git/stage", json={"repo": str(plain), "files": ["a.py"]})
    assert r.status_code == 404


@pytest.mark.parametrize("files", [[], None, "a.py", ["../escape.py"], [123]])
def test_git_stage_rejects_bad_files_payload(client, repo, files):
    r = client.post("/api/git/stage", json={"repo": str(repo), "files": files})
    assert r.status_code == 403
    assert r.json()["error"] == "denied"


def test_git_stage_then_status_reflects_index(client, repo):
    (repo / "a.py").write_text("print('a changed')\n")
    r = client.post("/api/git/stage", json={"repo": str(repo), "files": ["a.py"]})
    assert r.status_code == 200
    body = r.json()
    entry = next(f for f in body["files"] if f["file"] == "a.py")
    assert entry["index_status"] == "M"
    assert entry["worktree_status"] == ""


def test_git_unstage_keeps_working_tree_change(client, repo):
    (repo / "a.py").write_text("print('a changed')\n")
    client.post("/api/git/stage", json={"repo": str(repo), "files": ["a.py"]})
    r = client.post("/api/git/unstage", json={"repo": str(repo), "files": ["a.py"]})
    assert r.status_code == 200
    entry = next(f for f in r.json()["files"] if f["file"] == "a.py")
    assert entry["index_status"] == ""
    assert entry["worktree_status"] == "M"


def test_git_commit_rejects_empty_message(client, repo):
    (repo / "a.py").write_text("print('a changed')\n")
    client.post("/api/git/stage", json={"repo": str(repo), "files": ["a.py"]})
    r = client.post("/api/git/commit", json={"repo": str(repo), "message": "   "})
    assert r.status_code == 400


def test_git_commit_rejects_oversized_message(client, repo):
    (repo / "a.py").write_text("print('a changed')\n")
    client.post("/api/git/stage", json={"repo": str(repo), "files": ["a.py"]})
    r = client.post("/api/git/commit", json={"repo": str(repo), "message": "x" * 9000})
    assert r.status_code == 400


def test_git_commit_rejects_when_nothing_staged(client, repo):
    r = client.post("/api/git/commit", json={"repo": str(repo), "message": "no-op"})
    assert r.status_code == 400


def test_git_commit_success_reflects_in_status_and_log(client, repo):
    (repo / "a.py").write_text("print('a changed')\n")
    client.post("/api/git/stage", json={"repo": str(repo), "files": ["a.py"]})
    r = client.post("/api/git/commit", json={"repo": str(repo), "message": "update a"})
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True
    assert not any(f["file"] == "a.py" for f in body["files"])

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%s"],
        capture_output=True, check=True,
    ).stdout.decode()
    assert log.strip() == "update a"
