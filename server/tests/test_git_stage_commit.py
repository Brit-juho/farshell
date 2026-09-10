"""D16: 코드 뷰어 stage/commit 최소 쓰기 경로.

라우트 레벨 HTTP 테스트(TestClient)는 D12에서 files.py 전체와 함께 다룬다.
여기서는 그 라우트가 감싸고 있는 핵심 로직(파일 목록 검증, git add/reset/commit
호출, 상태 갱신)을 직접 검증한다.
"""

import subprocess
from pathlib import Path

import pytest

from routes.files import (
    _bad_repo_relpath,
    _collect_status,
    _commit,
    _git,
    _has_staged_changes,
    _validate_files,
)


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _run(r, "init", "-q")
    _run(r, "config", "user.email", "test@example.com")
    _run(r, "config", "user.name", "test")
    (r / "a.py").write_text("print('a')\n")
    (r / "b.py").write_text("print('b')\n")
    _run(r, "add", ".")
    _run(r, "commit", "-q", "-m", "init")
    return r


# --- 파일 목록 검증 ------------------------------------------------------------


def test_bad_repo_relpath_rejects_absolute_and_traversal():
    assert _bad_repo_relpath("/etc/passwd")
    assert _bad_repo_relpath("../../etc/passwd")
    assert not _bad_repo_relpath("src/main.py")


def test_validate_files_rejects_empty_and_non_list():
    assert _validate_files([]) is None
    assert _validate_files(None) is None
    assert _validate_files("a.py") is None  # 문자열 하나를 리스트로 오인하면 안 됨


def test_validate_files_rejects_any_bad_entry():
    assert _validate_files(["ok.py", "../escape.py"]) is None


def test_validate_files_accepts_clean_list():
    assert _validate_files(["a.py", "sub/b.py"]) == ["a.py", "sub/b.py"]


# --- stage/unstage ------------------------------------------------------------


def test_stage_moves_file_into_index(repo):
    (repo / "a.py").write_text("print('a changed')\n")
    rc, _ = _git(repo, "add", "--", "a.py")
    assert rc == 0
    status = _collect_status(repo)
    entry = next(f for f in status["files"] if f["file"] == "a.py")
    assert entry["index_status"] == "M"
    assert entry["worktree_status"] == ""  # 워킹트리 변경 없음(스테이지와 동일 내용)


def test_unstage_keeps_working_tree_change(repo):
    (repo / "a.py").write_text("print('a changed')\n")
    _git(repo, "add", "--", "a.py")
    rc, _ = _git(repo, "reset", "-q", "--", "a.py")
    assert rc == 0
    status = _collect_status(repo)
    entry = next(f for f in status["files"] if f["file"] == "a.py")
    assert entry["index_status"] == ""  # 인덱스는 깨끗
    assert entry["worktree_status"] == "M"
    assert (repo / "a.py").read_text() == "print('a changed')\n"


# --- commit --------------------------------------------------------------------


def test_has_staged_changes_reflects_index(repo):
    assert not _has_staged_changes(repo)
    (repo / "a.py").write_text("print('a changed')\n")
    _git(repo, "add", "--", "a.py")
    assert _has_staged_changes(repo)


def test_commit_only_includes_staged_file(repo):
    (repo / "a.py").write_text("print('a changed')\n")
    (repo / "b.py").write_text("print('b changed — not staged')\n")
    _git(repo, "add", "--", "a.py")

    rc, _ = _commit(repo, "stage a only")
    assert rc == 0
    assert not _has_staged_changes(repo)

    status = _collect_status(repo)
    # a.py는 커밋됐으니 더 이상 변경 목록에 없고, b.py는 여전히 워킹트리 변경으로 남는다.
    files = {f["file"]: f for f in status["files"]}
    assert "a.py" not in files
    assert files["b.py"]["index_status"] == ""
    assert files["b.py"]["worktree_status"] == "M"


def test_commit_message_with_newlines_survives_intact(repo):
    (repo / "a.py").write_text("print('a changed')\n")
    _git(repo, "add", "--", "a.py")
    msg = "제목\n\n본문 첫 줄\n본문 둘째 줄"
    rc, _ = _commit(repo, msg)
    assert rc == 0
    rc, out = _git(repo, "log", "-1", "--pretty=%B")
    assert out.strip() == msg


# --- N35 §6/40 §3: dock 소스컨트롤 머리말(브랜치 · ahead/behind · +/−) ----------
#
# 이 값들은 dock 헤더 한 줄에만 쓰이지만, 잘못 읽으면 "회사 저장소인 줄 알고
# 커밋" 같은 오판으로 이어질 수 있는 자리(브랜치 이름)라 파싱을 고정한다.


@pytest.mark.parametrize(
    "line, expected",
    [
        ("## master", ("master", None, 0, 0)),
        ("## feat/x...origin/feat/x", ("feat/x", "origin/feat/x", 0, 0)),
        ("## feat/x...origin/feat/x [ahead 3]", ("feat/x", "origin/feat/x", 3, 0)),
        ("## feat/x...origin/feat/x [behind 2]", ("feat/x", "origin/feat/x", 0, 2)),
        ("## feat/x...origin/feat/x [ahead 3, behind 2]", ("feat/x", "origin/feat/x", 3, 2)),
        ("## No commits yet on master", ("master", None, 0, 0)),
        ("## HEAD (no branch)", (None, None, 0, 0)),   # detached — 이름이 아니다
        ("쓰레기", (None, None, 0, 0)),
    ],
)
def test_parse_branch_header(line, expected):
    from routes.files import _parse_branch_header

    got = _parse_branch_header(line)
    assert (got["branch"], got["upstream"], got["ahead"], got["behind"]) == expected


def test_collect_status_reports_branch_and_diff_stat(repo):
    (repo / "a.py").write_text("print('a')\nprint('a2')\n")
    (repo / "b.py").write_text("")
    d = _collect_status(repo)
    assert d["repo"] is True
    assert d["branch"] in ("master", "main")   # git 기본 브랜치 설정에 따라 다르다
    assert d["upstream"] is None               # 원격이 없는 임시 저장소
    assert (d["ahead"], d["behind"]) == (0, 0)
    assert d["insertions"] == 1                # a.py 한 줄 추가
    assert d["deletions"] == 1                 # b.py 한 줄 삭제


def test_collect_status_untracked_file_still_parses(repo):
    """헤더 레코드를 파일 레코드로 오인하면 목록 맨 앞에 유령 항목이 생긴다."""
    (repo / "new.py").write_text("x\n")
    d = _collect_status(repo)
    assert [f["file"] for f in d["files"]] == ["new.py"]
    assert d["files"][0]["status"] == "??"
