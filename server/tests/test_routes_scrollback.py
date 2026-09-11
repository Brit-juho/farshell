"""N13 — GET /api/sessions/{id}/scrollback · GET /api/scrollback/usage 라우터 테스트.

scrollback_persist.py 자체의 파일 I/O·회전·보관 로직은 test_scrollback_persist.py에서
이미 단위 테스트하므로, 여기서는 라우터가 그 결과를 올바른 응답 모양(base64 인코딩,
쿼리 파라미터 전달)으로 내보내는지만 monkeypatch로 확인한다.
"""

import base64

import pytest
from starlette.testclient import TestClient

import main
import routes.pty as pty_mod


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_scrollback_encodes_data_as_base64(client, monkeypatch):
    monkeypatch.setattr(
        pty_mod.scrollback_persist, "read_before",
        lambda sid, before, limit: {"data": b"hello\x00world", "next_before": 42, "total": 100},
    )
    r = client.get("/api/sessions/s1/scrollback")
    assert r.status_code == 200
    body = r.json()
    assert base64.b64decode(body["data_b64"]) == b"hello\x00world"
    assert body["next_before"] == 42
    assert body["total"] == 100


def test_scrollback_passes_before_and_limit_through(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pty_mod.scrollback_persist, "read_before",
        lambda sid, before, limit: seen.update(sid=sid, before=before, limit=limit)
        or {"data": b"", "next_before": None, "total": 0},
    )
    client.get("/api/sessions/abc/scrollback", params={"before": 500, "limit": 2000})
    assert seen == {"sid": "abc", "before": 500, "limit": 2000}


def test_scrollback_clamps_limit_to_1mb(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pty_mod.scrollback_persist, "read_before",
        lambda sid, before, limit: seen.update(limit=limit)
        or {"data": b"", "next_before": None, "total": 0},
    )
    client.get("/api/sessions/abc/scrollback", params={"limit": 999_999_999})
    assert seen["limit"] == 1024 * 1024


def test_usage_reports_enabled_and_bytes(client, monkeypatch):
    monkeypatch.setattr(pty_mod.scrollback_persist, "is_enabled", lambda: True)
    monkeypatch.setattr(pty_mod.scrollback_persist, "disk_usage_bytes", lambda: 12345)
    r = client.get("/api/scrollback/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["bytes"] == 12345
    assert body["retention_days"] == 7
