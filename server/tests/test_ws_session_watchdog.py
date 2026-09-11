"""실사용 중 발견(2026-09-11) — 세션 쿠키가 24시간 뒤 만료돼도 그 전에 열린
WS(터미널 포함)는 핸드셰이크 때 딱 한 번만 인증을 검사해서 서버 재시작 전까지
영원히 살아있었다. `auth.spawn_session_watchdog`가 연결 유지 중 주기적으로
재검사해 만료되면 4001로 닫는지 검증한다.
"""

import asyncio
import importlib

import pytest


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


class _FakeWS:
    def __init__(self):
        self.closed = None  # (code, reason) | None

    async def close(self, code=None, reason=None):
        # 실제 WebSocket.close()도 이미 닫힌 소켓에 또 호출되면 예외를 던질 수
        # 있다 — 워치독의 try/except가 그걸 삼키는지도 이 스텁으로 간접 검증된다.
        self.closed = (code, reason)


def test_watchdog_closes_ws_when_token_expires_mid_connection(auth_mod, monkeypatch):
    """검증 1회 통과 → 그 다음부터 실패(만료 시뮬레이션) → 4001/session_expired로 닫힘."""
    calls = {"n": 0}

    def fake_check_request(token):
        calls["n"] += 1
        return calls["n"] == 1  # 첫 검사만 유효, 이후 전부 만료

    monkeypatch.setattr(auth_mod, "check_request", fake_check_request)

    async def main():
        ws = _FakeWS()
        task = auth_mod.spawn_session_watchdog(ws, "tok", interval=0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        return ws

    ws = asyncio.run(main())
    assert ws.closed == (4001, "session_expired")
    assert calls["n"] >= 2  # 최소 두 번은 재검사가 돌았다(한 번은 아직 유효, 한 번은 만료)


def test_watchdog_keeps_connection_alive_while_token_valid(auth_mod, monkeypatch):
    monkeypatch.setattr(auth_mod, "check_request", lambda token: True)

    async def main():
        ws = _FakeWS()
        task = auth_mod.spawn_session_watchdog(ws, "tok", interval=0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        return ws

    ws = asyncio.run(main())
    assert ws.closed is None


def test_watchdog_is_noop_when_token_empty(auth_mod, monkeypatch):
    """`_ws_auth_token`이 인증 자체가 꺼진 환경에서 반환하는 빈 문자열 — 재검사할
    대상이 없으므로 check_request를 아예 호출하지 않아야 한다(호출되면 즉시 실패하게
    스텁을 걸어 검증)."""

    def fail_if_called(token):
        raise AssertionError("token이 빈 문자열이면 check_request가 호출되면 안 된다")

    monkeypatch.setattr(auth_mod, "check_request", fail_if_called)

    async def main():
        ws = _FakeWS()
        task = auth_mod.spawn_session_watchdog(ws, "", interval=0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        return ws

    ws = asyncio.run(main())
    assert ws.closed is None


def test_watchdog_cancel_stops_further_checks(auth_mod, monkeypatch):
    """호출부가 연결 종료 시 task.cancel()하면, 그 뒤로는 더 이상 재검사가 돌지 않는다
    (취소 후에도 백그라운드에서 계속 돌면 세션 스토어를 계속 두드리는 누수가 된다)."""
    calls = {"n": 0}

    def fake_check_request(token):
        calls["n"] += 1
        return True

    monkeypatch.setattr(auth_mod, "check_request", fake_check_request)

    async def main():
        ws = _FakeWS()
        task = auth_mod.spawn_session_watchdog(ws, "tok", interval=0.01)
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        n_at_cancel = calls["n"]
        await asyncio.sleep(0.05)
        return n_at_cancel

    n_at_cancel = asyncio.run(main())
    assert calls["n"] == n_at_cancel
