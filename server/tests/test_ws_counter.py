"""A1 회귀: WebSocket 연결 카운터가 어떤 종료 경로에서도 새지 않아야 한다.

예전엔 E2E 핸드셰이크 실패(4400/4500) return이 try/finally 이전이라 카운터를
감소시키지 못해, ?e2e=1로 실패 핸드셰이크를 반복하면 WS_MAX_TOTAL(32)이 소진돼
정상 접속이 거부되는 DoS가 있었다. 이제 카운터는 E2E 성공 뒤에 증가하고 finally가
감소를 보장한다.
"""

import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
import deps


def _wait_counter_zero(timeout=2.0):
    """서버 finally 블록이 비동기로 돈다 — 카운터가 0으로 수렴할 때까지 대기."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if deps.ws_total_count == 0 and not deps.ws_count_per_session:
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def client(monkeypatch):
    # 필터 없이(all) 연결 자체를 검증. 카운터를 깨끗한 상태에서 시작.
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    deps.ws_total_count = 0
    deps.ws_count_per_session.clear()
    with TestClient(main.app) as c:
        yield c


def _make_session(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    return r.json()["id"]


def test_nonexistent_session_does_not_leak_counter(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/does-not-exist") as ws:
            ws.receive_text()  # 서버가 4004로 close → disconnect
    assert _wait_counter_zero(), f"counter leaked: {deps.ws_total_count}"


def test_normal_connect_disconnect_returns_counter_to_zero(client):
    sid = _make_session(client)
    try:
        with client.websocket_connect(f"/ws/{sid}") as ws:
            # scrollback/live 바이트가 올 수 있음 — 하나 받아보고 연결 확인
            time.sleep(0.1)
        assert _wait_counter_zero(), f"counter leaked: {deps.ws_total_count}"
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_e2e_handshake_failure_does_not_leak_counter(client):
    """A1 핵심: E2E ack를 잘못 보내 서버가 4400으로 끊어도 카운터가 새지 않는다."""
    nacl = pytest.importorskip("nacl")  # noqa: F841 — 없으면 스킵
    import crypto_channel
    if not crypto_channel.is_available():
        pytest.skip("nacl/crypto_channel 불가")

    sid = _make_session(client)
    try:
        # 여러 번 실패해도 카운터가 누적되지 않는지(과거엔 매번 +1 누수) 확인
        for _ in range(5):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/ws/{sid}?e2e=1") as ws:
                    hello = json.loads(ws.receive_text())
                    assert hello["type"] == "e2e-hello"
                    # 잘못된 ack — 서버가 4400으로 close
                    ws.send_text(json.dumps({"type": "e2e-ack", "pub": ""}))
                    ws.receive_text()  # close 유발 → disconnect
        assert _wait_counter_zero(), f"E2E 실패가 카운터를 누수시킴: {deps.ws_total_count}"
    finally:
        client.delete(f"/api/sessions/{sid}")
