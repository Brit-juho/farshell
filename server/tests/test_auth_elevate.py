"""N31 — 승격 세션(40-dock-git.md §2) 회귀.

두 층으로 나눠 검증한다:
1. auth.py 단위 — make_session(elev_exp)/session_elevated_until/require_elevated가
   서명·만료·미설정 환경 판정을 정확히 하는지. `test_auth.py`/`test_auth_session_otp.py`와
   동일한 관례(모듈 전역을 monkeypatch로 격리)를 따른다.
2. HTTP 통합 — `POST /api/auth/elevate`가 실제로 쿠키를 재발급하고, 승격이 필요한
   라우터(`/api/git/accounts` POST)가 승격 전/후/만료 후 각각 401을 내는지.

수용 기준(40-dock-git.md): 승격 발급·만료·OTP 필수·password_required 403.
"""

from __future__ import annotations

import importlib
import time

import pytest
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# 1. auth.py 단위 테스트
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_mod(tmp_path, monkeypatch):
    import auth as _auth

    importlib.reload(_auth)  # conftest가 비운 env 기준으로 깨끗하게 재로딩

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    yield _auth


class _FakeRequest:
    """require_elevated는 `.cookies.get(...)`만 쓰므로 이 정도 스텁으로 충분하다."""

    def __init__(self, cookies=None):
        self.cookies = cookies or {}


def test_make_session_with_elev_round_trips(auth_mod):
    token = auth_mod.make_session(device_id="-", elev_exp=int(time.time()) + 900)
    assert auth_mod.verify_session(token) is True  # 기본 세션 유효성도 그대로 유지
    until = auth_mod.session_elevated_until(token)
    assert until > int(time.time())


def test_session_elevated_until_zero_for_plain_v2_session(auth_mod):
    """elev 클레임이 아예 없는 일반(v2) 세션은 승격 만료시각이 0이어야 한다."""
    token = auth_mod.make_session(device_id="-")
    assert auth_mod.session_elevated_until(token) == 0


def test_session_elevated_until_expired_claim(auth_mod):
    """elev 클레임이 지났으면 0 — 단 기본 세션(exp)은 별개로 여전히 유효해야 한다."""
    token = auth_mod.make_session(device_id="-", elev_exp=int(time.time()) - 5)
    assert auth_mod.session_elevated_until(token) == 0
    assert auth_mod.verify_session(token) is True


def test_session_elevated_until_rejects_tampered_claim(auth_mod):
    """elev 값만 위조해 연장하려는 시도는 서명 검증에서 걸려야 한다."""
    token = auth_mod.make_session(device_id="-", elev_exp=int(time.time()) + 900)
    payload, sig = token.rsplit(".", 1)
    parts = payload.split(".")
    assert parts[0] == "v3"
    forged_payload = ".".join([parts[0], parts[1], parts[2], str(int(parts[3]) + 100000)])
    forged_token = f"{forged_payload}.{sig}"
    assert auth_mod.session_elevated_until(forged_token) == 0


def test_require_elevated_no_password_passes_when_not_network_all(auth_mod, monkeypatch):
    monkeypatch.setattr(auth_mod, "VT_AUTH_PASSWORD_HASH", "")
    monkeypatch.setenv("VT_NETWORK_MODE", "lan")
    auth_mod.require_elevated(_FakeRequest())  # 예외 없이 통과해야 한다


def test_require_elevated_no_password_and_network_all_is_403(auth_mod, monkeypatch):
    monkeypatch.setattr(auth_mod, "VT_AUTH_PASSWORD_HASH", "")
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    with pytest.raises(HTTPException) as exc:
        auth_mod.require_elevated(_FakeRequest())
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error": "password_required"}


def test_require_elevated_with_password_needs_elev_claim(auth_mod, monkeypatch):
    monkeypatch.setattr(auth_mod, "VT_AUTH_PASSWORD_HASH", auth_mod.hash_password("secret"))

    with pytest.raises(HTTPException) as exc:
        auth_mod.require_elevated(_FakeRequest())
    assert exc.value.status_code == 401
    assert exc.value.detail == {"error": "elevation_required"}

    elevated = auth_mod.make_session(device_id="-", elev_exp=int(time.time()) + 900)
    auth_mod.require_elevated(_FakeRequest({"vt_session": elevated}))  # 통과, 예외 없음

    expired = auth_mod.make_session(device_id="-", elev_exp=int(time.time()) - 5)
    with pytest.raises(HTTPException) as exc2:
        auth_mod.require_elevated(_FakeRequest({"vt_session": expired}))
    assert exc2.value.status_code == 401


