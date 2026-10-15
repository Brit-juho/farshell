"""완료 확인(ack) — 서버가 done을 idle로 내려야 다음 스냅샷이 완료를 되살리지 않는다.

화면만 idle로 바꾸면 서버의 done(30분 TTL)이 15초 하트비트로 다시 와서 「완료 · 확인하지 않음」이
반복됐다(2026-10-15 실서버, Final 세션).
"""

import asyncio

import pytest

import agent_status as A
import auth
from routes import agents as R


@pytest.fixture(autouse=True)
def _reset():
    A._state.clear()
    events = []
    A.subscribe(rec := lambda ent, reason: events.append((reason, ent)))
    yield events
    A.unsubscribe(rec)
    A._state.clear()


def _ev(event, sid, session="Final", **payload):
    return A.on_event(event, {"session_id": sid, "cwd": "/r", **payload}, session=session, agent="claude")


def test_ack_session_turns_done_into_idle_and_notifies(_reset):
    _ev("pre", "s1", tool_name="Bash")
    _ev("stop", "s1")
    assert A.status_for_session("Final") == A.DONE
    assert A.ack_session("Final") == 1
    assert A.known_status_for_session("Final") != A.DONE
    assert any(r == "ack" for r, _ in _reset), "ack 이벤트가 나가야 다른 기기도 idle을 받는다"


def test_ack_session_is_idempotent():
    _ev("pre", "s1", tool_name="Bash")
    _ev("stop", "s1")
    assert A.ack_session("Final") == 1
    assert A.ack_session("Final") == 0


def test_ack_session_never_touches_working_or_waiting():
    _ev("pre", "s1", tool_name="Bash")
    assert A.status_for_session("Final") == A.WORKING
    assert A.ack_session("Final") == 0
    assert A.status_for_session("Final") == A.WORKING


def test_ack_session_only_the_named_session():
    _ev("pre", "a", session="Final", tool_name="Bash"); _ev("stop", "a", session="Final")
    _ev("pre", "b", session="other", tool_name="Bash"); _ev("stop", "b", session="other")
    assert A.ack_session("Final") == 1
    assert A.status_for_session("other") == A.DONE


def test_ack_session_ignores_a_peer_host_entry_with_the_same_name():
    A.on_event("pre", {"session_id": "p1", "cwd": "/r", "tool_name": "Bash"}, session="Final", agent="claude", host="m1")
    A.on_event("stop", {"session_id": "p1", "cwd": "/r"}, session="Final", agent="claude", host="m1")
    assert A.ack_session("Final") == 0, "같은 이름의 다른 기계 세션은 로컬 확인으로 지우지 않는다"


def test_route_is_human_only_and_returns_the_count():
    deps = [d.dependency for d in R.human_router.dependencies]
    assert auth.require_human_session in deps, "기계 토큰(에이전트·훅)은 자기 완료를 지울 수 없다"
    paths = {r.path for r in R.human_router.routes}
    assert paths == {"/api/agent/ack"}

    _ev("pre", "s1", tool_name="Bash")
    _ev("stop", "s1")
    out = asyncio.run(R.agent_ack(R.AckRequest(tmux_session="Final")))
    assert out == {"ok": True, "acked": 1}
    assert asyncio.run(R.agent_ack(R.AckRequest(tmux_session="Final")))["acked"] == 0


def test_request_rejects_empty_or_huge_names():
    with pytest.raises(Exception):
        R.AckRequest(tmux_session="")
    with pytest.raises(Exception):
        R.AckRequest(tmux_session="x" * 129)
