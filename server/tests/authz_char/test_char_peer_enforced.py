"""peer·relay·host 중계 — `VT_AUTHZ_ENFORCE=peer`(3d)에서도 matrix.json 이 성립한다. 의도한 차이만 EXPECTED_CHANGE.

`test_char_peer.py`·`test_char_relay.py`·`test_char_hosts.py` 와 같은 사례를 플래그를 **켠 채** 돌려 같은 기록과 비교한다.

  - 받는 쪽(`peer`, `relay`, `machine_token`, `cli_writes`): **전부 같다.** 등급(view/control)·`agentMessages` 가 만들던 허용·거부와 응답 본문이
    라우트마다 그대로다(parity). 예외 없음.
  - H13  이 서버에 붙은 호출자가 다른 맥을 조작하는 길(`/api/hosts/{id}/*`, `/relay/*`, `/ws/hosts/{id}/relay/*`). 켜지면 호출자가
         `host:<id>` 에 필요한 scope 를 가져야 한다: 명시한 기계 토큰과 그 토큰을 쓰는 에이전트(비밀번호·token 모드)는 403
         `host_access_denied`(WS 는 4003)다. 로그인한 사람과 인증 수단이 없는 서버의 무인증 호출은 그대로다.
  - H3   새 라우트 `POST /api/host-grants/{id}/change` — 꺼짐에서는 409 `peer_enforce_off`, 켜지면 HTTP(토큰·쿠키) 호출자는 403
         `local_caller_required`(unix socket 의 cli·에이전트만 요청을 만들 수 있다).
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for

H13 = "H13: 기계 토큰·에이전트는 다른 맥을 조작할 수 없다(403 host_access_denied / WS 4003)"
H3 = "H3: HTTP 호출자는 호스트 권한 변경 요청을 만들 수 없다(403 local_caller_required)"
H13_CALLERS = ("machine", "agent_verified")
H13_MODES = ("password", "token")


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer")


def _expected_changes(matrix) -> dict[str, tuple[str, dict]]:
    """사례 id -> (이유, 켠 뒤의 기대값). 기록에서 규칙으로 만든다."""
    out: dict[str, tuple[str, dict]] = {}
    for c in cases_for("hosts"):
        row = matrix[c.id]["expected"]
        if c.route == "/api/host-grants/{peer_id}/change":
            if row.get("status") != 401:
                out[c.id] = (H3, {"status": 403, "error": "local_caller_required"})
        elif c.route != "/api/hosts/self" and c.caller in H13_CALLERS and c.mode in H13_MODES:
            out[c.id] = (H13, {"status": 403, "error": "host_access_denied"})
    for c in cases_for("host_relay"):
        if c.caller in H13_CALLERS and c.mode in H13_MODES:
            ws = c.method == "WS"
            want = {"close": 4003} if ws else {"status": 403, "error": "host_access_denied", "reached": False}
            if matrix[c.id]["expected"] != want:        # 허용 목록 밖 WS 는 옛 거절도 같은 4003 이라 겉으로는 변화가 없다
                out[c.id] = (H13, want)
    return out


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


@pytest.mark.parametrize("case", [c for c in cases_for("relay") if c.method != "FUNC"], ids=lambda c: c.id)
def test_relay_b_is_identical(case, world, golden):
    _check(case, world, golden)


@pytest.mark.parametrize("case", cases_for("machine_token"), ids=lambda c: c.id)
def test_machine_token_group_is_identical(case, world, golden):
    _check(case, world, golden)


@pytest.mark.parametrize("case", cases_for("hosts"), ids=lambda c: c.id)
def test_hosts_routes(case, world, golden):
    _check(case, world, golden)


@pytest.mark.parametrize("case", cases_for("host_relay"), ids=lambda c: c.id)
def test_host_relay_a(case, world, golden):
    _check(case, world, golden)


def test_expected_change_list_is_the_documented_set(golden):
    ch = _expected_changes(golden.matrix)
    by = {}
    for why, _ in ch.values():
        by[why] = by.get(why, 0) + 1
    hosts_h13 = sum(1 for i, (w, _) in ch.items() if w == H13 and i.startswith("hosts."))
    relay_h13 = sum(1 for i, (w, _) in ch.items() if w == H13 and i.startswith("host_relay."))
    # hosts: 라우트 9 × 호출자 2 × 모드 2 / host_relay: 호출자 2 × 모드 2 × 9(옛 응답이 이미 4003 인 WS 하나는 겉으로 변화 없음)
    assert (hosts_h13, relay_h13) == (36, 36), (hosts_h13, relay_h13)
    assert by[H3] == 14, by
