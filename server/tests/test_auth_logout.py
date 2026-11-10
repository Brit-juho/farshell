"""웹 로그아웃(`POST /api/auth/logout`) 계약 — FarShell 메뉴의 「로그아웃」이 쓰는 API.

세션 쿠키(`vt_session`)만 지우고 기기 등록(`vt_device`)은 건드리지 않는다. 교차 출처 요청은
`OriginGuardMiddleware`가 막는다(AGENTS.md 계약 3). 실제 앱(`main.app`) 위에서 TestClient로 돈다.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def app_auth(tmp_path, monkeypatch):
    import auth
    state = tmp_path / "vt"
    monkeypatch.setattr(auth, "STATE_DIR", state)
    monkeypatch.setattr(auth, "DEVICES_PATH", state / "devices.json")
    monkeypatch.setattr(auth, "TOTP_PATH", state / "totp.json")
    monkeypatch.setattr(auth, "TICKETS_PATH", state / "tickets.json")
    monkeypatch.setenv("VT_STATE_DIR", str(state))
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "logout-test-signing-key")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", auth.hash_password("pw-" + "logout"))
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "-".join(["machine", "tok", "logout"]))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    return auth


def _client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


def test_logout_clears_only_the_session_cookie(app_auth):
    c = _client()
    r = c.post("/api/auth/logout")
    assert r.status_code == 200 and r.json() == {"ok": True}
    set_cookies = r.headers.get_list("set-cookie")
    assert any(h.startswith("vt_session=") and ("Max-Age=0" in h or "expires=" in h.lower()) for h in set_cookies)
    assert not any(h.startswith("vt_device=") for h in set_cookies), "기기 등록(vt_device)은 건드리지 않는다"


def test_logout_needs_no_session_and_protected_api_stays_closed(app_auth):
    c = _client()
    assert c.post("/api/auth/logout").status_code == 200      # 세션 없이도 호출 가능(자기 쿠키만 지운다)
    assert c.get("/api/capabilities").status_code == 401, "쿠키가 없으면 보호 API는 401"


def test_logout_cross_origin_is_blocked(app_auth):
    c = _client()
    r = c.post("/api/auth/logout", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["reason"] == "cross_origin"


def test_auth_status_reports_password_set_for_the_menu(app_auth):
    """메뉴의 「로그아웃」은 `password_set`이 false면 끊을 세션이 없다고 안내만 한다."""
    r = _client().get("/api/auth/status")
    assert r.status_code == 200 and r.json()["password_set"] is True
