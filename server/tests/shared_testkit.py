"""여러 시험 파일이 똑같이 쓰던 헬퍼. 2026-11-06 중복 정리로 한 곳에 모았다(본문은 옮기기 전과 같다)."""
import importlib
import json

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




class _Sock:
    def __init__(self, app, principal):
        self.app, self.p = app, principal

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["fsh_sock"] = True
            scope["state"] = {**(scope.get("state") or {}), "fsh_principal": self.p}
        await self.app(scope, receive, send)


class Req:
    def __init__(self, body=b"", headers=None, query=None):
        self._body = body
        self.headers = {"authorization": "Bearer tok-a", **(headers or {})}
        self.query_params = query or {}
        self.cookies = {}

    async def body(self):
        return self._body

    async def json(self):
        return json.loads(self._body)



