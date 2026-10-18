"""에이전트 이름 push(2026-09-30) — 레일·플릿의 4초 폴링을 대신한다.

`/ws-agent` 연결이 있는 동안 서버 감시 하나가 detect 결과를 보고, 바뀌었을
때만 `agents_change`를 보낸다. 훅 이벤트는 주기를 기다리지 않고 바로 확인한다.
"""

import pytest
from starlette.testclient import TestClient

import agent_detector
import main
from routes import agents as agents_route


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def detected(monkeypatch):
    """detect_all()이 돌려줄 값을 테스트가 바꿀 수 있게 한다(tmux 없이)."""
    box = {"value": {"dev": {"agent": "shell"}}}
    monkeypatch.setattr(agent_detector, "detect_all", lambda: dict(box["value"]))
    monkeypatch.setattr(agents_route, "_agents_last", None)
    return box


def _next_of(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg


def test_snapshot_carries_agents(client, detected):
    with client.websocket_connect("/ws-agent") as ws:
        snap = _next_of(ws, "agent_snapshot")
        assert snap["agents"] == {"dev": {"agent": "shell"}}


def test_change_is_pushed(client, detected, monkeypatch):
    monkeypatch.setattr(agents_route, "_AGENTS_PUSH_INTERVAL", 0.05)
    with client.websocket_connect("/ws-agent") as ws:
        _next_of(ws, "agent_snapshot")
        detected["value"] = {"dev": {"agent": "claude"}}
        msg = _next_of(ws, "agents_change")
        assert msg["agents"]["dev"]["agent"] == "claude"


def test_hook_event_checks_immediately(client, detected, monkeypatch):
    # 주기를 길게 둬도 훅 이벤트가 오면 바로 확인한다 — 에이전트가 막 뜬 순간.
    monkeypatch.setattr(agents_route, "_AGENTS_PUSH_INTERVAL", 3600)
    with client.websocket_connect("/ws-agent") as ws:
        _next_of(ws, "agent_snapshot")
        detected["value"] = {"dev": {"agent": "codex"}}
        r = client.post("/api/agent/event", json={"event": "pre", "payload": {"session_id": "x1", "tool_name": "Bash"}})
        assert r.status_code == 200
        msg = _next_of(ws, "agents_change")
        assert msg["agents"]["dev"]["agent"] == "codex"


def test_unchanged_value_is_not_pushed_again(detected):
    # 루프 한 바퀴를 직접 돌려 "같으면 안 보낸다"를 확인한다(WS로는 '안 옴'을
    # 기다릴 방법이 타임아웃뿐이라).
    import asyncio

    sent = []

    class FakeWs:
        async def send_json(self, m):
            sent.append(m)

    async def run():
        agents_route._agent_event_clients.add(ws := FakeWs())
        try:
            agents_route._agents_last = None
            kick = asyncio.Event()
            task = asyncio.create_task(agents_route._agents_loop(kick))
            for _ in range(3):
                kick.set()
                await asyncio.sleep(0.05)
            detected["value"] = {"dev": {"agent": "aider"}}
            kick.set()
            # 바뀐 값이 나갈 때까지 기다린다 — 고정 50ms 는 부하가 있으면(병렬 실행·느린 러너) 모자랐다.
            deadline = asyncio.get_running_loop().time() + 2.0
            while len(sent) < 2 and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
        finally:
            agents_route._agent_event_clients.discard(ws)
            kick.set()
            await asyncio.wait_for(task, 2)
        return sent

    msgs = asyncio.run(run())
    assert [m["agents"]["dev"]["agent"] for m in msgs] == ["shell", "aider"]


def test_loop_stops_when_last_client_leaves(detected):
    import asyncio

    async def run():
        kick = asyncio.Event()
        task = asyncio.create_task(agents_route._agents_loop(kick))
        await asyncio.wait_for(task, 1)   # 클라이언트가 없으면 바로 끝난다
        return task.done()

    assert asyncio.run(run()) is True


def test_becoming_visible_checks_agents_immediately(client, detected, monkeypatch):
    monkeypatch.setattr(agents_route, "_AGENTS_PUSH_INTERVAL", 3600)
    with client.websocket_connect("/ws-agent") as ws:
        _next_of(ws, "agent_snapshot")
        ws.send_json({"type": "visibility", "hidden": True})
        detected["value"] = {"dev": {"agent": "claude"}}
        ws.send_json({"type": "visibility", "hidden": False})
        msg = _next_of(ws, "agents_change")
        assert msg["agents"]["dev"]["agent"] == "claude"
