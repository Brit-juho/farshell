"""P2 회귀: /api/git/diff 가 fsguard 거부 목록에 걸리는 파일의 내용을 가려야 한다.

/api/fs/file 은 .env·*.pem·id_rsa 등을 절대 내보내지 않는데, git diff 는 별도 경로라서
같은 파일이 추적(tracked)되어 있으면 diff 본문에 그대로 실려 나갔다. 그러면 코드 뷰어의
핵심 방어(fsguard 거부 목록)가 이 엔드포인트 하나로 우회된다.
"""

import subprocess
from pathlib import Path

import pytest

from routes import git as files_routes


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


# --- S7: 헤더 형식·점 디렉터리·병합 diff로 가림 처리를 우회하던 것 -------------------------------
# git 은 비ASCII·`"`·역슬래시·제어 문자가 든 경로를 따옴표로 감싸 `diff --git "a/\354..pem" "b/..."`로 내보낸다.
# 정규식이 이 줄을 못 읽어 본문이 그대로 나갔고, 따옴표 헤더는 새 섹션으로도 안 쳐서 앞 섹션에 흡수됐다.


def _commit_then_modify(tmp_path, monkeypatch, names):
    r = tmp_path / "proj"
    r.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    for n in names:
        p = r / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("old\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    for n in names:
        (r / n).write_text("LEAK-" + n.replace("/", "_") + "\n")
    return r


@pytest.mark.parametrize("name", ["서버.pem", 'x"y.key', "tab\tname.pem", "back\\slash.pem", "공백 이름.pem"])
def test_quoted_or_odd_names_of_denied_files_are_redacted(tmp_path, monkeypatch, name):
    if "\t" in name and not hasattr(__import__("os"), "fork"):
        pytest.skip("tab in file name")
    r = _commit_then_modify(tmp_path, monkeypatch, [name, "ok.txt"])
    diff = files_routes._collect_diff(r, "", False)["diff"]
    assert "LEAK-" + name.replace("/", "_") not in diff
    assert "LEAK-ok.txt" in diff  # 옆 파일은 그대로 보인다


def test_korean_names_stay_unquoted_so_normal_files_still_show(tmp_path, monkeypatch):
    r = _commit_then_modify(tmp_path, monkeypatch, ["문서.txt", "서버.pem"])
    diff = files_routes._collect_diff(r, "", False)["diff"]
    assert "LEAK-문서.txt" in diff
    assert "LEAK-서버.pem" not in diff


def test_quoted_section_is_not_swallowed_by_the_previous_one(tmp_path, monkeypatch):
    r = _commit_then_modify(tmp_path, monkeypatch, ["a.txt", 'q"uote.key', "z.txt"])
    diff = files_routes._collect_diff(r, "", False)["diff"]
    assert "LEAK-a.txt" in diff and "LEAK-z.txt" in diff
    assert "LEAK-q" not in diff


def test_dot_directories_are_redacted_like_the_file_viewer_refuses_them(tmp_path, monkeypatch):
    r = _commit_then_modify(tmp_path, monkeypatch, [".sometool/state.json", ".github/ci.yml", ".gitignore"])
    diff = files_routes._collect_diff(r, "", False)["diff"]
    assert "LEAK-.sometool_state.json" not in diff
    assert "LEAK-.github_ci.yml" in diff   # 소스 트리의 점 디렉터리는 허용 목록
    assert "LEAK-.gitignore" in diff        # 점 파일은 열람 대상


def test_unparseable_header_fails_closed():
    raw = ("diff --git SOMETHING-NOT-A-PATH-PAIR\n--- x\n+++ y\n@@ -1 +1 @@\n-secret-old\n+secret-new\n"
           "diff --git a/ok.py b/ok.py\n--- a/ok.py\n+++ b/ok.py\n@@ -1 +1 @@\n-1\n+2\n")
    out = files_routes._redact_diff(raw)
    assert "secret-new" not in out and "+2" in out


def test_combined_merge_diff_is_redacted(tmp_path, monkeypatch):
    r = tmp_path / "proj"
    r.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / ".env").write_text("A=1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "side")
    (r / ".env").write_text("A=side-secret\n")
    _git(r, "commit", "-q", "-am", "side")
    _git(r, "checkout", "-q", "main")
    (r / ".env").write_text("A=main-secret\n")
    _git(r, "commit", "-q", "-am", "main")
    subprocess.run(["git", "-C", str(r), "merge", "side"], capture_output=True)  # 충돌 — .env 가 unmerged
    diff = files_routes._collect_diff(r, "", False)["diff"]
    assert "diff --cc" in diff
    assert "main-secret" not in diff and "side-secret" not in diff


@pytest.mark.parametrize("line, expect", [
    ("diff --git a/x.py b/x.py", {"x.py"}),
    ("diff --git a/dir with space/f.pem b/dir with space/f.pem", {"dir with space/f.pem"}),
    ('diff --git "a/q\\"uote.key" "b/q\\"uote.key"', {'q"uote.key'}),
    ('diff --git "a/\\354\\204\\234.pem" "b/\\354\\204\\234.pem"', {"서.pem"}),
    ('diff --git a/old.txt "b/new\\tname.txt"', {"old.txt", "new\tname.txt"}),
    ("diff --cc .env", {".env"}),
    ('diff --combined "x\\"y"', {'x"y'}),
])
def test_header_paths_cover_the_real_path(line, expect):
    got = files_routes._header_paths(line)
    assert got is not None and expect <= set(got)


@pytest.mark.parametrize("line", ["diff --git nothing", 'diff --git "a/unterminated b/x', "diff --git ", "diff --cc "])
def test_header_paths_refuse_what_they_cannot_read(line):
    assert files_routes._header_paths(line) is None


def test_renamed_away_from_a_denied_name_is_redacted(tmp_path, monkeypatch):
    r = tmp_path / "proj"
    r.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / ".env").write_text("".join(f"KEY{i}=value{i}\n" for i in range(20)))
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    _git(r, "mv", ".env", "config.txt")
    (r / "config.txt").write_text("".join(f"KEY{i}=value{i}\n" for i in range(19)) + "KEY19=RENAMED-LEAK\n")
    _git(r, "add", "-A")
    diff = files_routes._collect_diff(r, "", True)["diff"]
    assert "rename from .env" in diff
    assert "RENAMED-LEAK" not in diff and "value3" not in diff


def test_commit_file_diff_uses_the_same_redaction(tmp_path, monkeypatch):
    r = _commit_then_modify(tmp_path, monkeypatch, ["서버.pem", "ok.txt"])
    _git(r, "commit", "-q", "-am", "change")
    sha = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    diff = files_routes._collect_commit_file_diff(r, sha, "")["diff"]
    assert "LEAK-ok.txt" in diff and "LEAK-서버.pem" not in diff
