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
def _isolated_state(monkeypatch, tmp_path):
    """이 검사는 **영속 로그까지** 훑는다(`scrollback_persist.logged_session_ids`).
    그 경로는 `VT_STATE_DIR`이 없으면 `~/.vt/scrollback`, 즉 **이 기계를 쓰는
    사람의 진짜 터미널 출력**이다. 2026-09-19에 확인했다: 이 파일만 따로 돌리면
    거기 쌓인 9.5MB를 읽어 `truncated: True`가 나오면서 두 건이 깨졌다. 전체
    스위트에서는 앞선 테스트가 `VT_STATE_DIR`을 tmp로 돌려놔서 우연히 통과했다 —
    통과 여부가 실행 순서에 달려 있었던 셈이고, 남의 데이터를 읽는 건 그 자체로
    막아야 한다. 빈 tmp 디렉터리로 고정한다.
    """
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    # 다른 테스트/이전 실행이 남긴 세션이 섞이지 않도록 앞뒤로 비운다.
    pty_mgr._sessions.clear()
    yield
    pty_mgr._sessions.clear()


def _seed_session(session_id: str, name: str, lines: list[str]):
    # pid·fd는 **의도적으로 "프로세스도 파일도 아닌 값"**이다. 예전엔 `pid=1,
    # fd=1`이었는데 1은 init이고 fd 1은 이 프로세스의 stdout이다 — 앱 lifespan이
    # 끝나며 `destroy_all()`이 돌면 그 값으로 `killpg(1, SIGKILL)`과 `close(1)`이
    # 나갔다(2026-09-19, GitHub 러너 VM이 이걸로 죽었다). pty_manager가 이제 0·1과
    # fd 0~2를 거르지만, 테스트가 위험한 값을 적어두는 것부터 그만둔다.
    s = PTYSession(session_id=session_id, pid=0, fd=-1)
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


# --- 영속 로그까지 확장 (2.1.2, 80-multihost-agents.md §3) -------------------------


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """격리된 ~/.vt/scrollback/. scrollback_persist는 매 호출 시 env를 다시 읽는다."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    d = tmp_path / "scrollback"
    d.mkdir()
    return d


def _write_log(logs, session_id, text, rotated=None):
    if rotated is not None:
        (logs / f"{session_id}.log.1").write_text(rotated)
    (logs / f"{session_id}.log").write_text(text)


def test_search_finds_output_of_a_session_that_no_longer_exists(client, logs):
    """이 확장의 핵심 — 서버 재시작·세션 종료로 링버퍼가 사라진 뒤에도 찾힌다."""
    _write_log(logs, "dead-1", "build ok\nTypeError: boom\ndone\n")
    r = client.get("/api/search/scrollback?q=TypeError")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["session_id"] == "dead-1"
    assert results[0]["source"] == "log"
    assert results[0]["line"] == "TypeError: boom"


def test_rotated_generation_is_searched_before_the_current_log(client, logs):
    _write_log(logs, "s9", "recent line\n", rotated="older marker line\n")
    lines = [x["line"] for x in client.get("/api/search/scrollback?q=marker").json()["results"]]
    assert lines == ["older marker line"]


def test_live_session_with_a_log_is_not_reported_twice(client, logs):
    """세션 하나당 한 소스만 본다 — 링버퍼는 로그의 꼬리라 겹친다."""
    _seed_session("s1", "dev", ["needle here"])
    _write_log(logs, "s1", "needle here\n")
    results = client.get("/api/search/scrollback?q=needle").json()["results"]
    assert len(results) == 1
    assert results[0]["source"] == "log"


def test_live_session_without_a_log_still_uses_the_ring_buffer(client, logs):
    _seed_session("s2", "dev", ["needle here"])
    results = client.get("/api/search/scrollback?q=needle").json()["results"]
    assert len(results) == 1
    assert results[0]["source"] == "live"


def test_sessions_filter_also_applies_to_logged_only_sessions(client, logs):
    _write_log(logs, "keep", "needle\n")
    _write_log(logs, "skip", "needle\n")
    results = client.get("/api/search/scrollback?q=needle&sessions=keep").json()["results"]
    assert [x["session_id"] for x in results] == ["keep"]


def test_huge_log_is_scanned_from_the_tail_and_marked_truncated(client, logs, monkeypatch):
    """오래된 쪽을 자른다 — 사람이 찾는 건 대개 최근이고, 잘렸다는 사실은 알린다."""
    import routes.search as search_mod
    monkeypatch.setattr(search_mod, "MAX_LOG_SCAN_BYTES", 64)
    _write_log(logs, "big", "old needle\n" + "x" * 200 + "\nnew needle\n")
    body = client.get("/api/search/scrollback?q=needle").json()
    assert body["truncated"] is True
    assert [x["line"] for x in body["results"]] == ["new needle"]


def test_peer_mirror_sessions_are_excluded(client):
    """`peer-<host>-<screen>`은 원격 화면의 거울 PTY다 — 같은 tmux 세션의 출력을
    한 벌 더 들고 있을 뿐이라 검색에 나오면 같은 줄이 두 번 뜨고, 세션 이름 자리에
    사람이 본 적 없는 내부 id가 뜬다(2.1.3 원격 검색 실측에서 잡혔다)."""
    _seed_session("s1", "dev", ["needle here"])
    _seed_session("peer-gpu-box-abc123", "", ["needle here"])
    results = client.get("/api/search/scrollback?q=needle").json()["results"]
    assert [r["session_id"] for r in results] == ["s1"]
