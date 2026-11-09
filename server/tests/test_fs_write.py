"""파일 쓰기(files-overhaul 단계 8) — 저장·새 파일·이름 바꾸기·휴지통 삭제의 보안 회귀.

여기 시험은 **순진한 구현에서 실패**하도록 짰다: 경로로 다시 여는 구현은 바꿔치기 시험에서 경계 밖 파일을 쓰고,
잠금 없는 구현은 동시 저장에서 둘 다 성공하고, save_id를 기억하지 않는 구현은 재시도에서 두 번 쓰고,
`rm`으로 떨어지는 구현은 휴지통 실패에서 파일을 잃는다.
"""
from __future__ import annotations

import codecs
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import auth as _auth
import fs_trash
import fs_write
import fsguard
import main
from authz import principals as P
from shared_testkit import _Sock

PW = "s3cret-pw"


def _auth_env(tmp_path, monkeypatch, key="test-session-signing-key"):
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", key)
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password(PW))
    monkeypatch.setattr(_auth, "VT_AUTH_TOKEN", "machine-tok")
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    return state_dir


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = (tmp_path / "root").resolve()
    (r / "proj").mkdir(parents=True)
    (r / "proj" / "a.txt").write_text("one\ntwo\n")
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    (outside / "victim.txt").write_text("keep me\n")
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(r))
    monkeypatch.delenv("VT_WRITE_ROOTS", raising=False)
    fs_write.SAVES.clear()
    return r


@pytest.fixture
def client(tmp_path, monkeypatch, root):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": PW}).status_code == 200
        assert c.post("/api/auth/elevate", json={"password": PW}).status_code == 200
        yield c


@pytest.fixture
def unelevated(tmp_path, monkeypatch, root):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": PW}).status_code == 200
        yield c


def rev(p: Path) -> str:
    st = os.stat(p)
    return f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}"


_n = [0]


def sid() -> str:
    _n[0] += 1
    return f"save-{os.getpid()}-{_n[0]:06d}"


def save_body(p: Path, content: str, **kw) -> dict:
    body = {"path": str(p), "content": content, "encoding": "utf-8", "expected_rev": rev(p), "save_id": sid()}
    body.update(kw)
    return body


# --- 저장: 정상 ----------------------------------------------------------------------


