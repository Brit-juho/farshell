"""2.2 — 노출 포트 프록시의 인증 게이트.

모드별 게이트 · fail-closed · `__Host-` 쿠키 · 기기 폐기 연동 · PIN 잠금 ·
우리 쿠키가 업스트림에 새지 않는지 · **프록시 리스너에 FarShell 라우트가 없는지**를
확인한다. 마지막 항목이 ADR-31의 요지다.
"""

from __future__ import annotations

import importlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

HOUR = 3600
HTTPS = {"x-forwarded-proto": "https"}


# --- 업스트림 더미 (노출 대상 앱 역할) ---------------------------------------------


class _Echo(BaseHTTPRequestHandler):
    """받은 요청을 그대로 JSON으로 돌려준다 — 무엇이 전달됐는지 확인용."""

    def _respond(self):
        body = json.dumps({
            "path": self.path,
            "method": self.command,
            "headers": {k.lower(): v for k, v in self.headers.items()},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("set-cookie", "upstream=1; Domain=localhost; Path=/")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = _respond

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


# --- 픽스처 --------------------------------------------------------------------


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

        def client(self):
            return TestClient(papp.build_proxy_app(upstream))

    return Env()


def _cookie_of(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.split("=", 1)[0].strip() == name:
            return raw
    return None


# --- ADR-31: 이 소켓에 FarShell 라우트는 없다 ---------------------------------------


def test_app_has_exactly_the_three_routes(env):
    """라우트를 늘리면 이 테스트가 깨진다 — 그게 의도다."""
    app = env.app_mod.build_proxy_app(env.port)
    paths = [(type(r).__name__, r.path) for r in app.routes]
    assert paths == [
        ("Route", "/__fsh/cb"),
        ("Route", "/__fsh/pin"),
        ("WebSocketRoute", "/{path:path}"),
        ("Route", "/{path:path}"),
    ]


def test_farshell_api_paths_are_proxied_not_handled(env):
    """`/api/tmux/*`가 로컬에서 처리되면 안 된다 — 업스트림으로 그대로 나가야 한다."""
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.get("/api/tmux/list")
    assert r.status_code == 200
    assert r.json()["path"] == "/api/tmux/list", "업스트림이 못 봤다면 로컬에서 가로챈 것"


# --- 모드별 게이트 ----------------------------------------------------------------


def test_public_mode_passes_through(env):
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.get("/hello?x=1")
    assert r.status_code == 200
    assert r.json()["path"] == "/hello?x=1"


def test_session_mode_without_cookie_shows_notice_without_main_url(env):
    """2026-10: 쿠키 없는 방문자에게 메인 FarShell 주소를 알려 주지 않는다 — 예전엔
    `/p/authorize`로 리다이렉트해서 앱 링크만 받은 사람도 터미널 주소를 알게 됐다."""
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    with env.client() as c:
        r = c.get("/deep/path", follow_redirects=False)
    assert r.status_code == 403
    assert "location" not in r.headers
    assert "포트 패널" in r.text
    assert "/p/authorize" not in r.text and share["shareId"] not in r.text
    assert r.headers["x-frame-options"] == "DENY"


def test_pin_mode_without_cookie_shows_pin_page(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "접근 코드" in r.text
    assert "form" in r.text


def test_missing_share_is_404(env):
    with env.client() as c:
        r = c.get("/")
    assert r.status_code == 404


def test_expired_share_is_404(env):
    env.store.create_share(env.port, "앱", "public", HOUR)
    env.store.update_share(env.port, exp=time.time() - 1)
    env.app_mod._shares.clear()
    with env.client() as c:
        r = c.get("/")
    assert r.status_code == 404


def test_unknown_mode_is_rejected_not_defaulted_to_public(env):
    """fail-closed의 핵심 — 모르는 값이 공개로 새면 안 된다."""
    env.store.create_share(env.port, "앱", "session", HOUR)
    env.store.update_share(env.port, mode="anyone")
    env.app_mod._shares.clear()
    with env.client() as c:
        r = c.get("/")
    assert r.status_code == 403
    assert r.json()["error"] == "bad_mode"


def test_session_mode_notice_does_not_depend_on_main_tunnel(env):
    """안내 화면은 메인 주소를 쓰지 않으므로 메인 터널이 없어도 닫힌 채로 같은 화면이다."""
    env.store.create_share(env.port, "앱", "session", HOUR)
    with env.client() as c:
        r = c.get("/")
    assert r.status_code == 403


# --- PIN 흐름 ------------------------------------------------------------------


def test_pin_submit_sets_cookie_and_then_passes(env):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin?next=/after", data={"pin": pin}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/after"
        assert _cookie_of(r, "fsh_pshare")
        # 쿠키를 들고 있으면 이제 통과한다
        r2 = c.get("/ok")
    assert r2.status_code == 200
    assert r2.json()["path"] == "/ok"


def test_pin_accepts_hyphenated_and_lowercase(env):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    shown = env.store.format_pin(pin).lower()
    with env.client() as c:
        r = c.post("/__fsh/pin", data={"pin": shown}, follow_redirects=False)
    assert r.status_code == 303


def test_wrong_pin_is_401_and_share_survives(env):
    """파일 공유와 다른 점 — 봇 하나가 데모를 죽이면 안 된다."""
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin", data={"pin": "WRONGPIN"})
    assert r.status_code == 401
    assert env.store.get_share(env.port) is not None, "share가 지워지면 안 된다"


def test_pin_locks_out_after_max_fails_without_deleting_share(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        for _ in range(env.app_mod.PIN_MAX_FAILS):
            c.post("/__fsh/pin", data={"pin": "WRONGPIN"})
        r = c.post("/__fsh/pin", data={"pin": "WRONGPIN"})
    assert r.status_code == 429
    assert env.store.get_share(env.port) is not None


def test_pin_lockout_is_per_client_ip(env):
    """전역 잠금이면 봇 하나가 모두를 막는다."""
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        for _ in range(env.app_mod.PIN_MAX_FAILS + 1):
            c.post("/__fsh/pin", data={"pin": "X"}, headers={"cf-connecting-ip": "9.9.9.9"})
        r = c.post("/__fsh/pin", data={"pin": pin},
                   headers={"cf-connecting-ip": "1.1.1.1"}, follow_redirects=False)
    assert r.status_code == 303, "다른 IP는 잠기면 안 된다"


def test_pin_endpoint_rejects_non_pin_share(env):
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin", data={"pin": "X"})
    assert r.status_code == 400


# --- 쿠키 (§2-1) ---------------------------------------------------------------


def test_cookie_uses_host_prefix_over_https(env):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    with env.client() as c:
        r = c.post("/__fsh/pin", data={"pin": pin}, headers=HTTPS, follow_redirects=False)
    raw = _cookie_of(r, "__Host-fsh_pshare")
    assert raw is not None, "형제 도메인의 쿠키 심기를 막으려면 __Host- 가 필요하다"
    assert "Secure" in raw
    assert "Path=/" in raw
    assert "Domain=" not in raw


def test_plain_cookie_is_not_accepted_over_https(env):
    """https에서 접두사 없는 이름을 받아주면 형제 도메인 우회가 되살아난다."""
    share, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    value, _ = env.app_mod.mint_cookie(env.store.get_share(env.port))
    with env.client() as c:
        c.cookies.set("fsh_pshare", value)
        r = c.get("/", headers=HTTPS)
    assert r.status_code == 200 and "접근 코드" in r.text


def test_cookie_of_other_port_is_rejected(env):
    share = env.store.create_share(env.port, "앱", "pin", HOUR)[0]
    other, _ = env.store.create_share(env.port + 1, "다른앱", "pin", HOUR)
    value, _ = env.app_mod.mint_cookie(other)
    with env.client() as c:
        c.cookies.set("fsh_pshare", value)
        r = c.get("/")
    assert "접근 코드" in r.text, "다른 포트의 쿠키가 먹으면 안 된다"


def test_cookie_dies_when_share_is_reissued(env):
    """껐다 켜면 새 shareId — 옛 쿠키는 죽어야 한다."""
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    value, _ = env.app_mod.mint_cookie(env.store.get_share(env.port))
    env.store.remove_share(env.port)
    env.store.create_share(env.port, "앱", "pin", HOUR)
    env.app_mod._shares.clear()
    with env.client() as c:
        c.cookies.set("fsh_pshare", value)
        r = c.get("/")
    assert "접근 코드" in r.text


def test_tampered_cookie_is_rejected(env):
    env.store.create_share(env.port, "앱", "pin", HOUR)
    value, _ = env.app_mod.mint_cookie(env.store.get_share(env.port))
    bad = value[:-4] + "0000"
    with env.client() as c:
        c.cookies.set("fsh_pshare", bad)
        r = c.get("/")
    assert "접근 코드" in r.text


def test_revoked_device_loses_port_access(env, monkeypatch):
    """fsh device revoke가 포트 접근까지 죽이는지 — 폰 분실 시의 요구사항."""
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    import auth
    monkeypatch.setattr(auth, "find_alive_device", lambda did: {"id": did, "expires_at": time.time() + HOUR})
    value, _ = env.app_mod.mint_cookie(env.store.get_share(env.port), "dev123")
    with env.client() as c:
        c.cookies.set("fsh_pshare", value)
        assert c.get("/", follow_redirects=False).status_code == 200
    monkeypatch.setattr(auth, "find_alive_device", lambda did: None)  # 폐기됨(또는 만료)
    with env.client() as c:
        c.cookies.set("fsh_pshare", value)
        assert c.get("/", follow_redirects=False).status_code == 403


# --- 우리 쿠키는 업스트림에 안 간다 -------------------------------------------------


def test_our_cookie_is_not_forwarded_upstream(env):
    _, pin = env.store.create_share(env.port, "앱", "pin", HOUR)
    value, _ = env.app_mod.mint_cookie(env.store.get_share(env.port))
    with env.client() as c:
        c.cookies.set("fsh_pshare", value)
        c.cookies.set("app_own", "keep-me")
        r = c.get("/")
    sent = r.json()["headers"].get("cookie", "")
    assert "fsh_pshare" not in sent, "앱이 우리 인증 쿠키를 읽을 이유가 없다"
    assert "app_own=keep-me" in sent, "앱 자신의 쿠키는 그대로 가야 한다"


def test_upstream_host_header_is_localhost(env):
    """Vite 등의 DNS rebinding 방어에 걸리지 않게."""
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.get("/", headers={"host": "abc.trycloudflare.com"})
    h = r.json()["headers"]
    assert h["host"] == f"localhost:{env.port}"
    assert h["x-forwarded-host"] == "abc.trycloudflare.com"


def test_upstream_set_cookie_domain_is_stripped(env):
    env.store.create_share(env.port, "앱", "public", HOUR)
    with env.client() as c:
        r = c.get("/")
    raw = _cookie_of(r, "upstream")
    assert raw is not None
    assert "Domain=" not in raw, "앱이 상위 도메인에 쿠키를 심으면 안 된다"


# --- 티켓 콜백 (§3) -------------------------------------------------------------


def test_callback_consumes_ticket_and_sets_cookie(env):
    import auth
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    t = auth.issue_ticket(label="port", ttl=60, share=share["shareId"], device="-")
    with env.client() as c:
        r = c.get(f"/__fsh/cb?t={t}&next=/welcome", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/welcome"
    assert _cookie_of(r, "fsh_pshare")


def test_ticket_cannot_be_reused(env):
    import auth
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    t = auth.issue_ticket(label="port", ttl=60, share=share["shareId"], device="-")
    with env.client() as c:
        assert c.get(f"/__fsh/cb?t={t}", follow_redirects=False).status_code == 303
        again = c.get(f"/__fsh/cb?t={t}", follow_redirects=False)
    assert again.status_code == 401


def test_ticket_of_other_share_is_rejected(env):
    import auth
    env.store.create_share(env.port, "앱", "session", HOUR)
    other, _ = env.store.create_share(env.port + 1, "다른앱", "session", HOUR)
    t = auth.issue_ticket(label="port", ttl=60, share=other["shareId"], device="-")
    with env.client() as c:
        r = c.get(f"/__fsh/cb?t={t}", follow_redirects=False)
    assert r.status_code == 401


def test_bad_ticket_is_rejected(env):
    env.store.create_share(env.port, "앱", "session", HOUR)
    with env.client() as c:
        r = c.get("/__fsh/cb?t=nope", follow_redirects=False)
    assert r.status_code == 401


# --- 오픈 리다이렉트 --------------------------------------------------------------


@pytest.mark.parametrize("evil", ["//evil.com", "https://evil.com", "//evil.com/x"])
def test_next_must_be_a_path(env, evil):
    """`//evil.com`은 프로토콜 상대 URL이라 브라우저가 외부로 나간다."""
    import auth
    share, _ = env.store.create_share(env.port, "앱", "session", HOUR)
    t = auth.issue_ticket(label="port", ttl=60, share=share["shareId"], device="-")
    with env.client() as c:
        r = c.get(f"/__fsh/cb?t={t}&next={evil}", follow_redirects=False)
    assert r.headers["location"] == "/"


def testsafe_next_allows_normal_paths(env):
    f = env.app_mod.safe_next
    assert f("/a/b?c=1") == "/a/b?c=1"
    assert f(None) == "/"
    assert f("javascript:alert(1)") == "/"
