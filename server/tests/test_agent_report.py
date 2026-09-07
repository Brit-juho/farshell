"""A2 — POST /api/agent/report (`fsh pane report`)와 세션 단위 상태 조회.

훅이 없는 에이전트(codex/aider/gemini)가 자기 상태를 알리는 유일한 경로라,
훅 경로와 **같은 3단 해석**을 타는지가 핵심이다.
"""

import pytest
from starlette.testclient import TestClient

import agent_status
import main
import tmux_runner
from tmux_runner import PaneInfo


@pytest.fixture
def client(monkeypatch):
    agent_status._state.clear()
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", "fsh")
    monkeypatch.delenv("VT_TMUX_SOCKET", raising=False)
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: [
        PaneInfo(session="a", command="claude", pid=1, path="/Users/x", pane_id="%1"),
        PaneInfo(session="b", command="codex", pid=2, path="/Users/x", pane_id="%2"),
    ])
    with TestClient(main.app) as c:
        yield c
    agent_status._state.clear()


def test_report_with_pane_id_targets_exactly_one_session(client):
    r = client.post("/api/agent/report", json={
        "state": "working", "agent": "codex",
        "pane": "%2", "tmux": "/tmp/tmux-501/fsh,1,0", "cwd": "/Users/x",
    })
    body = r.json()
    assert body["ok"] is True
    assert (body["session"], body["resolved_by"]) == ("b", "pane")
    # 같은 cwd에 세션이 둘인데도 b만 상태를 갖는다 — A2의 실증 포인트.
    assert agent_status.status_for_session("b") == "working"
    assert agent_status.status_for_session("a") == "idle"


def test_report_without_pane_gives_up_on_ambiguous_cwd(client):
    body = client.post("/api/agent/report", json={"state": "working", "cwd": "/Users/x"}).json()
    assert body["session"] is None
    assert body["resolved_by"] == "ambiguous"


def test_report_rejects_unknown_state(client):
    body = client.post("/api/agent/report", json={
        "state": "hurried", "pane": "%1", "tmux": "/tmp/tmux-501/fsh,1,0",
    }).json()
    assert body["ok"] is False
    assert body["error"] == "bad_state"


def test_report_waiting_then_idle_transitions(client):
    hdr = {"pane": "%1", "tmux": "/tmp/tmux-501/fsh,1,0", "cwd": "/Users/x"}
    client.post("/api/agent/report", json={"state": "waiting", **hdr})
    assert agent_status.status_for_session("a") == "waiting"
    client.post("/api/agent/report", json={"state": "idle", **hdr})
    assert agent_status.status_for_session("a") == "idle"


def test_hook_event_stores_resolved_session(client):
    """훅 경로도 같은 해석을 탄다 — pane을 실어 보내면 세션이 엔트리에 박힌다."""
    r = client.post("/api/agent/event", json={
        "event": "pre",
        "payload": {"session_id": "s1", "tool_name": "Bash", "cwd": "/Users/x"},
        "pane": "%1", "tmux": "/tmp/tmux-501/fsh,1,0",
    })
    assert r.json()["state"]["tmux_session"] == "a"
    assert agent_status.status_for_session("a") == "working"


def test_hook_event_keeps_session_when_later_event_lacks_clues(client):
    """pre에서 pane으로 특정해뒀으면, 단서 없는 stop이 와도 그 정보를 잃지 않는다."""
    client.post("/api/agent/event", json={
        "event": "pre", "payload": {"session_id": "s1", "tool_name": "Bash"},
        "pane": "%1", "tmux": "/tmp/tmux-501/fsh,1,0",
    })
    client.post("/api/agent/event", json={"event": "stop", "payload": {"session_id": "s1"}})
    assert agent_status.get_state("s1")["tmux_session"] == "a"
    assert agent_status.status_for_session("a") == "done"


def test_tmux_sessions_response_exposes_pane_id(client, monkeypatch):
    """프런트(A5)가 cwd 추측 대신 pane_id로 카드를 특정할 수 있어야 한다."""
    def fake_run_text(args, timeout=None):
        if "list-sessions" in args:
            return "a\t1\t0\nb\t1\t0"
        return "a\tclaude\t/Users/x\t%1\nb\tcodex\t/Users/x\t%2"

    # monkeypatch로 되돌려야 한다 — 모듈 속성을 직접 대입하면 이후 테스트가
    # 가짜 tmux를 계속 보게 된다(테스트 순서에 따라 조용히 깨진다).
    monkeypatch.setattr(tmux_runner, "run_text", fake_run_text)
    data = client.get("/api/tmux/sessions").json()
    assert {s["name"]: s["pane_id"] for s in data} == {"a": "%1", "b": "%2"}