def test_save_replaces_atomically_and_keeps_mode(client, root):
    p = root / "proj" / "a.txt"
    os.chmod(p, 0o640)
    ino = os.stat(p).st_ino
    r = client.put("/api/fs/file", json=save_body(p, "one\nTWO\nthree\n"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert p.read_text() == "one\nTWO\nthree\n"
    assert out["rev"] == rev(p) and out["eol"] == "lf" and out["durable"] is True
    assert os.stat(p).st_ino != ino                       # 새 inode — rename으로 바꿨다
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640
    assert not [x for x in os.listdir(p.parent) if x.startswith(".fsh-")]


def test_save_keeps_crlf_bom_and_cp949(client, root):
    p = root / "proj" / "win.txt"
    p.write_bytes(codecs.BOM_UTF8 + b"a\r\nb\r\n")
    r = client.put("/api/fs/file", json=save_body(p, "a\nb\nc\n"))
    assert r.status_code == 200, r.text
    assert p.read_bytes() == codecs.BOM_UTF8 + b"a\r\nb\r\nc\r\n"
    k = root / "proj" / "k.txt"
    k.write_bytes("가나\n".encode("cp949"))
    r = client.put("/api/fs/file", json=save_body(k, "가나다\n", encoding="cp949"))
    assert r.status_code == 200, r.text
    assert k.read_bytes() == "가나다\n".encode("cp949")


def test_save_refuses_mixed_eol_and_wrong_encoding_d9(client, root):
    p = root / "proj" / "mixed.txt"
    p.write_bytes(b"a\r\nb\nc\n")
    r = client.put("/api/fs/file", json=save_body(p, "x\n"))
    assert r.status_code == 422 and r.json()["detail"] == "mixed_eol"
    k = root / "proj" / "k.txt"
    k.write_bytes("가나\n".encode("cp949"))
    r = client.put("/api/fs/file", json=save_body(k, "x\n", encoding="utf-8"))
    assert r.status_code == 422 and r.json()["detail"] == "decode_errors"
    assert k.read_bytes() == "가나\n".encode("cp949") and p.read_bytes() == b"a\r\nb\nc\n"
    bad = client.put("/api/fs/file", json=save_body(k, "x", encoding="auto"))
    assert bad.status_code == 400


def test_save_reports_the_first_unencodable_character(client, root):
    k = root / "proj" / "k.txt"
    k.write_bytes("가\n".encode("cp949"))
    r = client.put("/api/fs/file", json=save_body(k, "가\nabőc\n", encoding="cp949"))
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "unencodable" and (body["line"], body["col"], body["char"]) == (2, 3, "ő")
    assert k.read_bytes() == "가\n".encode("cp949")


def test_save_conflict_returns_current_rev_and_leaves_file(client, root):
    p = root / "proj" / "a.txt"
    old = rev(p)
    time.sleep(0.01)
    p.write_text("someone else\n")
    r = client.put("/api/fs/file", json=save_body(p, "mine\n", expected_rev=old))
    assert r.status_code == 409 and r.json()["error"] == "conflict" and r.json()["rev"] == rev(p)
    assert p.read_text() == "someone else\n"


def test_save_size_cap_and_hardlink_and_readonly(client, root, monkeypatch):
    p = root / "proj" / "a.txt"
    monkeypatch.setattr(fs_write, "MAX_SAVE_BYTES", 10)
    assert client.put("/api/fs/file", json=save_body(p, "x" * 11)).status_code == 413
    monkeypatch.setattr(fs_write, "MAX_SAVE_BYTES", 2 * 1024 * 1024)
    os.link(p, root / "proj" / "hard.txt")
    r = client.put("/api/fs/file", json=save_body(p, "x\n"))
    assert r.status_code == 409 and r.json()["error"] == "hardlinked"
    os.unlink(root / "proj" / "hard.txt")
    os.chmod(p, 0o444)
    r = client.put("/api/fs/file", json=save_body(p, "x\n"))
    assert r.status_code == 403 and r.json()["error"] == "not_writable"
    assert p.read_text() == "one\ntwo\n"


def test_save_recreates_a_missing_file_only_without_overwrite(client, root):
    gone = root / "proj" / "gone.txt"
    body = {"path": str(gone), "content": "back\n", "encoding": "utf-8", "expected_rev": None, "save_id": sid()}
    assert client.put("/api/fs/file", json=body).status_code == 200
    assert gone.read_text() == "back\n"
    body["save_id"] = sid()
    r = client.put("/api/fs/file", json=body)               # 이제 있으니 409, 덮어쓰지 않는다
    assert r.status_code == 409 and r.json()["error"] == "exists"


# --- 경계 · 거부 목록 · 실행 설정 ---------------------------------------------------------


@pytest.mark.parametrize("rel", [
    "../outside/victim.txt", "proj/../proj/a.txt", "proj/.env", "proj/key.pem", "proj/.github/workflows/ci.yml",
    "proj/.vscode/tasks.json", "proj/.husky/pre-commit", "proj/.git", "proj/.GitHub/x.yml", "proj/.ssh/config",
])
def test_save_refuses_paths_outside_policy(client, root, rel):
    target = Path(str(root) + "/" + rel)
    if ".." not in rel:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")
    body = {"path": str(target), "content": "pwned\n", "encoding": "utf-8",
            "expected_rev": rev(target) if target.exists() else "1:1:1:1", "save_id": sid()}
    r = client.put("/api/fs/file", json=body)
    assert r.status_code == 403, (rel, r.text)
    assert ((root.parent / "outside") / "victim.txt").read_text() == "keep me\n"


def test_write_roots_off_closes_every_write(client, root, monkeypatch):
    monkeypatch.setenv("VT_WRITE_ROOTS", "off")
    p = root / "proj" / "a.txt"
    assert client.put("/api/fs/file", json=save_body(p, "x\n")).status_code == 403
    assert client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": "n.txt"}).status_code == 403
    assert client.post("/api/fs/delete", json={"path": str(p)}).status_code == 403
    assert p.read_text() == "one\ntwo\n"


# --- 바꿔치기(판정 뒤 심링크) ----------------------------------------------------------


def _swap_parent_after_check(monkeypatch, root):
    """판정(`resolve_for_write`)이 끝난 직후 `proj` 폴더를 경계 밖을 가리키는 심링크로 바꾼다."""
    real = fs_write._resolve

    def swapping(raw):
        p = real(raw)
        if (root / "proj").is_dir() and not (root / "proj").is_symlink():
            (root / "proj").rename(root / "proj.real")
            (root / "proj").symlink_to((root.parent / "outside"))
        return p
    monkeypatch.setattr(fs_write, "_resolve", swapping)


def test_parent_swap_during_save_never_writes_outside(client, root, monkeypatch):
    (root / "proj" / "victim.txt").write_text("in root\n")
    body = save_body(root / "proj" / "victim.txt", "pwned\n")
    _swap_parent_after_check(monkeypatch, root)
    r = client.put("/api/fs/file", json=body)
    assert r.status_code in (403, 404, 409), r.text
    assert ((root.parent / "outside") / "victim.txt").read_text() == "keep me\n"
    assert not [x for x in os.listdir((root.parent / "outside")) if x.startswith(".fsh-")]


def test_parent_swap_during_new_rename_delete(client, root, monkeypatch):
    (root / "proj" / "victim.txt").write_text("in root\n")
    _swap_parent_after_check(monkeypatch, root)
    r = client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": "planted.txt"})
    assert r.status_code in (403, 404, 409), r.text
    assert not ((root.parent / "outside") / "planted.txt").exists()
    # 이름 바꾸기·삭제(휴지통)도 경계 밖 파일을 건드리지 않는다
    (root / "proj").unlink()
    (root / "proj.real").rename(root / "proj")
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "victim.txt"), "to": str(root / "proj" / "v2.txt")})
    assert r.status_code in (403, 404, 409), r.text
    (root / "proj").unlink()
    (root / "proj.real").rename(root / "proj")
    called = []
    monkeypatch.setattr(fs_trash, "trash", lambda *a, **k: called.append(a))
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "victim.txt")})
    assert r.status_code in (403, 404, 409), r.text
    assert called == []
    assert ((root.parent / "outside") / "victim.txt").read_text() == "keep me\n"


