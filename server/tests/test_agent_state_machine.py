"""A1 — agent_status의 4상태 머신(idle/working/waiting/done, error는 예약).

전이표 전수 + TTL 만료 + 알 수 없는 이벤트 무시. cwd 전달 회귀는
test_agent_status.py가 계속 담당한다(그쪽은 A1 이전부터 있던 계약).
"""

import time

import pytest

import agent_status as A


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    A._state.clear()
    # TTL은 테스트마다 명시적으로 짧게 잡는다 — 기본값(15분/2분/30분)을
    # 기다릴 수 없고, 기본값에 의존하면 값이 바뀔 때 테스트가 조용히 무의미해진다.
    monkeypatch.setenv("VT_AGENT_TTL_WORKING", "100")
    monkeypatch.setenv("VT_AGENT_TTL_WAITING", "20")
    monkeypatch.setenv("VT_AGENT_TTL_DONE", "200")
    yield
    A._state.clear()


def _age(sid, seconds):
    """엔트리를 seconds만큼 과거로 밀어 TTL을 인위적으로 넘긴다."""
    A._state[sid]["updated_at"] = time.time() - seconds


# ── 전이표 ────────────────────────────────────────────────────────────────
def test_unknown_session_is_idle():
    assert A.get_status("nope") == A.IDLE


def test_pre_makes_working():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    assert A.get_status("s") == A.WORKING


def test_post_does_not_change_status():
    """`post`는 상태를 안 바꾼다 — 다음 도구가 곧바로 이어질 수 있다.

    A1 이전엔 이 규칙이 프론트 주석(grid.js)에만 있었다. 서버로 옮겨 명문화한다.
    """
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    ent = A.on_event("post", {"session_id": "s"})
    assert ent["status"] == A.WORKING
    assert ent["tool"] is None, "도구 자체는 끝났다"
    assert ent["last_tool"] == "Bash"


def test_stop_makes_done_and_keeps_entry():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    A.on_event("stop", {"session_id": "s"})
    assert A.get_status("s") == A.DONE
    assert "s" in A._state, "엔트리가 남아야 새로고침 후에도 done이 보인다"


def test_pre_after_done_returns_to_working():
    A.on_event("stop", {"session_id": "s"})
    A.on_event("pre", {"session_id": "s", "tool_name": "Read"})
    assert A.get_status("s") == A.WORKING


def test_waiting_enters_only_from_working():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    A.on_waiting("s", True)
    assert A.get_status("s") == A.WAITING


def test_waiting_is_not_applied_to_done_session():
    """done인 세션에 뒤늦게 도착한 패턴 감지가 완료 표시를 되돌리면 안 된다."""
    A.on_event("stop", {"session_id": "s"})
    A.on_waiting("s", True)
    assert A.get_status("s") == A.DONE


def test_waiting_cleared_returns_to_working():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    A.on_waiting("s", True)
    A.on_waiting("s", False)
    assert A.get_status("s") == A.WORKING


def test_pre_and_stop_win_over_waiting():
    for event, expected in (("pre", A.WORKING), ("stop", A.DONE)):
        A._state.clear()
        A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
        A.on_waiting("s", True)
        A.on_event(event, {"session_id": "s", "tool_name": "Bash"})
        assert A.get_status("s") == expected, f"{event} 훅이 waiting을 이겨야 한다"


def test_waiting_clear_on_unknown_session_is_noop():
    assert A.on_waiting("never-seen", False) is None
    assert A._state == {}


def test_ack_lowers_done_to_idle():
    A.on_event("stop", {"session_id": "s"})
    A.ack("s")
    assert A.get_status("s") == A.IDLE


def test_ack_does_not_touch_working():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    A.ack("s")
    assert A.get_status("s") == A.WORKING, "작업 중인 세션을 클릭했다고 지워지면 안 된다"


def test_unknown_event_is_ignored():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    before = dict(A._state["s"])
    A.on_event("subagent_stop", {"session_id": "s"})
    assert A._state["s"] == before


def test_unknown_event_on_unknown_session_creates_nothing():
    assert A.on_event("whatever", {"session_id": "ghost"}) is None
    assert A._state == {}


# ── TTL ───────────────────────────────────────────────────────────────────
def test_working_expires_to_idle():
    """훅 유실 의심 — 이전엔 영구히 working으로 남았다(TTL이 아예 없었다)."""
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    _age("s", 101)
    assert A.get_status("s") == A.IDLE
    assert A._state["s"]["tool"] is None


def test_waiting_expires_back_to_working():
    """감지 오탐 가정 — idle이 아니라 working으로 돌린다(아직 도는 중일 수 있다)."""
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    A.on_waiting("s", True)
    _age("s", 21)
    assert A.get_status("s") == A.WORKING


def test_done_expires_to_idle():
    A.on_event("stop", {"session_id": "s"})
    _age("s", 201)
    assert A.get_status("s") == A.IDLE


def test_idle_entry_is_eventually_dropped():
    A.on_event("stop", {"session_id": "s"})
    _age("s", 201)
    A.sweep()              # done → idle
    _age("s", 201)
    A.sweep()              # idle → 삭제
    assert "s" not in A._state


def test_not_yet_expired_stays():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    _age("s", 99)
    assert A.get_status("s") == A.WORKING


def test_sweep_is_triggered_by_reads_not_only_writes():
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"})
    _age("s", 101)
    # get_state/all_active도 sweeper를 태운다 — 조회만 하는 클라이언트도
    # stale working을 보지 않아야 한다.
    assert A.get_state("s")["status"] == A.IDLE
    assert A.all_active() == []


# ── 파생 조회 ─────────────────────────────────────────────────────────────
def test_all_active_only_lists_running_tools():
    A.on_event("pre", {"session_id": "run", "tool_name": "Bash"})
    A.on_event("stop", {"session_id": "fin"})
    ids = [a["session_id"] for a in A.all_active()]
    assert ids == ["run"]
    assert A.all_active()[0]["status"] == A.WORKING, "status가 함께 실려야 한다"


def test_status_for_cwd_picks_most_urgent():
    """같은 cwd에 여러 세션이면 개입이 필요한 쪽을 고른다(큐 안전 — A4의 전제)."""
    A.on_event("pre", {"session_id": "a", "tool_name": "Bash", "cwd": "/repo"})
    A.on_event("pre", {"session_id": "b", "tool_name": "Bash", "cwd": "/repo"})
    A.on_waiting("b", True)
    assert A.status_for_cwd("/repo") == A.WAITING


def test_status_for_cwd_unknown_is_idle():
    assert A.status_for_cwd("/nowhere") == A.IDLE
    assert A.status_for_cwd(None) == A.IDLE
