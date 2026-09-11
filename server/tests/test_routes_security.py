"""설정 「보안」 탭이 읽는 상태 엔드포인트(60 §2) — 읽기 전용."""

import pytest
from starlette.testclient import TestClient

import auth
import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_devices_truncates_id_and_marks_current(client, monkeypatch):
    monkeypatch.setattr(auth, "list_devices", lambda: [
        {"id": "a1b2c3d4e5f60718", "label": "iPhone", "added_at": 100, "last_seen": 200},
        {"id": "ff00aa1122334455", "label": "MacBook", "added_at": 50, "last_seen": 0},
    ])
    monkeypatch.setattr(auth, "verify_device", lambda secret: {"id": "a1b2c3d4e5f60718"})

    r = client.get("/api/devices")
    assert r.status_code == 200
    rows = r.json()["devices"]
    assert [d["id"] for d in rows] == ["a1b2c3d4", "ff00aa11"], "id는 앞 8자만 노출한다"
    assert rows[0]["current"] is True and rows[1]["current"] is False
    assert rows[0]["label"] == "iPhone"
    # 해시는 어떤 형태로도 응답에 실리지 않는다.
    assert "hash" not in rows[0]


def test_devices_no_current_when_cookie_unknown(client, monkeypatch):
    monkeypatch.setattr(auth, "list_devices", lambda: [
        {"id": "a1b2c3d4e5f60718", "label": "iPhone", "added_at": 100, "last_seen": 200},
    ])
    monkeypatch.setattr(auth, "verify_device", lambda secret: None)
    rows = client.get("/api/devices").json()["devices"]
    assert rows[0]["current"] is False


def test_elevation_reports_unused_reason(client, monkeypatch):
    monkeypatch.setattr(auth, "session_elevated_until", lambda v: 0)
    body = client.get("/api/auth/elevation").json()
    assert body["elevated"] is False
    assert body["elevated_until"] == 0
    # ADR-27 — 승격을 요구하는 경로가 없다는 사실을 화면이 같이 보여줘야 한다.
    assert body["unused"] is True and body["unused_reason"] == "ADR-27"


def test_elevation_reports_deadline(client, monkeypatch):
    monkeypatch.setattr(auth, "session_elevated_until", lambda v: 1757000000)
    body = client.get("/api/auth/elevation").json()
    assert body["elevated"] is True and body["elevated_until"] == 1757000000


def test_auth_status_separates_password_from_token(client, monkeypatch):
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "")
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "machine-token")
    body = client.get("/api/auth/status").json()
    assert body["protected"] is True
    assert body["password_set"] is False, "토큰만 있는 환경은 비밀번호 미설정이다"

    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "scrypt$x")
    assert client.get("/api/auth/status").json()["password_set"] is True
