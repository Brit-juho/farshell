"""에이전트 세션 공유(`routes/agent_share.py`) — 지금 판정 고정.

에이전트용 3개(기계 토큰 + pane 확인)와 사람용 7개(`require_human_session`)를 호출자 × 서버 모드 ×
tmux 상태로 돈다. 기대값은 matrix.json 에 있다(gen_matrix.py 가 기록).
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.mark.parametrize("case", cases_for("agent_share"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))


# --- 행렬 밖에서 직접 못 박아 두는 성질 -------------------------------------------------------


def _run(world, **kw):
    from authz_harness import Case
    return world.run(Case(id="adhoc", group="adhoc", **kw))


def test_grant_lets_the_verified_agent_read_but_not_a_foreign_process(world):
    """권한은 pane 확인을 지난 호출자에게만 쓰인다 — 같은 기계 토큰이라도 pane 을 주장만 하면 403."""
    world.grant_read()
    body = {"nickname": "shell", "lines": 5}
    ok = _run(world, caller="agent_verified", method="POST", route="", path="/api/agent/shared/read", body=body)
    assert ok == {"status": 200, "error": None}
    bad = _run(world, caller="agent_foreign", method="POST", route="", path="/api/agent/shared/read", body=body)
    assert bad == {"status": 403, "error": "identity_unverified"}


def test_machine_token_cannot_approve_its_own_request(world):
    """승인은 사람 세션만 — 기계 토큰·pane 확인된 에이전트도 403 human_session_required."""
    for caller in ("machine", "agent_verified"):
        r = _run(world, caller=caller, method="POST", route="",
                 path="/api/agent/share/requests/{rid}/decision", body={"decision": "allow"})
        assert r == {"status": 403, "error": "human_session_required"}