def test_last_component_swapped_to_symlink_is_not_followed(client, root, monkeypatch):
    p = root / "proj" / "a.txt"
    body = save_body(p, "pwned\n")
    real = fs_write._resolve

    def swap(raw):
        out = real(raw)
        if out.name == "a.txt" and not p.is_symlink():
            p.unlink()
            p.symlink_to((root.parent / "outside") / "victim.txt")
        return out
    monkeypatch.setattr(fs_write, "_resolve", swap)
    r = client.put("/api/fs/file", json=body)
    assert r.status_code in (400, 409), r.text
    assert ((root.parent / "outside") / "victim.txt").read_text() == "keep me\n"
    assert p.is_symlink()                                         # 링크도 그대로(바꿔 끼우지 않았다)


# --- 인가: 승격 · 사람 · 출처 ---------------------------------------------------------------


def test_save_without_elevation_is_401_and_writes_nothing(unelevated, root):
    p = root / "proj" / "a.txt"
    r = unelevated.put("/api/fs/file", json=save_body(p, "x\n"))
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"
    for path, body in (("/api/fs/new", {"dir": str(root / "proj"), "name": "n.txt"}),
                       ("/api/fs/rename", {"path": str(p), "to": str(root / "proj" / "b.txt")}),
                       ("/api/fs/delete", {"path": str(p)})):
        assert unelevated.post(path, json=body).status_code == 401
    assert p.read_text() == "one\ntwo\n" and not (root / "proj" / "n.txt").exists()


def test_machine_token_and_agent_principal_are_refused(tmp_path, monkeypatch, root):
    _auth_env(tmp_path, monkeypatch)
    p = root / "proj" / "a.txt"
    with TestClient(main.app) as c:
        r = c.put("/api/fs/file", json=save_body(p, "x\n"), headers={"Authorization": "Bearer machine-tok"})
        assert r.status_code == 403 and r.json()["error"] == "human_session_required"
    agent = TestClient(_Sock(main.app, P.agent("$1:101", name="alpha")))
    r = agent.put("/api/fs/file", json=save_body(p, "x\n"))
    assert r.status_code in (401, 403)
    r = agent.post("/api/fs/delete", json={"path": str(p)})
    assert r.status_code in (401, 403)
    assert p.read_text() == "one\ntwo\n"


def test_elevated_cookie_plus_token_is_not_a_human(client, root):
    """사람 쿠키에 기계 토큰이 같이 실리면 사람 요청이 아니다(에이전트가 쿠키를 훔쳐 섞어도 안 된다)."""
    p = root / "proj" / "a.txt"
    r = client.put("/api/fs/file", json=save_body(p, "x\n"), headers={"Authorization": "Bearer machine-tok"})
    assert r.status_code == 403


