"""S5: 터널(loopback + CF-Connecting-IP) 로그인 잠금은 방문자별이고, 그 위에 등록되지 않은 기기 전체 상한이 있다.

예전에는 잠금 키가 접속 IP였다. 터널로 들어온 요청은 전부 `127.0.0.1`이라 인터넷의 모든 방문자가 키 하나를
나눠 써서, URL을 아는 누구나 10분마다 5번 틀려 주인을 계속 잠갔다.
"""

import importlib

import pytest
from starlette.testclient import TestClient

import auth as auth_mod
import main
from authz import preauth


@pytest.fixture
def env(tmp_path, monkeypatch):
    importlib.reload(auth_mod)
    auth_mod.lockout.reset_all()
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(auth_mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(auth_mod, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(auth_mod, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(auth_mod, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(auth_mod, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(auth_mod, "VT_AUTH_PASSWORD_HASH", auth_mod.hash_password("correct-horse"))
    monkeypatch.setattr(auth_mod, "VT_AUTH_TOKEN", "")
    monkeypatch.delenv("VT_TRUST_PROXY", raising=False)  # 기본값: 프록시 헤더를 IP 필터에 믿지 않는다
    preauth.LIMITER._failures.clear()
    yield
    preauth.LIMITER._failures.clear()


@pytest.fixture
def tunnel(env):
    """cloudflared처럼 loopback에서 들어오는 클라이언트."""
    with TestClient(main.app, base_url="http://127.0.0.1:7777", client=("127.0.0.1", 50000)) as c:
        yield c


def _login(c, password, visitor, **kw):
    return c.post("/api/auth", json={"token": password},
                  headers={"CF-Connecting-IP": visitor, "Origin": "http://127.0.0.1:7777"}, **kw)


def test_one_tunnel_visitor_cannot_lock_out_another(tunnel):
    for _ in range(auth_mod.PASSWORD_MAX_FAILS):
        assert _login(tunnel, "wrong", "203.0.113.9").status_code == 401
    assert _login(tunnel, "wrong", "203.0.113.9").status_code == 429
    r = _login(tunnel, "correct-horse", "198.51.100.7")  # 주인(다른 방문자)은 그대로 로그인된다
    assert r.status_code == 200 and r.json()["ok"] is True


def test_rotating_sources_hit_the_new_device_cap(tunnel, monkeypatch):
    monkeypatch.setattr(preauth.LIMITER, "fails", 10_000)  # 이 테스트는 로그인 잠금만 본다
    per_minute = dict(auth_mod.NEW_DEVICE_FAIL_WINDOWS)[60]
    for i in range(per_minute):
        assert _login(tunnel, "wrong", f"203.0.113.{i + 1}").status_code == 401
    r = _login(tunnel, "correct-horse", "198.51.100.200")
    assert r.status_code == 429
    assert r.json()["scope"] == "new_device" and r.json()["retry_after"] > 0


def test_registered_device_is_exempt_from_the_new_device_cap(tunnel, monkeypatch):
    monkeypatch.setattr(preauth.LIMITER, "fails", 10_000)
    owner = _login(tunnel, "correct-horse", "198.51.100.7")
    device = owner.cookies.get("vt_device")
    assert device
    tunnel.cookies.clear()
    per_minute = dict(auth_mod.NEW_DEVICE_FAIL_WINDOWS)[60]
    for i in range(per_minute):
        _login(tunnel, "wrong", f"203.0.113.{i + 1}")
    assert _login(tunnel, "correct-horse", "198.51.100.8").status_code == 429  # 새 기기는 막힌다
    tunnel.cookies.clear()
    tunnel.cookies.set("vt_device", device)
    assert _login(tunnel, "correct-horse", "198.51.100.7").status_code == 200  # 등록된 폰은 아니다


def test_global_window_counts_and_expires(monkeypatch):
    import time
    from auth.lockout import _GlobalWindow
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    w = _GlobalWindow(((60, 2), (3600, 3)))
    w.note(); w.note()
    assert 0 < w.remaining() <= 61
    now[0] += 61
    assert w.remaining() == 0
    w.note()
    assert w.remaining() > 60  # 시간당 3회 창에 걸린다
    now[0] += 3601
    assert w.remaining() == 0
