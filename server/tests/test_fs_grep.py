"""파일 내용 검색(`GET /api/fs/grep`, `fs_grep.py`, `scan_worker.grep`) — 경계, 거부 목록, 심링크 탈출, 정규식 기한,
상한, 취소, 자식 프로세스 실패 시 503.

엔진은 자식 프로세스 하나라(계획 2.3) 저장소 안(`git ls-files`)과 밖(순회) 두 후보 경로를 모두 본다.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fs_grep
import main
from resilience import scan as res_scan
from resilience import scan_worker


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = (tmp_path / "browse").resolve()
    r.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(r))
    return r


def _repo(root: Path) -> Path:
    repo = root / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    return repo


def _search(path, q, mode="literal", case=False, word=False, glob="", **kw):
    spec = fs_grep.build_spec(q, mode, case, word, glob)
    return fs_grep.search(str(path), spec, [], **kw)


def _rels(out):
    return sorted({r["rel"] for r in out["results"]})


# --- 질의 검사 ------------------------------------------------------------------------------------------


def test_empty_query_is_empty_result(client, root):
    r = client.get("/api/fs/grep", params={"q": "   ", "path": str(root)})
    assert r.status_code == 200 and r.json()["results"] == []


def test_bad_regex_reports_position(client, root):
    r = client.get("/api/fs/grep", params={"q": "groupBy(Zone", "mode": "regex", "path": str(root)})
    assert r.status_code == 400
    d = r.json()
    assert d["error"] == "bad_regex" and d["pos"] == 7 and "8" in d["reason"]


def test_literal_mode_escapes_regex_characters(root):
    (root / "a.txt").write_text("call groupBy(Zone)\n")
    out = _search(root, "groupBy(Zone")
    assert [(r["line"], r["col"]) for r in out["results"]] == [(1, 6)]


@pytest.mark.parametrize("q", ["a*", "x|", "^", "(?:)"])
def test_regex_that_matches_empty_text_is_refused(client, root, q):
    r = client.get("/api/fs/grep", params={"q": q, "mode": "regex", "path": str(root)})
    assert r.status_code == 400 and r.json()["error"] == "empty_match"


def test_bad_mode_and_too_long_query(client, root):
    assert client.get("/api/fs/grep", params={"q": "x", "mode": "glob", "path": str(root)}).status_code == 400
    assert client.get("/api/fs/grep", params={"q": "x" * 1001, "path": str(root)}).status_code == 400


def test_glob_split_keeps_braces_and_bangs():
    inc, exc = fs_grep.split_globs("frontend/**, *.{ts,tsx}\n!**/*.test.*, ! docs/")
    assert inc == ["frontend/**", "*.{ts,tsx}"]
    assert exc == ["**/*.test.*", "docs/"]
    with pytest.raises(fs_grep.BadQuery):
        fs_grep.split_globs(",".join(["a"] * 40))


@pytest.mark.parametrize("pattern,path,expect", [
    ("*.ts", "frontend/js/a.ts", True),
    ("*.ts", "frontend/js/a.tsx", False),
    ("docs", "docs/ref/a.md", True),
    ("frontend/**", "frontend/js/a.ts", True),
    ("frontend/**", "server/frontend.py", False),
    ("frontend/js", "frontend/js/x/y.ts", True),
    ("**/*.test.*", "frontend/tests/a.test.js", True),
    ("**/*.test.*", "a.test.js", True),
    ("*.{ts,tsx}", "x/a.tsx", True),
    ("src/[ab].py", "src/b.py", True),
    ("src/[!ab].py", "src/b.py", False),
    ("./server/", "server/x.py", True),
])
def test_glob_semantics(pattern, path, expect):
    assert scan_worker.GlobSet([pattern]).match(path) is expect


def test_include_and_exclude_globs(root):
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("needle\n")
    (root / "src" / "a_test.py").write_text("needle\n")
    (root / "docs").mkdir()
    (root / "docs" / "n.md").write_text("needle\n")
    out = _search(root, "needle", glob="src/**, !*_test.py")
    assert _rels(out) == ["src/a.py"]
    assert out["skipped"]["excluded"] == 2


def test_case_and_word_options(root):
    (root / "a.txt").write_text("Needle\nneedles\nneedle\n")
    assert [r["line"] for r in _search(root, "needle")["results"]] == [1, 2, 3]
    assert [r["line"] for r in _search(root, "needle", case=True)["results"]] == [2, 3]
    assert [r["line"] for r in _search(root, "needle", word=True)["results"]] == [1, 3]
    assert [r["line"] for r in _search(root, "needle", case=True, word=True)["results"]] == [3]


# --- 경계와 거부 목록 -------------------------------------------------------------------------------------


def test_outside_root_is_denied_and_file_is_not_a_directory(client, root):
    assert client.get("/api/fs/grep", params={"q": "x", "path": "/etc"}).status_code == 403
    (root / "a.txt").write_text("x\n")
    assert client.get("/api/fs/grep", params={"q": "x", "path": str(root / "a.txt")}).status_code == 404


def _plant_secrets(base: Path) -> None:
    (base / "ok.txt").write_text("needle ok\n")
    (base / ".env").write_text("needle=1\n")
    (base / ".env.local").write_text("needle=1\n")
    (base / "id_rsa").write_text("needle\n")
    (base / "server.pem").write_text("needle\n")
    (base / ".zsh_history").write_text("needle\n")
    for d in (".ssh", ".aws", ".secretstate", "node_modules", ".claude"):
        (base / d).mkdir()
        (base / d / "f.txt").write_text("needle\n")
    (base / ".github").mkdir()
    (base / ".github" / "ci.yml").write_text("needle\n")
    (base / ".gitignore").write_text("ignored.txt\n")
    (base / ".config" / "gcloud").mkdir(parents=True)
    (base / ".config" / "gcloud" / "c.txt").write_text("needle\n")


@pytest.mark.parametrize("in_repo", [False, True])
def test_deny_list_is_applied_before_reading(root, in_repo):
    base = _repo(root) if in_repo else root
    _plant_secrets(base)
    if in_repo:
        # 비밀 파일이 **추적되고 있어도**(실수로 커밋) 목록 단계에서 빠진다.
        _git(base, "add", "-f", ".")
        _git(base, "commit", "-q", "-m", "x")
    out = _search(base, "needle")
    assert _rels(out) == [".github/ci.yml", "ok.txt"]
    assert out["engine"] == ["git" if in_repo else "walk"]


def test_gitignored_files_are_not_searched_in_a_repo(root):
    repo = _repo(root)
    (repo / ".gitignore").write_text("ignored.txt\nbuildout/\n")
    (repo / "tracked.txt").write_text("needle\n")
    (repo / "untracked.txt").write_text("needle\n")
    (repo / "ignored.txt").write_text("needle\n")
    (repo / "buildout").mkdir()
    (repo / "buildout" / "x.txt").write_text("needle\n")
    _git(repo, "add", "tracked.txt", ".gitignore")
    _git(repo, "commit", "-q", "-m", "x")
    assert _rels(_search(repo, "needle")) == ["tracked.txt", "untracked.txt"]


def test_hidden_parent_of_search_root_is_denied(client, root):
    (root / ".hidden").mkdir()
    (root / ".hidden" / "a.txt").write_text("needle\n")
    r = client.get("/api/fs/grep", params={"q": "needle", "path": str(root / ".hidden")})
    assert r.status_code == 403


# --- 심링크 탈출 -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("in_repo", [False, True])
def test_symlinks_out_of_the_root_are_not_followed(root, tmp_path, in_repo):
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    (outside / "secret.txt").write_text("needle outside\n")
    base = _repo(root) if in_repo else root
    (base / "ok.txt").write_text("needle\n")
    os.symlink(outside / "secret.txt", base / "link.txt")      # 파일 심링크
    os.symlink(outside, base / "linkdir")                        # 폴더 심링크
    if in_repo:
        _git(base, "add", ".")
        _git(base, "commit", "-q", "-m", "x")
    out = _search(base, "needle")
    assert _rels(out) == ["ok.txt"]
    assert all("outside" not in r["text"] for r in out["results"])


def test_result_whose_path_now_resolves_elsewhere_is_dropped(root, tmp_path, monkeypatch):
    """판정 뒤 중간 폴더를 심링크로 바꿔치기한 경우 — 돌려주기 전 재검사가 거른다."""
    (root / "sub").mkdir()
    (root / "sub" / "a.txt").write_text("needle\n")
    swapped = (tmp_path / "elsewhere").resolve()
    swapped.mkdir()
    (swapped / "a.txt").write_text("needle\n")
    real_stream = res_scan.stream

    def stream(req, timeout, on_message, cancel=None):
        result = real_stream(req, timeout, on_message, cancel)
        os.rename(root / "sub", root / "sub-old")
        os.symlink(swapped, root / "sub")
        return result

    monkeypatch.setattr(res_scan, "stream", stream)
    out = _search(root, "needle")
    assert out["results"] == [] and out["dropped"] == 1


# --- 상한 ---------------------------------------------------------------------------------------------------


def test_result_cap_stops_at_500_lines(root):
    (root / "a.txt").write_text("needle\n" * 600)
    (root / "b.txt").write_text("needle\n")
    out = _search(root, "needle")
    assert len(out["results"]) == 500 and out["limit"] == "results" and out["truncated"]


def test_large_and_binary_files_are_skipped(root, monkeypatch):
    monkeypatch.setattr(fs_grep, "MAX_FILE_BYTES", 1000)
    (root / "big.txt").write_text("needle\n" + "x" * 2000)
    (root / "bin.dat").write_bytes(b"\0\1needle")
    (root / "ok.txt").write_text("needle\n")
    out = _search(root, "needle")
    assert _rels(out) == ["ok.txt"]
    assert out["skipped"]["large"] == 1 and out["skipped"]["binary"] == 1


def test_total_read_cap(root, monkeypatch):
    monkeypatch.setattr(fs_grep, "MAX_TOTAL_BYTES", 50)
    for i in range(5):
        (root / f"f{i}.txt").write_text("needle " + "x" * 40 + "\n")
    out = _search(root, "needle")
    assert out["limit"] == "bytes" and out["searched"] < 5


def test_output_cap(root, monkeypatch):
    monkeypatch.setattr(fs_grep, "MAX_OUTPUT_BYTES", 1000)
    (root / "a.txt").write_text(("needle " + "y" * 300 + "\n") * 20)
    out = _search(root, "needle")
    assert out["limit"] == "output" and 0 < len(out["results"]) < 20


def test_long_line_is_clipped_around_the_match_with_utf16_columns(root):
    line = "😀" + "a" * 1500 + "needle" + "b" * 1500
    (root / "a.txt").write_text(line + "\n")
    [hit] = _search(root, "needle")["results"]
    assert len(hit["text"]) == 500
    assert hit["col"] == 2 + 1500 + 1          # 😀는 UTF-16 두 칸
    a, b = hit["ranges"][0]
    assert hit["text"][a:b] == "needle"
    assert hit["cut"] + a + 1 == hit["col"]


def test_multiple_matches_on_one_line_are_one_result(root):
    (root / "a.txt").write_text("x needle y needle\r\nz\n")
    [hit] = _search(root, "needle")["results"]
    assert hit["ranges"] == [[2, 8], [11, 17]] and hit["text"] == "x needle y needle"


# --- 기한·취소·자리 ---------------------------------------------------------------------------------------


def test_catastrophic_regex_is_cut_by_the_process_deadline(root):
    (root / "a.txt").write_text("a" * 40 + "!\n")
    (root / "b.txt").write_text("aaa\n")
    started = time.monotonic()
    out = _search(root, "(a+)+$", mode="regex", timeout=1.0)
    assert time.monotonic() - started < 5
    assert out["timeout"] is True and out["truncated"] is True


def test_cancel_kills_the_worker(root):
    (root / "a.txt").write_text("a" * 40 + "!\n")
    token = res_scan.Cancel()
    threading.Timer(0.3, token.cancel).start()
    started = time.monotonic()
    out = _search(root, "(a+)+$", mode="regex", cancel=token, timeout=30)
    assert out == {"cancelled": True}
    assert time.monotonic() - started < 5


def test_cancel_before_spawn_kills_at_once():
    token = res_scan.Cancel()
    token.cancel()
    result, reason = res_scan.stream({"mode": "grep", "roots": []}, 10, lambda m: None, token)
    assert (result, reason) == (None, "cancelled")


def test_slots_one_per_client_and_two_overall():
    slots = fs_grep.Slots(limit=2)
    a1 = slots.acquire("a")
    a2 = slots.acquire("a")            # 같은 클라이언트의 새 검색 → 이전 것 취소, 자리 수는 그대로
    assert a1.cancelled and not a2.cancelled and slots.active() == 1
    b = slots.acquire("b")
    with pytest.raises(fs_grep.Busy):
        slots.acquire("c")
    slots.release("a", a1)              # 밀려난 쪽의 release는 새 자리를 지우지 않는다
    assert slots.active() == 2
    slots.release("a", a2)
    slots.release("b", b)
    assert slots.active() == 0


def test_route_is_busy_when_two_other_clients_search(client, root, monkeypatch):
    slots = fs_grep.Slots(limit=2)
    slots.acquire("x")
    slots.acquire("y")
    monkeypatch.setattr(fs_grep, "SLOTS", slots)
    r = client.get("/api/fs/grep", params={"q": "x", "path": str(root)})
    assert r.status_code == 503 and r.json()["error"] == "grep_busy"


def test_worker_spawn_failure_is_503_not_inprocess(client, root, monkeypatch):
    (root / "a.txt").write_text("needle\n")

    def boom(*a, **k):
        raise OSError("no python")

    monkeypatch.setattr(res_scan.subprocess, "Popen", boom)
    monkeypatch.setattr(scan_worker, "grep", lambda req: pytest.fail("ran in-process"))
    r = client.get("/api/fs/grep", params={"q": "needle", "path": str(root)})
    assert r.status_code == 503 and r.json()["error"] == "grep_unavailable"
    assert r.headers["retry-after"]


def test_route_happy_path_shape(client, root):
    (root / "x").mkdir()
    (root / "x" / "a.py").write_text("def f():\n    return needle\n")
    r = client.get("/api/fs/grep", params={"q": "needle", "path": str(root), "sid": "tab-1"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["results"] == [{"path": str(root / "x" / "a.py"), "rel": "x/a.py", "line": 2, "col": 12,
                             "text": "    return needle", "ranges": [[11, 17]]}]
    assert d["files"] == 1 and d["root"] == str(root) and d["timeout"] is False and d["limit"] is None
    assert set(d["skipped"]) == {"large", "binary", "excluded", "denied", "unreadable"}
