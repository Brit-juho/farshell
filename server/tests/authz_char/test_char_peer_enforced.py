"""peer·relay·host 중계 — `VT_AUTHZ_ENFORCE=peer`(3d)에서도 matrix.json 이 성립한다. 의도한 차이만 EXPECTED_CHANGE.

`test_char_peer.py`·`test_char_relay.py`·`test_char_hosts.py` 와 같은 사례를 플래그를 **켠 채** 돌려 같은 기록과 비교한다.

  - 받는 쪽(`peer`, `relay`, `machine_token`, `cli_writes`): **전부 같다.** 등급(view/control)·`agentMessages` 가 만들던 허용·거부와 응답 본문이
    라우트마다 그대로다(parity). 예외 없음.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for



@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer")


def _expected_changes(matrix) -> dict[str, tuple[str, dict]]:
    return {}                                   # 받는 쪽은 예외 없이 기록과 같다


def _check(case, world, golden):
    changes = _expected_changes(golden.matrix)
    actual = world.run(case)
    row = golden.matrix[case.id]
    if case.id in changes:
        why, want = changes[case.id]
        assert actual != row["expected"], f"{case.id}: EXPECTED_CHANGE 인데 차이가 없다 — 규칙에서 뺄 것"
        assert actual == want, f"{case.id}: {actual} ≠ 예상한 변경 {want} ({why})"
        return
    assert actual == row["expected"], f"{case.id}: 켠 상태 {actual} ≠ 기록 {row['expected']}"


@pytest.mark.parametrize("case", cases_for("peer"), ids=lambda c: c.id)
def test_peer_routes_are_identical(case, world, golden):
    _check(case, world, golden)


@pytest.mark.parametrize("case", cases_for("machine_token"), ids=lambda c: c.id)
def test_machine_token_group_is_identical(case, world, golden):
    _check(case, world, golden)
