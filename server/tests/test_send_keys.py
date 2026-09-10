"""N38(70-mobile.md §2) — POST /api/sessions/{id}/keys.

모바일 플릿 홈의 인라인 승인 버튼이 부르는 신규 엔드포인트. 세 가지를
지켜야 한다: 존재하지 않는 세션은 404 · 실제로 PTY에 텍스트가 들어감 ·
TokenAuthMiddleware를 그대로 상속(별도 예외 경로를 만들지 않았다).
"""

import pytest
from starlette.testclient import TestClient

import auth
import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _make_session(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    return r.json()["id"]


def test_unknown_session_is_404(client):
    r = client.post("/api/sessions/does-not-exist/keys", json={"text": "1\r"})
    assert r.status_code == 404


def test_missing_text_is_400(client):
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/keys", json={})
        assert r.status_code == 400
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_writes_text_into_pty(client):
    """실제로 셸에 도달하는지 — echo 왕복으로 확인(E2E 레시피와 같은 방식)."""
    sid = _make_session(client)
    try:
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "echo vt38ok\r"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_inherits_token_auth_middleware(client, monkeypatch):
    """새 경로를 TokenAuthMiddleware의 예외 목록('/', '/api/auth' 등)에 추가하지
    않았는지 — 추가돼 있었다면 인증 없이 통과해 401이 아니라 200/404가 온다."""
    sid = _make_session(client)
    try:
        monkeypatch.setattr(auth, "is_protected", lambda: True)
        r = client.post(f"/api/sessions/{sid}/keys", json={"text": "x"})
        assert r.status_code == 401
    finally:
        monkeypatch.setattr(auth, "is_protected", lambda: False)
        client.delete(f"/api/sessions/{sid}")
