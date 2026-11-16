"""`/api/client-log` — 사람 세션만(기계 토큰·소켓 주체 거부), 중계 밖, 본문·묶음 상한, 429, 읽기·지우기."""
from __future__ import annotations

import json
import time

import pytest
from starlette.testclient import TestClient

import auth as _auth
import client_log
import main
import relay_policy
from authz import principals as P
from authz import route_table
from shared_testkit import _Sock

PW = "s3cret-pw"
SPECS = [("post", {"events": []}), ("get", None), ("delete", None)]


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
    client_log._reset_for_tests()


@pytest.fixture
def client(tmp_path, monkeypatch):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": PW}).status_code == 200
        yield c


@pytest.fixture
def anon(tmp_path, monkeypatch):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        yield c


def ev(i=1, **kw):
    now = int(time.time() * 1000)
    base = {"v": 1, "id": f"e_{i}", "ts": now, "last": now, "count": 1, "level": "error", "source": "js", "msg": f"실패 {i}"}
    base.update(kw)
    return base


def call(c, spec, **kw):
    method, body = spec
    return getattr(c, method)("/api/client-log", **({"json": body} if body is not None else {}), **kw)


@pytest.mark.parametrize("spec", SPECS)
def test_login_is_required(anon, spec):
    assert call(anon, spec).status_code in (401, 403)


@pytest.mark.parametrize("spec", SPECS)
def test_machine_token_and_agent_are_refused(tmp_path, monkeypatch, spec):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        r = call(c, spec, headers={"Authorization": "Bearer machine-tok"})
        assert r.status_code == 403 and r.json()["error"] == "human_session_required"
    r = call(TestClient(_Sock(main.app, P.agent("$1:101", name="alpha"))), spec)
    assert r.status_code in (401, 403)
    assert client_log.read() == []


def test_declared_human_and_outside_relay_and_socket_lists():
    for m in ("POST", "GET", "DELETE"):
        rule = route_table.TABLE[(m, "/api/client-log")]
        assert rule.cls == route_table.HUMAN and rule.elevated is False
        assert not relay_policy.http_allowed(m, "/api/client-log"), "다른 맥이 이 맥의 기록을 읽거나 지우면 안 된다"
        assert route_table.socket_entry(m, "/api/client-log") is None


def test_post_then_get_round_trip(client):
    r = client.post("/api/client-log", json={"events": [ev(1), ev(2, level="warn"), ev(3, level="info")]})
    assert r.status_code == 200 and r.json() == {"ok": True, "accepted": 2, "ignored": 1}
    items = client.get("/api/client-log").json()["items"]
    assert {i["id"] for i in items} == {"e_1", "e_2"}
    assert [i["id"] for i in client.get("/api/client-log?level=warn").json()["items"]] == ["e_2"]
    assert [i["id"] for i in client.get("/api/client-log?q=%EC%8B%A4%ED%8C%A8%202").json()["items"]] == ["e_2"]


def test_get_validates_query(client):
    assert client.get("/api/client-log?limit=0").status_code == 422
    assert client.get("/api/client-log?limit=501").status_code == 422
    assert client.get("/api/client-log?level=info").status_code == 422


def test_bad_bodies(client):
    assert client.post("/api/client-log", content=b"{nope", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/api/client-log", json={"events": "x"}).status_code == 400
    assert client.post("/api/client-log", json=[1, 2]).status_code == 400
    assert client.post("/api/client-log", json={}).status_code == 400


def test_oversize_body_is_413_by_header_and_by_stream(client):
    big = {"events": [ev(1, detail="x" * (client_log.MAX_BODY_BYTES + 10))]}
    assert client.post("/api/client-log", json=big).status_code == 413

    def gen():                                   # Content-Length 없이 청크로 보내도 상한에서 끊긴다
        for _ in range(20):
            yield b" " * 8192
    assert client.post("/api/client-log", content=gen()).status_code == 413
    assert client_log.read() == []


def test_batch_over_50_is_413(client):
    r = client.post("/api/client-log", json={"events": [ev(i) for i in range(client_log.MAX_BATCH + 1)]})
    assert r.status_code == 413 and r.json()["error"] == "too_large"


def test_rate_limit_is_429_with_retry_after(client):
    for k in range(client_log.RATE_PER_MIN // client_log.MAX_BATCH):
        assert client.post("/api/client-log", json={"events": [ev(k * 100 + i) for i in range(client_log.MAX_BATCH)]}).status_code == 200
    r = client.post("/api/client-log", json={"events": [ev(99_999)]})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and r.json()["error"] == "rate_limited"


def test_delete_removes_everything(client):
    client.post("/api/client-log", json={"events": [ev(1)]})
    r = client.delete("/api/client-log")
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert client.get("/api/client-log").json()["items"] == []


def test_stored_response_never_contains_secrets(client):
    client.post("/api/client-log", json={"events": [ev(1, msg="GET /x?token=SECRETQ", detail='{"password":"SECRETP"}')]})
    assert "SECRETQ" not in json.dumps(client.get("/api/client-log").json()) and "SECRETP" not in json.dumps(client.get("/api/client-log").json())


def test_cross_site_post_is_blocked_by_the_origin_guard(client):
    r = client.post("/api/client-log", json={"events": [ev(1)]}, headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code in (400, 403)
    assert client_log.read() == []
