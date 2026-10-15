"""plan-2.6 2단계 — 「최근 24h legacy 토큰 사용 N」과 소켓 상태 보고(`python -m authz sock-status`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authz import legacy_use, sock
from sock_testkit import LiveSock, rmtree, short_dir

SERVER = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    legacy_use._pending.clear()
    yield
    legacy_use._pending.clear()


def lines():
    p = legacy_use.path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_record_aggregates_per_minute_and_flushes_on_minute_change():
    t = 1_000_000 * 60.0
    for _ in range(3):
        legacy_use.record("/api/tmux/sessions", "curl/8.7", now=t + 5)
    legacy_use.record("/api/tmux/sessions", "curl/8.7", now=t + 70)       # 다음 분 → 앞 분이 쓰인다
    assert lines() == [{"ts": int(t), "path": "/api/tmux/sessions", "ua": "curl/8.7", "n": 3}]
    legacy_use.flush()
    assert sum(r["n"] for r in lines()) == 4


def test_record_stores_no_content():
    legacy_use.record("/api/files/read?token=SECRET&path=/etc/passwd", "UA " + "x" * 500, now=60.0)
    legacy_use.flush()
    raw = legacy_use.path().read_text()
    assert "SECRET" not in raw and "passwd" not in raw and "token" not in raw
    r = lines()[0]
    assert r["path"] == "/api/files/read" and len(r["ua"]) <= legacy_use.UA_MAX
    assert set(r) == {"ts", "path", "ua", "n"}
    assert (legacy_use.path().stat().st_mode & 0o777) == 0o600


def test_count_24h_window_and_both_generations():
    now = time.time()
    legacy_use.record("/a", "u", now=now - 90000)             # 25h 전 — 제외
    legacy_use.record("/a", "u", now=now - 3600)
    legacy_use.record("/b", "u", now=now - 3600)
    legacy_use.flush()
    assert legacy_use.count_24h(now=now) == 2
    p = legacy_use.path()
    os.replace(p, p.with_name(p.name + ".1"))                  # 한 세대 뒤로 돌아간 파일도 센다
    legacy_use.record("/c", "u", now=now - 10)
    legacy_use.flush()
    assert legacy_use.count_24h(now=now) == 3


def test_file_is_bounded(monkeypatch):
    monkeypatch.setattr(legacy_use, "MAX_BYTES", 2000)
    for i in range(300):
        legacy_use.record(f"/p{i}", "u", now=60.0 * (i + 1))
    legacy_use.flush()
    p = legacy_use.path()
    assert p.stat().st_size < 2000 + 400 and p.with_name(p.name + ".1").exists()
    assert not p.with_name(p.name + ".2").exists()


def test_record_never_raises(monkeypatch):
    monkeypatch.setattr(legacy_use, "path", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    legacy_use.record("/a", "u", now=60.0)
    legacy_use.record("/a", "u", now=200.0)
    legacy_use.flush()
    assert legacy_use.count_24h() == 0


def test_count_ignores_corrupt_lines():
    p = legacy_use.path()
    p.parent.mkdir(parents=True)
    p.write_text('garbage\n{"ts": %d, "n": 4}\n{"nope": 1}\n' % int(time.time()))
    assert legacy_use.count_24h() == 4


def test_middleware_counts_machine_token_requests_only(monkeypatch):
    import auth
    import main
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "tok-xyz")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "")
    c = TestClient(main.app)
    assert c.get("/api/capabilities", headers={"Authorization": "Bearer tok-xyz", "User-Agent": "unit/1"}).status_code != 401
    assert c.get("/api/capabilities?token=tok-xyz").status_code != 401
    assert c.get("/api/capabilities").status_code == 401        # 인증 실패는 센 대상이 아니다
    assert c.get("/api/capabilities", headers={"Authorization": "Bearer wrong"}).status_code == 401
    legacy_use.flush()
    recs = lines()
    assert sum(r["n"] for r in recs) == 2
    assert {r["path"] for r in recs} == {"/api/capabilities"} and "tok-xyz" not in legacy_use.path().read_text()


def test_socket_requests_are_not_counted_as_legacy(monkeypatch):
    import auth
    import main
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "tok-xyz")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "")
    d = short_dir()
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", os.path.join(d, "r", "f.sock"))
    pid = os.getpid()
    ls = LiveSock(main.app, sock.Resolver(rows_fn=lambda: [], ps_fn=lambda: {pid: (1, 0, 0, "py")}))
    try:
        assert ls.start()["state"] == "listening"
        import fsh_client
        s, _ = fsh_client.socket_request("GET", "/api/capabilities", sock_path=os.path.join(d, "r", "f.sock"))
        assert s != 401
    finally:
        ls.stop()
        rmtree(d)
    legacy_use.flush()
    assert lines() == []


# --- 소켓 상태 보고 ---------------------------------------------------------------------------------


def run_status(env_extra):
    env = {**os.environ, **env_extra}
    r = subprocess.run([sys.executable, "-m", "authz", "sock-status", "--json"], cwd=str(SERVER), env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_status_report_listening_shows_perms_selftest_and_legacy_count(monkeypatch, tmp_path):
    d = short_dir()
    path = os.path.join(d, "run", "fsh.sock")
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", path)
    from test_authz_sock import make_app, fake_resolver
    pid = os.getpid()
    ls = LiveSock(make_app(), fake_resolver([], {pid: (1, 0, 0, "py")}))
    try:
        assert ls.start()["state"] == "listening"
        legacy_use.record("/a", "u")
        legacy_use.flush()
        r = sock.status_report()
        assert r["state"] == "listening" and r["selftest"] is True and r["reachable"] is True
        assert r["perms"] == "0o600" and r["dir_perms"] == "0o700" and r["legacy_token_24h"] == 1
        assert r["whoami"]["pid"] == pid
        cli = run_status({"VT_AUTHZ_SOCKET": "1", "VT_AUTHZ_SOCKET_PATH": path, "VT_STATE_DIR": os.environ["VT_STATE_DIR"],
                          "PYTHONPATH": str(SERVER)})
        assert cli["state"] == "listening" and cli["legacy_token_24h"] == 1
    finally:
        ls.stop()
        rmtree(d)


def test_status_report_failed_selftest_is_visible(monkeypatch):
    d = short_dir()
    path = os.path.join(d, "run", "fsh.sock")
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", path)
    monkeypatch.setattr(sock, "peer_creds", lambda s: (os.geteuid(), os.getpid() + 3))
    from test_authz_sock import make_app
    ls = LiveSock(make_app())
    try:
        assert ls.start()["state"] == "failed"
        r = sock.status_report()
        assert r["state"] == "failed" and r["selftest"] is False and r["reachable"] is False and r["perms"] is None
        assert "불일치" in r["reason"]
    finally:
        ls.stop()
        rmtree(d)


def test_status_report_absent_and_stale(monkeypatch):
    d = short_dir()
    path = os.path.join(d, "run", "fsh.sock")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", path)
    assert sock.status_report()["state"] == "absent"
    os.makedirs(os.path.dirname(path))
    Path(sock.status_path(Path(path))).write_text(json.dumps({"state": "listening", "pid": 2_000_000_000}))
    r = sock.status_report()
    assert r["state"] == "stale" and r["reachable"] is False
    rmtree(d)
