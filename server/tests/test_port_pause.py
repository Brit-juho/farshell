"""일시 닫기 / 다시 열기 / 주소 갱신 — 정책(shareId·PIN)은 남기고 터널만 다룬다."""

from __future__ import annotations

import asyncio
import importlib

import pytest

HOUR = 3600


@pytest.fixture
def routes(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7960")
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    import port_share_store
    import routes.port_proxy_app as papp
    import port_proxy
    import routes.ports as ports
    for m in (port_share_store, papp, port_proxy, ports):
        importlib.reload(m)
    papp._shares.clear()

    calls = {"start": [], "vt": []}

    async def _start(share):
        calls["start"].append(share["port"])
        return True

    async def _stop(port):
        pass

    state = {"rc": 0}

    def _vt(*a):
        calls["vt"].append(a)
        return state["rc"], "  ✓ 앱 → https://new.trycloudflare.com"

    monkeypatch.setattr(port_proxy, "start", _start)
    monkeypatch.setattr(port_proxy, "stop", _stop)
    monkeypatch.setattr(ports, "_run_vt", _vt)
    monkeypatch.setattr(ports.tunnel_registry, "exposed_ports", lambda: {})
    ports.calls = calls
    ports.state = state
    return ports


def _run(coro):
    return asyncio.run(coro)


def test_pause_keeps_policy_and_closes_tunnel(routes):
    share, _ = routes.pshare.create_share(3000, "앱", "session", HOUR, True)
    r = _run(routes.pause_share(3000))
    assert r["paused"] is True
    assert routes.calls["vt"][-1] == ("tunnel", "unexpose", "3000", "--keep-policy")
    kept = routes.pshare.get_share(3000)
    assert kept["shareId"] == share["shareId"] and kept["paused"] is True


def test_pause_failure_reverts_flag(routes):
    routes.pshare.create_share(3000, "앱", "session", HOUR)
    routes.state["rc"] = 1
    r = _run(routes.pause_share(3000))
    assert r.status_code == 500
    assert routes.pshare.get_share(3000)["paused"] is False


def test_pause_unknown_port_404(routes):
    assert _run(routes.pause_share(3999)).status_code == 404


def test_resume_reopens_with_same_policy(routes):
    share, _ = routes.pshare.create_share(3000, "앱", "session", HOUR)
    _run(routes.pause_share(3000))
    r = _run(routes.resume_share(3000))
    assert r["url"] == "https://new.trycloudflare.com"
    got = routes.pshare.get_share(3000)
    assert got["paused"] is False and got["shareId"] == share["shareId"]
    assert routes.calls["vt"][-1][:3] == ("tunnel", "expose", "3000")
    assert "--proxy-port" in routes.calls["vt"][-1]


def test_resume_requires_paused(routes):
    routes.pshare.create_share(3000, "앱", "session", HOUR)
    assert _run(routes.resume_share(3000)).status_code == 409


def test_refresh_url_keeps_share_id(routes):
    share, _ = routes.pshare.create_share(3000, "앱", "session", HOUR)
    r = _run(routes.refresh_share_url(3000))
    assert r["url"] == "https://new.trycloudflare.com"
    assert routes.pshare.get_share(3000)["shareId"] == share["shareId"]
    verbs = [c[:2] for c in routes.calls["vt"]]
    assert verbs == [("tunnel", "unexpose"), ("tunnel", "expose")]


def test_refresh_failure_marks_paused(routes):
    routes.pshare.create_share(3000, "앱", "session", HOUR)
    orig = routes._run_vt
    n = {"i": 0}

    def flaky(*a):
        n["i"] += 1
        return (0, "ok") if n["i"] == 1 else (1, "boom")

    routes._run_vt = flaky
    r = _run(routes.refresh_share_url(3000))
    routes._run_vt = orig
    assert r.status_code == 500
    assert routes.pshare.get_share(3000)["paused"] is True


def test_restore_skips_paused(routes):
    routes.pshare.create_share(3000, "앱", "session", HOUR, True)
    _run(routes.pause_share(3000))
    routes.calls["vt"].clear()
    assert _run(routes.restore_auto_renew_tunnels()) == []
    assert routes.calls["vt"] == []


def test_tunnel_list_shows_paused(routes):
    routes.pshare.create_share(3000, "앱", "session", HOUR)
    _run(routes.pause_share(3000))
    d = _run(routes.list_tunnels())
    row = [e for e in d["extra"] if e["port"] == 3000][0]
    assert row["paused"] is True and row["url"] == ""