@pytest.mark.parametrize("method,path", [("put", "/api/fs/file"), ("post", "/api/fs/new"),
                                         ("post", "/api/fs/rename"), ("post", "/api/fs/delete")])
def test_cross_site_origin_is_refused(client, root, method, path):
    p = root / "proj" / "a.txt"
    body = save_body(p, "x\n") if method == "put" else {"path": str(p), "dir": str(root / "proj"), "name": "n.txt",
                                                        "to": str(root / "proj" / "b.txt")}
    r = getattr(client, method)(path, json=body, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert p.read_text() == "one\ntwo\n"


# --- 직렬화 · save_id -------------------------------------------------------------------


def test_concurrent_saves_one_wins_one_conflicts(root, monkeypatch):
    p = root / "proj" / "a.txt"
    expected = rev(p)
    real = fs_write._lstat_at

    def slow(*a, **k):
        # rename 직전 재검사와 rename 사이를 벌린다 — 잠금이 없으면 여럿이 재검사를 통과해 모두 「성공」한다.
        out = real(*a, **k)
        time.sleep(0.05)
        return out
    monkeypatch.setattr(fs_write, "_lstat_at", slow)
    results = []

    def go(i):
        try:
            fs_write.save(str(p), f"writer {i}\n", encoding="utf-8", eol=None, expected_rev=expected)
            results.append("ok")
        except fs_write.WriteError as e:
            results.append(e.body["error"])
    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("conflict") == 5, results


def test_save_id_retry_returns_the_first_result_without_writing_twice(client, root):
    p = root / "proj" / "a.txt"
    body = save_body(p, "first\n")
    r1 = client.put("/api/fs/file", json=body)
    assert r1.status_code == 200
    ino = os.stat(p).st_ino
    p_rev = rev(p)
    r2 = client.put("/api/fs/file", json=body)                      # 시간 초과 뒤의 같은 재시도
    assert r2.status_code == 200 and r2.json()["replayed"] is True and r2.json()["rev"] == r1.json()["rev"]
    assert os.stat(p).st_ino == ino and rev(p) == p_rev            # 두 번 쓰지 않았다
    other = dict(body, content="different\n")
    r3 = client.put("/api/fs/file", json=other)                    # 같은 id에 다른 내용
    assert r3.status_code == 409 and r3.json()["error"] == "save_id_reused"
    assert p.read_text() == "first\n"


def test_save_id_retry_while_the_first_is_still_writing(root, monkeypatch):
    """첫 요청이 아직 쓰는 중에 온 재시도는 기다렸다가 첫 결과를 받는다(새로 쓰지 않는다)."""
    import routes.fs_write as rw
    p = root / "proj" / "a.txt"
    expected = rev(p)
    gate = threading.Event()
    real = fs_write.encode_content
    calls = []

    def slow(*a, **k):
        calls.append(1)
        gate.wait(2)
        return real(*a, **k)
    monkeypatch.setattr(fs_write, "encode_content", slow)
    fp = fs_write.fingerprint(str(p), "x\n", "utf-8", None, expected)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("a", rw._save_once("sid-12345678", "dev", fp, str(p), "x\n",
                                                                        "utf-8", None, expected)))
    t.start()
    time.sleep(0.1)
    threading.Timer(0.2, gate.set).start()
    second = rw._save_once("sid-12345678", "dev", fp, str(p), "x\n", "utf-8", None, expected)
    t.join()
    assert second["replayed"] is True and second["rev"] == out["a"]["rev"] and len(calls) == 1


