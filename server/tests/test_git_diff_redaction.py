"""P2 회귀: /api/git/diff 가 fsguard 거부 목록에 걸리는 파일의 내용을 가려야 한다.

/api/fs/file 은 .env·*.pem·id_rsa 등을 절대 내보내지 않는데, git diff 는 별도 경로라서
같은 파일이 추적(tracked)되어 있으면 diff 본문에 그대로 실려 나갔다. 그러면 코드 뷰어의
핵심 방어(fsguard 거부 목록)가 이 엔드포인트 하나로 우회된다.
"""

import subprocess
from pathlib import Path

import pytest

from routes import files as files_routes


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """작은 git 저장소: 커밋된 .py 파일 + 커밋된 .env 파일, 둘 다 워킹 트리에서 수정."""
    r = tmp_path / "proj"
    r.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")

    (r / "main.py").write_text("print('hello')\n")
    (r / ".env").write_text("SECRET=old\n")
    _git(r, "add", "main.py", ".env")
    _git(r, "commit", "-q", "-m", "init")

    # 워킹 트리 수정 — 둘 다 uncommitted diff에 잡힌다.
    (r / "main.py").write_text("print('hello')\nprint('world')\n")
    (r / ".env").write_text("SECRET=leaked-value-should-not-appear\n")

    return r


def test_denied_file_diff_is_redacted(repo):
    result = files_routes._collect_diff(repo, "", False)
    diff = result["diff"]
    assert "leaked-value-should-not-appear" not in diff
    assert files_routes.REDACTION_PLACEHOLDER in diff
    # 어떤 파일이 바뀌었는지는 여전히 알 수 있어야 한다 (경로 자체는 비밀이 아니다).
    assert ".env" in diff


def test_normal_file_diff_passes_through_unchanged(repo):
    result = files_routes._collect_diff(repo, "", False)
    diff = result["diff"]
    assert "print('world')" in diff
    assert "main.py" in diff


def test_redact_diff_keeps_headers_and_drops_hunks():
    raw = (
        "diff --git a/.env b/.env\n"
        "index 111..222 100644\n"
        "--- a/.env\n"
        "+++ b/.env\n"
        "@@ -1 +1 @@\n"
        "-SECRET=old\n"
        "+SECRET=leaked\n"
        "diff --git a/main.py b/main.py\n"
        "index 333..444 100644\n"
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1 +1,2 @@\n"
        " print('hello')\n"
        "+print('world')\n"
    )
    redacted = files_routes._redact_diff(raw)
    assert "SECRET=leaked" not in redacted
    assert files_routes.REDACTION_PLACEHOLDER in redacted
    assert "diff --git a/.env b/.env" in redacted
    assert "print('world')" in redacted


def test_diff_path_denied_matches_fsguard_denylist():
    assert files_routes._diff_path_denied(".env") is True
    assert files_routes._diff_path_denied("services/api/.env.production") is True
    assert files_routes._diff_path_denied("nested/.ssh/config") is True
    assert files_routes._diff_path_denied("main.py") is False
