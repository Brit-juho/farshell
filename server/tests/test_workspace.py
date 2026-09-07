"""routes/system.py의 GET/PUT /api/workspace — L4에서 발견한 회귀 테스트.

`workspace_put`이 `request` 파라미터에 타입 힌트가 없어서 FastAPI가 그걸
(Request 객체가 아니라) 필수 쿼리 파라미터로 해석해 매번 422를 냈다 — 이
라우트를 실제로 호출하는 프런트 코드가 지금까지 하나도 없어서(rail.js가
L4에서 처음) 발견되지 않고 있었다.
"""

import pytest
from starlette.testclient import TestClient

import main
import workspace


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 실제 ~/.config/vt/workspace.json을 건드리지 않도록 격리.
    monkeypatch.setattr(workspace, "WS_PATH", tmp_path / "workspace.json")
    with TestClient(main.app) as c:
        yield c


def test_get_workspace_returns_defaults_when_no_file(client):
    r = client.get("/api/workspace")
    assert r.status_code == 200
    data = r.json()
    assert data["tabs"] == []
    assert data["active"] is None


def test_put_workspace_accepts_json_body(client):
    # 이 assert 하나가 실측 버그의 핵심 — 고치기 전에는 항상 422였다.
    r = client.put("/api/workspace", json={"ui": {"rail": {"open": "session", "width": 320}}})
    assert r.status_code == 200, f"PUT이 실패하면 안 된다 (got {r.status_code}: {r.text})"
    # PUT 응답은 {"ok": True, "data": <merged workspace>} 모양이다(GET과 다름).
    assert r.json()["data"]["ui"]["rail"] == {"open": "session", "width": 320}


def test_put_workspace_persists_across_requests(client):
    client.put("/api/workspace", json={"ui": {"rail": {"open": "settings", "width": 400}}})
    r = client.get("/api/workspace")
    assert r.json()["ui"]["rail"] == {"open": "settings", "width": 400}


def test_put_workspace_merges_rather_than_replaces_ui(client):
    client.put("/api/workspace", json={"ui": {"theme": "dark"}})
    client.put("/api/workspace", json={"ui": {"rail": {"open": None, "width": 280}}})
    r = client.get("/api/workspace")
    # ui.theme(첫 PUT)이 두 번째 PUT(ui.rail만 보냄)에 지워지지 않아야 한다 —
    # workspace.save()가 "ui" 레벨에서 얕은 병합을 하기 때문(workspace.py 참고).
    assert r.json()["ui"]["theme"] == "dark"
    assert r.json()["ui"]["rail"] == {"open": None, "width": 280}