def test_bad_bodies_are_400(client, root):
    p = root / "proj" / "a.txt"
    good = save_body(p, "x\n")
    for patch in ({"save_id": "short"}, {"save_id": None}, {"content": 5}, {"path": ""}, {"expected_rev": 7},
                  {"encoding": None}):
        r = client.put("/api/fs/file", json={**good, **patch})
        assert r.status_code == 400, (patch, r.text)
    r = client.put("/api/fs/file", json={k: v for k, v in good.items() if k != "expected_rev"})
    assert r.status_code == 400
    r = client.put("/api/fs/file", content=b"[1,2]", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_oversized_body_is_refused_before_reading_it_all(client, root, monkeypatch):
    monkeypatch.setattr(fs_write, "MAX_BODY_BYTES", 1000)
    p = root / "proj" / "a.txt"
    r = client.put("/api/fs/file", json=save_body(p, "x" * 5000))
    assert r.status_code == 413
    assert p.read_text() == "one\ntwo\n"


# --- 새 파일 --------------------------------------------------------------------------


def test_new_file_creates_without_overwrite(client, root):
    r = client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": "n.txt"})
    assert r.status_code == 200, r.text
    assert (root / "proj" / "n.txt").read_bytes() == b"" and r.json()["rev"] == rev(root / "proj" / "n.txt")
    r = client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": "a.txt"})
    assert r.status_code == 409 and r.json()["error"] == "exists"
    assert (root / "proj" / "a.txt").read_text() == "one\ntwo\n"


@pytest.mark.parametrize("name,status", [("a/b.txt", 400), ("..", 400), (".", 400), ("a\\b", 400), ("", 400),
                                         ("x\ny", 400), (".env", 403), ("id_rsa", 403), (".git", 403)])
def test_new_file_name_rules(client, root, name, status):
    r = client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": name})
    assert r.status_code == status, (name, r.text)


def test_new_file_over_an_existing_symlink_is_refused(client, root):
    (root / "proj" / "ln").symlink_to(root / "proj" / "a.txt")
    r = client.post("/api/fs/new", json={"dir": str(root / "proj"), "name": "ln"})
    assert r.status_code == 409
    assert (root / "proj" / "a.txt").read_text() == "one\ntwo\n"


# --- 이름 바꾸기 ----------------------------------------------------------------------------


def test_rename_refuses_existing_target_unless_overwrite(client, root):
    a, b = root / "proj" / "a.txt", root / "proj" / "b.txt"
    b.write_text("bee\n")
    r = client.post("/api/fs/rename", json={"path": str(a), "to": str(b)})
    assert r.status_code == 409 and r.json()["error"] == "exists"
    assert a.read_text() == "one\ntwo\n" and b.read_text() == "bee\n"
    r = client.post("/api/fs/rename", json={"path": str(a), "to": str(b), "overwrite": True})
    assert r.status_code == 200 and not a.exists() and b.read_text() == "one\ntwo\n"
    r = client.post("/api/fs/rename", json={"path": str(b), "to": str(root / "proj" / "sub" / "c.txt")})
    assert r.status_code == 404                                    # 없는 폴더로는 옮기지 않는다
    (root / "proj" / "sub").mkdir()
    r = client.post("/api/fs/rename", json={"path": str(b), "to": str(root / "proj" / "sub" / "c.txt")})
    assert r.status_code == 200 and (root / "proj" / "sub" / "c.txt").read_text() == "one\ntwo\n"


def test_rename_validates_both_ends(client, root):
    a = root / "proj" / "a.txt"
    (root / "proj" / ".github").mkdir()
    for to in ((root.parent / "outside") / "x.txt", root / "proj" / ".github" / "ci.yml", root / "proj" / ".env",
               root / "proj" / ".git"):
        r = client.post("/api/fs/rename", json={"path": str(a), "to": str(to)})
        assert r.status_code == 403, (to, r.text)
    r = client.post("/api/fs/rename", json={"path": str((root.parent / "outside") / "victim.txt"), "to": str(root / "proj" / "v.txt")})
    assert r.status_code == 403
    assert a.read_text() == "one\ntwo\n" and ((root.parent / "outside") / "victim.txt").exists()


def test_rename_and_delete_refuse_directories_and_symlinks(client, root, monkeypatch):
    (root / "proj" / "d").mkdir()
    (root / "proj" / "ln").symlink_to(root / "proj" / "a.txt")
    (root / "proj" / "ln2").symlink_to(root / "proj" / "a.txt")
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "d"), "to": str(root / "proj" / "e")})
    assert r.status_code == 400 and r.json()["error"] == "is_directory"
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "ln"), "to": str(root / "proj" / "ln3")})
    assert r.status_code == 400 and r.json()["error"] == "is_symlink"
    r = client.post("/api/fs/rename", json={"path": str(root / "proj" / "a.txt"), "to": str(root / "proj" / "ln2")})
    assert r.status_code == 409                                    # 링크 자리로 옮기면 링크 대상을 덮어쓰게 된다
    called = []
    monkeypatch.setattr(fs_trash, "trash", lambda *a, **k: called.append(a))
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "d")})
    assert r.status_code == 400 and r.json()["error"] == "is_directory"
    r = client.post("/api/fs/delete", json={"path": str(root / "proj" / "ln")})
    assert r.status_code == 400 and r.json()["error"] == "is_symlink"
    assert called == [] and (root / "proj" / "a.txt").read_text() == "one\ntwo\n"


