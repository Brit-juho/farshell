"""tmux 세션 목록 push(2026-09-30) — 레일·플릿의 5초 폴링을 대신한다.

`/ws-workspace` 연결이 있는 동안 서버 감시 하나가 목록을 보고, 바뀌었을 때만
`tmux_sessions`를 보낸다. 서버를 거친 변경(생성·종료·이름 변경)은 바로 확인한다.
"""

import asyncio

import pytest
from starlette.testclient import TestClient

import main
from routes import system as system_route
from routes import tmux as tmux_route


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def listing(monkeypatch):
    """collect_tmux_sessions()가 돌려줄 목록을 테스트가 바꿀 수 있게 한다(tmux 없이)."""
    box = {"value": [{"name": "dev", "cwd": "/a"}]}

    async def fake():
        return [dict(s) for s in box["value"]]
    monkeypatch.setattr(tmux_route, "collect_tmux_sessions", fake)
    monkeypatch.setattr(system_route, "_sessions_last", None)
    return box


def _next_of(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg


def test_route_and_push_share_one_source(client, listing):
    assert client.get("/api/tmux/sessions").json() == [{"name": "dev", "cwd": "/a"}]


def test_connect_gets_current_list(client, listing):
    with client.websocket_connect("/ws-workspace") as ws:
        assert _next_of(ws, "tmux_sessions")["sessions"] == [{"name": "dev", "cwd": "/a"}]


def test_change_is_pushed(client, listing, monkeypatch):
    monkeypatch.setattr(system_route, "_SESSIONS_PUSH_INTERVAL", 0.05)
    with client.websocket_connect("/ws-workspace") as ws:
        _next_of(ws, "tmux_sessions")
        listing["value"] = [{"name": "dev", "cwd": "/b"}]   # 터미널에서 cd — 서버를 안 거친다
        while True:
            msg = _next_of(ws, "tmux_sessions")
            if msg["sessions"][0]["cwd"] == "/b":
                break


def test_kick_checks_immediately(client, listing, monkeypatch):
    # 주기를 길게 둬도 서버를 거친 변경은 바로 나간다.
    monkeypatch.setattr(system_route, "_SESSIONS_PUSH_INTERVAL", 3600)
    with client.websocket_connect("/ws-workspace") as ws:
        _next_of(ws, "tmux_sessions")
        listing["value"] = [{"name": "dev", "cwd": "/a"}, {"name": "new", "cwd": "/a"}]
        tmux_route._kick_sessions()
        while True:
            msg = _next_of(ws, "tmux_sessions")
            if len(msg["sessions"]) == 2:
                break


def test_unchanged_list_is_not_pushed_again(listing):
    sent = []

    class FakeWs:
        async def send_json(self, m):
            sent.append(m)

    async def run():
        ws = FakeWs()
        system_route._workspace_clients.add(ws)
        try:
            kick = asyncio.Event()
            task = asyncio.create_task(system_route._sessions_loop(kick))
            for _ in range(3):
                kick.set()
                await asyncio.sleep(0.05)
            listing["value"] = [{"name": "dev2", "cwd": "/a"}]
            kick.set()
            await asyncio.sleep(0.05)
        finally:
            system_route._workspace_clients.discard(ws)
            kick.set()
            await asyncio.wait_for(task, 2)

    asyncio.run(run())
    assert [m["sessions"][0]["name"] for m in sent] == ["dev", "dev2"]


def test_loop_stops_without_clients(listing):
    async def run():
        task = asyncio.create_task(system_route._sessions_loop(asyncio.Event()))
        await asyncio.wait_for(task, 1)
        return task.done()

    assert asyncio.run(run()) is True


def test_hidden_clients_pause_the_loop(listing):
    # 보이는 화면이 없으면 tmux를 조회하지도 보내지도 않는다(예전 폴링의 document.hidden).
    sent = []
    calls = {"n": 0}

    async def counting():
        calls["n"] += 1
        return [dict(s) for s in listing["value"]]

    class FakeWs:
        async def send_json(self, m):
            sent.append(m)

    async def run():
        import routes.tmux as t
        orig = t.collect_tmux_sessions
        t.collect_tmux_sessions = counting
        ws = FakeWs()
        system_route._workspace_clients.add(ws)
        system_route._workspace_hidden.add(ws)
        try:
            kick = asyncio.Event()
            task = asyncio.create_task(system_route._sessions_loop(kick))
            for _ in range(3):
                kick.set()
                await asyncio.sleep(0.05)
            hidden_calls = calls["n"]
            system_route._workspace_hidden.discard(ws)
            kick.set()
            await asyncio.sleep(0.05)
        finally:
            t.collect_tmux_sessions = orig
            system_route._workspace_clients.discard(ws)
            system_route._workspace_hidden.discard(ws)
            kick.set()
            await asyncio.wait_for(task, 2)
        return hidden_calls

    assert asyncio.run(run()) == 0
    assert len(sent) == 1


def test_becoming_visible_catches_up_immediately(client, listing, monkeypatch):
    monkeypatch.setattr(system_route, "_SESSIONS_PUSH_INTERVAL", 3600)
    with client.websocket_connect("/ws-workspace") as ws:
        _next_of(ws, "tmux_sessions")
        ws.send_json({"type": "visibility", "hidden": True})
        listing["value"] = [{"name": "while-hidden", "cwd": "/a"}]
        ws.send_json({"type": "visibility", "hidden": False})
        while True:
            msg = _next_of(ws, "tmux_sessions")
            if msg["sessions"][0]["name"] == "while-hidden":
                break
