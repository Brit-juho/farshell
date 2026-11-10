"""원격 칸 WS 프록시(`routes/peer_proxy.py`)의 좀비 연결 감시 — 가짜 상류/브라우저로 시험한다.

실제 원격 호스트·네트워크는 쓰지 않는다. 로컬 칸(`routes/pty.py`)과 같은 기준을 본다:
- 브라우저가 침묵하면(pong 없음) 1001 로 닫는다, 조용하지만 살아 있으면 ping 만 보낸다.
- 상류 연결에 프로토콜 ping/pong 이 명시돼 있다(15초/15초).
- 상류가 죽어 닫히면 브라우저에는 재연결 가능한 코드(1000)가 가고, 중단 코드(4004/4001)가 아니다
  (오프라인 호스트 레이아웃 보존 규칙과 충돌하지 않는다).
"""

from __future__ import annotations

import asyncio

import pytest

from routes import peer_proxy


def _sync(fn):
    """pytest-asyncio 없이 async 시험을 돌린다(이 저장소의 다른 시험과 같은 방식)."""
    import functools, inspect

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


class FakeBrowser:
    def __init__(self, inbox: "asyncio.Queue | None" = None):
        self.sent: list = []
        self.closed: list = []
        self.inbox = inbox or asyncio.Queue()
        self.query_params: dict = {}
        self.block_send = False

    async def accept(self): ...

    async def send_text(self, t):
        if self.block_send:
            await asyncio.sleep(3600)
        self.sent.append(t)

    async def send_bytes(self, b):
        self.sent.append(b)

    async def receive(self):
        return await self.inbox.get()

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))


@_sync
async def test_heartbeat_pings_while_browser_alive():
    ws = FakeBrowser()
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(peer_proxy.browser_heartbeat(ws, lambda: loop.time(), interval=0.02, timeout=5))
    await asyncio.sleep(0.1)
    task.cancel()
    assert ws.sent and all(m == '{"type":"ping"}' for m in ws.sent)
    assert ws.closed == []


@_sync
async def test_heartbeat_closes_silent_browser_with_1001():
    ws = FakeBrowser()
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.wait_for(
        peer_proxy.browser_heartbeat(ws, lambda: start, interval=0.02, timeout=0.05), 2)
    assert ws.closed == [(1001, "heartbeat timeout")]


@_sync
async def test_heartbeat_closes_when_send_blocks(monkeypatch):
    """송신이 막힌(반쯤 열린) 브라우저는 SEND_TIMEOUT 안에 끊는다."""
    monkeypatch.setattr(peer_proxy.wsutil, "SEND_TIMEOUT", 0.05)
    ws = FakeBrowser()
    ws.block_send = True
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(
        peer_proxy.browser_heartbeat(ws, lambda: loop.time(), interval=0.01, timeout=60), 2)
    assert ws.closed and ws.closed[0][0] == 1011


def test_upstream_keepalive_uses_local_pane_constants():
    kw = peer_proxy.upstream_keepalive()
    assert kw["ping_interval"] == peer_proxy.WS_HEARTBEAT_INTERVAL == 15.0
    assert kw["ping_timeout"] == peer_proxy.WS_HEARTBEAT_INTERVAL


class FakeUpstream:
    """응답 없는 상류 — 닫힐 때까지 아무것도 안 준다."""

    def __init__(self):
        self.close_code = None
        self.sent: list = []
        self._closed = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._closed.wait()
        raise StopAsyncIteration

    async def send(self, m):
        self.sent.append(m)

    async def close(self):
        self.close_code = self.close_code or 1006
        self._closed.set()


def _patch_proxy(monkeypatch, upstream, connect_kwargs):
    import websockets

    monkeypatch.setattr(peer_proxy, "_ws_auth_token", lambda ws: "tok")
    monkeypatch.setattr(peer_proxy.host_gate, "enforced", lambda: False)
    monkeypatch.setattr(peer_proxy.host_store, "find_peer", lambda hid: {"id": "h", "url": "http://x:1"})
    monkeypatch.setattr(peer_proxy.peer_client, "_signed_headers", lambda *a, **k: {})

    async def fake_connect(url, **kw):
        connect_kwargs.update(kw)
        return upstream

    monkeypatch.setattr(websockets, "connect", fake_connect)


@_sync
async def test_proxy_closes_silent_browser_and_upstream(monkeypatch):
    """브라우저가 pong 도 안 보내면 프록시가 1001 로 닫고 상류도 정리한다(좀비 누수 없음)."""
    up = FakeUpstream()
    kw: dict = {}
    _patch_proxy(monkeypatch, up, kw)
    monkeypatch.setattr(peer_proxy.browser_heartbeat, "__defaults__", (0.02, 0.06))
    ws = FakeBrowser()
    await asyncio.wait_for(peer_proxy.ws_remote(ws, "h", "dev"), 3)
    assert kw["ping_interval"] == 15.0 and kw["ping_timeout"] == 15.0
    assert ws.closed[0] == (1001, "heartbeat timeout")
    assert up.close_code is not None   # 상류도 닫았다


@_sync
async def test_proxy_pong_keeps_alive_and_is_not_forwarded(monkeypatch):
    up = FakeUpstream()
    _patch_proxy(monkeypatch, up, {})
    monkeypatch.setattr(peer_proxy.browser_heartbeat, "__defaults__", (0.02, 0.06))
    ws = FakeBrowser()
    task = asyncio.create_task(peer_proxy.ws_remote(ws, "h", "dev"))
    for _ in range(8):                              # 0.16초 > timeout 0.06초, pong 으로 계속 살림
        await ws.inbox.put({"type": "websocket.receive", "text": '{"type":"pong"}'})
        await asyncio.sleep(0.02)
    assert not task.done()
    await ws.inbox.put({"type": "websocket.receive", "text": '{"type":"resize","cols":9,"rows":3}'})
    await asyncio.sleep(0.03)
    assert up.sent == ['{"type":"resize","cols":9,"rows":3}']   # pong 은 B 로 안 간다
    await ws.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 2)


@_sync
async def test_proxy_dead_upstream_gives_reconnectable_code(monkeypatch):
    """상류가 죽어 끊기면(keepalive 1011/1006) 브라우저에는 1000 — 재연결한다. 4004/4001 이 아니다."""
    up = FakeUpstream()
    _patch_proxy(monkeypatch, up, {})
    ws = FakeBrowser()
    task = asyncio.create_task(peer_proxy.ws_remote(ws, "h", "dev"))
    await asyncio.sleep(0.05)
    up.close_code = 1011
    up._closed.set()
    await asyncio.wait_for(task, 2)
    assert ws.closed[-1][0] == 1000
