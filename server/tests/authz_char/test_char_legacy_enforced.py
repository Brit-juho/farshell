"""공용 기계 토큰 정리 — `VT_AUTHZ_ENFORCE=legacy`(3e)에서도 matrix.json 이 성립한다. 의도한 차이만 EXPECTED_CHANGE.

`test_char_*.py` 의 모든 사례를 플래그를 **켠 채** 돌려 같은 기록과 비교한다. 소켓·쿠키(로그인한 사람)·peer 서명 사례는 전부 같다.
달라지는 것은 세 갈래뿐이고, 어느 사례가 바뀌는지는 기록(matrix.json) 자체에서 규칙으로 만든다(사례 목록을 손으로 적지 않는다).

  - H1   Authorization: Bearer 로 들어오는 호출자(기계 토큰과 그 토큰을 쓰는 에이전트, 비밀번호·token 모드)는 소켓 허용표 밖의
         (메서드, 경로)에서 403 `legacy_token_scope` 다. 허용표 안 라우트는 그대로다. 호스트 중계는 `reached` 도 거짓이 되고,
         WS 는 핸드셰이크 인증이 실패해 4001 로 닫힌다. 사람 전용 라우트는 옛날엔 403 `human_session_required` 였고 같은 403 의
         다른 이름으로 바뀐다(미들웨어가 먼저 거절).
  - H14  비밀번호 없이 토큰만 있는 서버(token 모드)의 서명 쿠키는 사람이 아니다: 사람 전용 라우트가 200 대신 403 `human_session_required`.
  - D8   같은 token 모드에서 승인 요청을 새로 만들던 호출(202 보류)은 403 `approval_unavailable_no_password`.
"""

from __future__ import annotations

import pytest

from authz_cases import all_cases
from authz import sock

H1 = "H1: 기계 토큰은 소켓 허용표 밖의 라우트를 못 쓴다(403 legacy_token_scope)"
H14 = "H14: 비밀번호 없는 서버의 서명 쿠키는 사람이 아니다(사람 전용 라우트 403 human_session_required)"
D8 = "D8: 비밀번호 없는 서버는 승인 요청을 만들지 않는다(202 대신 403 approval_unavailable_no_password)"
BEARER_CALLERS = ("machine", "agent_verified", "agent_child", "agent_foreign")
SESSION_CALLERS = ("session", "session_elev")
MODES = ("password", "token")
CASES = [c for c in all_cases() if c.method != "FUNC"]


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "legacy")


def _concrete_path(case, world) -> str:
    return world.fill(case.path).split("?", 1)[0]


def _expected_change(case, world, matrix):
    """(이유, 켠 뒤 기대값) 또는 None."""
    old = matrix[case.id]["expected"]
    if case.mode not in MODES or case.group in ("peer", "cli_writes"):
        return None
    method = "GET" if case.method == "WS" else case.method
    if case.caller in BEARER_CALLERS:
        if sock.route_allowed(method, _concrete_path(case, world)):
            if case.mode == "token" and old.get("status") == 202:
                return D8, {**old, "status": 403, "error": "approval_unavailable_no_password"}
            return None
        if case.method == "WS":
            return (H1, {"close": 4001}) if old != {"close": 4001} else None
        if old.get("status") == 401:
            return None
        want = {**old, "status": 403, "error": "legacy_token_scope"}
        if "reached" in old:
            want["reached"] = False
        return H1, want
    if case.caller in SESSION_CALLERS and case.mode == "token" and case.method != "WS":
        # 사람 전용 라우트인지는 기록이 말해 준다: 같은 라우트에서 기계 토큰이 human_session_required 로 거절됐다.
        twin_row = matrix.get(case.id.replace(f".{case.caller}.", ".machine."))
        twin_err = (twin_row or {}).get("expected", {}).get("error")
        if (old.get("status") == 200 or old.get("error") in ("password_required", "not_found")) \
                and twin_err == "human_session_required":       # 승격 필요 라우트(panic)는 사람 확인 뒤에 password_required, 없는 대상은 not_found(사람 확인은 통과한 것)
            return H14, {"status": 403, "error": "human_session_required"}
        if case.group == "leases" and twin_err == "identity_unverified" and old.get("error") != "identity_unverified":
            # lease 라우트는 사람 전용이 아니다 — 사람이 아니게 된 쿠키는 호출자를 확인할 수 없는 기계 토큰과 같은 취급(identity_unverified).
            return H14, {"status": 403, "error": "identity_unverified"}
    return None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_matrix_with_legacy_on(case, world, golden):
    row = golden.matrix[case.id]
    actual = world.run(case)
    change = _expected_change(case, world, golden.matrix)
    if change is None:
        assert actual == row["expected"], f"{case.id}: 켠 상태 {actual} ≠ 기록 {row['expected']}"
        return
    why, want = change
    assert actual != row["expected"], f"{case.id}: EXPECTED_CHANGE 인데 차이가 없다 — 규칙에서 뺄 것"
    assert actual == want, f"{case.id}: {actual} ≠ 예상한 변경 {want} ({why})"


def test_the_three_rules_each_change_something():
    """규칙이 아무것도 못 잡는 죽은 규칙이 되지 않게 — 켠 상태에서 실제로 달라지는 사례 수의 하한."""
    # 실행 결과는 위 매개변수화된 시험이 단언한다. 여기서는 기록만으로 규칙이 닿는 사례가 있는지 센다.
    from authz_harness import World  # noqa: F401  (임포트 가능 확인)
    import json
    from pathlib import Path
    matrix = json.loads((Path(__file__).parent / "matrix.json").read_text(encoding="utf-8"))

    class _W:
        @staticmethod
        def fill(p):
            return p.replace("{rid}", "x").replace("{gid}", "x").replace("{did}", "x").replace("{tmux_name}", "x") \
                .replace("{session_id}", "x").replace("{host_id}", "x").replace("{peer_id}", "x")

    seen = {H1: 0, H14: 0, D8: 0}
    for c in CASES:
        ch = _expected_change(c, _W, matrix)
        if ch:
            seen[ch[0]] += 1
    assert all(n > 0 for n in seen.values()), seen
