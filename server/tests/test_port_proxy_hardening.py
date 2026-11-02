"""2026-10 — 노출 포트 보안 점검에서 고친 것들의 회귀 테스트.

1. 클라이언트가 보낸 프록시 헤더(X-Forwarded-For 등)를 업스트림에 넘기지 않는다.
2. PIN 재발급·모드 변경이 기존 통과자의 쿠키를 죽인다(shareId 교체).
3. 만료된 기기의 포트 쿠키·티켓이 통하지 않는다.
4. 열린 WS가 자격 변화 뒤에도 살아 있지 않다(재검사 판정).
5. session 모드는 쿠키 없는 방문자에게 메인 주소를 드러내지 않는다(test_port_proxy_auth).
6. PIN: 본문 크기 제한 · next 유지 · 페이지 보안 헤더 · 입력 상태 표시 ·
   실패 누적/로그 · scrypt 동시 실행 상한 · 잠금 키는 XFF 맨 오른쪽.
7. 위험 포트는 확인 없이는 노출하지 않는다(웹·CLI 같은 판정).
8. 만료된 노출은 터널·리스너·정책까지 내린다.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

HOUR = 3600


class _Echo(BaseHTTPRequestHandler):
    def _respond(self):
        body = json.dumps({"path": self.path,
                           "headers": [[k.lower(), v] for k, v in self.headers.items()]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _respond

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7800")
    import auth
    import port_share_store
    import routes.port_proxy_app as papp
    importlib.reload(port_share_store)
    importlib.reload(papp)
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "0123456789abcdef", raising=False)
    papp._shares.clear()
    papp._pin_lockout._failures.clear()

    class Env:
        store = port_share_store
        app_mod = papp
        port = upstream
        auth_mod = auth

        def client(self):
            return TestClient(papp.build_proxy_app(upstream))

    return Env()


def _headers_seen(resp):
    return resp.json()["headers"]


def _values(headers, name):
    return [v for k, v in headers if k == name]


# --- 1. 프록시 헤더 -------------------------------------------------------------


def test_client_supplied_forwarding_headers_are_not_passed_upstream(env):
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.get("/", headers={
            "x-forwarded-for": "127.0.0.1",
            "x-real-ip": "127.0.0.1",
            "forwarded": "for=127.0.0.1",
            "x-forwarded-host": "localhost",
            "true-client-ip": "127.0.0.1",
            "cf-connecting-ip": "203.0.113.9",
            "cf-ray": "abc",
        })
    h = _headers_seen(r)
    assert _values(h, "x-forwarded-for") == ["203.0.113.9"], "맨 앞에 클라이언트 값이 오면 안 된다"
    for name in ("x-real-ip", "forwarded", "true-client-ip", "cf-ray", "cf-connecting-ip"):
        assert _values(h, name) == [], name
    assert len(_values(h, "x-forwarded-host")) == 1


def test_without_cf_header_client_xff_leftmost_is_not_trusted(env):
    """ngrok처럼 CF 헤더가 없는 경로 — 우리 바로 앞 홉(맨 오른쪽)만 쓴다."""
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.get("/", headers={"x-forwarded-for": "127.0.0.1, 198.51.100.4"})
    assert _values(_headers_seen(r), "x-forwarded-for") == ["198.51.100.4"]


# --- 2. 자격 교체 → 기존 쿠키 무효 --------------------------------------------------


def test_reissued_pin_kills_cookies_from_old_pin(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    old_cookie, _ = env.app_mod.mint_cookie(env.store.get_share(env.port))
    with env.client() as c:
        c.cookies.set("fsh_pshare", old_cookie)
        assert c.get("/").headers["content-type"].startswith("application/json")
    env.store.regenerate_pin(env.port)
    env.app_mod.invalidate_share(env.port)
    with env.client() as c:
        c.cookies.set("fsh_pshare", old_cookie)
        r = c.get("/")
    assert "접근 코드" in r.text, "재발급 뒤에는 PIN을 다시 물어야 한다"


def test_mode_change_kills_cookies_from_previous_mode(env, monkeypatch):
    env.store.create_share(env.port, "앱", "session", HOUR)
    monkeypatch.setattr(env.auth_mod, "find_alive_device",
                        lambda did: {"id": did, "expires_at": time.time() + HOUR})
    cookie, _ = env.app_mod.mint_cookie(env.store.get_share(env.port), "dev1")
    env.store.set_mode(env.port, "pin")
    env.store.regenerate_pin(env.port)
    env.app_mod.invalidate_share(env.port)
    with env.client() as c:
        c.cookies.set("fsh_pshare", cookie)
        assert "접근 코드" in c.get("/").text


# --- 3. 기기 만료 ---------------------------------------------------------------


def test_cookie_of_expired_device_is_rejected(env, tmp_path, monkeypatch):
    auth = env.auth_mod
    monkeypatch.setattr(auth, "STATE_DIR", tmp_path / "vt")
    monkeypatch.setattr(auth, "DEVICES_PATH", tmp_path / "vt" / "devices.json")
    _, did = auth.register_device("Mac", ttl=60)
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    cookie, ttl = env.app_mod.mint_cookie(share, did)
    assert 0 < ttl <= 60, "쿠키 수명은 기기 남은 수명을 넘지 않는다"
    assert env.app_mod.verify_cookie(cookie, share) is True
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 61)
    assert env.app_mod.verify_cookie(cookie, share) is False


def test_callback_rejects_ticket_whose_device_expired(env, monkeypatch):
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    monkeypatch.setattr(env.auth_mod, "consume_ticket",
                        lambda t, purpose: {"share": share["shareId"], "device": "dev1"})
    monkeypatch.setattr(env.auth_mod, "find_alive_device", lambda did: None)
    with env.client() as c:
        r = c.get("/__fsh/cb?t=x", follow_redirects=False)
    assert r.status_code == 401 and r.json()["error"] == "device_expired"


# --- 4. WS 재검사 판정 ------------------------------------------------------------


def test_ws_recheck_detects_rotation_removal_and_mode_change(env, monkeypatch):
    still = env.app_mod._ws_still_admitted
    share, _ = env.store.create_share(env.port, "앱", "pin", HOUR)
    cookie, _ = env.app_mod.mint_cookie(share)
    admitted = (share["mode"], share["shareId"])
    assert asyncio.run(still(env.port, admitted, cookie)) is True

    env.store.regenerate_pin(env.port)
    env.app_mod.invalidate_share(env.port)
    assert asyncio.run(still(env.port, admitted, cookie)) is False

    pub, _ = env.store.create_share(env.port, "앱", "public", HOUR)
    env.app_mod.invalidate_share(env.port)
    admitted = (pub["mode"], pub["shareId"])
    assert asyncio.run(still(env.port, admitted, "")) is True
    env.store.set_mode(env.port, "pin")
    env.app_mod.invalidate_share(env.port)
    assert asyncio.run(still(env.port, admitted, "")) is False

    env.store.remove_share(env.port)
    env.app_mod.invalidate_share(env.port)
    assert asyncio.run(still(env.port, admitted, "")) is False


def test_ws_recheck_detects_expired_device(env, monkeypatch):
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    monkeypatch.setattr(env.auth_mod, "find_alive_device",
                        lambda did: {"id": did, "expires_at": time.time() + HOUR})
    cookie, _ = env.app_mod.mint_cookie(share, "dev1")
    admitted = (share["mode"], share["shareId"])
    assert asyncio.run(env.app_mod._ws_still_admitted(env.port, admitted, cookie)) is True
    monkeypatch.setattr(env.auth_mod, "find_alive_device", lambda did: None)
    assert asyncio.run(env.app_mod._ws_still_admitted(env.port, admitted, cookie)) is False


# --- 6. PIN ---------------------------------------------------------------------


def test_pin_page_has_security_headers_next_field_and_input_state(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.get("/deep/page?q=1")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert 'name="next" value="/deep/page?q=1"' in r.text
    assert "compositionupdate" in r.text and 'id="st"' in r.text


def test_pin_success_returns_to_original_path(env):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin", data={"pin": pin, "next": "/deep/page?q=1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/deep/page?q=1"


@pytest.mark.parametrize("evil", ["//evil.example/x", "https://evil.example/", "javascript:alert(1)"])
def test_pin_next_field_cannot_redirect_offsite(env, evil):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin", data={"pin": pin, "next": evil}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_pin_next_is_html_escaped(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.get('/x"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in r.text


def test_pin_body_over_limit_is_413_without_counting(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin", content=b"pin=" + b"A" * 5000,
                   headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert env.store.get_share(env.port)["attempts"] == 0


def test_pin_failures_are_counted_and_logged_but_share_survives(env, caplog):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    caplog.set_level(logging.WARNING)
    with env.client() as c:
        for _ in range(3):
            c.post("/__fsh/pin", data={"pin": "WRONG000"}, headers={"cf-connecting-ip": "203.0.113.7"})
    share = env.store.get_share(env.port)
    assert share is not None and share["attempts"] == 3
    assert any("PIN 실패" in m and "203.0.113.7" in m for m in caplog.messages)


def test_pin_lockout_is_60s_per_ip_and_logged(env, caplog):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    caplog.set_level(logging.WARNING)
    with env.client() as c:
        for _ in range(env.app_mod.PIN_MAX_FAILS):
            c.post("/__fsh/pin", data={"pin": "WRONG000"}, headers={"cf-connecting-ip": "203.0.113.8"})
        r = c.post("/__fsh/pin", data={"pin": "WRONG000"}, headers={"cf-connecting-ip": "203.0.113.8"})
    assert r.status_code == 429
    assert env.app_mod.PIN_LOCK_SEC == 60
    assert any("60초 차단" in m for m in caplog.messages)


def test_pin_lockout_cannot_be_dodged_by_rotating_leftmost_xff(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        for i in range(env.app_mod.PIN_MAX_FAILS):
            c.post("/__fsh/pin", data={"pin": "WRONG000"},
                   headers={"x-forwarded-for": f"10.0.0.{i}, 198.51.100.20"})
        r = c.post("/__fsh/pin", data={"pin": "WRONG000"},
                   headers={"x-forwarded-for": "10.9.9.9, 198.51.100.20"})
    assert r.status_code == 429


def test_pin_verification_is_capped_when_slots_are_busy(env):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)

    async def _run():
        slots = env.app_mod._pin_slots
        for _ in range(env.app_mod._PIN_VERIFY_SLOTS):
            await slots.acquire()
        try:
            import httpx
            transport = httpx.ASGITransport(app=env.app_mod.build_proxy_app(env.port))
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.post("/__fsh/pin", data={"pin": pin})
        finally:
            for _ in range(env.app_mod._PIN_VERIFY_SLOTS):
                slots.release()

    r = asyncio.run(_run())
    assert r.status_code == 429
    assert env.store.get_share(env.port)["attempts"] == 0, "검증하지 않았으니 실패로 세지 않는다"


# --- 7. 위험 포트 ----------------------------------------------------------------


@pytest.mark.parametrize("port", [9222, 9229, 2375, 2376, 4040, 4043, 20241, 20245, 7800, 7899])
def test_danger_reason_flags_remote_control_ports(env, port):
    assert env.store.danger_reason(port)


@pytest.mark.parametrize("port", [3000, 5173, 8080, 4044, 20246, 7900])
def test_danger_reason_leaves_ordinary_ports_alone(env, port):
    assert env.store.danger_reason(port) is None


def test_cli_danger_exit_codes(env, capsys):
    assert env.store._cli(["cli-danger", "9222"]) == 0
    assert "Chrome" in capsys.readouterr().out
    assert env.store._cli(["cli-danger", "3000"]) == 1
    assert capsys.readouterr().out == ""


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
    for m in (port_share_store, papp, port_proxy, ports):
        importlib.reload(m)
    papp._shares.clear()

    calls = {"stop": [], "vt": []}

    async def _start(*a, **kw):
        return True

    async def _stop(port):
        calls["stop"].append(port)

    def _vt(*a):
        calls["vt"].append(a)
        return 0, "  ✓ 앱 → https://abc.trycloudflare.com"

    monkeypatch.setattr(port_proxy, "start", _start)
    monkeypatch.setattr(port_proxy, "stop", _stop)
    monkeypatch.setattr(ports, "_run_vt", _vt)
    ports.calls = calls
    return ports


def _status(resp):
    return getattr(resp, "status_code", 200)


def test_web_expose_of_dangerous_port_needs_explicit_force(routes):
    r = asyncio.run(routes.expose_port(9222, _Body({"confirm": True, "mode": "pin"})))
    assert _status(r) == 409
    body = json.loads(r.body)
    assert body["error"] == "dangerous_port" and "Chrome" in body["reason"]
    assert routes.calls["vt"] == [], "확인 전에는 터널을 띄우지 않는다"

    for falsy in ("true", 1, "yes"):
        r = asyncio.run(routes.expose_port(9222, _Body({"confirm": True, "mode": "pin", "force_danger": falsy})))
        assert _status(r) == 409, f"force_danger={falsy!r}는 확인으로 치지 않는다"

    r = asyncio.run(routes.expose_port(9222, _Body({"confirm": True, "mode": "pin", "force_danger": True})))
    assert _status(r) == 200 and r["ok"] is True


def test_web_expose_of_ordinary_port_needs_no_force(routes):
    r = asyncio.run(routes.expose_port(3000, _Body({"confirm": True, "mode": "pin"})))
    assert _status(r) == 200


# --- 8. 만료 정리 ----------------------------------------------------------------


def test_sweep_takes_down_expired_share_tunnel_and_listener(routes):
    store = routes.pshare
    store.create_share(3000, "앱", "pin", HOUR)
    store.create_share(4000, "다른 앱", "public", HOUR)
    store.update_share(3000, exp=time.time() - 1)

    done = asyncio.run(routes.sweep_expired_shares())
    assert done == [3000]
    assert routes.calls["stop"] == [3000]
    assert ("tunnel", "unexpose", "3000") in routes.calls["vt"]
    assert store.list_shares(include_expired=True)[0]["port"] == 4000, "만료 안 된 노출은 그대로"


def test_sweep_removes_policy_even_when_cli_fails(routes, monkeypatch):
    store = routes.pshare
    store.create_share(3000, "앱", "pin", HOUR)
    store.update_share(3000, exp=time.time() - 1)
    monkeypatch.setattr(routes, "_run_vt", lambda *a: (127, "fsh CLI를 찾을 수 없습니다"))
    assert asyncio.run(routes.sweep_expired_shares()) == [3000]
    assert store.list_shares(include_expired=True) == []


def test_sweep_leaves_a_freshly_reexposed_port_alone(routes, monkeypatch):
    """만료 목록을 읽은 뒤 같은 포트가 새로 노출되면(새 shareId) 그쪽은 건드리지 않는다."""
    store = routes.pshare
    store.create_share(3000, "앱", "pin", HOUR)
    store.update_share(3000, exp=time.time() - 1)
    stale = store.expired_shares()
    store.create_share(3000, "앱", "pin", HOUR)  # 재노출
    monkeypatch.setattr(store, "expired_shares", lambda: stale)
    assert asyncio.run(routes.sweep_expired_shares()) == []
    assert routes.calls["vt"] == []
    assert store.get_share(3000) is not None