# --- 삭제 = 휴지통 -----------------------------------------------------------------------


def test_trash_failure_never_unlinks(client, root, monkeypatch):
    p = root / "proj" / "a.txt"
    monkeypatch.setattr(fs_trash, "IMPL", "none")
    removed = []
    real_unlink = os.unlink
    monkeypatch.setattr(os, "unlink", lambda *a, **k: (removed.append(a), real_unlink(*a, **k)))
    monkeypatch.setattr(os, "remove", lambda *a, **k: (removed.append(a), real_unlink(*a, **k)))
    r = client.post("/api/fs/delete", json={"path": str(p)})
    assert r.status_code == 409 and r.json()["error"] == "trash_unavailable"
    assert p.read_text() == "one\ntwo\n"
    assert not [a for a in removed if "a.txt" in str(a[0])]


def test_macos_helper_failure_keeps_the_file(client, root, monkeypatch):
    import subprocess
    p = root / "proj" / "a.txt"
    monkeypatch.setattr(fs_trash, "IMPL", "macos")
    monkeypatch.setattr(fs_trash.proc, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 0, json.dumps({"ok": False, "code": 513, "reason": "no trash on this volume"}).encode(), b""))
    r = client.post("/api/fs/delete", json={"path": str(p)})
    assert r.status_code == 409 and r.json()["error"] == "trash_unavailable"
    assert p.exists()
    monkeypatch.setattr(fs_trash.proc, "run", lambda argv, **k: subprocess.CompletedProcess(argv, -11, b"", b"segv"))
    r = client.post("/api/fs/delete", json={"path": str(p)})
    assert r.status_code == 409 and p.exists()


def test_freedesktop_trash_moves_with_trashinfo(client, root, monkeypatch, tmp_path):
    data = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setattr(fs_trash, "IMPL", "freedesktop")
    p = root / "proj" / "a b.txt"
    p.write_text("first\n")
    r = client.post("/api/fs/delete", json={"path": str(p), "expected_rev": rev(p)})
    assert r.status_code == 200, r.text
    assert not p.exists()
    trash = data / "Trash"
    assert (trash / "files" / "a b.txt").read_text() == "first\n"
    info = (trash / "info" / "a b.txt.trashinfo").read_text()
    assert info.startswith("[Trash Info]\nPath=") and "a%20b.txt" in info and "DeletionDate=" in info
    assert stat.S_IMODE(os.stat(trash).st_mode) == 0o700
    p.write_text("second\n")
    r = client.post("/api/fs/delete", json={"path": str(p)})
    assert r.status_code == 200 and r.json()["trash_name"] == "a b.2.txt"
    assert (trash / "files" / "a b.2.txt").read_text() == "second\n"


def test_freedesktop_trash_on_another_device_without_a_usable_trash_refuses(client, root, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(fs_trash, "IMPL", "freedesktop")
    real = fs_trash._pick_trash

    def other_device(path, dev):
        raise fs_trash.TrashUnavailable("no trash")                # 다른 장치 + 꼭대기 휴지통을 못 만듦
    monkeypatch.setattr(fs_trash, "_pick_trash", other_device)
    p = root / "proj" / "a.txt"
    r = client.post("/api/fs/delete", json={"path": str(p)})
    assert r.status_code == 409 and p.exists()
    monkeypatch.setattr(fs_trash, "_pick_trash", real)


def test_delete_with_stale_rev_is_a_conflict(client, root, monkeypatch):
    monkeypatch.setattr(fs_trash, "IMPL", "none")
    p = root / "proj" / "a.txt"
    r = client.post("/api/fs/delete", json={"path": str(p), "expected_rev": "1:2:3:4"})
    assert r.status_code == 409 and r.json()["error"] == "conflict"


# --- 감사 ----------------------------------------------------------------------------------


def test_writes_are_audited_without_path_or_content(client, root, tmp_path):
    p = root / "proj" / "a.txt"
    assert client.put("/api/fs/file", json=save_body(p, "SECRET-CONTENT\n")).status_code == 200
    log = (tmp_path / "vt" / "audit" / "authz.jsonl").read_text()
    rows = [json.loads(x) for x in log.splitlines()]
    mine = [r for r in rows if r.get("action") == "fs.save"]
    assert [r["result"] for r in mine] == ["started", "ok"]
    assert "SECRET-CONTENT" not in log and str(p) not in log
    assert mine[0]["principal"].startswith("human:")
