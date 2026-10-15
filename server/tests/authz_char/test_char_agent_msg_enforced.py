"""에이전트 간 메시지 — `VT_AUTHZ_ENFORCE=msg`(3c)에서도 matrix.json 이 성립한다. 의도한 차이만 EXPECTED_CHANGE.

`test_char_agent_msg.py` 와 같은 사례를 플래그를 **켠 채** 돌려 같은 기록과 비교한다. 다른 곳은 아래 둘뿐이고, 사례마다 이유를
적는다(목록은 기록에서 규칙으로 만든다 — 규칙에 안 맞는 차이, 또는 규칙에 맞는데 차이가 없는 사례는 실패):

  H17  보낸 쪽을 확인할 수 없는 호출(기계 토큰만, 사람 세션, 남의 pid, tmux 밖…)은 200(보낸 세션 비어 있음/본문 주장)이 아니라
       403 `identity_unverified` 가 된다. 본문의 pane/tmux/cwd 는 더 이상 보낸 쪽을 정하지 않는다.
  D6   확인된 에이전트(`agent_verified`)가 관계 없는 세션(`target`)에 보내면 승인 전까지 202 보류다. 본문이 다른 칸을 주장해도
       (spoofed/foreign socket 사례) 보낸 쪽은 헤더로 확인된 세션이다. 원격 전송(`remote`)은 받는 호스트가 판정하므로 그대로 200.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for

CURRENT_BEHAVIOR = True

H17 = "H17: 보낸 쪽을 확인할 수 없어 200 대신 403 identity_unverified (본문 pane/tmux 주장은 무시, H2)"
D6 = "D6: 관계 없는 세션 앞으로는 사람의 승인 전까지 202 보류 (보낸 쪽은 헤더로 확인된 세션)"


def _expected_changes(matrix) -> dict[str, str]:
    out = {}
    for c in cases_for("agent_msg"):
        if c.route != "/api/agent/message" or c.method != "POST":
            continue
        var = c.id.rsplit(".", 1)[-1]
        if matrix[c.id]["expected"].get("status") != 200:
            continue                               # 이미 막히던 사례(401 등)는 그대로여야 한다
        if c.caller == "agent_verified":
            if var.startswith("local_"):
                out[c.id] = D6
            continue                               # remote 는 받는 호스트가 판정 — 200 그대로
        out[c.id] = H17
    return out


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")


@pytest.mark.parametrize("case", cases_for("agent_msg"), ids=lambda c: c.id)
def test_matrix_enforced(case, world, golden):
    changes = _expected_changes(golden.matrix)
    actual = world.run(case)
    row = golden.matrix[case.id]
    if case.id in changes:
        assert actual != row["expected"], f"{case.id}: EXPECTED_CHANGE 인데 차이가 없다 — 규칙에서 뺄 것"
        want = {"status": 202, "error": None} if changes[case.id] == D6 else {"status": 403, "error": "identity_unverified"}
        assert actual == want, f"{case.id}: {actual} ≠ 예상한 변경 {want} ({changes[case.id]})"
        return
    assert actual == row["expected"], f"{case.id}: 켠 상태 {actual} ≠ 기록 {row['expected']}"


def test_expected_change_list_is_the_documented_48(golden):
    ch = _expected_changes(golden.matrix)
    assert len(ch) == 48 and sum(v == D6 for v in ch.values()) == 6
