"""N40 — GET /api/search/scrollback (팔레트 `~` 모드) 라우터 레이어 테스트.

pty_manager.py의 scrollback 링버퍼(test_scrollback_memory.py가 트리밍 자체를
단위 테스트한다)를 새 세션을 실제로 스폰하지 않고도 검사하기 위해, deps.pty_mgr의
내부 세션 딕셔너리에 PTYSession을 직접 심고 _append_scrollback으로 채운다 —
test_scrollback_memory.py와 같은 요령이다.
"""

import pytest
from starlette.testclient import TestClient

import main
from deps import pty_mgr, session_store
from pty_manager import PTYSession


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_sessions():
    # 다른 테스트/이전 실행이 남긴 세션이 섞이지 않도록 앞뒤로 비운다.
    pty_mgr._sessions.clear()
    yield
    pty_mgr._sessions.clear()


def _seed_session(session_id: str, name: str, lines: list[str]):
    s = PTYSession(session_id=session_id, pid=1, fd=1)
    pty_mgr._sessions[session_id] = s
    # 실제 PTY 출력처럼 각 줄이 개행으로 끝나되, 마지막 줄 뒤에는 아직 개행이
    # 안 왔을 수도 있는 상태(진행 중인 출력)를 그대로 흉내 — 트레일링 빈 줄을
    # 만들지 않는다.
    body = "\n".join(lines).encode()
    pty_mgr._append_scrollback(s, body)
    session_store.add(session_id, name=name)


def test_empty_query_returns_no_results_without_scanning(client):
    _seed_session("s1", "dev", ["hello world"])
    r = client.get("/api/search/scrollback?q=")
    assert r.status_code == 200
    assert r.json() == {"results": [], "truncated": False}


def test_matches_line_with_context(client):
    _seed_session("s1", "dev", [
        "line before 2",
        "line before 1",
        "ERROR: something broke",
        "line after 1",
        "line after 2",
    ])
    r = client.get("/api/search/scrollback?q=ERROR")
    assert r.status_code == 200
    data = r.json()
    assert data["truncated"] is False
    assert len(data["results"]) == 1
    item = data["results"][0]
    assert item["session_id"] == "s1"
    assert item["session_name"] == "dev"
    assert "ERROR: something broke" == item["line"]
    assert item["context_before"] == ["line before 2", "line before 1"]
    assert item["context_after"] == ["line after 1", "line after 2"]


def test_case_insensitive_match(client):
    _seed_session("s1", "dev", ["Hello World"])
    r = client.get("/api/search/scrollback?q=hello")
    assert r.json()["results"][0]["line"] == "Hello World"


def test_no_match_returns_empty(client):
    _seed_session("s1", "dev", ["nothing interesting here"])
    r = client.get("/api/search/scrollback?q=zzz-not-found")
    assert r.json() == {"results": [], "truncated": False}


def test_searches_across_multiple_sessions(client):
    _seed_session("s1", "dev", ["needle in session one"])
    _seed_session("s2", "web", ["needle in session two"])
    r = client.get("/api/search/scrollback?q=needle")
    data = r.json()
    names = sorted(item["session_name"] for item in data["results"])
    assert names == ["dev", "web"]


def test_sessions_param_filters_to_one_session(client):
    _seed_session("s1", "dev", ["needle here"])
    _seed_session("s2", "web", ["needle here too"])
    r = client.get("/api/search/scrollback?q=needle&sessions=s1")
    data = r.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["session_id"] == "s1"


def test_result_cap_sets_truncated(client):
    # 세션당 상한(20)이 있으므로 한 세션만으로는 전체 상한(50)에 못 미친다 —
    # 여러 세션에 걸쳐 60건을 만들어 전체 상한 트리밍을 검사한다.
    for i in range(3):
        lines = [f"match line {n}" for n in range(30)]
        _seed_session(f"s{i}", f"dev{i}", lines)
    r = client.get("/api/search/scrollback?q=match")
    data = r.json()
    assert len(data["results"]) <= 50
    assert data["truncated"] is True
