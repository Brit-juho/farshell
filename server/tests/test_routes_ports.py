"""D12: routes/ports.py 라우터 레이어 HTTP 테스트.

portscan.py(스캔/kill 판정 로직)는 test_portscan.py에서 이미 단위 테스트되므로,
여기서는 그 결과를 라우터가 올바른 상태코드로 매핑하는지만 확인한다.
실제 lsof/kill/vt CLI는 monkeypatch로 대체해 시스템 상태를 건드리지 않는다.
"""

import pytest
from starlette.testclient import TestClient

import main
import network_access
import portscan
import routes.ports as ports_mod


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


# --- GET /api/ports ------------------------------------------------------------


def test_list_ports_returns_scan_result(client, monkeypatch):
    monkeypatch.setattr(portscan, "scan", lambda use_cache=True: {"ports": [{"port": 3000}], "cached": False, "truncated": False})
    r = client.get("/api/ports")
    assert r.status_code == 200
    assert r.json()["ports"] == [{"port": 3000}]


def test_list_ports_fresh_flag_disables_cache(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(portscan, "scan", lambda use_cache=True: seen.setdefault("use_cache", use_cache) or {"ports": []})
    client.get("/api/ports?fresh=true")
    assert seen["use_cache"] is False


# --- DELETE /api/ports/{port} --------------------------------------------------


@pytest.mark.parametrize("port", [0, -1, 70000])
def test_kill_port_rejects_out_of_range_port(client, port):
    r = client.delete(f"/api/ports/{port}")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_port"


@pytest.mark.parametrize(
    "error,expected_status",
    [("protected", 403), ("permission", 403), ("not_found", 404), ("pid_changed", 409), ("weird_unmapped", 500)],
)
def test_kill_port_maps_errors_to_status(client, monkeypatch, error, expected_status):
    monkeypatch.setattr(portscan, "kill_port", lambda port, pid=None: {"ok": False, "error": error, "reason": "x"})
    r = client.delete("/api/ports/3000")
    assert r.status_code == expected_status


def test_kill_port_success(client, monkeypatch):
    monkeypatch.setattr(portscan, "kill_port", lambda port, pid=None: {"ok": True, "pid": 123, "signal": "TERM"})
    r = client.delete("/api/ports/3000")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# --- POST /api/ports/{port}/expose ---------------------------------------------


@pytest.mark.parametrize("port", [0, 70000])
def test_expose_rejects_out_of_range_port(client, port):
    r = client.post(f"/api/ports/{port}/expose", json={"confirm": True})
    assert r.status_code == 400


def test_expose_requires_explicit_confirm(client, monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    r = client.post("/api/ports/3000/expose", json={})
    assert r.status_code == 428
    assert r.json()["error"] == "confirm_required"


def test_expose_rejected_when_network_mode_restricted(client, monkeypatch):
    # NetworkAccessMiddleware도 VT_NETWORK_MODE를 보므로, 라우트 자체의 판정(409)에
    # 도달하려면 미들웨어의 IP 필터는 통과시켜 둬야 한다(라우터 레이어만 검증하는 게 목적).
    monkeypatch.setattr(network_access, "get_current_spec", lambda: network_access.AccessSpec(networks=[], allow_all=True))
    monkeypatch.setenv("VT_NETWORK_MODE", "lan")
    r = client.post("/api/ports/3000/expose", json={"confirm": True})
    assert r.status_code == 409
    assert r.json()["error"] == "network_mode"


def test_expose_success_parses_url(client, monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (0, "tunnel up at https://example.trycloudflare.com\n"))
    r = client.post("/api/ports/3000/expose", json={"confirm": True, "label": "dev"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == "https://example.trycloudflare.com"


def test_expose_failure_returns_500(client, monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (1, "cloudflared not found"))
    r = client.post("/api/ports/3000/expose", json={"confirm": True})
    assert r.status_code == 500
    assert r.json()["error"] == "expose_failed"


# --- DELETE /api/ports/{port}/expose -------------------------------------------


def test_unexpose_success(client, monkeypatch):
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (0, "unexposed"))
    r = client.delete("/api/ports/3000/expose")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unexpose_failure_returns_500(client, monkeypatch):
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (1, "no tunnel running"))
    r = client.delete("/api/ports/3000/expose")
    assert r.status_code == 500
    assert r.json()["error"] == "unexpose_failed"
