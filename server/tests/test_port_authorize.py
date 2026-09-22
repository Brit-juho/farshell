"""2.2 — `/p/authorize` 교차 origin 핸드셰이크 (plan-2.2/00-port-access.md §3 · §9).

오픈 리다이렉트 차단 · 티켓의 share/기기 바인딩 · 미로그인 시 로그인 게이트로
되돌리기를 확인한다. 티켓 소비 쪽(`/__fsh/cb`)은 test_port_proxy_auth.py가 본다.
"""

from __future__ import annotations

import asyncio
import importlib
from urllib.parse import parse_qs, urlsplit

import pytest

HOUR = 3600


class _Req:
    """`/p/authorize`가 쓰는 것은 query_params와 cookies뿐이다."""

    def __init__(self, params: dict, cookies: dict | None = None):
        self.query_params = params
        self.cookies = cookies or {}


@pytest.fixture
def sysmod(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_PORT_PROXY_BASE", "7980")

    import auth
    import port_share_store
    import routes.system as system
    import tunnel_registry
    importlib.reload(port_share_store)

    monkeypatch.setattr(auth, "STATE_DIR", tmp_path)
    monkeypatch.setattr(auth, "TICKETS_PATH", tmp_path / "tickets.json")
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "0123456789abcdef")
    # 기본은 "로그인 + 등록 기기" 상태. 개별 테스트가 뒤집는다.
    monkeypatch.setattr(auth, "verify_device", lambda c: {"id": "dev-1"} if c else None)
    monkeypatch.setattr(auth, "verify_session", lambda c: bool(c))
    monkeypatch.setattr(tunnel_registry, "exposed_ports",
                        lambda: {3000: {"url": "https://app.example", "label": "앱"}})
    return system


def _store():
    import port_share_store
    return port_share_store


AUTHED = {"vt_device": "d", "vt_session": "s"}


def _call(sysmod, params, cookies=None):
    return asyncio.run(sysmod.port_share_authorize(_Req(params, cookies)))


def _loc(resp):
    return resp.headers["location"]


def _status(resp):
    return resp.status_code


# --- 사전 조건 -----------------------------------------------------------------


def test_unknown_share_is_404(sysmod):
    assert _status(_call(sysmod, {"share": "nope"}, AUTHED)) == 404


def test_non_session_share_is_rejected(sysmod):
    """public/pin 모드에는 이 핸드셰이크가 의미 없다."""
    share, _ = _store().create_share(3000, "앱", "pin", HOUR)
    assert _status(_call(sysmod, {"share": share["shareId"]}, AUTHED)) == 400


def test_missing_tunnel_entry_is_404(sysmod, monkeypatch):
    import tunnel_registry
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    monkeypatch.setattr(tunnel_registry, "exposed_ports", lambda: {})
    assert _status(_call(sysmod, {"share": share["shareId"]}, AUTHED)) == 404


# --- 미인증은 로그인 게이트로 -------------------------------------------------------


def test_unauthenticated_goes_to_login_gate(sysmod):
    """최상위 내비게이션이라 JSON 401이 아니라 로그인 화면으로 보내야 한다."""
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    r = _call(sysmod, {"share": share["shareId"], "next": "/x"}, {})
    assert _status(r) == 302
    assert _loc(r).startswith("/?next=")
    assert "%2Fp%2Fauthorize" in _loc(r), "로그인 후 여기로 돌아와야 한다"


def test_session_without_registered_device_goes_to_gate(sysmod, monkeypatch):
    import auth
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    monkeypatch.setattr(auth, "verify_device", lambda c: None)  # 미등록 기기
    r = _call(sysmod, {"share": share["shareId"]}, AUTHED)
    assert _status(r) == 302
    assert _loc(r).startswith("/?next=")


def test_registered_device_without_valid_session_goes_to_gate(sysmod, monkeypatch):
    import auth
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    monkeypatch.setattr(auth, "verify_session", lambda c: False)
    assert _loc(_call(sysmod, {"share": share["shareId"]}, AUTHED)).startswith("/?next=")


# --- 성공 경로 -----------------------------------------------------------------


def test_authorized_redirects_to_share_url_with_ticket(sysmod):
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    r = _call(sysmod, {"share": share["shareId"], "next": "/deep/path"}, AUTHED)
    assert _status(r) == 302
    parts = urlsplit(_loc(r))
    assert f"{parts.scheme}://{parts.netloc}" == "https://app.example"
    assert parts.path == "/__fsh/cb"
    q = parse_qs(parts.query)
    assert q["next"] == ["/deep/path"]
    assert q["t"][0]


def test_ticket_is_bound_to_share_and_device(sysmod):
    import auth
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    r = _call(sysmod, {"share": share["shareId"]}, AUTHED)
    ticket = parse_qs(urlsplit(_loc(r)).query)["t"][0]
    rec = auth.consume_ticket(ticket)
    assert rec["share"] == share["shareId"]
    assert rec["device"] == "dev-1"


def test_ticket_ttl_is_short(sysmod):
    """URL에 실리는 값이라 창을 좁힌다 — 기기 등록 티켓(5분)과 별개."""
    import auth
    import time
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    _call(sysmod, {"share": share["shareId"]}, AUTHED)
    rec = auth._load_tickets()[0]
    assert rec["exp"] - time.time() <= sysmod.PORT_TICKET_TTL + 1
    assert sysmod.PORT_TICKET_TTL < auth.TICKET_TTL


# --- 오픈 리다이렉트: 구조로 막는다 ---------------------------------------------------


def test_route_ignores_any_client_supplied_destination(sysmod):
    """`host`/`url` 같은 목적지 파라미터를 줘도 레지스트리 주소로만 간다."""
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    r = _call(sysmod, {
        "share": share["shareId"],
        "host": "evil.com", "url": "https://evil.com", "redirect": "https://evil.com",
    }, AUTHED)
    assert urlsplit(_loc(r)).netloc == "app.example"


@pytest.mark.parametrize("evil", ["//evil.com", "https://evil.com", "javascript:x", "//evil.com/a"])
def test_next_is_reduced_to_a_path(sysmod, evil):
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    r = _call(sysmod, {"share": share["shareId"], "next": evil}, AUTHED)
    assert parse_qs(urlsplit(_loc(r)).query)["next"] == ["/"]


def test_next_with_ampersand_does_not_split_the_query(sysmod):
    """인코딩을 빠뜨리면 next가 잘리거나 다른 파라미터가 주입된다."""
    share, _ = _store().create_share(3000, "앱", "session", HOUR)
    r = _call(sysmod, {"share": share["shareId"], "next": "/a?b=1&t=injected"}, AUTHED)
    q = parse_qs(urlsplit(_loc(r)).query)
    assert q["next"] == ["/a?b=1&t=injected"]
    assert len(q["t"]) == 1, "티켓 파라미터가 주입으로 늘어났다"


# --- 미들웨어 우회 경로가 열려 있는가 -------------------------------------------------


def test_authorize_path_bypasses_token_middleware():
    """JSON 401이 나면 브라우저가 로그인 화면을 못 본다 — /s/ 와 같은 이유."""
    import main
    import inspect
    src = inspect.getsource(main.TokenAuthMiddleware.__call__)
    assert "/p/authorize" in src
