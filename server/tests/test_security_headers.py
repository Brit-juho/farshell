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


@pytest.mark.parametrize("path", ["/api/auth/status", "/static/js/main.js", "/no-such-route"])
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


# ── 앱 문서 전체 CSP(script-src 'self') ────────────────────────────────────────────────────────────

def _directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        toks = part.split()
        if toks:
            out[toks[0]] = toks[1:]
    return out


def test_app_document_carries_the_full_csp_once(client):
    import security_headers
    r = client.get("/")
    assert r.headers.get_list("content-security-policy") == [security_headers.APP_CSP]
    # 나머지 보안 헤더는 그대로
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_app_csp_string_contract():
    """정책 문자열 계약 — 약해지는 변경(unsafe-inline/eval·와일드카드·blob 스크립트)을 막는다."""
    import security_headers
    d = _directives(security_headers.APP_CSP)
    assert d["script-src"] == ["'self'"]
    for bad in ("'unsafe-inline'", "'unsafe-eval'", "'wasm-unsafe-eval'", "*", "data:", "blob:", "http:", "https:"):
        assert bad not in d["script-src"] and bad not in d["worker-src"] and bad not in d["connect-src"]
    assert not any(t.startswith("'nonce-") for t in d["script-src"])
    assert d["default-src"] == ["'self'"]
    assert d["connect-src"] == ["'self'"] and d["worker-src"] == ["'self'"]
    assert d["object-src"] == ["'none'"] and d["base-uri"] == ["'self'"] and d["form-action"] == ["'self'"]
    assert d["frame-ancestors"] == ["'none'"]                    # 기존 클릭재킹 방어 유지
    assert "'unsafe-inline'" in d["style-src"]                   # 스타일만 인라인 허용(로그인 게이트 <style>)
    assert "blob:" in d["img-src"] and "data:" in d["img-src"]   # 뷰어 이미지·TTS 오디오
    assert "blob:" in d["media-src"]
    assert d["frame-src"] == ["'self'", "http:", "https:"]       # 포트 미리보기 iframe


def test_only_the_app_document_gets_the_script_src_policy(client):
    """정적 파일·API 는 frame-ancestors 만 — 전체 정책은 문서(`/`)에만. relay 응답은 relay_policy 가 따로 정한다."""
    for path in ("/static/js/main.js", "/api/auth/status", "/manifest.json"):
        assert client.get(path).headers["content-security-policy"] == "frame-ancestors 'none'", path


def test_app_document_has_no_inline_script_or_handlers(client):
    """script-src 'self' 와 모순되지 않는다 — 인라인 <script>·on*= 핸들러·javascript: URL 이 없다(en 렌더 포함)."""
    import re
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.bad = []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "script" and "src" not in a:
                self.bad.append("inline <script>")
            for k, v in attrs:
                if k.startswith("on"):
                    self.bad.append(f"{tag} {k}=")
                if (v or "").strip().lower().startswith("javascript:"):
                    self.bad.append(f"{tag} {k}=javascript:")

    for lang_html in (client.get("/").text,):
        p = P()
        p.feed(lang_html)
        assert not p.bad, p.bad
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", lang_html)


def test_index_scripts_are_same_origin_files(client):
    import re
    srcs = re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", client.get("/").text)
    assert "/static/js/theme-boot.js" in srcs and "/static/js/e2e-boot.js" in srcs
    assert all(s.startswith("/static/") for s in srcs), srcs
