"""pair API(`routes/pairs.py`) — 호출자 × 서버 모드 matrix. 기대값은 matrix.json(gen_matrix.py 가 기록).

기록하는 것은 마스터 스위치(`VT_AUTHZ_ENFORCE` 의 `pair`)가 **꺼진** 서버의 동작이다: 도달한 호출자는 모두 409 `pair_disabled`.
환경에 `pair` 가 켜져 있어도 이 파일은 끄고 돈다(켠 뒤의 규칙은 test_authz_pairs*.py).
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.fixture(autouse=True)
def _pair_flag_off(monkeypatch):
    from authz import flags
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(sorted(flags.tokens() - {"pair"})))


@pytest.mark.parametrize("case", cases_for("pairs"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
