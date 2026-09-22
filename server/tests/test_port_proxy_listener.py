"""2.2 — 프록시 리스너 수명 관리 (plan-2.2/00-port-access.md ADR-31 · §9).

loopback 전용 바인드 · sync 멱등 · 재기동 후 같은 주소 복귀 · 바인드 실패 시
조용히 죽지 않고 broken 표시를 확인한다.

> 리스너는 uvicorn 태스크라 **만든 이벤트 루프 안에서 정리해야 한다.**
> `asyncio.run`으로 열고 다른 `asyncio.run`에서 닫으려 하면 태스크가 죽은 루프에
> 묶여 소켓이 영영 안 닫힌다(실제로 겪음 — 다음 테스트가 "Address already in
> use"로 깨진다). 그래서 `_run`이 시나리오와 정리를 **한 루프 안에** 묶는다.
> 운영에서는 루프가 하나뿐이라 없는 문제다.
"""

from __future__ import annotations

import asyncio
import importlib
import socket
import time

import httpx
import pytest

HOUR = 3600


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    # 테스트 대역을 기본값(7800)과 떼어 놓는다 — 개발자 기계에서 실제 프록시가
    # 돌고 있어도 충돌하지 않게.
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7920")

    import port_share_store
    import routes.port_proxy_app as papp
    import port_proxy
    importlib.reload(port_share_store)
    importlib.reload(papp)
    importlib.reload(port_proxy)
    papp._shares.clear()

    yield port_proxy

    # 안전망 — 루프가 닫힌 뒤에도 소켓은 동기로 닫을 수 있다.
    for lis in list(port_proxy._listeners.values()):
        try:
            lis.sock.close()
        except OSError:
            pass
    port_proxy._listeners.clear()


def _run(mod, fn):
    """시나리오와 정리를 같은 루프 안에서 돌린다(모듈 docstring 참고)."""
    async def wrapped():
        try:
            return await fn()
        finally:
            await mod.shutdown_all()

    return asyncio.run(wrapped())


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _store():
    import port_share_store
    return port_share_store


# --- 기동·종료 -----------------------------------------------------------------


def test_start_and_stop_roundtrip(mod):
    share, _ = _store().create_share(3000, "앱", "public", HOUR)

    async def scenario():
        ok = await mod.start(share)
        running = mod.status()
        await mod.stop(3000)
        return ok, running, mod.status()

    ok, running, after = _run(mod, scenario)
    assert ok is True
    assert running[0]["running"] is True
    assert after == []


def test_start_is_idempotent(mod):
    share, _ = _store().create_share(3000, "앱", "public", HOUR)

    async def scenario():
        await mod.start(share)
        await mod.start(share)
        return mod.status()

    assert len(_run(mod, scenario)) == 1


def test_stop_unknown_port_is_noop(mod):
    _run(mod, lambda: mod.stop(9999))


def test_share_without_proxy_port_is_skipped(mod):
    assert _run(mod, lambda: mod.start({"port": 3000, "shareId": "x"})) is False


# --- loopback 전용 ---------------------------------------------------------------


def test_listener_binds_loopback_only(mod):
    """0.0.0.0에 열면 같은 망의 누구나 인증 게이트를 건너뛴다."""
    share, _ = _store().create_share(3000, "앱", "pin", HOUR)

    async def scenario():
        await mod.start(share)
        return mod._listeners[3000].sock.getsockname()

    host, _port = _run(mod, scenario)
    assert host == "127.0.0.1"


# --- sync: store가 진실이다 --------------------------------------------------------


def test_sync_starts_listeners_for_all_shares(mod):
    _store().create_share(3000, "a", "public", HOUR)
    _store().create_share(4000, "b", "session", HOUR)

    async def scenario():
        return await mod.sync(), mod.status()

    result, status = _run(mod, scenario)
    assert sorted(result["active"]) == [3000, 4000]
    assert result["failed"] == []
    assert len(status) == 2


