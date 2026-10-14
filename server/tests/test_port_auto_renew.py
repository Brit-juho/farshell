"""자동 재노출 — 만료돼도 자격을 유지한 채 연장하고, 터널이 사라지면 다시 연다.

1. 정책에 autoRenew·ttl이 남고, 켜고 끄는 것은 자격(shareId·PIN)을 바꾸지 않는다.
2. 웹 노출과 PATCH는 auto_renew를 bool로만 받는다.
3. 정리 루프: autoRenew 정책은 내리지 않고 만료만 연장한다.
4. 복구: autoRenew 정책인데 터널이 없으면 `fsh tunnel expose --proxy-port`로 다시 연다.
5. CLI(`cli-create`의 6번째 인자, `cli-set-auto`).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time

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
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7960")
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    import port_share_store
    import routes.port_proxy_app as papp
    import port_proxy
    import routes.ports as ports
    for m in (port_share_store, papp, port_proxy, ports):
        importlib.reload(m)
    papp._shares.clear()

    calls = {"start": [], "stop": [], "vt": []}

    async def _start(share):
        calls["start"].append(share["port"])
        return True

    async def _stop(port):
        calls["stop"].append(port)

    def _vt(*a):
        calls["vt"].append(a)
        return 0, "  ✓ 앱 → https://abc.trycloudflare.com"

    monkeypatch.setattr(port_proxy, "start", _start)
    monkeypatch.setattr(port_proxy, "stop", _stop)
    monkeypatch.setattr(ports, "_run_vt", _vt)
    monkeypatch.setattr(ports.tunnel_registry, "exposed_ports", lambda: {})
    ports.calls = calls
    return ports


def _status(resp):
    return getattr(resp, "status_code", 200)


# --- 1. 저장소 -------------------------------------------------------------------


def test_create_records_auto_renew_and_ttl(routes):
    store = routes.pshare
    share, _ = store.create_share(3000, "앱", "session", 86400, auto_renew=True)
    assert share["autoRenew"] is True and share["ttl"] == 86400
    off, _ = store.create_share(4000, "앱2", "session", HOUR)
    assert off["autoRenew"] is False, "기본값은 끔"


def test_toggling_auto_renew_keeps_credentials(routes):
    store = routes.pshare
    share, _ = store.create_share(3000, "앱", "pin", HOUR)
    before = (share["shareId"], share["pinHash"])
    updated = store.set_auto_renew(3000, True)
    assert updated["autoRenew"] is True
    assert (updated["shareId"], updated["pinHash"]) == before, "켜고 끄는 것은 자격을 바꾸지 않는다"
    assert store.set_auto_renew(9999, True) is None


def test_old_policy_without_fields_reads_as_off(routes, tmp_path):
    (tmp_path / "port_shares.json").write_text(json.dumps(
        {"version": 1, "shares": [{"port": 3000, "shareId": "x", "mode": "public",
                                   "proxyPort": 7960, "exp": time.time() + HOUR}]}))
    assert routes.pshare.get_share(3000)["autoRenew"] is False


def test_renew_extends_by_ttl_and_keeps_share_id(routes):
    store = routes.pshare
    share, _ = store.create_share(3000, "앱", "pin", 86400, auto_renew=True)
    store.update_share(3000, exp=time.time() - 5)
    renewed = store.renew_share(3000, share["shareId"])
    assert renewed["shareId"] == share["shareId"]
    assert renewed["exp"] > time.time() + 86400 - 60


def test_renew_refuses_other_share_id_or_disabled(routes):
    store = routes.pshare
    share, _ = store.create_share(3000, "앱", "pin", HOUR, auto_renew=True)
    assert store.renew_share(3000, "other") is None
    store.set_auto_renew(3000, False)
    assert store.renew_share(3000, share["shareId"]) is None


def test_renew_falls_back_to_one_hour_for_policy_without_ttl(routes):
    store = routes.pshare
    share, _ = store.create_share(3000, "앱", "session", HOUR, auto_renew=True)
    store.update_share(3000, ttl=None, exp=time.time() - 1)
    renewed = store.renew_share(3000, share["shareId"])
    assert renewed["ttl"] == HOUR and renewed["exp"] > time.time() + HOUR - 60


# --- 2. 웹 노출과 PATCH ------------------------------------------------------------


def test_web_expose_passes_auto_renew(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "session", "auto_renew": True})))
    assert _status(r) == 200 and r["share"]["autoRenew"] is True


def test_web_expose_defaults_auto_renew_off(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True})))
    assert _status(r) == 200 and r["share"]["autoRenew"] is False


@pytest.mark.parametrize("bad", ["true", 1, "yes", [True]])
def test_web_expose_rejects_non_bool_auto_renew(routes, bad):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "auto_renew": bad})))
    assert _status(r) == 400 and json.loads(r.body)["error"] == "bad_auto_renew"
    assert routes.calls["vt"] == [], "검증 실패면 터널을 띄우지 않는다"


def test_patch_auto_renew_only_keeps_mode_and_share_id(routes):
    share, _ = routes.pshare.create_share(3000, "앱", "pin", HOUR)
    r = asyncio.run(routes.change_share_mode(3000, _Body({"auto_renew": True})))
    assert _status(r) == 200 and r["share"]["autoRenew"] is True
    assert r["mode"] == "pin" and r["share"]["shareId"] == share["shareId"]


def test_patch_mode_and_auto_renew_together(routes):
    routes.pshare.create_share(3000, "앱", "public", HOUR)
    r = asyncio.run(routes.change_share_mode(3000, _Body({"mode": "session", "auto_renew": True})))
    assert _status(r) == 200 and r["share"]["mode"] == "session" and r["share"]["autoRenew"] is True


def test_patch_requires_mode_or_auto_renew(routes):
    routes.pshare.create_share(3000, "앱", "pin", HOUR)
    assert _status(asyncio.run(routes.change_share_mode(3000, _Body({})))) == 400
    r = asyncio.run(routes.change_share_mode(3000, _Body({"auto_renew": "on"})))
    assert _status(r) == 400 and json.loads(r.body)["error"] == "bad_auto_renew"


# --- 3. 정리 루프 ------------------------------------------------------------------


def test_sweep_renews_auto_renew_share_instead_of_tearing_down(routes):
    store = routes.pshare
    share, _ = store.create_share(3000, "앱", "pin", HOUR, auto_renew=True)
    store.create_share(4000, "다른 앱", "public", HOUR)
    store.update_share(3000, exp=time.time() - 1)
    store.update_share(4000, exp=time.time() - 1)

    done = asyncio.run(routes.sweep_expired_shares())
    assert done == [4000], "자동 재노출이 꺼진 쪽만 내린다"
    assert 3000 not in routes.calls["stop"]
    kept = store.get_share(3000)
    assert kept is not None and kept["shareId"] == share["shareId"]


# --- 4. 터널 복구 ------------------------------------------------------------------


def test_restore_reopens_missing_tunnel_with_proxy_port(routes):
    share, _ = routes.pshare.create_share(3000, "앱", "session", HOUR, auto_renew=True)
    routes.pshare.create_share(4000, "끈 앱", "session", HOUR)
    restored = asyncio.run(routes.restore_auto_renew_tunnels())
    assert restored == [3000]
    assert routes.calls["vt"] == [("tunnel", "expose", "3000", "앱", "--proxy-port", str(share["proxyPort"]))]


def test_restore_skips_ports_whose_tunnel_is_alive(routes, monkeypatch):
    routes.pshare.create_share(3000, "앱", "session", HOUR, auto_renew=True)
    monkeypatch.setattr(routes.tunnel_registry, "exposed_ports",
                        lambda: {3000: {"url": "https://x.trycloudflare.com", "label": "앱"}})
    assert asyncio.run(routes.restore_auto_renew_tunnels()) == []
    assert routes.calls["vt"] == []


def test_restore_does_nothing_when_network_is_narrowed(routes, monkeypatch):
    routes.pshare.create_share(3000, "앱", "session", HOUR, auto_renew=True)
    monkeypatch.setenv("VT_NETWORK_MODE", "tailscale")
    assert asyncio.run(routes.restore_auto_renew_tunnels()) == []
    assert routes.calls["vt"] == []


def test_restore_reports_failure_without_raising(routes, monkeypatch):
    routes.pshare.create_share(3000, "앱", "session", HOUR, auto_renew=True)
    monkeypatch.setattr(routes, "_run_vt", lambda *a: (1, "터널 URL 대기 시간 초과"))
    assert asyncio.run(routes.restore_auto_renew_tunnels()) == []
    assert routes.pshare.get_share(3000) is not None, "실패해도 정책은 남겨 다음 회차에 다시 시도한다"


def test_restore_skips_when_listener_cannot_bind(routes, monkeypatch):
    routes.pshare.create_share(3000, "앱", "session", HOUR, auto_renew=True)

    async def _fail(share):
        return False

    monkeypatch.setattr(routes.port_proxy, "start", _fail)
    assert asyncio.run(routes.restore_auto_renew_tunnels()) == []
    assert routes.calls["vt"] == []


# --- 5. CLI ---------------------------------------------------------------------


def test_cli_create_takes_auto_renew_flag(routes, capsys):
    assert routes.pshare._cli(["cli-create", "3000", "앱", "session", "3600", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["autoRenew"] is True
    assert routes.pshare._cli(["cli-create", "4000", "앱", "session", "3600"]) == 0
    assert json.loads(capsys.readouterr().out)["autoRenew"] is False


def test_cli_set_auto(routes, capsys):
    routes.pshare.create_share(3000, "앱", "session", HOUR)
    assert routes.pshare._cli(["cli-set-auto", "3000", "on"]) == 0
    assert json.loads(capsys.readouterr().out)["autoRenew"] is True
    assert routes.pshare._cli(["cli-set-auto", "3000", "maybe"]) == 1
    assert routes.pshare._cli(["cli-set-auto", "9999", "on"]) == 1


def test_cli_list_has_auto_renew_column(routes, capsys):
    routes.pshare.create_share(3000, "앱", "session", HOUR, auto_renew=True)
    routes.pshare._cli(["cli-list"])
    assert capsys.readouterr().out.strip().split("\t") == ["3000", "session", "0", "0", "1"]
