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

import file_store
import main
import tmux_target


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


# --- fs 검색 (N5/N40, 60-settings-palette.md §3 `/` 모드) --------------------


def test_fs_search_empty_query_returns_empty(client, repo):
    r = client.get("/api/fs/search", params={"q": ""})
    assert r.status_code == 200
    assert r.json() == {"results": [], "truncated": False}


def test_fs_search_finds_fuzzy_subsequence_match(client, repo):
    (repo / "settings.js").write_text("x")
    r = client.get("/api/fs/search", params={"q": "stgs"})
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["results"]]
    assert "settings.js" in names


def test_fs_search_scores_closer_matches_first(client, repo):
    (repo / "settings.js").write_text("x")
    (repo / "s_e_t_t_i_n_g_s_extra_long_name.js").write_text("x")
    r = client.get("/api/fs/search", params={"q": "settings"})
    names = [x["name"] for x in r.json()["results"]]
    assert names[0] == "settings.js"


def test_fs_search_excludes_denied_and_vcs_dirs(client, repo):
    (repo / ".env").write_text("SECRET=1")
    nm = repo / "node_modules"
    nm.mkdir()
    (nm / "envfile.js").write_text("x")
    r = client.get("/api/fs/search", params={"q": "env"})
    paths = [x["path"] for x in r.json()["results"]]
    assert not any(".env" in p.split("/")[-1] == ".env" for p in paths)
    assert not any("node_modules" in p for p in paths)


def test_fs_search_respects_path_scope_and_denies_outside_root(client, repo):
    sub = repo / "sub"
    sub.mkdir()
    (sub / "target.py").write_text("x")
    r = client.get("/api/fs/search", params={"q": "target", "path": str(sub)})
    assert r.status_code == 200
    assert [x["name"] for x in r.json()["results"]] == ["target.py"]

    r2 = client.get("/api/fs/search", params={"q": "x", "path": "/etc"})
    assert r2.status_code == 403


def test_fs_search_caps_results_at_50(client, repo):
    for i in range(60):
        (repo / f"file_{i:03d}.txt").write_text("x")
    r = client.get("/api/fs/search", params={"q": "file"})
    data = r.json()
    assert len(data["results"]) <= 50
    assert data["truncated"] is True


# --- 파일 저장소 API (N19) -----------------------------------------------------


