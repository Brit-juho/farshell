"""2.2 — 노출 포트 라우트 (plan-2.2/00-port-access.md §5 · §9).

승격 요구 · 428 확인 · VT_NETWORK_MODE 전 모드 차단 · 모드/TTL 검증 ·
실패 시 되감기 · PIN이 목록에 안 실리는지를 확인한다.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

HOUR = 3600


class _Body:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


@pytest.fixture
def routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7940")
    monkeypatch.setenv("VT_NETWORK_MODE", "all")

    import port_share_store
    import routes.port_proxy_app as papp
    import port_proxy
    import routes.ports as ports
    importlib.reload(port_share_store)
    importlib.reload(papp)
    importlib.reload(port_proxy)
    importlib.reload(ports)
    papp._shares.clear()

    # 리스너와 CLI는 이 파일의 관심사가 아니다 — 라우트의 판정만 본다.
    monkeypatch.setattr(port_proxy, "start", _ok_async(True))
    monkeypatch.setattr(port_proxy, "stop", _ok_async(None))
    monkeypatch.setattr(ports, "_run_vt",
                        lambda *a: (0, "  ✓ 앱 → https://abc.trycloudflare.com"))
    return ports


def _ok_async(value):
    async def _f(*a, **kw):
        return value
    return _f


def _store():
    import port_share_store
    return port_share_store


def _json(resp):
    import json as _j
    if hasattr(resp, "body"):
        return _j.loads(resp.body)
    return resp


def _status(resp):
    return getattr(resp, "status_code", 200)


# --- 승격이 라우터 단위로 걸려 있는가 -----------------------------------------------


def test_write_routes_are_on_the_elevated_router(routes):
    """핸들러마다 붙이면 하나쯤 빠뜨린다 — 라우터 단위여야 한다."""
    paths = {r.path for r in routes.elevated_router.routes}
    assert paths == {
        "/api/ports/{port}/expose",
        "/api/ports/{port}/share/pin",
        "/api/ports/{port}/share/pause",
        "/api/ports/{port}/share/resume",
        "/api/ports/{port}/share/refresh-url",
        "/api/ports/{port}/share",
    }


def test_read_routes_stay_unelevated(routes):
    paths = {r.path for r in routes.router.routes}
    assert "/api/ports" in paths
    assert "/api/tunnel/list" in paths
    assert not any("expose" in p for p in paths), "쓰기 라우트가 비승격 라우터에 남아 있다"


# --- expose 게이트 ---------------------------------------------------------------


def test_expose_requires_confirm(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({})))
    assert _status(r) == 428
    assert _json(r)["error"] == "confirm_required"


def test_expose_rejects_bad_port(routes):
    r = asyncio.run(routes.expose_port(0, _Body({"confirm": True})))
    assert _status(r) == 400


def test_expose_blocked_when_network_mode_narrowed(routes, monkeypatch):
    """인증 붙은 모드도 함께 막는다(plan §10-3)."""
    monkeypatch.setenv("VT_NETWORK_MODE", "tailscale")
    for mode in ("public", "session", "pin"):
        r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": mode})))
        assert _status(r) == 409, f"{mode} 모드가 새어나갔다"


def test_expose_rejects_unknown_mode(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "anyone"})))
    assert _status(r) == 400
    assert _json(r)["error"] == "bad_mode"


@pytest.mark.parametrize("ttl", [60, 99999, "abc", 0])
def test_expose_rejects_bad_ttl(routes, ttl):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": ttl})))
    assert _status(r) == 400
    assert _json(r)["error"] == "bad_ttl"


# --- expose 성공 경로 -------------------------------------------------------------


def test_expose_defaults_to_session_mode(routes):
    """ADR-32 — 기본값이 공개면 오탭 한 번이 인터넷 공개가 된다."""
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    assert r["ok"] is True
    assert r["mode"] == "session"
    assert _store().get_share(3000)["mode"] == "session"


def test_expose_pin_mode_returns_pin_once(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "pin", "ttl": HOUR})))
    assert r["pin"] and "-" in r["pin"]
    assert "pinHash" not in r["share"]
    assert r["share"]["hasPin"] is True


def test_expose_public_mode_has_no_pin(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "public", "ttl": HOUR})))
    assert r["pin"] is None


def test_expose_starts_listener_before_tunnel(routes, monkeypatch):
    """터널이 뜬 뒤에 리스너를 띄우면 그 사이 요청이 502가 된다."""
    order = []
    monkeypatch.setattr(routes.port_proxy, "start",
                        lambda share: order.append("listener") or _ok_async(True)())
    monkeypatch.setattr(routes, "_run_vt",
                        lambda *a: (order.append("tunnel"), (0, "https://x"))[1])
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    assert order == ["listener", "tunnel"]


def test_expose_passes_proxy_port_to_cli(routes, monkeypatch):
    seen = {}
    monkeypatch.setattr(routes, "_run_vt", lambda *a: (seen.update(args=a), (0, "https://x"))[1])
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    expected = str(_store().get_share(3000)["proxyPort"])
    assert "--proxy-port" in seen["args"]
    assert expected in seen["args"]


# --- 실패 시 되감기 --------------------------------------------------------------


def test_expose_rolls_back_share_when_listener_fails(routes, monkeypatch):
    monkeypatch.setattr(routes.port_proxy, "start", _ok_async(False))
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    assert _status(r) == 500
    assert _json(r)["error"] == "proxy_bind_failed"
    assert _store().get_share(3000) is None, "터널 없는 share가 남으면 안 된다"


def test_expose_rolls_back_when_tunnel_fails(routes, monkeypatch):
    stopped = []
    monkeypatch.setattr(routes.port_proxy, "stop",
                        lambda p: stopped.append(p) or _ok_async(None)())
    monkeypatch.setattr(routes, "_run_vt", lambda *a: (1, "cloudflared 미설치"))
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    assert _status(r) == 500
    assert _store().get_share(3000) is None
    assert stopped == [3000], "리스너가 남으면 포트가 물린 채로 떠 있다"


# --- unexpose --------------------------------------------------------------------


def test_unexpose_removes_share_and_listener(routes):
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    r = asyncio.run(routes.unexpose_port(3000))
    assert r["ok"] is True
    assert _store().get_share(3000) is None


# --- PIN 재발급 ------------------------------------------------------------------


def test_reissue_pin_keeps_url_alive(routes):
    """터널을 안 건드리는 것이 이 기능의 존재 이유다."""
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "pin", "ttl": HOUR})))
    before = _store().get_share(3000)
    r = asyncio.run(routes.reissue_pin(3000))
    after = _store().get_share(3000)
    assert r["pin"]
    # URL이 사는 근거는 proxyPort(살아 있는 cloudflared가 같은 주소를 찾는다)다.
    assert after["proxyPort"] == before["proxyPort"]
    assert after["pinHash"] != before["pinHash"]
    # shareId는 바뀐다(2026-10) — 옛 PIN으로 받은 쿠키를 죽이려고. URL에는 안 들어간다.
    assert after["shareId"] != before["shareId"]


def test_reissue_pin_rejects_non_pin_mode(routes):
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "public", "ttl": HOUR})))
    r = asyncio.run(routes.reissue_pin(3000))
    assert _status(r) == 409


def test_reissue_pin_unknown_port_is_404(routes):
    assert _status(asyncio.run(routes.reissue_pin(9999))) == 404


# --- 모드 변경 -------------------------------------------------------------------


def test_change_mode_keeps_proxy_port_and_rotates_share_id(routes):
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "public", "ttl": HOUR})))
    before = _store().get_share(3000)
    r = asyncio.run(routes.change_share_mode(3000, _Body({"mode": "session"})))
    after = _store().get_share(3000)
    assert r["mode"] == "session"
    assert after["proxyPort"] == before["proxyPort"], "주소가 죽지 않아야 한다"
    assert after["shareId"] != before["shareId"], "이전 모드로 통과한 쿠키는 죽어야 한다"


def test_change_to_pin_mode_generates_a_pin(routes):
    """PIN 없이 pin 모드가 되면 아무도 못 들어온다."""
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "public", "ttl": HOUR})))
    r = asyncio.run(routes.change_share_mode(3000, _Body({"mode": "pin"})))
    assert r["pin"]
    assert _store().get_share(3000)["pinHash"]


def test_change_mode_rejects_unknown(routes):
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    assert _status(asyncio.run(routes.change_share_mode(3000, _Body({"mode": "anyone"})))) == 400


def test_change_mode_unknown_port_is_404(routes):
    assert _status(asyncio.run(routes.change_share_mode(9999, _Body({"mode": "public"})))) == 404


# --- 목록 API는 비밀을 싣지 않는다 --------------------------------------------------


def test_tunnel_list_never_carries_pin_hash(routes, monkeypatch):
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "pin", "ttl": HOUR})))
    monkeypatch.setattr(routes.tunnel, "get_tunnel_status",
                        lambda: {"running": False, "url": "", "mode": "anon"})
    monkeypatch.setattr(routes.tunnel_registry, "exposed_ports",
                        lambda: {3000: {"url": "https://abc.example", "label": "앱"}})
    out = asyncio.run(routes.list_tunnels())
    row = out["extra"][0]
    assert row["mode"] == "pin"
    assert "pinHash" not in row["share"]
    assert "pin" not in row


def test_ports_list_hides_internal_proxy_listeners(routes, monkeypatch):
    """자기 공유를 끊는 kill 버튼이 목록에 뜨면 안 된다."""
    asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "ttl": HOUR})))
    proxy_port = _store().get_share(3000)["proxyPort"]
    monkeypatch.setattr(routes.portscan, "scan",
                        lambda cached: {"ports": [{"port": 3000}, {"port": proxy_port}],
                                        "cached": False, "truncated": False})
    monkeypatch.setattr(routes.port_proxy, "proxy_ports", lambda: {proxy_port})
    monkeypatch.setattr(routes.tunnel_registry, "exposed_ports", lambda: {})
    out = asyncio.run(routes.list_ports(fresh=True))
    assert [p["port"] for p in out["ports"]] == [3000]
    assert out["ports"][0]["share"]["mode"] == "session"
