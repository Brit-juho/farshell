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

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import main

# `dist/`는 커밋하지 않는다(ADR-2) — Vite가 만든다. 그래서 이 파일의 두
# 파라미터(`/static/dist/app.{js,css}`)는 **빌드를 돌린 환경에서만** 의미가
# 있다. CI의 server 잡은 node를 쓰지 않으므로 항상 404였고, 그 두 건이
# 2026-09-19까지 CI에서 조용히 빨간 상태로 있었다(잡이 러너째 죽는 문제에
# 가려 아무도 못 봤다). 산출물 자체의 존재·형태는 `frontend` 잡의 「산출물
# 형태 검증」이 이미 게이트로 잡고 있으므로, 여기서는 빌드가 없으면 건너뛴다 —
# 이 파일이 지키려는 것은 "산출물이 있는가"가 아니라 "Cache-Control이 붙는가"다.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_needs_build = pytest.mark.skipif(
    not (_DIST / "app.js").exists(),
    reason="frontend/dist/ 없음 — `npm run build` 뒤에만 의미가 있는 경로다",
)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.mark.parametrize("path", [
    # F4: terminal.js는 frontend/js/term/ 아래로, grid.js는 frontend/js/agent/ 아래로,
    # voice.js(최상위 파일)는 frontend/js/voice/ 아래로 쪼개졌다(session.js/preview.js/
    # index.js가 각각의 후신 중 하나) — voice.js는 이제 js/ 규칙에 그대로 포함된다.
    "/static/js/term/session.js",
    "/static/js/agent/preview.js",
    "/static/js/voice/index.js",
    # F1(Vite/Tailwind 도입) — frontend/css/app.css 는 폐기되고 frontend/dist/app.{js,css}
    # 로 대체됐다. 옛날 css/app.css 가 겪었던 것과 똑같은 브라우저 고정 캐싱 사고가
    # 빌드 산출물에서도 재현될 수 있어 같은 회귀 테스트로 묶는다.
    pytest.param("/static/dist/app.js", marks=_needs_build),
    pytest.param("/static/dist/app.css", marks=_needs_build),
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
    r = client.get("/static/js/term/session.js")
    etag = r.headers.get("etag")
    assert etag
    r2 = client.get("/static/js/term/session.js", headers={"If-None-Match": etag})
    assert r2.status_code == 304
