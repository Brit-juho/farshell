"""「확인 안 함」은 모든 기기 공통이고 완료 턴 단위다(세션 로직 점검 단계 4, D4).

- 완료(done)로 들어갈 때마다 `turn`이 1씩 는다.
- `/api/agent/seen`은 그 턴까지 봤다고 `seen_turn`을 올린다(done은 그대로). 더 새 완료는 건드리지 않는다.
- `/api/agent/ack`에 턴을 실으면, 그 사이 새 완료가 왔을 때 새 완료를 치우지 않는다.
"""

import pytest

import agent_status


@pytest.fixture(autouse=True)
def clean():
    agent_status._state.clear()
    yield
    agent_status._state.clear()


def _done(sid="s1"):
    agent_status.on_event("prompt", {"session_id": sid, "cwd": "/tmp"}, session="w")
    agent_status.on_event("stop", {"session_id": sid, "cwd": "/tmp"}, session="w")
    return next(v for v in agent_status._state.values() if v.get("session_id") == sid)


def test_each_completion_gets_the_next_turn():
    ent = _done()
    assert (ent["status"], ent["turn"]) == ("done", 1)
    _done()
    assert ent["turn"] == 2


def test_seen_marks_the_turn_for_every_device_and_keeps_done():
    ent = _done()
    events = []
    agent_status.subscribe(lambda e, reason: events.append(reason))
    assert agent_status.mark_seen("w", 1) == 1
    assert (ent["status"], ent["seen_turn"]) == ("done", 1)
    assert "seen" in events
    assert agent_status.mark_seen("w", 1) == 0, "두 번째부터는 바뀌는 게 없다"


def test_seeing_an_older_turn_does_not_hide_a_newer_completion():
    ent = _done()
    _done()                                   # 턴 2가 왔다
    assert agent_status.mark_seen("w", 1) == 0
    assert ent.get("seen_turn", 0) < ent["turn"]


def test_ack_with_an_older_turn_keeps_the_newer_completion():
    ent = _done()
    _done()
    assert agent_status.ack_session("w", turn=1) == 0
    assert ent["status"] == "done"
    assert agent_status.ack_session("w", turn=2) == 1
    assert ent["status"] == "idle" and ent["seen_turn"] == 2


def test_ack_without_a_turn_still_works_for_old_screens():
    ent = _done()
    assert agent_status.ack_session("w") == 1
    assert ent["status"] == "idle"


def test_seen_route_validates_and_calls(monkeypatch):
    import asyncio
    import routes.agents as agents
    seen = []
    monkeypatch.setattr(agent_status, "mark_seen", lambda name, turn: seen.append((name, turn)) or 1)
    out = asyncio.run(agents.agent_seen(agents.SeenRequest(tmux_session="w", turn=3)))
    assert out == {"ok": True, "seen": 1} and seen == [("w", 3)]
    with pytest.raises(Exception):
        agents.SeenRequest(tmux_session="w", turn=-1)
