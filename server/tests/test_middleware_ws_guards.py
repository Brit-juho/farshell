"""S3 회귀: OriginGuardMiddleware/NetworkAccessMiddleware를 순수 ASGI로 재작성한 뒤,
WebSocket 핸드셰이크 단계에서도 HTTP와 동일하게 차단되는지 확인한다.

BaseHTTPMiddleware 시절엔 HTTP(dispatch)와 WS(__call__ 오버라이드)가 서로 다른
코드 경로였다 — 순수 ASGI 전환으로 한 분기에서 처리하게 됐으니, 이 경로가
그대로 살아있는지(회귀 없는지)를 WS 레벨에서 직접 검증하는 테스트가
기존엔 없었다(HTTP 쪽만 test_middleware_origin.py가 커버).
"""

import pytest
from starlette.testclient import TestClient, WebSocketDenialResponse

import main
import network_access


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VT_NETWORK_MODE", raising=False)
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    with TestClient(main.app) as c:
        yield c


def _make_session(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    return r.json()["id"]


# --- OriginGuardMiddleware (WS) --------------------------------------------

def test_ws_cross_origin_rejected(client):
    sid = _make_session(client)
    try:
        with pytest.raises(WebSocketDenialResponse) as exc:
            with client.websocket_connect(f"/ws/{sid}", headers={"Origin": "https://evil.com"}):
                pass
        assert exc.value.status_code == 403
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_ws_origin_null_rejected(client):
    sid = _make_session(client)
    try:
        with pytest.raises(WebSocketDenialResponse) as exc:
            with client.websocket_connect(f"/ws/{sid}", headers={"Origin": "null"}):
                pass
        assert exc.value.status_code == 403
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_ws_no_origin_header_allowed(client):
    # curl/데몬 등 비브라우저 클라이언트 — Origin 헤더 자체가 없으면 통과해야 한다.
    sid = _make_session(client)
    try:
        with client.websocket_connect(f"/ws/{sid}") as ws:
            pass
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_ws_matching_origin_allowed(client):
    sid = _make_session(client)
    try:
        with client.websocket_connect(f"/ws/{sid}", headers={"Origin": "http://testserver"}) as ws:
            pass
    finally:
        client.delete(f"/api/sessions/{sid}")


# --- NetworkAccessMiddleware (WS) ------------------------------------------

def test_ws_ip_not_allowed_rejected(client, monkeypatch):
    sid = _make_session(client)
    try:
        # 아무 것도 허용하지 않는 스펙 — TestClient의 기본 client host도 차단된다.
        monkeypatch.setattr(
            network_access, "get_current_spec",
            lambda: network_access.AccessSpec(networks=[], allow_all=False),
        )
        with pytest.raises(WebSocketDenialResponse) as exc:
            with client.websocket_connect(f"/ws/{sid}"):
                pass
        assert exc.value.status_code == 403
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_ws_ip_allowed_when_allow_all(client, monkeypatch):
    sid = _make_session(client)
    try:
        monkeypatch.setattr(
            network_access, "get_current_spec",
            lambda: network_access.AccessSpec(networks=[], allow_all=True),
        )
        with client.websocket_connect(f"/ws/{sid}") as ws:
            pass
    finally:
        client.delete(f"/api/sessions/{sid}")
