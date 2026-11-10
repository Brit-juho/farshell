"""`/api/update/*` — 인가(사람 세션·승격·에이전트/peer/중계 거부)·감사·검증·끔 설정. 점검 자체는 test_update_check.py."""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

import auth as _auth
import main
import relay_policy
import update_check
from authz import principals as P
from authz import route_table
from shared_testkit import _Sock

PW = "s3cret-pw"


def _auth_env(tmp_path, monkeypatch):
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password(PW))
    monkeypatch.setattr(_auth, "VT_AUTH_TOKEN", "machine-tok")
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    return state_dir


@pytest.fixture
def client(tmp_path, monkeypatch):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": PW}).status_code == 200
        yield c


@pytest.fixture
def elevated(client):
    assert client.post("/api/auth/elevate", json={"password": PW}).status_code == 200
    return client


@pytest.fixture
def anon(tmp_path, monkeypatch):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        yield c


def audit_rows(tmp_path):
    p = tmp_path / "vt" / "audit" / "authz.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


ALL = [("get", "/api/update/status", None), ("post", "/api/update/check", None),
       ("put", "/api/update/settings", {"auto": "stable-only"})]


def test_status_shape_for_a_logged_in_person(client):
    r = client.get("/api/update/status")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    b = r.json()
    assert b["settings"] == {"check": True, "auto": "off", "auto_hour": 4}
    assert b["check_enabled"] is False and b["env_off"] is True        # conftest 가 VT_UPDATE_CHECK=0 로 꺼 둔다
    for k in ("checked_at", "channel", "current", "latest", "available", "signature_ok", "signer", "error", "attempts", "last_apply"):
        assert k in b


@pytest.mark.parametrize("method,path,body", ALL)
def test_login_is_required(anon, method, path, body):
    r = getattr(anon, method)(path, **({"json": body} if body else {}))
    assert r.status_code in (401, 403)


@pytest.mark.parametrize("method,path,body", ALL)
def test_machine_token_agent_and_peer_are_refused(tmp_path, monkeypatch, method, path, body):
    _auth_env(tmp_path, monkeypatch)
    kw = {"json": body} if body else {}
    with TestClient(main.app) as c:
        r = getattr(c, method)(path, headers={"Authorization": "Bearer machine-tok"}, **kw)
        assert r.status_code == 403 and r.json()["error"] == "human_session_required"
    for principal in (P.agent("$1:101", name="alpha"),):
        r = getattr(TestClient(_Sock(main.app, principal)), method)(path, **kw)
        assert r.status_code in (401, 403)
    assert update_check.load_settings()["auto"] == "off"


def test_cookie_plus_machine_token_is_not_a_person(elevated):
    r = elevated.put("/api/update/settings", json={"auto": "stable-only"}, headers={"Authorization": "Bearer machine-tok"})
    assert r.status_code == 403
    assert update_check.load_settings()["auto"] == "off"


def test_routes_are_declared_human_and_out_of_the_relay_and_socket_lists():
    for m, path, elev in (("GET", "/api/update/status", False), ("POST", "/api/update/check", False), ("PUT", "/api/update/settings", True)):
        rule = route_table.TABLE[(m, path)]
        assert rule.cls == route_table.HUMAN and rule.elevated is elev
        assert not relay_policy.http_allowed(m, path), "호스트 중계로 다른 맥의 업데이트를 건드리면 안 된다"
        assert route_table.socket_entry(m, path) is None


def test_settings_need_elevation(client, tmp_path):
    r = client.put("/api/update/settings", json={"auto": "stable-only"})
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"
    assert update_check.load_settings()["auto"] == "off" and audit_rows(tmp_path) == []


def test_elevated_person_changes_settings_and_it_is_audited_without_content(elevated, tmp_path):
    r = elevated.put("/api/update/settings", json={"auto": "stable-only", "auto_hour": 3, "check": True})
    assert r.status_code == 200
    assert r.json()["settings"] == {"check": True, "auto": "stable-only", "auto_hour": 3}
    assert update_check.load_settings() == {"check": True, "auto": "stable-only", "auto_hour": 3}
    rows = [x for x in audit_rows(tmp_path) if x["action"] == "update.settings"]
    assert [x["result"] for x in rows] == ["started", "ok"]
    assert rows[0]["principal"].startswith("human:")
    assert rows[0]["detail"]["changes"] == {"auto": "stable-only", "auto_hour": 3, "check": True}
    assert PW not in json.dumps(rows)


@pytest.mark.parametrize("body", [{}, [], "x", {"auto": "dev"}, {"auto_hour": 25}, {"auto_hour": "4"}, {"check": "no"}, {"channel": "dev"}])
def test_bad_settings_are_rejected_before_anything_changes(elevated, tmp_path, body):
    r = elevated.put("/api/update/settings", json=body)
    assert r.status_code == 400 and r.json()["error"] == "bad_request"
    assert update_check.load_settings() == update_check.DEFAULT_SETTINGS
    assert [x for x in audit_rows(tmp_path) if x["action"] == "update.settings"] == []


def test_settings_change_is_refused_when_the_audit_log_cannot_be_written(elevated, monkeypatch):
    from authz import audit

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(audit, "append", boom)
    r = elevated.put("/api/update/settings", json={"auto": "stable-only"})
    assert r.status_code == 503 and r.json()["error"] == "audit_unavailable"
    assert update_check.load_settings()["auto"] == "off"


def test_manual_check_is_refused_while_checking_is_off(client):
    r = client.post("/api/update/check")
    assert r.status_code == 409 and r.json()["error"] == "update_check_disabled"


def test_manual_check_runs_then_is_rate_limited(client, monkeypatch):
    import routes.update as ru
    monkeypatch.delenv("VT_UPDATE_CHECK")
    monkeypatch.setattr(ru, "_last_manual", -100.0)
    calls = []

    async def fake_manual(root, notify):
        calls.append(root)
        return update_check.read_status(), True
    monkeypatch.setattr(update_check, "manual_check", fake_manual)
    r1 = client.post("/api/update/check")
    assert r1.status_code == 200 and r1.json()["ran"] is True and "status" in r1.json()
    r2 = client.post("/api/update/check")
    assert r2.status_code == 200 and r2.json()["ran"] is False
    assert len(calls) == 1


def test_manual_check_failure_does_not_break_the_server(client, monkeypatch):
    import routes.update as ru
    monkeypatch.delenv("VT_UPDATE_CHECK")
    monkeypatch.setattr(ru, "_last_manual", -100.0)

    async def boom(root, notify):
        raise TimeoutError("pool deadline")
    monkeypatch.setattr(update_check, "manual_check", boom)
    r = client.post("/api/update/check")
    assert r.status_code == 503 and r.json()["error"] == "check_failed"
    assert client.get("/api/update/status").status_code == 200


def test_status_reflects_the_live_version_even_if_the_saved_one_is_stale(client):
    update_check.mutate_status(lambda st: st.update(current="0.0.1", latest_tag="v0.0.2", latest="0.0.2", available=True,
                                                    channel="stable", signature_ok=True))
    b = client.get("/api/update/status").json()
    assert b["current"] != "0.0.1" and b["available"] is False       # 지금 VERSION 이 더 새로우면 「있음」으로 보이지 않는다
