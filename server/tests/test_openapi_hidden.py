"""P2-3(ADR-38) — Swagger UI(/docs)·ReDoc(/redoc)·원문 스펙(/openapi.json)을
끈다. FarShell은 공개 터널 뒤에 있고, 전체 API 표면을 익명에게 그려주는 건
접근 제어 사상(ADR-30~32 계열)과 어긋난다.

스펙 자체는 `scripts/check_docs.py`의 `app_openapi()`가 프로세스 안에서
`app.openapi()`를 호출해 CI에서만 꺼낸다 — 엔드포인트는 필요 없다.
"""

import pytest
from starlette.testclient import TestClient

import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_openapi_endpoints_are_off(client, path):
    r = client.get(path)
    assert r.status_code == 404
