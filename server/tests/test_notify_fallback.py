"""P5 회귀: 작업 완료 알림이 두 번 오거나, 아예 안 오면 안 된다.

on_task_complete 의 분기는 셋 중 하나여야 한다:
  - WS 클라이언트 있음  → WS 로만 보낸다 (푸시 금지 — 같은 알림이 두 번 온다)
  - WS 없음             → 로컬 TTS + Web Push (앱이 닫혀 있다는 뜻)
  - push 미설치         → 로컬 TTS 만, 예외 없이
"""

import asyncio

import pytest

import deps
from routes import pty as pty_routes


class _FakeWS:
    def __init__(self):
        self.texts = []
        self.blobs = []

    async def send_text(self, t):
        self.texts.append(t)

    async def send_bytes(self, b):
        self.blobs.append(b)


@pytest.fixture(autouse=True)
def clean_clients():
    deps.notify_clients.clear()
    yield
    deps.notify_clients.clear()


def test_push_not_sent_when_ws_client_connected(monkeypatch):
    ws = _FakeWS()
    deps.notify_clients.add(ws)

    pushed, spoke = [], []
    monkeypatch.setattr("push.send", lambda *a, **k: pushed.append(a))
    monkeypatch.setattr(pty_routes._platform_utils, "tts_speak", lambda s: spoke.append(s))

    asyncio.run(pty_routes.on_task_complete("s1", "빌드 완료", b""))

    assert len(ws.texts) == 1, "WS 클라이언트에는 항상 보내야 한다"
    assert pushed == [], "WS가 살아 있으면 푸시를 보내면 안 된다(중복 알림)"
    assert spoke == [], "WS가 살아 있으면 로컬 TTS도 울리지 않는다"


def test_push_sent_when_no_ws_client(monkeypatch):
    pushed, spoke = [], []
    monkeypatch.setattr("push.available", lambda: True)
    monkeypatch.setattr("push.send", lambda *a, **k: pushed.append(a) or {"ok": True})
    monkeypatch.setattr(pty_routes._platform_utils, "tts_speak", lambda s: spoke.append(s))

    asyncio.run(pty_routes.on_task_complete("s1", "빌드 완료", b""))

    assert len(pushed) == 1, "앱이 닫혀 있으면 푸시로 알려야 한다"
    assert spoke == ["빌드 완료"], "맥 앞에 있을 수 있으니 로컬 TTS도 유지"


def test_push_body_carries_no_command_details(monkeypatch):
    """잠금화면에 뜨는 내용이다 — 요약(명령·경로가 섞일 수 있다)을 그대로 싣지 않는다."""
    pushed = []
    monkeypatch.setattr("push.available", lambda: True)
    monkeypatch.setattr("push.send", lambda *a, **k: pushed.append(a) or {"ok": True})
    monkeypatch.setattr(pty_routes._platform_utils, "tts_speak", lambda s: None)

    secret = "rm -rf /Users/neo/GitHub/services/culf/.env"
    asyncio.run(pty_routes.on_task_complete("s1", secret, b""))

    assert pushed, "푸시가 나가야 한다"
    assert all(secret not in str(part) for part in pushed[0])


def test_dead_ws_client_is_removed_and_push_fires(monkeypatch):
    """끊긴 소켓만 남아 있으면 '연결 없음'과 같다 — 푸시로 넘어가야 한다."""
    class Dead:
        async def send_text(self, t):
            raise RuntimeError("closed")

        async def send_bytes(self, b):
            raise RuntimeError("closed")

    deps.notify_clients.add(Dead())
    pushed = []
    monkeypatch.setattr("push.available", lambda: True)
    monkeypatch.setattr("push.send", lambda *a, **k: pushed.append(a) or {"ok": True})
    monkeypatch.setattr(pty_routes._platform_utils, "tts_speak", lambda s: None)

    asyncio.run(pty_routes.on_task_complete("s1", "완료", b""))

    assert len(deps.notify_clients) == 0, "죽은 클라이언트는 정리돼야 한다"
    assert len(pushed) == 1


def test_push_failure_does_not_break_notification(monkeypatch):
    """푸시가 터져도 알림 경로 전체가 죽으면 안 된다."""
    spoke = []
    monkeypatch.setattr("push.available", lambda: True)
    monkeypatch.setattr("push.send", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pty_routes._platform_utils, "tts_speak", lambda s: spoke.append(s))

    asyncio.run(pty_routes.on_task_complete("s1", "완료", b""))
    assert spoke == ["완료"]