@pytest.fixture
def file_state(tmp_path, monkeypatch):
    """files.json/실파일을 격리된 ~/.vt로 — file_store가 매 호출 시 env를 다시 읽으므로
    setenv만으로 충분하다(repo 픽스처가 VT_BROWSE_ROOTS에 하는 것과 동일한 패턴)."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    return tmp_path


def _upload_via_api(client, name="a.txt", content=b"hello"):
    r = client.post("/api/upload", files={"file": (name, content)})
    assert r.status_code == 200
    return r.json()


def test_upload_then_files_list_shows_id_only_no_disk_path(client, file_state):
    body = _upload_via_api(client)
    assert "id" in body and body["path"]
    r = client.get("/api/files")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == body["id"]
    assert "path" not in items[0]  # 실 디스크 경로는 노출하지 않는다


def test_download_by_id_succeeds_and_sets_security_headers(client, file_state):
    body = _upload_via_api(client, name="report.pdf", content=b"pdfdata")
    r = client.get(f"/api/files/{body['id']}/download")
    assert r.status_code == 200
    assert r.content == b"pdfdata"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]
    assert "attachment" in r.headers["content-disposition"]


def test_download_unknown_id_is_404(client, file_state):
    r = client.get("/api/files/does-not-exist/download")
    assert r.status_code == 404


def test_legacy_download_path_route_is_gone(client, file_state):
    """/tmp/vt-uploads 시절의 경로 기반 다운로드 API는 완전히 제거됐다 — id 기반으로만 접근한다."""
    r = client.get("/api/download", params={"path": "/etc/passwd"})
    assert r.status_code == 404  # 라우트 자체가 없다(FastAPI 기본 404)


def test_delete_file_removes_it(client, file_state):
    body = _upload_via_api(client)
    r = client.delete(f"/api/files/{body['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/files/{body['id']}/download").status_code == 404
    assert client.delete(f"/api/files/{body['id']}").status_code == 404


def test_insert_types_path_without_enter(client, file_state, monkeypatch):
    body = _upload_via_api(client)
    monkeypatch.setattr(tmux_target, "session_pane", lambda name: "%3" if name == "dev" else None)
    calls = []
    monkeypatch.setattr(tmux_target, "type_to_tmux", lambda pane, text: calls.append((pane, text)) or True)
    r = client.post(f"/api/files/{body['id']}/insert", json={"session": "dev"})
    assert r.status_code == 200
    assert calls and calls[0][0] == "%3"


def test_insert_unknown_session_is_404(client, file_state, monkeypatch):
    body = _upload_via_api(client)
    monkeypatch.setattr(tmux_target, "session_pane", lambda name: None)
    r = client.post(f"/api/files/{body['id']}/insert", json={"session": "ghost"})
    assert r.status_code == 404


def test_files_filter_shared_and_expiring(client, file_state, monkeypatch):
    a = _upload_via_api(client, name="shared.txt")
    b = _upload_via_api(client, name="soon.txt")
    _upload_via_api(client, name="normal.txt")

    items = file_store.list_items()
    for x in items:
        if x["id"] == a["id"]:
            x["shares"] = [{"shareId": "s1"}]
        if x["id"] == b["id"]:
            x["created"] -= file_store.TTL_SECONDS - 1  # 3일 미만 남김
    file_store._write_unlocked(items)

    shared = client.get("/api/files", params={"filter": "shared"}).json()["items"]
    assert {x["id"] for x in shared} == {a["id"]}

    expiring = client.get("/api/files", params={"filter": "expiring"}).json()["items"]
    assert {x["id"] for x in expiring} == {b["id"]}


def test_files_list_carries_quota_for_dock_footer(client, file_state):
    """dock 파일 탭 푸터 게이지(50 §4). used는 **필터와 무관하게 저장소 전체**다 —
    필터 결과를 세면 칩을 누를 때마다 게이지가 움직인다."""
    _upload_via_api(client, name="a.txt", content=b"0123456789")
    _upload_via_api(client, name="b.txt", content=b"01234")
    q = client.get("/api/files").json()["quota"]
    assert q["used"] == 15
    assert q["max"] == file_store.MAX_TOTAL_BYTES
    assert q["ttl_days"] == file_store.TTL_SECONDS // 86400
    # 필터를 걸어도 quota는 그대로.
    assert client.get("/api/files", params={"filter": "shared"}).json()["quota"]["used"] == 15


def test_files_list_never_leaks_pin_hash(client, file_state):
    """PIN 해시가 목록에 실리면 오프라인에서 4자리를 그냥 맞춰볼 수 있다
    (5회 제한은 서버 시도에만 걸린다)."""
    body = _upload_via_api(client)
    file_store.add_share(body["id"], "pin", 3600, False, pin="1234")
    shares = client.get("/api/files").json()["items"][0]["shares"]
    assert len(shares) == 1
    assert shares[0]["mode"] == "pin"
    assert set(shares[0]) == {"shareId", "mode", "exp", "once", "views", "lastAccess"}


def test_file_path_route_returns_disk_path_for_copy_action(client, file_state):
    """「경로 복사」(50 §4의 5개 동작 중 하나)가 쓰는 경로. 입력은 id뿐이다."""
    body = _upload_via_api(client)
    r = client.get(f"/api/files/{body['id']}/path")
    assert r.status_code == 200
    assert r.json()["path"] == str(file_store.real_path_for(body["id"]))
    assert client.get("/api/files/nope/path").status_code == 404


# --- [T3] 인라인 이미지 미리보기 ---------------------------------------------
#
# 이 엔드포인트에서 나간 바이트는 브라우저가 **이 오리진의 리소스로** 해석한다.
# 그래서 테스트의 무게가 "이미지가 보이는가"보다 **"이미지가 아닌 것이 절대
# 안 나가는가"**에 있다.

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_image_file_is_reported_as_image_not_just_binary(client, repo):
    p = repo / "shot.png"
    p.write_bytes(PNG_1PX)
    d = client.get(f"/api/fs/file?path={p}").json()
    assert d["binary"] is True and d["image"] is True
    assert d["mime"] == "image/png" and d["too_large"] is False


def test_raw_serves_the_image_bytes_with_nosniff(client, repo):
    p = repo / "shot.png"
    p.write_bytes(PNG_1PX)
    r = client.get(f"/api/fs/raw?path={p}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == PNG_1PX


def test_raw_refuses_svg(client, repo):
    """SVG는 스크립트를 품을 수 있다 — 같은 오리진에서 열면 XSS다.
    (텍스트라 뷰어는 어차피 소스로 보여준다 — 잃는 게 없다.)"""
    p = repo / "icon.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    assert client.get(f"/api/fs/raw?path={p}").status_code == 403


def test_raw_refuses_html_renamed_to_png(client, repo):
    """확장자만 믿으면 `.png`로 이름만 바꾼 HTML이 image/png로 나간다 —
    매직 바이트가 안 맞으면 거부한다."""
    p = repo / "evil.png"
    p.write_text("<html><script>alert(1)</script></html>")
    assert client.get(f"/api/fs/raw?path={p}").status_code == 403


def test_raw_refuses_plain_text(client, repo):
    assert client.get(f"/api/fs/raw?path={repo / 'a.py'}").status_code == 403


def test_raw_honours_the_browse_boundary(client, repo, tmp_path):
    """경계는 /api/fs/file과 같은 fsguard 하나다 — 새 엔드포인트가 뒷문이 되면 안 된다."""
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG_1PX)
    assert client.get(f"/api/fs/raw?path={outside}").status_code == 403


def test_raw_refuses_denied_names_even_if_they_are_images(client, repo):
    p = repo / ".env.png"
    p.write_bytes(PNG_1PX)
    assert client.get(f"/api/fs/raw?path={p}").status_code == 403


def test_raw_over_the_cap_is_413(client, repo, monkeypatch):
    import fsguard
    monkeypatch.setattr(fsguard, "MAX_IMAGE_BYTES", 4)
    p = repo / "big.png"
    p.write_bytes(PNG_1PX)
    r = client.get(f"/api/fs/raw?path={p}")
    assert r.status_code == 413
    # 목록 응답도 같은 사실을 알려야 화면이 "너무 큽니다"를 그릴 수 있다.
    assert client.get(f"/api/fs/file?path={p}").json()["too_large"] is True
