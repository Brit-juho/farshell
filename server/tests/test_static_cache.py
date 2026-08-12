"""회귀: 앱 코드(js/css)는 매번 재검증돼야 한다.

예전엔 StaticFiles 가 ETag/Last-Modified 만 보내고 Cache-Control 이 없었다.
Cache-Control 이 없으면 브라우저는 **휴리스틱 캐싱**(Last-Modified 경과 시간의 10%)으로
마음대로 캐시한다 → 코드를 고치고 새로고침해도 옛 js 가 계속 돈다.
실제로 물렸다: terminal.js 를 고쳤는데 브라우저가 51KB 짜리 구버전을 들고 있었다.

sw.js 의 network-first 가 평소엔 가려주지만, SW 가 활성화되기 전이나 SW 가 없는
상황(http 접속 등)에서는 그대로 노출된다.

vendor/* 는 immutable 전제(파일명 고정 + sw.js 캐시 키 bump 로 관리)라 예외다 —
여기에 no-cache 를 붙이면 SWR 캐시 이득이 사라진다.
"""

import pytest
from starlette.testclient import TestClient

import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.mark.parametrize("path", [
    "/static/js/terminal.js",
    "/static/js/grid.js",
    "/static/css/app.css",
    "/static/voice.js",
])
def test_app_code_is_revalidated(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert r.headers.get("cache-control") == "no-cache", (
        f"{path} 에 Cache-Control 이 없으면 브라우저가 옛 코드를 계속 쓴다")


def test_vendor_is_not_forced_to_revalidate(client):
    """vendor 는 immutable — no-cache 를 붙이면 SWR 캐시 이득이 사라진다."""
    r = client.get("/static/vendor/xterm.min.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") != "no-cache"


def test_etag_still_present_for_304(client):
    """no-cache 는 '캐시하되 재검증' — ETag 가 있어야 304 로 싸게 끝난다."""
    r = client.get("/static/js/terminal.js")
    etag = r.headers.get("etag")
    assert etag
    r2 = client.get("/static/js/terminal.js", headers={"If-None-Match": etag})
    assert r2.status_code == 304
