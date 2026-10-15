"""에이전트 세션 공유 — `VT_AUTHZ_ENFORCE=share`(3a)에서도 matrix.json 이 그대로 성립한다.

`test_char_agent_share.py` 와 같은 230 사례를 플래그를 **켠 채** 돌려 같은 기록과 비교한다. 기록과 다르면 실패하고,
의도한 차이는 `EXPECTED_CHANGE`(사례 id → 이유)에 적어야 통과한다. 지금은 비어 있다 — 3a 는 공개 계약(상태 코드·
본문 키)을 바꾸지 않는다. 엔진이 정말 판정을 했는지는 `test_engine_decides_when_enforced` 가 지킨다.

그리고 H1: 공유 판정을 엔진이 쥐어도 **공용 기계 토큰으로 직접 닿는 라우트는 아직 막히지 않는다**(3e, plan-2.6 40-decisions
H1). 그 사실을 고정해 둔다 — 3e 가 막으면 여기가 깨지는 게 정상이고, 그때 matrix 와 함께 바꾼다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for
from authz_harness import Case

CURRENT_BEHAVIOR = True

# 사례 id → 이유. 3a 가 의도적으로 응답을 바꾼 사례만. (없음)
EXPECTED_CHANGE: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")


@pytest.mark.parametrize("case", cases_for("agent_share"), ids=lambda c: c.id)
def test_matrix_enforced(case, world, golden):
    actual = world.run(case)
    row = golden.matrix[case.id]
    if case.id in EXPECTED_CHANGE:
        assert actual != row["expected"], f"{case.id}: EXPECTED_CHANGE 인데 차이가 없다 — 목록에서 지울 것"
        return
    assert actual == row["expected"], f"{case.id}: 켠 상태 {actual} ≠ 기록 {row['expected']}"


def _run(world, **kw):
    return world.run(Case(id="adhoc", group="adhoc", **kw))


def test_engine_decides_when_enforced(world, monkeypatch):
    from authz import engine, store
    seen = []
    real = engine.decide

    def spy(p, scope, resource, *a, **k):
        seen.append((p.kind, scope))
        return real(p, scope, resource, *a, **k)

    monkeypatch.setattr(engine, "decide", spy)
    body = {"nickname": "shell", "lines": 5}
    r = _run(world, caller="agent_verified", method="POST", path="/api/agent/shared/read", body=body)
    assert r == {"status": 202, "error": None}                               # grant 없음 → 보류
    world.grant_read()
    r = _run(world, caller="agent_verified", method="POST", path="/api/agent/shared/read", body=body)
    assert r == {"status": 200, "error": None}
    assert ("agent", "session.read") in seen and ("agent", "authz.request") in seen
    assert [g["id"].startswith("ag-") for g in store._read("grants")[0]["items"]] == [True]   # 엔진 저장소가 진실


def test_h1_still_open_agent_share_enforcement_does_not_cover_machine_token_routes(world):
    """grant 가 없는 에이전트: 공유 경로는 보류(202)지만, 같은 기계 토큰으로 PTY 입력·큐·tmux 종료는 그대로 된다 — 3e."""
    body = {"nickname": "shell", "text": "echo x"}
    assert _run(world, caller="agent_verified", method="POST", path="/api/agent/shared/send", body=body)["status"] == 202
    r = _run(world, caller="agent_foreign", method="POST", path="/api/sessions/pty-1/keys", body={"text": "y\r"})
    assert r["status"] == 200 and world.pty_writes == [("pty-1", b"y\r")]
    import queue_store
    r = _run(world, caller="machine", method="POST", path="/api/queue",
             body={"text": "echo pwned", "target": {"session": "target"}})
    assert r["status"] == 200 and any((x.get("target") or {}).get("session") == "target" for x in queue_store.list_items())
    r = _run(world, caller="machine", method="DELETE", path="/api/tmux/kill/target")
    assert r == {"status": 200, "error": None} and "target" not in world.tmux.sessions