def test_sync_stops_listeners_for_removed_shares(mod):
    _store().create_share(3000, "a", "public", HOUR)

    async def scenario():
        await mod.sync()
        _store().remove_share(3000)
        await mod.sync()
        return mod.status()

    assert _run(mod, scenario) == []


def test_sync_drops_expired_shares(mod):
    _store().create_share(3000, "a", "public", HOUR)

    async def scenario():
        await mod.sync()
        _store().update_share(3000, exp=time.time() - 1)
        await mod.sync()
        return mod.status()

    assert _run(mod, scenario) == []


def test_sync_is_idempotent(mod):
    _store().create_share(3000, "a", "public", HOUR)

    async def scenario():
        await mod.sync()
        first = mod._listeners[3000].sock.getsockname()
        await mod.sync()
        return first, mod._listeners[3000].sock.getsockname()

    a, b = _run(mod, scenario)
    assert a == b, "sync를 다시 불렀다고 주소가 바뀌면 재기동 복귀가 깨진다"


# --- 재기동 후 같은 주소 복귀 (ADR-31의 핵심 요구) -----------------------------------


def test_proxy_port_is_stable_across_restart(mod):
    """살아남은 cloudflared가 같은 주소에서 프록시를 다시 찾아야 한다."""
    share, _ = _store().create_share(3000, "앱", "public", HOUR)
    expected = share["proxyPort"]

    async def scenario():
        await mod.sync()
        addr1 = mod._listeners[3000].sock.getsockname()
        await mod.shutdown_all()          # fsh restart 흉내
        await mod.sync()
        return addr1, mod._listeners[3000].sock.getsockname()

    addr1, addr2 = _run(mod, scenario)
    assert addr1 == addr2 == ("127.0.0.1", expected)


# --- 바인드 실패는 조용히 죽지 않는다 ------------------------------------------------


def test_bind_failure_marks_share_broken(mod):
    share, _ = _store().create_share(3000, "앱", "public", HOUR)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", share["proxyPort"]))
    blocker.listen(1)
    try:
        assert _run(mod, lambda: mod.start(share)) is False
    finally:
        blocker.close()
    assert _store().get_share(3000)["broken"] is True


def test_broken_flag_clears_on_successful_start(mod):
    _store().create_share(3000, "앱", "public", HOUR)
    _store().update_share(3000, broken=True)

    _run(mod, lambda: mod.start(_store().get_share(3000)))
    assert _store().get_share(3000)["broken"] is False


def test_sync_reports_failed_ports(mod):
    share, _ = _store().create_share(3000, "앱", "public", HOUR)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", share["proxyPort"]))
    blocker.listen(1)
    try:
        result = _run(mod, mod.sync)
    finally:
        blocker.close()
    assert result["failed"] == [3000]


# --- 실제로 트래픽이 흐르는지 -------------------------------------------------------


def test_listener_actually_serves_the_proxy_app(mod):
    """리스너가 떴다는 상태값만이 아니라 진짜 HTTP가 오가는지 확인한다."""
    upstream_port = _free_port()
    share, _ = _store().create_share(upstream_port, "앱", "pin", HOUR)

    async def scenario():
        await mod.start(share)
        for _ in range(50):  # 리스너가 받을 준비가 될 때까지
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    return await c.get(f"http://127.0.0.1:{share['proxyPort']}/")
            except httpx.ConnectError:
                await asyncio.sleep(0.05)
        raise AssertionError("리스너가 뜨지 않았다")

    resp = _run(mod, scenario)
    # 업스트림은 없지만 게이트가 먼저 걸리므로 PIN 페이지가 나온다.
    assert resp.status_code == 200
    assert "접근 코드" in resp.text


def test_proxy_ports_helper_lists_live_listeners(mod):
    share, _ = _store().create_share(3000, "앱", "public", HOUR)

    async def scenario():
        await mod.start(share)
        return mod.proxy_ports()

    assert _run(mod, scenario) == {share["proxyPort"]}
