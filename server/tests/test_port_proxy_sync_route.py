"""2.2 — POST /api/ports/_proxy/sync.

CLI가 port_share_store를 직접 쓴 뒤 실행 중인 서버를 깨우는 경로다.
**승격을 요구하지 않는다** — 접근 결정은 이미 디스크에 적혀 있고, 여기서는
리스너에 반영할 뿐이다.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

HOUR = 3600


@pytest.fixture
def routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7900")
    import port_share_store
    import routes.port_proxy_app as papp
    import port_proxy
    import routes.ports as ports
    importlib.reload(port_share_store)
    importlib.reload(papp)
    importlib.reload(port_proxy)
    importlib.reload(ports)
    papp._shares.clear()

    async def _ok(*a, **kw):
        return True

    monkeypatch.setattr(port_proxy, "start", _ok)
    return ports


def test_sync_route_is_not_elevated(routes):
    paths = {r.path for r in routes.elevated_router.routes}
    assert "/api/ports/_proxy/sync" not in paths
    assert any(r.path == "/api/ports/_proxy/sync" for r in routes.router.routes)


def test_sync_route_starts_listener_for_cli_created_share(routes, monkeypatch):
    """CLI가 store에 쓴 뒤 이 엔드포인트로 서버를 깨우는 흐름 그대로."""
    import port_share_store
    port_share_store.create_share(3000, "앱", "public", HOUR)

    seen = []

    async def _record(share):
        seen.append(share["port"])
        return True

    monkeypatch.setattr(routes.port_proxy, "start", _record)
    result = asyncio.run(routes.sync_port_proxy())
    assert result["ok"] is True
    assert seen == [3000]


def test_sync_route_invalidates_proxy_app_cache(routes):
    """모드를 바꾼 뒤 sync를 부르면 프록시 앱이 캐시된 옛 share를 안 써야 한다."""
    import port_share_store
    import routes.port_proxy_app as papp

    port_share_store.create_share(3000, "앱", "public", HOUR)
    papp._shares.set("3000", port_share_store.get_share(3000))  # 캐시 채움

    port_share_store.set_mode(3000, "session")
    asyncio.run(routes.sync_port_proxy())

    assert papp._shares.get("3000") is None, "무효화가 안 됐다 — 옛 모드가 계속 먹힌다"


# --- 회귀: unexpose 직후 재노출이 옛(더 약한) 모드로 캐시에 남는 문제 -----------------
#
# 2026-09-22 로컬 E2E 스모크에서 실제로 재현됨: expose(public) → unexpose →
# 즉시 expose(pin)을 캐시 TTL(2초) 안에 연달아 하면 PIN 게이트를 건너뛰고 그대로
# 통과했다. 원인은 sync_port_proxy가 sync **이후** 살아있는 share만 무효화해서,
# "방금 막 제거된" 포트가 무효화 대상에서 빠졌기 때문. 위 `routes` 픽스처는
# `port_proxy.start`를 스텁으로 바꿔놔서 `port_proxy.status()`가 항상 빈 목록을
# 돌려주므로(이 결함을 못 잡는다), 이 회귀 테스트만 진짜 리스너로 돌린다.


@pytest.fixture
def real_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7910")
    import port_share_store
    import routes.port_proxy_app as papp
    import port_proxy
    import routes.ports as ports
    importlib.reload(port_share_store)
    importlib.reload(papp)
    importlib.reload(port_proxy)
    importlib.reload(ports)
    papp._shares.clear()
    return ports


def test_sync_invalidates_cache_for_a_port_removed_moments_ago(real_routes):
    # 리스너(uvicorn Server)는 만든 이벤트 루프 안에서 정리해야 한다 — 다른
    # asyncio.run()에서 shutdown하면 태스크가 죽은 루프에 묶여 CancelledError가
    # 난다(test_port_proxy_listener.py의 `_run` 패턴과 같은 함정).
    import port_share_store
    import routes.port_proxy_app as papp

    async def scenario():
        try:
            share, _ = port_share_store.create_share(3000, "앱", "public", HOUR)
            await real_routes.port_proxy.start(share)
            # "방금 이 포트로 요청이 와서 캐시에 실렸다"를 흉내낸다.
            papp._shares.set("3000", share)

            port_share_store.remove_share(3000)              # fsh tunnel unexpose
            port_share_store.create_share(3000, "앱", "pin", HOUR)  # 즉시 재노출, 더 엄격한 모드

            await real_routes.sync_port_proxy()
            return papp._shares.get("3000")
        finally:
            await real_routes.port_proxy.shutdown_all()

    cached = asyncio.run(scenario())
    assert cached is None or cached["mode"] == "pin", (
        "옛(public) 캐시가 살아남으면 그 TTL 동안 PIN 게이트를 건너뛴다"
    )
