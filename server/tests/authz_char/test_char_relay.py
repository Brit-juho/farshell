"""호스트 전환 중계 — B(`routes/peer_relay.py`)의 판정과 `relay_policy` 규칙 전부, A(`routes/host_relay.py`).

B 사례는 판정(서명 → 등급 → 허용 목록)까지만 보고 안쪽 라우트 실행은 멈춘다(`reached`가 그 표시).
A 사례는 바깥 호출을 가짜 업스트림으로 받는다(`reached` = 업스트림까지 갔는가).
"""

from __future__ import annotations

import pytest

import relay_policy
from authz_cases import cases_for


def _relay_or_func(case, world):
    if case.method == "FUNC":
        return {"allowed": relay_policy.ws_allowed(case.path)}
    return world.run(case)


@pytest.mark.parametrize("case", cases_for("relay"), ids=lambda c: c.id)
def test_matrix_b(case, world, golden):
    golden.check(case, _relay_or_func(case, world))


@pytest.fixture
def _peer_flag_off(monkeypatch):
    """A 쪽(`host_relay`)은 3d(H13) 이전의 옛 동작을 고정한다 — 환경에 `peer` 가 켜져 있어도 끄고 돈다(켠 판은 `test_char_peer_enforced.py`)."""
    from authz import flags
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(sorted(flags.tokens() - {"peer"})))


@pytest.mark.usefixtures("_peer_flag_off")
@pytest.mark.parametrize("case", cases_for("host_relay"), ids=lambda c: c.id)
def test_matrix_a(case, world, golden):
    golden.check(case, world.run(case))
