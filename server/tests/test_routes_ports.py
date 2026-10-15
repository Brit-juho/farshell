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


@pytest.fixture
def elevated(tmp_path, monkeypatch):
    """2.2부터 expose/unexpose는 **승격**을 요구한다 — 포트를 외부에
    여는 것은 공유 링크 발급과 같은 등급의 쓰기 액션이다. 로그인 → 승격까지
    거친 클라이언트를 돌려준다(test_auth_elevate.py와 같은 패턴)."""
    import auth as _auth
    import port_proxy
    import port_share_store

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7960")

    # 리스너 자체는 test_port_proxy_listener.py가 본다 — 여기선 라우터만 본다.
    async def _start(share):
        return True

    async def _stop(port):
        return None

    monkeypatch.setattr(port_proxy, "start", _start)
    monkeypatch.setattr(port_proxy, "stop", _stop)

    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": "s3cret-pw"}).status_code == 200
        assert c.post("/api/auth/elevate", json={"password": "s3cret-pw"}).status_code == 200
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


def test_expose_requires_elevation(client, monkeypatch):
    """승격 없이는 포트를 외부에 열 수 없다 — 2.2의 새 계약."""
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    r = client.post("/api/ports/3000/expose", json={"confirm": True})
    assert r.status_code in (401, 403)
    assert r.json()["error"] in ("elevation_required", "password_required")


def test_unexpose_requires_elevation(client):
    r = client.delete("/api/ports/3000/expose")
    assert r.status_code in (401, 403)


@pytest.mark.parametrize("port", [0, 70000])
def test_expose_rejects_out_of_range_port(elevated, port):
    r = elevated.post(f"/api/ports/{port}/expose", json={"confirm": True})
    assert r.status_code == 400


def test_expose_requires_explicit_confirm(elevated):
    r = elevated.post("/api/ports/3000/expose", json={})
    assert r.status_code == 428
    assert r.json()["error"] == "confirm_required"


def test_expose_rejected_when_network_mode_restricted(client, monkeypatch):
    # NetworkAccessMiddleware도 VT_NETWORK_MODE를 보므로, 라우트 자체의 판정(409)에
    # 도달하려면 미들웨어의 IP 필터는 통과시켜 둬야 한다(라우터 레이어만 검증하는 게 목적).
    # 비밀번호 미설정 + network_mode != all 이면 require_elevated는 그냥 통과한다.
    monkeypatch.setattr(network_access, "get_current_spec", lambda: network_access.AccessSpec(networks=[], allow_all=True))
    monkeypatch.setenv("VT_NETWORK_MODE", "lan")
    r = client.post("/api/ports/3000/expose", json={"confirm": True})
    assert r.status_code == 409
    assert r.json()["error"] == "network_mode"


def test_expose_success_parses_url(elevated, monkeypatch):
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (0, "tunnel up at https://example.trycloudflare.com\n"))
    r = elevated.post("/api/ports/3000/expose", json={"confirm": True, "label": "dev"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == "https://example.trycloudflare.com"
    assert body["mode"] == "session"  # ADR-32 — 기본은 로그인 기기만


def test_expose_failure_returns_500(elevated, monkeypatch):
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (1, "cloudflared not found"))
    r = elevated.post("/api/ports/3000/expose", json={"confirm": True})
    assert r.status_code == 500
    assert r.json()["error"] == "expose_failed"


# --- DELETE /api/ports/{port}/expose -------------------------------------------


def test_unexpose_success(elevated, monkeypatch):
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (0, "unexposed"))
    r = elevated.delete("/api/ports/3000/expose")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unexpose_failure_returns_500(elevated, monkeypatch):
    monkeypatch.setattr(ports_mod, "_run_vt", lambda *a: (1, "no tunnel running"))
    r = elevated.delete("/api/ports/3000/expose")
    assert r.status_code == 500
    assert r.json()["error"] == "unexpose_failed"


# --- GET /api/tunnel/list (N22) ------------------------------------------------


def test_tunnel_list_empty_when_nothing_running(client, monkeypatch):
    monkeypatch.setattr(ports_mod.tunnel, "get_tunnel_status", lambda: {
        "running": False, "url": None, "mode": "anonymous",
    })
    monkeypatch.setattr(ports_mod.tunnel_registry, "exposed_ports", lambda: {})
    r = client.get("/api/tunnel/list")
    assert r.status_code == 200
    body = r.json()
    assert body == {"main": None, "extra": [], "count": 0}


def test_tunnel_list_includes_main_and_extra(client, monkeypatch):
    monkeypatch.setenv("VT_PORT", "7777")
    monkeypatch.setattr(ports_mod.tunnel, "get_tunnel_status", lambda: {
        "running": True, "url": "https://main.trycloudflare.com", "mode": "named",
    })
    monkeypatch.setattr(ports_mod.tunnel_registry, "exposed_ports", lambda: {
        3000: {"url": "https://example-app.trycloudflare.com", "label": "RAPA 앱"},
    })
    r = client.get("/api/tunnel/list")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["main"] == {
        "port": 7777, "label": "터미널 (VT)",
        "url": "https://main.trycloudflare.com", "named": True,
    }
    assert body["extra"] == [
        {"port": 3000, "label": "RAPA 앱", "url": "https://example-app.trycloudflare.com", "named": False},
    ]


def test_tunnel_list_main_absent_when_no_url(client, monkeypatch):
    monkeypatch.setattr(ports_mod.tunnel, "get_tunnel_status", lambda: {
        "running": True, "url": None, "mode": "anonymous",
    })
    monkeypatch.setattr(ports_mod.tunnel_registry, "exposed_ports", lambda: {})
    r = client.get("/api/tunnel/list")
    assert r.json()["main"] is None