# ---------------------------------------------------------------------------
# 2. HTTP 통합 — /api/auth/elevate + require_elevated가 걸린 실제 라우트
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth as _auth
    import git_account_store as _store

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")

    import main
    with TestClient(main.app) as c:
        yield c


def _login(client, password="s3cret-pw"):
    r = client.post("/api/auth", json={"token": password})
    assert r.status_code == 200, r.text
    return r


def test_write_route_401_before_elevation(client):
    _login(client)
    r = client.post("/api/git/accounts", json={"provider": "github", "token": "x"})
    assert r.status_code == 401
    assert r.json() == {"error": "elevation_required"}


def test_elevate_then_write_route_no_longer_401(client, monkeypatch):
    _login(client)

    # 이 테스트는 승격 게이트 통과만 확인한다 — 실제 GitHub GET /user 호출은 막는다.
    import git_account_store as _store
    monkeypatch.setattr(_store, "verify_token", lambda *a, **k: {"ok": True, "login": "neo"})

    r = client.post("/api/auth/elevate", json={"password": "s3cret-pw"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["elevated_until"] > time.time()

    r2 = client.post("/api/git/accounts", json={"provider": "github", "token": "ghp_x"})
    assert r2.status_code != 401
    assert r2.status_code == 200, r2.text
    assert r2.json()["account"]["auth"]["masked"]


def test_elevate_wrong_password_401(client):
    _login(client)
    r = client.post("/api/auth/elevate", json={"password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid"


def test_elevate_expires_after_ttl(client, monkeypatch):
    import auth as _auth
    monkeypatch.setattr(_auth, "ELEVATION_TTL", -1)  # 발급 즉시 과거 시각
    _login(client)

    r = client.post("/api/auth/elevate", json={"password": "s3cret-pw"})
    assert r.status_code == 200

    r2 = client.post("/api/git/accounts", json={"provider": "github", "token": "x"})
    assert r2.status_code == 401
    assert r2.json() == {"error": "elevation_required"}


def test_elevate_otp_required_and_verified(client, monkeypatch):
    import auth as _auth
    # 로그인(기기 등록)까지는 OTP 미연동 상태로 통과시키고, 승격 호출부터만 OTP를 켠다 —
    # 안 그러면 로그인 자체가 "새 기기 등록" 경로를 타 OTP를 요구해 로그인이 막힌다.
    _login(client)
    monkeypatch.setattr(_auth, "totp_enabled", lambda: True)
    monkeypatch.setattr(_auth, "verify_totp", lambda code: code == "123456")

    r = client.post("/api/auth/elevate", json={"password": "s3cret-pw"})
    assert r.status_code == 401
    assert r.json()["error"] == "otp_required"

    r2 = client.post("/api/auth/elevate", json={"password": "s3cret-pw", "otp": "000000"})
    assert r2.status_code == 401
    assert r2.json()["error"] == "otp_invalid"

    r3 = client.post("/api/auth/elevate", json={"password": "s3cret-pw", "otp": "123456"})
    assert r3.status_code == 200
    assert r3.json()["ok"] is True


def test_password_required_403_when_no_password_and_network_all(tmp_path, monkeypatch):
    """비밀번호 자체가 없는데(auth 완전 비활성) VT_NETWORK_MODE=all이면
    쓰기류는 403 password_required — 공개 터널 + 무인증 + 쓰기 조합 금지(§2)."""
    import auth as _auth
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", "")
    monkeypatch.setattr(_auth, "VT_AUTH_TOKEN", "")
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")

    import main
    with TestClient(main.app) as c:
        r = c.post("/api/git/accounts", json={"provider": "github", "token": "x"})
        assert r.status_code == 403
        assert r.json() == {"error": "password_required"}
