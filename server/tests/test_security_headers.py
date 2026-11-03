"""S10: 메인 앱 보안 헤더 — 클릭재킹(frame-ancestors)·nosniff·리퍼러·권한 정책, 그리고 공유 PIN 폼이 계속 되는지."""
import pytest
from starlette.testclient import TestClient

import main

EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "referrer-policy": "same-origin",
}


@pytest.fixture
def client():
    with TestClient(main.app, base_url="http://127.0.0.1:7777") as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/api/auth/status", "/static/js/main.js", "/no-such-route"])
def test_every_kind_of_response_carries_the_headers(client, path):
    r = client.get(path)
    for k, v in EXPECTED.items():
        assert r.headers.get(k) == v, (path, k, r.headers.get(k))
    assert "camera=()" in r.headers["permissions-policy"]
    assert "microphone" not in r.headers["permissions-policy"]  # 음성 모드가 쓴다


def test_refusals_from_the_guards_carry_them_too():
    c = TestClient(main.app, base_url="http://rebind.evil:7777")
    r = c.get("/api/auth/status")
    assert r.status_code == 403 and r.headers["x-frame-options"] == "DENY"
    c2 = TestClient(main.app, base_url="http://127.0.0.1:7777")
    r = c2.get("/api/auth/status", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_a_header_the_route_already_set_is_kept():
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from security_headers import SecurityHeadersMiddleware
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    def x():
        return PlainTextResponse("ok", headers={"Content-Security-Policy": "sandbox", "X-Frame-Options": "SAMEORIGIN"})

    r = TestClient(app).get("/x")
    assert r.headers["content-security-policy"] == "sandbox" and r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert list(r.headers.get_list("content-security-policy")) == ["sandbox"]


def test_websocket_upgrade_is_untouched():
    from security_headers import SecurityHeadersMiddleware
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    import asyncio
    asyncio.run(SecurityHeadersMiddleware(inner)({"type": "websocket"}, None, None))
    assert seen == ["websocket"]


def test_referrer_policy_is_not_no_referrer():
    """`no-referrer` 문서의 폼 POST 는 `Origin: null`을 보내 OriginGuard 가 거부한다(security_headers.py 설명)."""
    import security_headers
    assert (b"referrer-policy", b"same-origin") in security_headers._HEADERS
    assert all(v != b"no-referrer" for _, v in security_headers._HEADERS)
