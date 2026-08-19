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


def test_concurrent_e2e_connects_enforce_per_session_limit(client, monkeypatch):
    """A2 회귀: per-session 한도 검사와 카운터 증가 사이에 E2E 핸드셰이크의 await가
    끼어 있으면(TOCTOU), 동시에 들어온 두 번째 ?e2e=1 연결이 첫 번째가 아직 핸드셰이크
    중(카운터 미증가)인 틈을 타 한도 검사를 통과해버릴 수 있었다. 이제 카운터는 한도
    검사 통과 직후(핸드셰이크 시작 전)에 증가하므로, 한도=1일 때 두 번째 연결은
    첫 번째가 핸드셰이크를 끝내기 전이라도 즉시 "Max per-session connections"로
    거부돼야 한다.
    """
    nacl = pytest.importorskip("nacl")  # noqa: F841 — 없으면 스킵
    import crypto_channel
    if not crypto_channel.is_available():
        pytest.skip("nacl/crypto_channel 불가")

    import routes.pty as pty_route
    monkeypatch.setattr(pty_route, "WS_MAX_PER_SESSION", 1)

    sid = _make_session(client)
    try:
        # A: e2e-hello까지만 받고 ack는 보내지 않은 채 핸드셰이크 대기 상태로 둔다 —
        # 옛 코드였다면 이 시점에 카운터가 아직 0이라 B가 한도 검사를 통과했을 것.
        with client.websocket_connect(f"/ws/{sid}?e2e=1") as ws_a:
            hello = json.loads(ws_a.receive_text())
            assert hello["type"] == "e2e-hello"

            # B: A가 핸드셰이크를 마치지 않은 상태에서 같은 세션에 동시 접속 시도.
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(f"/ws/{sid}?e2e=1") as ws_b:
                    ws_b.receive_text()
            assert exc_info.value.code == 1013
            assert "Max per-session connections" in (exc_info.value.reason or "")
            assert deps.ws_count_per_session.get(sid, 0) == 1, (
                f"B가 한도 검사를 통과해 카운터가 새었다: {deps.ws_count_per_session}"
            )

            # A는 정상적으로 핸드셰이크를 마무리해서 정리한다.
            server_pub = hello["pub"]
            from nacl.public import PrivateKey
            import base64
            client_sk = PrivateKey.generate()
            client_pub_b64 = base64.b64encode(bytes(client_sk.public_key)).decode()
            ws_a.send_text(json.dumps({"type": "e2e-ack", "pub": client_pub_b64}))
    finally:
        client.delete(f"/api/sessions/{sid}")
    assert _wait_counter_zero(), f"counter leaked: {deps.ws_total_count}"


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
