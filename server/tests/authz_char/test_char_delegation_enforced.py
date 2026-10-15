"""에이전트 위임 — `VT_AUTHZ_ENFORCE=deleg`(3b)에서도 matrix.json 이 성립한다.

`test_char_delegation.py` 의 168 사례(+ CLI 직접 쓰기 1건)를 플래그를 **켠 채** 돌려 같은 기록과 비교한다. 다르면 실패하고,
의도한 차이는 `EXPECTED_CHANGE`(사례 id → 이유)에 적어야 통과한다. 엔진이 정말 판정했는지는 `test_engine_decides_when_enforced`.

의도한 차이는 H9 뿐이다: 확인된 에이전트가 `cwd: "/"` 로 하위 세션을 만들려던 사례(기록: 200)가 403 `cwd_outside_roots` 가 된다.
`agent_child`(깊이 한도 403)는 쿼터 검사가 cwd 검사보다 먼저라 그대로다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for
from authz_harness import Case

CURRENT_BEHAVIOR = True

_H9 = ("H9 — 명시한 cwd(`/`)가 fsguard 루트(VT_BROWSE_ROOTS) 밖이라 `fs.read` 가 거부된다: 200 → 403 cwd_outside_roots "
       "(깊이·쿼터 검사 뒤)")
EXPECTED_CHANGE: dict[str, str] = {
    f"delegation.api_delegations.post.agent_verified.{mode}": _H9 for mode in ("password", "token", "none")}


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    from authz import flags
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(sorted(flags.tokens() | {"deleg"})))


@pytest.mark.parametrize("case", cases_for("delegation"), ids=lambda c: c.id)
def test_matrix_enforced(case, world, golden):
    actual = world.run(case)
    row = golden.matrix[case.id]
    if case.id in EXPECTED_CHANGE:
        assert actual != row["expected"], f"{case.id}: EXPECTED_CHANGE 인데 차이가 없다 — 목록에서 지울 것"
        assert actual == {"status": 403, "error": "cwd_outside_roots"}, f"{case.id}: 예상한 새 응답이 아니다: {actual}"
        return
    assert actual == row["expected"], f"{case.id}: 켠 상태 {actual} ≠ 기록 {row['expected']}"


def test_expected_changes_are_all_real_cases():
    ids = {c.id for c in cases_for("delegation")}
    assert set(EXPECTED_CHANGE) <= ids


def test_engine_decides_when_enforced(world, monkeypatch):
    """spawn(쿼터)·부모 control·자식 report 가 실제로 엔진을 지난다 — 껐을 때와 응답은 같고 판정권자만 다르다."""
    from authz import engine
    seen = []
    real = engine.decide

    def spy(p, scope, resource, *a, **k):
        seen.append((p.kind, scope, resource.split("/")[0]))
        return real(p, scope, resource, *a, **k)

    monkeypatch.setattr(engine, "decide", spy)
    for caller, method, route, path, body in (
            ("agent_verified", "POST", "/api/delegations", "/api/delegations", {"task": "t", "cwd": "/"}),
            ("agent_verified", "POST", "/api/delegations/{did}/send", "/api/delegations/{did}/send", {"text": "m"}),
            ("agent_child", "POST", "/api/delegations/report", "/api/delegations/report",
             {"outcome": "succeeded", "summary": "s"})):
        world.run(Case(id="adhoc", group="adhoc", caller=caller, method=method, path=path, body=body, route=route))
    kinds = {(k, s) for k, s, _ in seen}
    assert ("agent", "deleg.spawn") in kinds and ("agent", "fs.read") in kinds
    assert ("agent", "deleg.control") in kinds and ("agent", "deleg.report") in kinds


def test_cli_close_case_still_closes_without_a_server(world, capsys):
    """서버 소켓이 없으면(시험은 VT_AUTHZ_SOCKET=0) 켜짐의 CLI 도 직접 경로로 닫는다 — matrix 의 cli_writes 기록 그대로."""
    import delegation
    assert delegation._cli(["close", "dseed"]) == 0
    x = delegation.get("dseed00001")
    assert x["state"] == "closed" and x["close_reason"] == "closed_by_user"
