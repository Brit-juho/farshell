"""H3 — CLI 가 저장소를 직접 쓴다. 사람 확인이 없어 같은 uid 의 에이전트도 같은 함수를 부를 수 있다.

`bin/fsh host …`는 `python -m host_store <sub>`, `bin/fsh delegate close`는 `python delegation.py close`
로 그대로 넘긴다(아래 마지막 테스트가 그 연결을 본다). 그래서 실제 `bin/fsh`를 돌리지 않고 같은
진입점(`host_store._cli`, `delegation._cli`)을 tmp 상태 폴더 위에서 부른다 — 사용자 홈에 닿지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from authz_cases import cases_for

CURRENT_BEHAVIOR = True


def _after(fn: str, args: list[str]):
    import delegation
    import host_store
    if fn == "delegation._cli":
        x = delegation.get("dseed00001")
        return {"state": x["state"], "close_reason": x.get("close_reason")}
    g = host_store.find_grant(args[1])
    return None if g is None else {"level": g["level"], "agentMessages": bool(g.get("agentMessages"))}


def run_cli(fn: str, args: list[str]) -> dict:
    import delegation
    import host_store
    rc = (delegation._cli if fn == "delegation._cli" else host_store._cli)(list(args))
    return {"rc": rc, "after": _after(fn, args)}


@pytest.mark.parametrize("case", cases_for("cli_writes"), ids=lambda c: c.id)
def test_matrix(case, world, golden, capsys):
    world.set_mode(case.mode if case.mode in ("password", "token", "none") else "password")
    golden.check(case, run_cli(case.route, case.path.split()))


def test_allow_control_and_allow_agent_raise_a_peer_with_no_human_check(world, capsys):
    """에이전트가 셸에서 `fsh host allow-control <id>`·`allow-agent`를 치면 그대로 올라간다."""
    import host_store
    assert host_store._cli(["allow-control", "pv"]) == 0
    assert host_store._cli(["allow-agent", "pv"]) == 0
    g = host_store.find_grant("pv")
    assert g["level"] == host_store.LEVEL_CONTROL and g["agentMessages"] is True


def test_delegation_close_by_cli_skips_the_human_session_gate(world, capsys):
    """같은 동작의 HTTP 판(close-by-user)은 사람 세션만 되지만, CLI 진입점은 아무 확인 없이 닫는다."""
    from authz_harness import Case
    r = world.run(Case(id="adhoc", group="adhoc", caller="machine", method="POST",
                       path="/api/delegations/dseed00001/close-by-user"))
    assert r == {"status": 403, "error": "human_session_required"}
    import delegation
    assert delegation._cli(["close", "dseed"]) == 0
    assert delegation.get("dseed00001")["state"] == delegation.CLOSED


def test_bin_fsh_routes_to_these_entry_points():
    """이 파일이 부르는 함수가 실제 `bin/fsh`가 부르는 것과 같다는 근거(실행하지 않고 본문만 본다)."""
    src = (Path(__file__).resolve().parents[3] / "bin" / "fsh").read_text(encoding="utf-8")
    assert '-m host_store "$sub" "$@"' in src
    assert 'delegation.py "$sub" "$@"' in src
    assert "allow-control|deny-control|allow-agent|deny-agent" in src
