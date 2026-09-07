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


def test_put_workspace_stores_layout_tree_next_to_rail(client):
    """L8 — ui.layout(분할 트리 정본)이 ui.rail과 나란히 살아남는다.

    둘은 ui 아래의 서로 다른 키라 얕은 병합으로 충분하지만, 프런트가 두 값을
    각각 다른 시점에 PUT하므로(rail.js는 패널 토글 때, persist.js는 트리 변경
    때) 한쪽이 다른 쪽을 지우면 새로고침 때마다 둘 중 하나가 날아간다.
    """
    layout = {
        "v": 1,
        "savedAt": 1234,
        "active": "pane-2",
        "tree": {
            "t": "split", "id": "split-1", "dir": "row", "ratio": 0.4,
            "a": {"t": "leaf", "id": "pane-1", "session": {"id": "web-1", "tmux": "dev"}},
            "b": {"t": "leaf", "id": "pane-2", "session": None},
        },
    }
    client.put("/api/workspace", json={"ui": {"rail": {"open": "session", "width": 320}}})
    client.put("/api/workspace", json={"ui": {"layout": layout}})

    ui = client.get("/api/workspace").json()["ui"]
    assert ui["rail"] == {"open": "session", "width": 320}
    assert ui["layout"] == layout
    # 중첩 구조가 통째로 왕복되는지 — leaf의 tmux 이름이 복원의 핵심 키다.
    assert ui["layout"]["tree"]["a"]["session"]["tmux"] == "dev"
