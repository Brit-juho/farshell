"""OriginGuardMiddleware 회귀: Origin: null 우회 차단.

리뷰(codex outside-voice pass)에서 발견: `Origin` 헤더가 아예 없는 경우(curl 등
비브라우저 클라이언트)와, 브라우저가 sandboxed iframe·data: URL 등 opaque origin에서
실제로 보내는 문자열 `"null"`을 같은 값으로 취급해 둘 다 통과시키고 있었다.
후자는 브라우저가 보내는 진짜 값이라 크로스 사이트 방지가 뚫리는 우회로였다.
- Origin 헤더 자체가 없음 → 여전히 허용(회귀 방지)
- Origin: null → 403
- Origin: https://evil.com → 여전히 403(회귀 방지)
- Origin: 자기 자신(Host와 일치) → 여전히 허용(회귀 방지)
"""

import pytest
from starlette.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch):
    # NetworkAccessMiddleware(IP 필터)는 기본값(all)이면 통과하므로 origin 판정만 검증된다.
    monkeypatch.delenv("VT_NETWORK_MODE", raising=False)
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    with TestClient(main.app) as c:
        yield c


def test_no_origin_header_is_allowed(client):
    # curl/데몬/훅 등 Origin 헤더 자체가 없는 비브라우저 클라이언트는 통과해야 한다.
    r = client.get("/api/sessions")
    assert r.status_code == 200


def test_origin_null_is_rejected(client):
    # 브라우저의 opaque origin(sandboxed iframe, data: URL 등)이 보내는 실제 값.
    # "헤더 없음"과 다르므로 차단돼야 한다.
    r = client.get("/api/sessions", headers={"Origin": "null"})
    assert r.status_code == 403


def test_cross_origin_is_rejected(client):
    r = client.get("/api/sessions", headers={"Origin": "https://evil.com"})
    assert r.status_code == 403


def test_matching_self_origin_is_allowed(client):
    # TestClient의 기본 base_url은 http://testserver 이므로 Host 헤더도 testserver.
    r = client.get("/api/sessions", headers={"Origin": "http://testserver"})
    assert r.status_code == 200
