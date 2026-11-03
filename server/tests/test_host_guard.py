"""S2: Host 허용 목록 — DNS rebinding으로 들어온 요청(Origin == Host == 공격자 이름)을 거절한다."""
import pytest
from starlette.testclient import TestClient

import main
from host_guard import host_allowed


@pytest.mark.parametrize("host", [
    "localhost:7777", "LOCALHOST", "app.localhost:7777", "127.0.0.1:7777", "192.168.0.12:7777",
    "100.101.102.103:7777", "[::1]:7777", "[fd7a:115c:a1e0::1]", "my-mac:7777", "my-mac.local:7777",
    "my-mac.tail1234.ts.net", "random-words.trycloudflare.com", "abc.ngrok-free.app", "testserver",
])
def test_names_attackers_cannot_rebind_are_allowed(host):
    assert host_allowed(host)


@pytest.mark.parametrize("host", [
    "rebind.evil:7777", "127.0.0.1.nip.io:7777", "evil.com", "localhost.evil.com", "trycloudflare.com.evil.com",
    "a b", "evil.com:99999", "[::1", "[::1]x", "::1", "exa_mple.com", "evil.com:7777:1",
])
def test_rebindable_or_malformed_names_are_refused(host):
    assert not host_allowed(host)


def test_missing_host_header_is_non_browser():
    assert host_allowed(None) and host_allowed("")


def test_configured_names(monkeypatch):
    assert not host_allowed("fsh.example.com")
    monkeypatch.setenv("VT_TUNNEL_HOSTNAME", "fsh.example.com")
    monkeypatch.setenv("VT_NGROK_DOMAIN", "mine.example.net")
    monkeypatch.setenv("VT_ALLOWED_HOSTS", "proxy.example.org:8443, *.corp.example")
    monkeypatch.setenv("VT_ALLOWED_ORIGINS", "https://ui.example.io")
    for h in ("fsh.example.com", "mine.example.net", "proxy.example.org", "a.corp.example:7777", "ui.example.io"):
        assert host_allowed(h), h
    assert not host_allowed("corp.example.evil")


def test_rebinding_request_is_refused_before_auth_for_http_and_ws():
    c = TestClient(main.app, base_url="http://rebind.evil:7777")
    r = c.get("/api/auth/status", headers={"Origin": "http://rebind.evil:7777"})
    assert r.status_code == 403 and r.json()["reason"] == "host_not_allowed"
    with pytest.raises(Exception):
        with c.websocket_connect("/ws-workspace", headers={"Origin": "http://rebind.evil:7777"}):
            pass
    ok = TestClient(main.app, base_url="http://127.0.0.1:7777")
    assert ok.get("/api/auth/status").status_code == 200
