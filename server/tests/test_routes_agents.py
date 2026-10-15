"""P2-1(ADR-34) — `GET /api/agents` · `GET /api/agents/{tmux_name}` ·
`POST /api/agent/event`가 엔트리의 `agent`(1차 신호)를 `agent_detector`
(2차 추측)보다 우선하는지.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import agent_detector
import agent_status
import main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_state():
    agent_status._state.clear()
    yield
    agent_status._state.clear()


def test_entry_agent_overrides_the_detector_in_list(client, monkeypatch):
    """탐지기가 아직 옛 CLI를 말해도(TTL 캐시), 엔트리의 1차 신호가 이긴다."""
    monkeypatch.setattr(
        agent_detector, "detect_all",
        lambda: {"dev": {"agent": "claude", **agent_detector.KNOWN_AGENTS["claude"]}},
    )
    agent_status.report("s1", agent_status.WORKING, session="dev", agent="codex")

    r = client.get("/api/agents")
    assert r.status_code == 200
    assert r.json()["dev"]["agent"] == "codex"


def test_falls_back_to_the_detector_when_no_entry_has_an_agent(client, monkeypatch):
    monkeypatch.setattr(
        agent_detector, "detect_all",
        lambda: {"dev": {"agent": "claude", **agent_detector.KNOWN_AGENTS["claude"]}},
    )
    r = client.get("/api/agents")
    assert r.json()["dev"]["agent"] == "claude"


def test_neither_entry_nor_detector_gives_null_not_shell(client, monkeypatch):
    """`null`(모른다)과 `"shell"`(확인했고 셸이다)은 다르다(§3.3)."""
    monkeypatch.setattr(agent_detector, "detect", lambda name: None)
    r = client.get("/api/agents/never-seen")
    assert r.status_code == 200
    assert r.json()["agent"] is None


def test_single_session_lookup_prefers_the_entry_too(client, monkeypatch):
    monkeypatch.setattr(
        agent_detector, "detect",
        lambda name: {"agent": "claude", **agent_detector.KNOWN_AGENTS["claude"]},
    )
    agent_status.report("s1", agent_status.WORKING, session="dev", agent="aider")
    r = client.get("/api/agents/dev")
    assert r.json()["agent"] == "aider"


def test_single_session_lookup_falls_back_when_entry_has_no_agent(client, monkeypatch):
    monkeypatch.setattr(
        agent_detector, "detect",
        lambda name: {"agent": "codex", **agent_detector.KNOWN_AGENTS["codex"]},
    )
    r = client.get("/api/agents/dev")
    assert r.json()["agent"] == "codex"


def test_remote_host_entry_does_not_leak_into_local_list(client, monkeypatch):
    """원격 호스트의 같은 이름 세션이 로컬 결과에 섞이면 안 된다."""
    monkeypatch.setattr(agent_detector, "detect_all", lambda: {})
    agent_status.report("s-remote", agent_status.WORKING, session="dev",
                         agent="aider", host="gpu-box")
    r = client.get("/api/agents")
    assert "dev" not in r.json()


def test_agent_event_forwards_agent_to_the_entry(client):
    """이 항목의 핵심 단언 — 훅이 실어 보낸 `agent`가 엔트리까지 간다."""
    r = client.post("/api/agent/event", json={
        "event": "pre",
        "payload": {"session_id": "hook-1", "cwd": "/x", "tool_name": "Bash"},
        "agent": "codex",
    })
    assert r.status_code == 200
    assert r.json()["state"]["agent"] == "codex"
    assert agent_status.get_state("hook-1")["agent"] == "codex"
