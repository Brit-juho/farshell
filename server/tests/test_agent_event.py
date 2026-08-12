"""회귀: POST /api/agent/event 가 본문을 받아야 한다.

예전엔 `async def agent_event(request):` 처럼 타입 annotation이 없어서 FastAPI가
`request` 를 **필수 쿼리 파라미터**로 해석했다. 그래서 agent_hook.sh 가 보내는
pre/post/stop 이벤트가 전부 422로 떨어졌고, 아무도 눈치채지 못했다
(훅은 실패해도 조용하다). 이 위에 P4 자동 드레인이 얹히므로 회귀를 막아둔다.
"""

import pytest
from starlette.testclient import TestClient

import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_agent_event_accepts_json_body(client):
    r = client.post("/api/agent/event",
                    json={"event": "pre", "payload": {"session_id": "t1", "tool_name": "Bash"}})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_agent_event_is_not_a_query_param(client):
    """쿼리 파라미터 'request' 없이도 통과해야 한다(422가 나오면 회귀)."""
    r = client.post("/api/agent/event", json={"event": "stop", "payload": {}})
    assert r.status_code != 422
    assert r.status_code == 200


def test_agent_event_survives_empty_body(client):
    r = client.post("/api/agent/event", content=b"", headers={"Content-Type": "application/json"})
    assert r.status_code == 200


def test_stop_event_reports_queue_scheduling(client):
    r = client.post("/api/agent/event", json={"event": "stop", "payload": {"session_id": "t2"}})
    assert r.status_code == 200
    # 큐가 비어 있으면 예약하지 않는다 — 필드 자체는 항상 있어야 한다.
    assert "queue_scheduled" in r.json()
