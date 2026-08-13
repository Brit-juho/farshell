"""agent_status.on_event 의 cwd 전달 회귀.

그리드 뷰가 "어느 tmux 세션이 지금 작업 중인지"를 판정하려면 훅 이벤트를
cwd로 매칭해야 한다(서버에 hook session_id ↔ tmux 세션명 매핑이 따로 없음).
pre/post에서 state에 cwd가 쌓이는지, stop에서 state를 지우기 직전에 cwd만은
돌려주는지가 이 매칭의 전제 조건이다.
"""

import pytest

import agent_status


@pytest.fixture(autouse=True)
def _reset_state():
    agent_status._state.clear()
    yield
    agent_status._state.clear()


def test_pre_stores_cwd():
    state = agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    assert state["cwd"] == "/repo/a"


def test_post_keeps_cwd_from_pre():
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    state = agent_status.on_event("post", {"session_id": "s1"})
    assert state["cwd"] == "/repo/a"
    assert state["tool"] is None


def test_stop_returns_cwd_even_though_state_is_cleared():
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    result = agent_status.on_event("stop", {"session_id": "s1"})
    assert result == {"cwd": "/repo/a"}
    assert agent_status.get_state("s1") == {}


def test_stop_without_any_known_cwd_returns_none():
    result = agent_status.on_event("stop", {"session_id": "never-seen"})
    assert result is None


def test_stop_payload_cwd_wins_over_stale_state_cwd():
    """stop 페이로드에 cwd가 직접 오면(정상 케이스) 그걸 우선한다."""
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/old"})
    result = agent_status.on_event("stop", {"session_id": "s1", "cwd": "/repo/new"})
    assert result == {"cwd": "/repo/new"}


def test_all_active_includes_cwd():
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    active = agent_status.all_active()
    assert len(active) == 1
    assert active[0]["cwd"] == "/repo/a"
    assert active[0]["session_id"] == "s1"
