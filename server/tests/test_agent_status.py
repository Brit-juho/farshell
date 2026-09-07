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


def test_stop_keeps_cwd_and_entry_survives():
    """A1에서 계약이 바뀌었다 — stop이 엔트리를 지우지 않는다.

    이전에는 `_state.pop`이라 "done이라는 상태"가 서버에 남지 않았고, 그래서
    새로고침하면 done 배지가 사라졌다. 이제 엔트리를 유지하고 status만
    done으로 바꾼다(A5 복원의 전제). cwd는 여전히 그대로 실려 나온다.
    """
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    result = agent_status.on_event("stop", {"session_id": "s1"})
    assert result["cwd"] == "/repo/a"
    assert result["status"] == "done"
    assert agent_status.get_state("s1")["status"] == "done"


def test_stop_without_prior_pre_still_records_done():
    """도구를 하나도 안 쓴 응답(claude -p "ok" 같은)도 완료로 남아야 한다."""
    result = agent_status.on_event("stop", {"session_id": "never-seen"})
    assert result["status"] == "done"
    assert result["cwd"] is None


def test_stop_payload_cwd_wins_over_stale_state_cwd():
    """stop 페이로드에 cwd가 직접 오면(정상 케이스) 그걸 우선한다."""
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/old"})
    result = agent_status.on_event("stop", {"session_id": "s1", "cwd": "/repo/new"})
    assert result["cwd"] == "/repo/new"


def test_all_active_includes_cwd():
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash", "cwd": "/repo/a"})
    active = agent_status.all_active()
    assert len(active) == 1
    assert active[0]["cwd"] == "/repo/a"
    assert active[0]["session_id"] == "s1"
